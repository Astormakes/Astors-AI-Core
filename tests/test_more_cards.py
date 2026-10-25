"""Schedule, HA event, Heartbeat, HA Assist, Decide, Memory, File, Timer, Builder."""

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import conversation
from homeassistant.core import Context as HaContext, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.aac.flow import Step
from custom_components.aac.context import Context

from .conftest import model_answers, reply, setup_aac


async def test_a_schedule_fires_at_its_time(hass: HomeAssistant, mock_entry, aioclient_mock, freezer: FrozenDateTimeFactory):
    """A Schedule entry at 05:25 wakes the chain; the answer lands in its log."""
    asked = model_answers(aioclient_mock, reply("The bed light is on."))
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    schedule = await d.add_card("schedule", "Schedule", {"entries": [{"name": "Bed light", "at": "05:25", "weekdays": [],
                                                                       "text": "Turn on the bed light.", "enabled": True}]}, card_id="schedule")
    d.set_wires([*d.wires, {"from": "schedule.out", "to": "charter.in"}, {"from": "model.out", "to": "schedule.answers"}])
    freezer.move_to(dt_util.as_utc(datetime(2026, 10, 26, 5, 25, 0, tzinfo=dt_util.get_default_time_zone())))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "Turn on the bed light." in asked[-1]["messages"][-1]["content"]
    assert schedule.data["log"][-1]["answer"] == "The bed light is on."


