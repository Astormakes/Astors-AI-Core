"""Schedule, HA event, Heartbeat, HA Assist, Decide, Memory, File, Timer, Builder."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import conversation
from homeassistant.core import Context as HaContext, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.aac.flow import Step
from custom_components.aac.context import Context

from .conftest import model_answers, reply, setup_aac


async def test_a_schedule_fires_at_its_time(hass: HomeAssistant, mock_entry, aioclient_mock, freezer: FrozenDateTimeFactory):
    """A Schedule entry at 05:25 wakes the chain; the answer lands in its log. Entries saved as a setting (before 1.3.0)
    become its data, the owner's."""
    asked = model_answers(aioclient_mock, reply("The bed light is on."))
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    schedule = await d.add_card("schedule", "Schedule")
    old = {"entries": [{"name": "Bed light", "at": "05:25", "weekdays": [], "text": "Turn on the bed light.", "enabled": True}]}
    d._specs[schedule.id].update(version="1.2.0", settings=old)  # as 1.2.0 saved it
    await d.save_card_data(schedule.id, {"log": []})
    schedule = await d.remake(schedule.id)
    assert "entries" not in d._specs[schedule.id]["settings"] and [(e["name"], e["by"]) for e in schedule.entries] == [("Bed light", "owner")]
    d.set_wires([*d.wires, {"from": f"{schedule.id}.out", "to": "charter.in"}, {"from": "model.out", "to": f"{schedule.id}.answers"}])
    at = (dt_util.now() + timedelta(days=1)).replace(hour=5, minute=25, second=0, microsecond=0)  # always ahead
    freezer.move_to(dt_util.as_utc(at))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "Turn on the bed light." in asked[-1]["messages"][-1]["content"]
    assert schedule.data["log"][-1]["answer"] == "The bed light is on."


