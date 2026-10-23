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