async def test_ha_event_and_heartbeat_fire(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A watched entity change starts the chain (once, cooldown)."""
    core = await setup_aac(hass, mock_entry)
    event = await core.domain.add_card("ha_event", "Door", {"watches": [{"name": "door", "entity_id": "binary_sensor.door", "to": "on", "text": "Tell me."}]})
    fired = []
    event.fire = lambda name, text: fired.append(text)
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("binary_sensor.door", "on")
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("binary_sensor.door", "on")
    await hass.async_block_till_done()
    assert len(fired) == 1 and fired[0].endswith("Tell me.")
    beat = await core.domain.add_card("heartbeat", "Beat", {"quiet_from": "00:00", "quiet_to": "00:00"})
    assert not beat._quiet(dt_util.now())


async def test_ha_conversations_go_to_whoever_takes_the_channel(hass: HomeAssistant, mock_entry, aioclient_mock):
    """HA's Assist reaches the card on the core's ha_conversation channel: the Chat (a chat "Assist · …", a follow-up
    continues it), nobody (a clear error), or a card of your own."""
    from custom_components.aac.cards import Card, register

    core = await setup_aac(hass, mock_entry)
    model_answers(aioclient_mock, reply("It is noon."))
    result = await conversation.async_converse(hass, "what time is it?", None, HaContext(), agent_id=mock_entry.entry_id)
    assert result.response.speech["plain"]["speech"] == "It is noon."
    chat = core.domain.cards["chat"].get(result.conversation_id)
    assert chat["title"].startswith("Assist · Home Assistant · ") and chat["messages"][0]["text"] == "what time is it?"
    await core.domain.update_card("chat", settings={"ha_agent": False})
    result = await conversation.async_converse(hass, "hello?", None, HaContext(), agent_id=mock_entry.entry_id)
    assert result.response.error_code is not None

    @register
    class Echo(Card):  # a card of your own
        type = "test_echo"

        async def start(self) -> None:
            self.core.subscribe("ha_conversation", self, self.converse)

        async def converse(self, text, conversation_id, where):
            return f"{where} said: {text}"
    await core.domain.add_card("test_echo", "Echo")
    result = await conversation.async_converse(hass, "ping", None, HaContext(), agent_id=mock_entry.entry_id)
    assert result.response.speech["plain"]["speech"] == "Home Assistant said: ping"


async def test_decide_picks_an_output(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The model's word picks the output."""
    core = await setup_aac(hass, mock_entry)
    decide = await core.domain.add_card("decide", "For me?", {"outputs": [{"name": "yes", "description": "for AAC"}, {"name": "no", "description": "else"}]})
    yes = await core.domain.add_card("relay", "Go")
    core.domain.set_wires([*core.domain.wires, {"from": "for_me.yes", "to": "go.in"}, {"from": "models.models", "to": "for_me.pool"}])
    model_answers(aioclient_mock, reply("yes"))
    context = Context()
    context.add("chat", "said", "computer, lights on")
    assert await decide.handle(context, "in", Step(core.domain.flow, decide, "in", context)) == [("yes", yes.id)]


async def test_memory_file_timer_builder(hass: HomeAssistant, mock_entry):
    """Memory by words, a file, a timer, the builder within its permissions."""
    core = await setup_aac(hass, mock_entry)
    memory = await core.domain.add_card("memory", "Memory")
    await memory.call("remember", {"text": "The drill is on basement shelf 2."})
    context = Context()
    context.add("chat", "said", "where is the drill?")
    await memory.handle(context, "in", None)
    assert "basement shelf 2" in context.last("note")["text"]
    file = await core.domain.add_card("file", "Notes", {"name": "test"})
    await file.call("write", {"text": "# Hi"})
    await file.call("append", {"text": "more"})
    assert await file.call("read", {}) == "# Hi\nmore\n"
    timer = await core.domain.add_card("timer", "Wait", {"seconds": 0})
    assert await timer.handle(Context(), "in", None) is None
    builder = await core.domain.add_card("builder", "Builder")
    added = await builder.call("add_card", {"type": "buffer", "name": "Room"})
    assert added["id"] == "room" and (await builder.call("remove_card", {"card": "room"}))["error"]
    assert (await builder.call("wire", {"from": "room.out", "to": "notes.in"}))["wired"]


async def test_calendar_card_and_entity(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The default Calendar card is the model's tool; its entries and dated list entries show in calendar.aac_calendar."""
    from datetime import timedelta

    core = await setup_aac(hass, mock_entry)
    cal = core.domain.cards["calendar"]
    assert {"from": "model.out", "to": "calendar.in"} in core.domain.wires
    tomorrow = (dt_util.now() + timedelta(days=1)).date().isoformat()
    assert (await cal.call("add", {"summary": "Dentist", "start": f"{tomorrow}T10:00"}))["done"] == "add"
    lists = core.domain.cards["lists"]
    await lists.call("create_list", {"list": "Errands"})
    await lists.call("add_items", {"list": "Errands", "items": ["Pay rent"]})
    item = lists.data["lists"][-1]["items"][0]
    item["fields"]["due"] = tomorrow
    found = await cal.call("calendar", {})
    assert [e["summary"] for e in found] == ["Pay rent (Errands)", "Dentist"]
    uid = next(e["uid"] for e in found if e["summary"] == "Dentist")
    await cal.call("change", {"uid": uid, "summary": "Dentist (moved)"})
    await hass.async_block_till_done()
    state = hass.states.get("calendar.aac_calendar")
    assert state is not None
    start, end = dt_util.now(), dt_util.now() + timedelta(days=3)
    events = await hass.services.async_call("calendar", "get_events", {"entity_id": "calendar.aac_calendar", "start_date_time": start,
                                                                        "end_date_time": end}, blocking=True, return_response=True)
    assert {e["summary"] for e in events["calendar.aac_calendar"]["events"]} == {"Pay rent (Errands)", "Dentist (moved)"}
    await cal.call("remove", {"uid": uid})
    assert len(await cal.call("calendar", {})) == 1


async def test_card_pages_through_nav_and_ui(hass: HomeAssistant, mock_entry, aioclient_mock, hass_ws_client):
    """Cards with a page are in the sidebar with their entries (aac/nav); their page talks to them through aac/card/ui."""
    core = await setup_aac(hass, mock_entry)
    card = core.domain.cards["chat"]
    chat = card._new("Test")
    await card.save()
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "aac/nav"})
    nav = {n["card"]: n for n in (await client.receive_json())["result"]["nav"]}
    assert nav["chat"]["element"] == "aac-card-chat" and nav["chat"]["ui"].startswith("/aac_cards/chat/page.js")
    assert chat["id"] in [e["id"] for e in nav["chat"]["nav"]["entries"]] and "lists" in nav
    await client.send_json_auto_id({"type": "aac/card/ui", "card": "chat", "action": "delete", "args": {"chat_id": chat["id"]}})
    assert (await client.receive_json())["success"]
    assert chat["id"] not in [c["id"] for c in card.chats()]
    await client.send_json_auto_id({"type": "aac/card/ui", "card": "lists", "action": "delete", "args": {}})
    assert not (await client.receive_json())["success"]  # Lists' page has no such action