async def test_schedule_like_v1(hass: HomeAssistant, mock_entry, aioclient_mock, freezer: FrozenDateTimeFactory):
    """v1's time triggers (progress 39): entries are data (no Undo step), each says who made it; AAC touches only its own,
    at most 20, as the owner allows (ask: it waits for the OK); day names; AAC's entries wait out quiet hours; an entry
    the model fails on goes out at 'unanswered'; its times are on AAC's calendar (through the core)."""
    from custom_components.aac.cards.schedule.card import days

    assert days("Mon-Fri") == [0, 1, 2, 3, 4] and days("weekend") == [5, 6] and days(["mo", "Thursday"]) == [0, 3]
    assert days("Fr-Mo") == [0, 4, 5, 6] and days("") == [] and days([1, 2]) == [1, 2]
    model_answers(aioclient_mock, {"error": "boom"})
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    s = await d.add_card("schedule", "Schedule", {"quiet_from": "22:00", "quiet_to": "07:00", "answer_within": 5})
    await d.add_card("relay", "Catch")
    d.set_wires([*d.wires, {"from": "schedule.out", "to": "charter.in"}, {"from": "model.out", "to": "schedule.answers"},
                 {"from": "schedule.unanswered", "to": "catch.in"}])
    await s.ui_call("save", {"entry": {"name": "Alarm", "at": "06:30", "weekdays": [0, 1, 2, 3, 4], "text": "Wake up."}})
    undo = len(d.undo)
    assert (await s.call("add", {"name": "Tea", "at": "23:15", "text": "Say: the tea is ready.", "days": "daily"}))["done"] == "add"
    assert len(d.undo) == undo  # an entry is no Undo step
    assert "owner's entry" in (await s.call("remove", {"name": "Alarm"}))["error"]
    assert "owner's entry" in (await s.call("change", {"name": "Alarm", "at": "07:00"}))["error"]
    assert (await s.call("change", {"name": "Tea", "days": "Mon-Fri"}))["done"] == "change"
    assert next(e for e in s.entries if e["name"] == "Tea")["weekdays"] == [0, 1, 2, 3, 4]
    assert "HH:MM" in (await s.call("add", {"name": "Bad", "at": "soon", "text": "x"}))["error"]

    s = await d.update_card("schedule", settings={"plans": "ask"})
    got = await s.call("add", {"name": "Plants", "at": "18:00", "text": "Water the plants.", "date": "2099-01-05"})
    plants = next(e for e in s.entries if e["name"] == "Plants")
    assert got["waiting"] and plants["proposed"] and not plants["enabled"]
    await s.ui_call("allow", {"id": plants["id"], "ok": True})
    assert plants["enabled"] and "proposed" not in plants
    s = await d.update_card("schedule", settings={"plans": "off"})
    assert "add" not in s.offered() and "run" in s.offered() and "not allow" in (await s.call("add", {"name": "X", "at": "10:00", "text": "x"}))["error"]
    s = await d.update_card("schedule", settings={"plans": "allowed"})
    for i in range(19):
        await s.call("add", {"name": f"P{i}", "at": "12:00", "text": "x", "date": "2099-01-01"})
    assert f"{20} entries" in (await s.call("add", {"name": "One more", "at": "12:00", "text": "x"}))["error"]

    from datetime import datetime as dt_
    start = dt_util.start_of_local_day(dt_.fromisoformat("2099-01-05"))
    on_cal = [e for e in core.calendar.entries(start, start + timedelta(days=1)) if e["source"] == "schedule"]
    assert {e["summary"] for e in on_cal} >= {"Alarm", "Tea", "Plants"} and all(not e["can"] for e in on_cal)

    tea_at = (dt_util.now() + timedelta(days=7 - dt_util.now().weekday())).replace(hour=23, minute=15, second=0, microsecond=0)  # a Monday
    freezer.move_to(dt_util.as_utc(tea_at))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not any(x["name"] == "Tea" for x in s.data["log"]) and s.data["waiting"]  # quiet: AAC's entry waits
    freezer.move_to(dt_util.as_utc(tea_at.replace(hour=7, minute=0) + timedelta(days=1)))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    tea = next(x for x in s.data["log"] if x["name"] == "Tea")
    assert not s.data["waiting"] and tea["failed"] and tea["unanswered"]  # the model failed: it went out at 'unanswered'
    sent = d.flow.last["schedule.unanswered"]["messages"][-1]
    assert sent["kind"] == "answer" and "Not answered" in sent["text"] and "the tea is ready" in sent["text"]
    assert d.log.read(card="schedule", what="unanswered") and not d.log.read(levels=["error"])  # a model error, no crash

    d.set_wires([w for w in d.wires if w["to"] != "schedule.answers"])  # nothing comes back at all: after 5 minutes
    s._ring(next(e for e in s.entries if e["name"] == "Alarm"), "07:00")
    await hass.async_block_till_done(wait_background_tasks=True)
    alarm = s.data["log"][-1]
    assert alarm["answer"] is None and not alarm.get("unanswered")
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert alarm["unanswered"] and "failed" not in alarm and "Wake up." in d.flow.last["schedule.unanswered"]["messages"][-1]["text"]


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
    assert "messages" not in core.domain.cards["chat"].data["chats"][result.conversation_id]  # what it shows is not stored
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
    await memory.call("remember", {"text": "The car is blue."})
    missed = await memory.call("forget", {"text": "the"})  # a word is not a memory: nothing goes, the close ones are named
    assert missed["error"] and len(memory.data["memories"]) == 2 and missed["close"]
    car = next(m["id"] for m in memory.data["memories"] if "car" in m["text"])
    assert (await memory.call("forget", {"text": car}))["memories"] == 1
    assert (await memory.call("forget", {"text": "the drill is on basement shelf 2."}))["memories"] == 0  # its whole text
    file = await core.domain.add_card("file", "Notes", {"name": "test"})
    await file.call("write", {"text": "# Hi"})
    await file.call("append", {"text": "more"})
    assert await file.call("read", {}) == "# Hi\nmore\n"
    (held,) = file.held("provides").entries  # its read output: the file as one entry
    assert held["text"] == "# Hi\nmore\n" and held["tags"] == ["notes.text"]
    again = await core.domain.add_card("file", "Same", {"name": "test"})  # reads the file when it starts
    assert again.held("provides").entries[0]["text"] == "# Hi\nmore\n"
    await core.domain.add_card("relay", "Reader")
    core.domain.set_wires([*core.domain.wires, {"from": "notes.provides", "to": "reader.reads"}])
    assert {"from": "notes.provides", "to": "reader.reads"} in core.domain.wires  # a dotted wire into a read input
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
    day = (dt_util.now() + timedelta(days=200)).date()  # far from anything the backup holds
    tomorrow, after = day.isoformat(), (day + timedelta(days=1)).isoformat()
    assert (await cal.call("add", {"summary": "Dentist", "start": f"{tomorrow}T10:00"}))["done"] == "add"
    lists = core.domain.cards["lists"]
    await lists.call("create_list", {"list": "Errands"})
    await lists.call("add_items", {"list": "Errands", "items": ["Pay rent"]})
    item = lists.data["lists"][-1]["items"][0]
    item["fields"]["due"] = tomorrow
    found = await cal.call("calendar", {"start": tomorrow, "end": after})
    assert [e["summary"] for e in found] == ["Pay rent (Errands)", "Dentist"]
    uid = next(e["uid"] for e in found if e["summary"] == "Dentist")
    await cal.call("change", {"uid": uid, "summary": "Dentist (moved)"})
    await hass.async_block_till_done()
    state = hass.states.get("calendar.aac_calendar")
    assert state is not None
    start = dt_util.start_of_local_day(day)
    end = start + timedelta(days=1)
    events = await hass.services.async_call("calendar", "get_events", {"entity_id": "calendar.aac_calendar", "start_date_time": start,
                                                                        "end_date_time": end}, blocking=True, return_response=True)
    assert {e["summary"] for e in events["calendar.aac_calendar"]["events"]} == {"Pay rent (Errands)", "Dentist (moved)"}
    await cal.call("remove", {"uid": uid})
    assert len(await cal.call("calendar", {"start": tomorrow, "end": after})) == 1


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
    old["context"]["messages"] = [{"id": "e1", "by": "chat", "kind": "said", "text": "the LoRa mast drone", "tags": [], "time": "", "data": {}, "state": "closed"}]
    d = core.domain  # the Chat as the model's tool: wired straight to it and back
    d.set_wires([w for w in d.wires if w["from"] != "chat.said"] + [{"from": "chat.said", "to": "model.in"},
                                                                   {"from": "model.out", "to": "chat.answers"}])
    old["updated"], other["updated"] = "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"
    await card.save()
    found = await card.call("chats", {"search": "lora"})
    assert [c["id"] for c in found] == [old["id"]] and found[0]["first"] == "the LoRa mast drone"
    assert (await card.call("read", {"chat": old["id"]}))[0]["text"] == "the LoRa mast drone"
    assert card.current() is None  # both quiet for long: the next spoken turn would start a new chat
    model_answers(aioclient_mock, reply(calls=[("continue", {"chat": old["id"]})]), reply("Back to the drone."))
    before = {c["id"] for c in card.chats()}
    chat_id, answer = await card.send("let's go on with the drone chat")
    assert chat_id == old["id"] and answer == "Back to the drone."
    assert card.current()["id"] == old["id"]
    assert {c["id"] for c in card.chats()} == before  # the asking chat is gone
    assert [m["text"] for m in card.get(old["id"])["messages"]] == ["the LoRa mast drone"]  # the switch is not kept


