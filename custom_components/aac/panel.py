"""The panel's commands on the running domain (docs/v2 page 6): the card layer's side of the websocket API. The core's
`websocket.py` hands each of these messages on as it came (`layer.handle("panel", msg)`); the answer is plain data.
What fails is a ValueError or KeyError (the panel shows it)."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from .cards import CARD_TYPES, CATEGORIES
from .domain import UNDO_STEPS

COMMANDS: dict[str, Any] = {}


def command(kind: str) -> Any:
    def wrap(fn: Any) -> Any:
        COMMANDS[kind] = fn
        return fn
    return wrap


async def run(layer: Any, msg: dict[str, Any]) -> Any:
    """One of the panel's commands."""
    if (fn := COMMANDS.get(msg["type"])) is None:
        raise ValueError(f"no panel command {msg['type']}")
    if layer.domain is None:
        raise ValueError("no domain runs")
    return await fn(layer.domain, msg)


def _card(domain: Any, card_id: str) -> Any:
    if (card := domain.cards.get(card_id)) is None:
        raise ValueError("no such card")
    return card


@command("aac/state")
async def state(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    """What the panel shows of the running domain, and the card types it can add."""
    return {"domain": domain.summary(), "types": [t.describe() for t in CARD_TYPES.values()], "categories": CATEGORIES}


@command("aac/card/add")
async def card_add(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    card = await domain.add_card(msg["card_type"], msg.get("title", ""), msg.get("settings"), {"x": msg.get("x", 0), "y": msg.get("y", 0)})
    return card.summary()


@command("aac/card/remove")
async def card_remove(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    await domain.remove_card(*msg["cards"], drop_data=msg.get("drop_data", False))
    return {}


@command("aac/values")
async def values(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    """The value pool: what templates can use now, as plain JSON."""
    return {"values": json.loads(json.dumps(await domain.template_values(), default=str))}


@command("aac/card/update")
async def card_update(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    return (await domain.update_card(msg["card"], msg.get("title"), msg.get("settings"))).summary()


@command("aac/card/call")
async def card_call(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    """One of a card's functions (the panel uses them like a model does)."""
    return {"result": await _card(domain, msg["card"]).run(msg["function"], msg.get("args") or {})}


@command("aac/card/data")
async def card_data(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    card = domain.cards.get(msg["card"])
    return {"data": card.data if card else None}


@command("aac/card/ui")
async def card_ui(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    """A request from a card's own page."""
    return {"result": await _card(domain, msg["card"]).ui_call(msg["action"], msg.get("args") or {})}


@command("aac/wires")
async def wires(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    domain.set_wires(msg["wires"])
    return {"wires": domain.wires}


@command("aac/wire/unblock")
async def wire_unblock(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    return {"unblocked": domain.unblock(msg["from"], msg["to"]), "red": domain.red}


@command("aac/positions")
async def positions(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    """Where the cards sit in the grid (Arrange: undoable)."""
    if msg.get("undoable"):
        domain.undo = [*domain.undo, {**domain._snapshot(), "positions": deepcopy(domain.positions)}][-UNDO_STEPS:]  # noqa: SLF001
    domain.positions = {k: {"x": float(v.get("x", 0)), "y": float(v.get("y", 0))} for k, v in msg["positions"].items()
                        if k in domain.cards}
    domain.save()
    return {}


@command("aac/undo")
async def undo(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    return {"undone": await domain.undo_last()}


@command("aac/redo")
async def redo(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    return {"redone": await domain.redo_last()}


@command("aac/slot")
async def slot(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    if msg["action"] == "save":
        domain.save_slot(msg["slot"])
        return {"saved": True}
    return {"loaded": await domain.load_slot(msg["slot"])}


@command("aac/trial")
async def trial(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    """A model's switch that waits for keep: the owner keeps it, or takes it back now."""
    return {"done": domain.keep() if msg["action"] == "keep" else await domain.go_back(by="you")}


@command("aac/setup/export")
async def setup_export(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    return {"setup": domain.export()}


@command("aac/setup/import")
async def setup_import(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    await domain.import_setup(msg["setup"])
    return {}


@command("aac/stop")
async def stop(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    await domain.stop()
    return {}


@command("aac/monitor")
async def monitor(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    """The newest context that passed an output, and what passed it lately (off: the card's Log setting writes
    nothing going out)."""
    card_id, _, port = msg["wire"].partition(".")
    card = domain.cards.get(card_id)
    off = card is None or card.settings.get("log") == "nothing" or card.settings.get("log_at") not in ("out", "both")
    records = [{"time": e["time"], "text": e["text"]} for e in domain.log.read(card=card_id, port=port, what="out", last=20)]
    return {"context": domain.flow.last.get(msg["wire"]), "records": records, "off": off}


@command("aac/log")
async def log(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    filters = {k: msg[k] for k in ("card", "part", "levels", "search", "before") if k in msg}
    return {"entries": domain.log.read(**filters, last=max(1, min(msg.get("last", 300), 5000)))}


@command("aac/log/clear")
async def log_clear(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    await domain.log.clear()
    return {}


@command("aac/tags/add")
async def tags_add(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    tag = msg["tag"].strip()
    tag = tag if tag.startswith("user.") else f"user.{tag}"
    if tag not in domain.user_tags:
        domain.user_tags.append(tag)
        domain.save()
    return {"tags": domain.tag_pool()}


@command("aac/tags/remove")
async def tags_remove(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    domain.user_tags = [t for t in domain.user_tags if t != msg["tag"]]
    domain.save()
    return {"tags": domain.tag_pool()}


@command("aac/nav")
async def nav(domain: Any, msg: dict[str, Any]) -> dict[str, Any]:
    """The sidebar: every card with a page, with its entries (chats, lists …)."""
    found = []
    for card in domain.cards.values():
        if card.ui_url:
            try:
                entries = card.nav()
            except Exception as err:  # noqa: BLE001 - one card must not break the sidebar
                entries = {"entries": [], "error": str(err)}
            found.append({"card": card.id, "title": card.name, "ui": card.ui_url, "element": f"aac-card-{card.type.replace('_', '-')}",
                          "nav": entries})
    return {"nav": found}