async def test_models_have_tags_and_memory_uses_embedding_models(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Models carry tags (guessed from the name until set); Memory uses only embedding models from its list."""
    from .conftest import BASE_URL

    core = await setup_aac(hass, mock_entry)
    core.save_model({"id": "m_bge", "name": "bge-m3", "base_url": BASE_URL, "model": "bge-m3"})
    core.save_model({"id": "m_whisper", "name": "Whisper", "base_url": BASE_URL, "model": "large-v3-turbo", "tags": ["speech to text"]})
    assert core.pool.tags("m_bge") == ["embeddings"] and core.pool.tags("m_big") == ["text", "tools"]
    memory = await core.domain.add_card("memory", "Memory", {"models": [{"model": "m_big", "queue": True}, {"model": "m_bge", "queue": True}]})
    assert core.pool.entries(memory.settings["models"], "embeddings") == [{"model": "m_bge", "queue": True}]  # not for text models
    aioclient_mock.post(f"{BASE_URL}/embeddings", json={"data": [{"embedding": [1.0, 0.0]}]})
    await memory.call("remember", {"text": "The drill is on basement shelf 2."})
    assert memory.data["memories"][0]["vector"] == [1.0, 0.0]
    assert [m["text"] for m in await memory.find("where is the drill?", 3)] == ["The drill is on basement shelf 2."]
    assert aioclient_mock.mock_calls[-1][2]["model"] == "bge-m3"


async def test_busy_models_queue_or_drop(hass: HomeAssistant, mock_entry):
    """Down the list: a busy model queues the context when its entry says so and the next free one takes it; the first
    to get free takes a waiting context (it leaves the other queues); with no queue ticked it is dropped."""
    import asyncio

    core = await setup_aac(hass, mock_entry)
    pool = core.pool
    both = [{"model": "m_big", "queue": True}, {"model": "m_small", "queue": True}]
    assert await pool._take(both) == "m_big" and await pool._take(both) == "m_small"  # one request each: both busy
    assert await pool._take([{"model": "m_big", "queue": False}, {"model": "m_small", "queue": False}]) is None  # dropped
    waiting = asyncio.ensure_future(pool._take(both))
    await asyncio.sleep(0)
    assert [m["waiting"] for m in pool.summary()] == [1, 1]  # in both queues
    pool._release("m_small")  # small is done first: the waiting context gets it
    assert await waiting == "m_small" and [m["waiting"] for m in pool.summary()] == [0, 0]
    for model_id in ("m_big", "m_small"):
        pool._release(model_id)
    assert [m["busy"] for m in pool.summary()] == [0, 0]


async def test_chat_finds_and_switches_to_old_chats(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A model looks up older chats and switches to one: when the turn is done, that chat is the current one and the
    asking turn is not kept (a chat that was only that turn goes)."""
    core = await setup_aac(hass, mock_entry)
    card = core.domain.cards["chat"]
    old, other = card._new("About the drone"), card._new("Groceries")
    old["entries"] = [{"id": "e1", "by": "chat", "kind": "said", "text": "the LoRa mast drone", "tags": [], "time": "", "data": {}, "state": "closed"}]
    old["updated"], other["updated"] = "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"
    await card.save()
    found = await card.call("chats", {"search": "lora"})
    assert [c["id"] for c in found] == [old["id"]] and found[0]["first"] == "the LoRa mast drone"
    assert (await card.call("read", {"chat": old["id"]}))[0]["text"] == "the LoRa mast drone"
    assert card.current() is None  # both quiet for long: the next spoken turn would start a new chat
    model_answers(aioclient_mock, reply(calls=[("chat__continue", {"chat": old["id"]})]), reply("Back to the drone."))
    before = {c["id"] for c in card.chats()}
    chat_id, answer = await card.send("let's go on with the drone chat")
    assert chat_id == old["id"] and answer == "Back to the drone."
    assert card.current()["id"] == old["id"]
    assert {c["id"] for c in card.chats()} == before  # the asking chat is gone
    assert [e["text"] for e in card.get(old["id"])["entries"]] == ["the LoRa mast drone"]  # the switch is not kept


async def test_home_access_list(hass: HomeAssistant, mock_entry):
    """The Home card's access list: its entries come from HA, anything not set is hidden; an old 'everything else'
    level is dropped when the card is loaded."""
    core = await setup_aac(hass, mock_entry)
    hass.states.async_set("light.desk", "on", {"friendly_name": "Desk"})
    home = core.domain.cards["home"]
    items = await home.ui_call("access_items", {})
    assert {"id": "light.desk", "name": "Desk", "area": "", "group": "light"} in items
    assert home.level("light.desk") == "hidden"
    assert type(home).upgrade({"default": "read", "levels": {"light.desk": "full"}}, "1.1.0") == {"levels": {"light.desk": "full"}}


async def test_log_takes_anything(hass: HomeAssistant, mock_entry):
    """The Log card's input takes every kind; it writes down contexts, clips, audio start and stop, and what is new on
    a dotted wire (a Chat's provides)."""
    import asyncio
    import time as time_mod
    from unittest.mock import patch

    core = await setup_aac(hass, mock_entry)
    d = core.domain
    log = await d.add_card("log", "Log")
    d.set_wires([*d.wires, {"from": "chat.provides", "to": f"{log.id}.in"}, {"from": "model.out", "to": f"{log.id}.in"}])
    assert {"from": "chat.provides", "to": f"{log.id}.in"} in d.wires and {"from": "model.out", "to": f"{log.id}.in"} in d.wires
    c = Context(origin="chat")
    c.way = ["model", log.id]
    c.add("model", "answer", "Hello there", ["answer"])
    await log.handle(c, "in", None)
    await log.clip("in", {"pcm": b"\x01\x00" * 160, "seconds": 1.4, "name": "PC", "part": 2})
    log.stream("in", b"\x00\x00", {"source": "pc", "name": "PC"})
    log._audio["PC"] = [time_mod.monotonic() - 5, time_mod.monotonic() - 3]  # quiet for a while
    with patch(f"{type(log).__module__}.READ_EVERY", 0):
        task = hass.async_create_task(log._watch())
        await asyncio.sleep(0.05)
        chat = d.cards["chat"]
        chat._new("Talk")["entries"].append({"id": "n1", "by": "chat", "kind": "said", "text": "a new line", "tags": [], "time": "", "data": {}, "state": "closed"})
        await asyncio.sleep(0.05)
        task.cancel()
    texts = [(x["what"], x["text"]) for x in log.data["log"]]
    kept = next(x for x in log.data["log"] if x["what"] == "clip")
    assert kept["audio"].endswith(".wav") and (await log.ui_call("audio", {"file": kept["audio"]}))  # playable
    await log.ui_call("clear", {})
    assert not list(log._folder.glob("*.wav"))  # cleared with the log
    import shutil
    shutil.rmtree(log._folder.parent, ignore_errors=True)  # HA's test config folder is shared: leave nothing behind
    assert ("context", "answer #model.answer: Hello there  [1 entries]") in texts
    assert ("clip", "1.4 s, phrase 2") in texts and ("audio", "started") in texts
    assert any(w == "audio" and t.startswith("stopped") for w, t in texts) and ("read", "said : a new line") in texts


async def test_chat_archive(hass: HomeAssistant, mock_entry):
    """Sidebar actions: archive moves a chat into 'Archive (n)'; an archived chat is never the current one, and models
    see it marked but cannot read or continue it; restore and delete work the same way."""
    core = await setup_aac(hass, mock_entry)
    card = core.domain.cards["chat"]
    chat = card._new("Old talk")
    chat["entries"] = [{"id": "e1", "by": "chat", "kind": "said", "text": "hello", "tags": [], "time": "", "data": {}, "state": "closed"}]
    await card.save()
    entry = next(e for e in card.nav()["entries"] if e["id"] == chat["id"])
    assert [a["id"] for a in entry["actions"]] == ["delete", "archive", "rename"]
    await card.ui_call("nav_action", {"entry": chat["id"], "action": "archive"})
    nav = card.nav()["entries"]
    assert chat["id"] not in [e["id"] for e in nav] and nav[-1]["id"] == "archive" and nav[-1]["title"] == "Archive (1)"
    assert [c["id"] for c in await card.ui_call("archived", {})] == [chat["id"]]
    assert (card.current() or {}).get("id") != chat["id"]
    assert next(c for c in await card.call("chats", {}) if c["id"] == chat["id"])["archived"] is True
    assert "error" in await card.call("read", {"chat": chat["id"]}) and "error" in await card.call("continue", {"chat": chat["id"]})
    await card.ui_call("nav_action", {"entry": chat["id"], "action": "rename", "value": "Renamed"})
    await card.ui_call("nav_action", {"entry": chat["id"], "action": "restore"})
    assert next(e for e in card.nav()["entries"] if e["id"] == chat["id"])["title"] == "Renamed"
    await card.ui_call("nav_action", {"entry": chat["id"], "action": "delete"})
    assert chat["id"] not in [c["id"] for c in card.chats()]


async def test_model_servers(hass: HomeAssistant, mock_entry, aioclient_mock, hass_ws_client):
    """A llama-server router behind the pool: the Models page and the Model servers card list its models with their
    state and numbers; Use lets an AAC model ask for another one (and loads it); download and delete go to the server."""
    core = await setup_aac(hass, mock_entry)
    root = "http://llm.local:8080"
    aioclient_mock.get(f"{root}/models", json={"data": [
        {"id": "big", "status": {"value": "loaded", "args": ["llama-server", "--ctx-size", "16384"]}, "can_remove": False,
         "source": "preset", "context_length": 262144, "architecture": {"input_modalities": ["text"]}},
        {"id": "user/new-GGUF:Q4_K_M", "status": {"value": "downloading"}, "can_remove": True, "source": "cache"}]})
    aioclient_mock.get(f"{root}/models/sse", text='data: {"model": "user/new-GGUF:Q4_K_M", "event": "download_progress", '
                       '"data": {"progress": {"https://x/a.gguf": {"done": 25, "total": 100}}}}\n\n')  # the list has no progress
    aioclient_mock.get(f"{root}/v1/models", json={"data": [{"id": "big", "meta": {"n_params": 11.9e9, "size": 6.7e9, "ftype": "Q4_0", "n_ctx": 16384}}]})
    aioclient_mock.get(f"{root}/props", json={"chat_template_caps": {"supports_tools": True}, "build_info": "b1-test"})
    aioclient_mock.get(f"{root}/metrics", text="# HELP x\nllamacpp:prompt_tokens_seconds 1043.8\nllamacpp:predicted_tokens_seconds 45.2\n")
    for path in ("/models/load", "/models/unload", "/models"):
        aioclient_mock.post(f"{root}{path}", json={"success": True})
    aioclient_mock.delete(f"{root}/models", json={"success": True})

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "aac/models/servers"})
    (server,) = (await client.receive_json())["result"]["servers"]  # both AAC models are on the one address
    assert server["router"] and server["address"] == root and [u["name"] for u in server["uses"]] == ["Big", "Small"]
    big, new = server["models"]
    assert big["status"] == "loaded" and big["quant"] == "Q4_0" and big["tools"] and big["speed"] == {"read": 1044, "write": 45}
    assert big["context"] == 16384 and big["context_max"] == 262144
    assert new["status"] == "downloading" and new["progress"] == 25 and new["can_remove"]

    await client.send_json({"id": 2, "type": "aac/models/router", "model_id": "m_small", "action": "use", "name": "big"})
    assert (await client.receive_json())["success"]
    assert core.pool.get("m_small")["model"] == "big"
    assert any(m[0] == "POST" and str(m[1]).endswith("/models/load") and m[2] == {"model": "big"} for m in aioclient_mock.mock_calls)
    await client.send_json({"id": 3, "type": "aac/models/router", "model_id": "m_big", "action": "download",
                            "name": "user/new-GGUF:Q4_K_M", "sidecars": ["mtp"]})
    assert (await client.receive_json())["success"]
    assert any(m[0] == "POST" and str(m[1]) == f"{root}/models" and m[2] == {"model": "user/new-GGUF:Q4_K_M", "sidecars": ["mtp"]}
               for m in aioclient_mock.mock_calls)
    await client.send_json({"id": 4, "type": "aac/models/router", "model_id": "m_big", "action": "delete", "name": "user/new-GGUF:Q4_K_M"})
    assert (await client.receive_json())["success"]
    assert any(m[0] == "DELETE" and m[1].query.get("model") == "user/new-GGUF:Q4_K_M" for m in aioclient_mock.mock_calls)

    card = await core.domain.add_card("model_servers", "Servers")
    (listed,) = await card.call("servers", {})
    assert listed["aac_models"] == ["Big (uses big)", "Small (uses big)"]
    assert listed["offers"][1] == {"model": "user/new-GGUF:Q4_K_M", "state": "downloading", "downloaded": "25 %"}
    assert await card.call("use", {"aac_model": "big", "model": "user/new-GGUF:Q4_K_M"}) == {"done": "Big uses user/new-GGUF:Q4_K_M"}
    assert core.pool.get("m_big")["model"] == "user/new-GGUF:Q4_K_M"
    assert "error" in await card.call("use", {"aac_model": "nobody", "model": "big"})