async def test_home_access_list(hass: HomeAssistant, mock_entry):
    """The Home card's access list: its entries come from HA, anything not set is hidden."""
    core = await setup_aac(hass, mock_entry)
    hass.states.async_set("light.desk", "on", {"friendly_name": "Desk"})
    home = core.domain.cards["home"]
    items = await home.ui_call("access_items", {})
    assert {"id": "light.desk", "name": "Desk", "area": "", "group": "light"} in items
    assert home.level("light.desk") == "hidden"


async def test_log_takes_anything(hass: HomeAssistant, mock_entry):
    """The Log card's input takes every kind; it writes contexts, clips, audio start and stop, and what is new on a
    dotted wire (a Chat's provides) into AAC's log. Its page shows what it wrote; allowed to read the log, all of it,
    and models and dotted wires get it too."""
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
        chat._new("Talk")["context"]["messages"].append({"id": "n1", "by": "chat", "kind": "said", "text": "a new line", "tags": [], "time": "", "data": {}, "state": "closed"})
        await asyncio.sleep(0.05)
        task.cancel()
    own = (await log.ui_call("entries", {}))
    texts = [(x["what"], x["text"]) for x in own["entries"]]
    assert not own["all"] and all(x["card"] == log.id for x in own["entries"])  # not allowed: only what it wrote
    assert ("context", "Model: answer #model.answer: Hello there  [1 entries]") in texts
    assert ("clip", "PC: 1.4 s, phrase 2") in texts and ("audio", "PC: started") in texts
    assert any(w == "audio" and t.startswith("PC: stopped") for w, t in texts) and ("read", "Chat.provides: said : a new line") in texts
    kept = next(x for x in own["entries"] if x["what"] == "clip")
    assert kept["audio"].endswith(".wav") and (await log.ui_call("audio", {"file": kept["audio"]}))  # playable
    assert "may not read" in (await log.call("read", {}))["error"] and not log.held("provides").entries
    log = await d.update_card(log.id, settings={"may_read_log": True})  # the Permissions page
    assert (await log.ui_call("entries", {}))["all"]
    found = await log.call("read", {"card": "Log", "search": "phrase"})
    assert "Log clip: PC: 1.4 s, phrase 2" in found and found.count("\n") == 0
    assert "Added (Log)" in log.held("provides").entries[0]["text"]


