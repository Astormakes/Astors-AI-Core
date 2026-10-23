"""The default setup: a domain built from ordinary cards (docs/v2: defaults come from presets). At the first start
of v2 it takes over what v1 left behind: the models, the charter, the lists and which entities AAC may see.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import LOGGER

if TYPE_CHECKING:
    from .core import Core

CHARTER = """Charter (the owner's values; you follow it and never change it):
- Honesty over comfort: never hide or soften facts to be liked, never fake progress, say when you made a mistake.
- Little noise: speak up only when it matters; suggest something at most once, accept "no".
- My decisions stay mine: you advise, I decide. Ask before anything that cannot be undone or leaves the house.
- What you learn about me stays private.
- Limits are not puzzles: if a rule stops you, say so instead of working around it."""
TIME = "Local time: {{ now().strftime('%A %Y-%m-%d %H:%M') }}"


async def async_first_start(core: Core) -> None:
    """Build the default domain once (and take over v1's data when it is there)."""
    from .core import Domain, read_v1  # noqa: PLC0415

    hass = core.hass
    v1 = await hass.async_add_executor_job(read_v1, hass, "aac.config") or {}
    v1_lists = await hass.async_add_executor_job(read_v1, hass, "aac.mod.lists.lists") or {}

    # models: v1's list, its master order first
    order = (v1.get("pools") or {}).get("master") or []
    for m in v1.get("models") or []:
        if m.get("base_url") and m.get("model"):
            core.data["models"].append({"id": m["id"], "name": m.get("name") or m["model"], "base_url": m["base_url"],
                                        "model": m["model"], "api_key": m.get("api_key")})
    chat_models = [i for i in order if core.pool.get(i)] or [m["id"] for m in core.data["models"][:1]]

    charter = ((((v1.get("graphs") or {}).get("assistant") or {}).get("modules") or {}).get("charter") or {}) \
        .get("settings", {}).get("text") or CHARTER

    # which entities AAC may see (read) and switch (full), as v1 had it
    levels: dict[str, str] = {}
    for dom in v1.get("domains") or []:
        if dom.get("id") == "assistant":
            levels.update({e: "read" for e in dom.get("visible") or []})
    for entity_id, access in (v1.get("entity_access") or {}).items():
        if access.get("write"):
            levels[entity_id] = "full"
        elif access.get("read"):
            levels[entity_id] = "read"

    core.data["domains"] = [{"id": "assistant", "name": "Assistant"}]
    core.data["active"] = "assistant"
    domain = Domain(core, "assistant", "Assistant")
    await domain.load()
    await build_default(domain, charter, chat_models, levels, v1_lists.get("lists") or [])
    domain.undo = []
    await domain.save_now()
    core.save()
    LOGGER.info("AAC: default domain built (%s models, %s lists from v1)", len(core.data["models"]),
                len(v1_lists.get("lists") or []))


async def build_default(domain, charter: str, models: list[str], levels: dict[str, str],
                        lists: list[dict[str, Any]]) -> None:
    """The default setup: Chat → Charter → Time → Model → back to Chat; Lists and Home as the model's tools."""
    add = domain.add_card
    await add("chat", "Chat", card_id="chat", at={"x": 0, "y": 0})
    await add("relay", "Charter", {"position": "start", "text": charter, "replace": True}, card_id="charter", at={"x": 240, "y": 0})
    await add("relay", "Time", {"position": "after", "text": TIME, "replace": False}, card_id="time", at={"x": 480, "y": 0})
    await add("pool", "Models", {"models": models}, card_id="models", at={"x": 720, "y": -170})
    await add("model", "Model", card_id="model", at={"x": 720, "y": 0})
    lists_card = await add("lists", "Lists", card_id="lists", at={"x": 960, "y": 110})
    await add("homeassistant", "Home", {"levels": levels, "default": "hidden"}, card_id="home", at={"x": 960, "y": 260})
    if lists:
        lists_card.data = {"lists": lists}
        await lists_card.save()
    domain.set_wires([
        {"from": "chat.said", "to": "charter.in"}, {"from": "charter.out", "to": "time.in"},
        {"from": "time.out", "to": "model.in"}, {"from": "models.models", "to": "model.pool"},
        {"from": "model.out", "to": "chat.answers"},
        {"from": "model.out", "to": "lists.in"}, {"from": "lists.out", "to": "model.in"},
        {"from": "model.out", "to": "home.in"}, {"from": "home.out", "to": "model.in"},
    ])


async def async_upgrade(core: Core) -> None:
    """One-time steps for a domain made by an older v2: v1's time triggers become a Schedule card (wired like a chat:
    into the line, the model's answer back into it)."""
    from .core import read_v1  # noqa: PLC0415

    if not core.data.get("v1_audio"):  # v1's voice settings and the satellite add-on's HA user
        core.data["v1_audio"] = True
        v1c = await core.hass.async_add_executor_job(read_v1, core.hass, "aac.config") or {}
        pools, audio = v1c.get("pools") or {}, v1c.get("audio") or {}
        core.data["audio"] = {"stt": list(pools.get("stt") or []), "tts": (pools.get("tts") or [None])[0],
                              "voices": audio.get("voices") or {}, "volume": audio.get("volume", 40),
                              "known": audio.get("known") or []}
        if v1c.get("satellite_app") and not core.data.get("satellite_app"):
            core.data["satellite_app"] = v1c["satellite_app"]
        core.save()
    if core.data.get("v1_triggers"):
        return
    core.data["v1_triggers"] = True
    core.save()
    v1 = await core.hass.async_add_executor_job(read_v1, core.hass, "aac.mod.triggers.triggers") or {}
    entries = [{"name": t["name"], "at": t["when"]["at"], "weekdays": t["when"].get("weekdays") or [],
                "text": (t.get("do") or {}).get("instruction", ""), "enabled": bool(t.get("enabled", True))}
               for t in v1.get("triggers") or [] if (t.get("when") or {}).get("type") == "time" and t["when"].get("at")]
    domain = core.domain
    if not entries or any(c.type == "schedule" for c in domain.cards.values()):
        return
    card = await domain.add_card("schedule", "Schedule", {"entries": entries}, at={"x": 0, "y": 200}, card_id="schedule")
    first = "charter" if "charter" in domain.cards else next((c.id for c in domain.cards.values() if c.type == "model"), None)
    model = next((c.id for c in domain.cards.values() if c.type == "model"), None)
    wires = list(domain.wires)
    if first:
        wires.append({"from": f"{card.id}.out", "to": f"{first}.in"})
    if model:
        wires.append({"from": f"{model}.out", "to": f"{card.id}.answers"})
    domain.set_wires(wires)
    domain.undo = []
    LOGGER.info("AAC: %s v1 time trigger(s) became the Schedule card", len(entries))


async def async_voice_chain(core: Core) -> None:
    """One-time: when satellites are known (v1 had voice), the default domain gets the voice chain as ordinary cards:
    Microphone → Pause cut → Speech to text → Voice chat → Wake word (a Conditional: "computer", or a follow-up)
    → Voice rules (a Relay after the turn) → the line; the model's answer → Voice chat → Voice out → status back."""
    if core.data.get("voice_chain"):
        return
    core.data["voice_chain"] = True
    core.save()
    domain = core.domain
    if not core.audio.settings.get("known") or any(c.type == "microphone" for c in domain.cards.values()):
        return
    names = ["computer"]
    v1 = await core.hass.async_add_executor_job(_read, core, "aac.config")
    names = ((v1 or {}).get("audio") or {}).get("names") or names
    add = domain.add_card
    await add("microphone", "Microphone", {"source": "any"}, card_id="mic", at={"x": -720, "y": 420})
    await add("pause_cut", "Pause cut", {"pause": 800}, card_id="cut", at={"x": -480, "y": 420})
    await add("speech_to_text", "Speech to text", card_id="ears", at={"x": -240, "y": 420})
    await add("voice_chat", "Voice chat", card_id="voice", at={"x": 0, "y": 420})
    rules = [{"name": f"says {n}", "select": {"by": "kind", "value": "said", "which": "newest"}, "test": "contains",
              "value": n, "actions": [{"do": "output", "a": "yes"}]} for n in names]
    rules.append({"name": "follow-up", "select": {"by": "kind", "value": "said", "which": "newest"}, "test": "has_tag",
                  "value": "voice.follow_up", "actions": [{"do": "output", "a": "yes"}]})
    await add("conditional", "Wake word", {"outputs": ["yes"], "rules": rules}, card_id="wake", at={"x": 240, "y": 420})
    await add("relay", "Voice rules", {"position": "after", "replace": False,
                                       "text": "You are heard and answer aloud: one or two short sentences, plain words, no lists."},
              card_id="voice_rules", at={"x": 480, "y": 420})
    await add("voice_out", "Voice out", card_id="speaker", at={"x": 240, "y": 600})
    first = "charter" if "charter" in domain.cards else None
    model = next((c.id for c in domain.cards.values() if c.type == "model"), None)
    wires = [*domain.wires, {"from": "mic.out", "to": "cut.in"}, {"from": "cut.out", "to": "ears.in"},
             {"from": "ears.out", "to": "voice.heard"}, {"from": "voice.said", "to": "wake.in"},
             {"from": "wake.yes", "to": "voice_rules.in"}, {"from": "voice.speak", "to": "speaker.in"},
             {"from": "speaker.status", "to": "voice.status"}]
    if first:
        wires.append({"from": "voice_rules.out", "to": f"{first}.in"})
    if model:
        wires.append({"from": f"{model}.out", "to": "voice.answers"})
    domain.set_wires(wires)
    domain.undo = []
    LOGGER.info("AAC: the voice chain was added to the default domain")


def _read(core: Core, key: str):
    from .core import read_v1  # noqa: PLC0415
    return read_v1(core.hass, key)


async def async_assist_card(core: Core) -> None:
    """One-time: the default domain gets an HA Assist card (AAC stays HA's conversation agent, as in v1)."""
    if core.data.get("assist_card"):
        return
    core.data["assist_card"] = True
    core.save()
    domain = core.domain
    if any(c.type == "assist" for c in domain.cards.values()):
        return
    await domain.add_card("assist", "HA Assist", card_id="assist", at={"x": 0, "y": 140})
    model = next((c.id for c in domain.cards.values() if c.type == "model"), None)
    wires = list(domain.wires)
    if "charter" in domain.cards:
        wires.append({"from": "assist.said", "to": "charter.in"})
    if model:
        wires.append({"from": f"{model}.out", "to": "assist.answers"})
    domain.set_wires(wires)
    domain.undo = []


async def async_calendar_card(core: Core) -> None:
    """One-time: the default domain gets a Calendar card as the model's tool (calendar.aac_calendar, as in v1)."""
    if core.data.get("calendar_card"):
        return
    core.data["calendar_card"] = True
    core.save()
    domain = core.domain
    if any(c.type == "calendar" for c in domain.cards.values()):
        return
    await domain.add_card("calendar", "Calendar", card_id="calendar", at={"x": 960, "y": 410})
    model = next((c.id for c in domain.cards.values() if c.type == "model"), None)
    if model:
        domain.set_wires([*domain.wires, {"from": f"{model}.out", "to": "calendar.in"}, {"from": "calendar.out", "to": f"{model}.in"}])
    domain.undo = []