async def test_chat_archive(hass: HomeAssistant, mock_entry):
    """Sidebar actions: archive moves a chat into 'Archive (n)'; an archived chat is never the current one, and models
    see it marked but cannot read or continue it; restore and delete work the same way."""
    core = await setup_aac(hass, mock_entry)
    card = core.domain.cards["chat"]
    chat = card._new("Old talk")
    chat["context"]["messages"] = [{"id": "e1", "by": "chat", "kind": "said", "text": "hello", "tags": [], "time": "", "data": {}, "state": "closed"}]
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
        {"id": "user/new-GGUF:Q4_K_M", "status": {"value": "downloading"}, "can_remove": True, "source": "cache"},
        {"id": "slow", "status": {"value": "loading"}, "can_remove": False, "source": "preset"}]})
    aioclient_mock.get(f"{root}/models/sse", text='data: {"model": "user/new-GGUF:Q4_K_M", "event": "download_progress", '
                       '"data": {"progress": {"https://x/a.gguf": {"done": 25, "total": 100}}}}\n\n'
                       'data: {"model": "slow", "event": "status_change", "data": {"status": "loading", "progress": '
                       '{"stages": ["text_model", "mmproj_model"], "current": "mmproj_model", "value": 0.5}}}\n\n')  # the list has no progress
    aioclient_mock.get(f"{root}/v1/models", json={"data": [{"id": "big", "meta": {"n_params": 11.9e9, "size": 6.7e9, "ftype": "Q4_0", "n_ctx": 16384}}]})
    aioclient_mock.get(f"{root}/props", json={"chat_template_caps": {"supports_tools": True}, "build_info": "b1-test"})
    aioclient_mock.get(f"{root}/metrics", text="# HELP x\nllamacpp:prompt_tokens_seconds 1043.8\nllamacpp:predicted_tokens_seconds 45.2\n")
    for path in ("/models/load", "/models/unload", "/models"):
        aioclient_mock.post(f"{root}{path}", json={"success": True})
    aioclient_mock.delete(f"{root}/models", json={"success": True})

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "aac/models/servers"})
    (server,) = (await client.receive_json())["result"]["servers"]  # both AAC models are on the one address: one server
    assert server["router"] and server["address"] == root and [u["name"] for u in server["uses"]] == ["Big", "Small"]
    sid = server["id"]
    assert core.pool.get("m_big")["server"] == sid and core.pool.get("m_small")["server"] == sid
    big, new, slow = server["models"]
    assert slow["status"] == "loading" and slow["progress"] == 75  # the second of two stages, half done
    assert big["status"] == "loaded" and big["quant"] == "Q4_0" and big["tools"] and big["speed"] == {"read": 1044, "write": 45}
    assert big["context"] == 16384 and big["context_max"] == 262144
    assert new["status"] == "downloading" and new["progress"] == 25 and new["can_remove"]
    assert core.data["facts"][f"{root} big"] == {"params": 11.9e9, "size": 6.7e9, "quant": "Q4_0"}  # kept for when it is not loaded

    await client.send_json({"id": 2, "type": "aac/models/router", "server_id": sid, "action": "load", "name": "big"})
    assert (await client.receive_json())["success"]
    assert any(m[0] == "POST" and str(m[1]).endswith("/models/load") and m[2] == {"model": "big"} for m in aioclient_mock.mock_calls)
    await client.send_json({"id": 3, "type": "aac/models/router", "server_id": sid, "action": "download",
                            "name": "user/new-GGUF:Q4_K_M", "sidecars": ["mtp"]})
    assert (await client.receive_json())["success"]
    assert any(m[0] == "POST" and str(m[1]) == f"{root}/models" and m[2] == {"model": "user/new-GGUF:Q4_K_M", "sidecars": ["mtp"]}
               for m in aioclient_mock.mock_calls)
    core.data["facts"][f"{root} user/new-GGUF:Q4_K_M"] = {"params": 3e9}
    await client.send_json({"id": 4, "type": "aac/models/router", "server_id": sid, "action": "delete", "name": "user/new-GGUF:Q4_K_M"})
    assert (await client.receive_json())["success"]
    assert f"{root} user/new-GGUF:Q4_K_M" not in core.data["facts"]  # deleted: what AAC kept about it goes
    assert any(m[0] == "DELETE" and m[1].query.get("model") == "user/new-GGUF:Q4_K_M" for m in aioclient_mock.mock_calls)

    assert all(m[1].query.get("autoload") == "false" for m in aioclient_mock.mock_calls if m[1].path in ("/props", "/metrics"))
    aioclient_mock.post(f"{root}/v1/chat/completions", json={"choices": [{"message": {"content": "Foxes …"}}], "timings": {
        "prompt_n": 1520, "prompt_per_second": 1043.6, "prompt_ms": 1456.5, "predicted_n": 256, "predicted_per_second": 107.2, "predicted_ms": 2388.1}})
    await client.send_json({"id": 5, "type": "aac/models/router", "server_id": sid, "action": "bench", "name": "big"})
    assert (await client.receive_json())["success"]
    kept = core.data["benches"][f"{root} big"]
    assert (kept["read"], kept["write"], kept["prompt"], kept["written"], kept["seconds"]) == (1044, 107, 1520, 256, 3.8)
    bench_body = next(m[2] for m in aioclient_mock.mock_calls if str(m[1]).endswith("/v1/chat/completions"))
    assert bench_body["model"] == "big" and bench_body["ignore_eos"] and not bench_body["cache_prompt"]
    assert not core.pool.heard  # nothing asked yet
    core.pool.mark_down("m_small", "timeout")  # a request that got no answer, then one that did (the same server)
    core.pool.mark_up("m_big")
    core.data["facts"][f"{root} slow"] = {"params": 3e9, "quant": "Q8_0"}  # from an earlier load
    await client.send_json({"id": 6, "type": "aac/models/servers"})
    listed = (await client.receive_json())["result"]["servers"][0]
    assert listed["models"][0]["bench"]["write"] == 107
    assert listed["models"][2]["params"] == 3e9 and listed["models"][2]["quant"] == "Q8_0"  # loading: shown from what was kept
    assert listed["heard"]["error"] == "timeout" and listed["heard"]["answered"] >= listed["heard"]["failed"]
    core.pool.mark_up("m_small")

    core.save_model({"id": "m_small", "follow": True, "model": "something old"})  # follows: asks for what is loaded
    from custom_components.aac.models import Client
    assert await Client(hass, core.pool.get("m_small")).following() == "big" and core.pool.get("m_small")["model"] == "big"

    await client.send_json({"id": 7, "type": "aac/servers/save", "server": {"id": sid, "name": "Box", "base_url": "http://box.local:8080/v1"}})
    assert (await client.receive_json())["success"]  # a new address: its models follow
    assert core.pool.get("m_big")["base_url"] == "http://box.local:8080/v1" and core.pool.get("m_big")["api_key"] is None
    await client.send_json({"id": 8, "type": "aac/servers/save", "server": {"id": sid, "name": "Box", "base_url": f"{root}/v1"}})
    assert (await client.receive_json())["success"]

    card = await core.domain.add_card("model_servers", "Servers")
    assert await card.call("bench", {"aac_model": "Small", "model": "big"}) == {"reads": "1044 tokens/s", "writes": "107 tokens/s"}
    (listed,) = await card.call("servers", {})
    assert listed["aac_models"] == ["Big (uses big)", "Small (uses big)"]
    assert listed["offers"][1] == {"model": "user/new-GGUF:Q4_K_M", "state": "downloading", "progress": "25 %"}
    assert await card.call("use", {"aac_model": "big", "model": "user/new-GGUF:Q4_K_M"}) == {"done": "Big uses user/new-GGUF:Q4_K_M"}
    assert core.pool.get("m_big")["model"] == "user/new-GGUF:Q4_K_M"
    assert "error" in await card.call("use", {"aac_model": "nobody", "model": "big"})


async def test_download_progress_holds_between_events(hass: HomeAssistant, aioclient_mock):
    """The router's live events come about once a second and AAC listens a moment: a round that hears nothing keeps
    the last progress (it showed 0 % now and then); a finished download forgets it."""
    from custom_components.aac.models import Client
    root, known = "http://llm.local:8080", {}
    client = Client(hass, {"base_url": f"{root}/v1", "model": ""})
    def serve(status, sse):
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{root}/models", json={"data": [{"id": "new", "status": {"value": status}, "can_remove": True}]})
        aioclient_mock.get(f"{root}/models/sse", text=sse)
    serve("downloading", 'data: {"model": "new", "event": "download_progress", "data": {"progress": {"u": {"done": 12, "total": 100}}}}\n\n')
    assert (await client.router(known))[0]["progress"] == 12
    serve("downloading", "")  # no event this round
    assert (await client.router(known))[0]["progress"] == 12
    serve("unloaded", "")
    assert "progress" not in (await client.router(known))[0] and not known



async def test_servers_and_their_models(hass: HomeAssistant, mock_entry):
    """Every model is on a server: a new one by its server (address and key come from it), deleting a server takes its
    models along; a model saved with only an address gets the server there."""
    core = await setup_aac(hass, mock_entry)
    server = core.save_server({"name": "PC", "base_url": "http://pc.local:8080/v1", "api_key": "k"})
    model = core.save_model({"name": "Gemma", "model": "gemma", "server": server["id"]})
    assert (model["base_url"], model["api_key"], model["server"]) == ("http://pc.local:8080/v1", "k", server["id"])
    same = core.save_model({"name": "Other", "model": "x", "base_url": "http://pc.local:8080/v1", "api_key": "k"})
    assert same["server"] == server["id"] and same["id"] != model["id"]
    core.delete_server(server["id"])
    assert not core.pool.get(model["id"]) and not core.pool.get(same["id"]) and server not in core.data["servers"]


async def test_a_model_asked_as_a_tool(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Another model (or a Relay's script) asks a Model card through its function ask: its answer comes back."""
    model_answers(aioclient_mock, reply("Forty-two."))
    core = await setup_aac(hass, mock_entry)
    expert = await core.domain.add_card("model", "Expert")
    assert await expert.call("ask", {"text": "What is the answer?"}) == "Forty-two."
