"""The panel's websocket API (admin only)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.loader import async_get_integration

from .cards import CARD_TYPES, CATEGORIES
from .const import DOMAIN, SIGNAL
from .core import UNDO_STEPS
from .models import MODEL_TAGS, Client, ModelError

COMMANDS: list = []


def command(schema: dict) -> Any:
    """Register a command (admin only, async)."""
    def wrap(fn):
        handler = websocket_api.websocket_command(schema)(websocket_api.require_admin(websocket_api.async_response(fn)))
        COMMANDS.append(handler)
        return handler
    return wrap


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the commands once."""
    if hass.data.get(f"{DOMAIN}_ws"):
        return
    hass.data[f"{DOMAIN}_ws"] = True
    for handler in COMMANDS:
        websocket_api.async_register_command(hass, handler)


def _core(hass: HomeAssistant):
    return hass.data[DOMAIN]


async def _fail(connection, msg, err: Exception) -> None:
    connection.send_error(msg["id"], "failed", str(err))


@command({vol.Required("type"): "aac/state"})
async def ws_state(hass, connection, msg) -> None:
    """Everything the panel shows."""
    core = _core(hass)
    connection.send_result(msg["id"], {
        "version": str((await async_get_integration(hass, DOMAIN)).version),
        "domain": core.domain.summary(), "domains": core.data["domains"], "active": core.data["active"],
        "types": [t.describe() for t in CARD_TYPES.values()], "categories": CATEGORIES, "models": core.pool.summary(), "model_tags": MODEL_TAGS})


@websocket_api.websocket_command({vol.Required("type"): "aac/subscribe"})
@websocket_api.require_admin
@callback
def ws_subscribe(hass, connection, msg) -> None:
    """Changes pushed to the panel."""
    @callback
    def forward(event: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], event))
    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL, forward)
    connection.send_result(msg["id"])


COMMANDS.append(ws_subscribe)


@command({vol.Required("type"): "aac/card/add", vol.Required("card_type"): str, vol.Optional("title"): str,
          vol.Optional("settings"): dict, vol.Optional("x"): vol.Coerce(float), vol.Optional("y"): vol.Coerce(float)})
async def ws_card_add(hass, connection, msg) -> None:
    """A new card."""
    try:
        card = await _core(hass).domain.add_card(msg["card_type"], msg.get("title", ""), msg.get("settings"),
                                                 {"x": msg.get("x", 0), "y": msg.get("y", 0)})
    except ValueError as err:
        return await _fail(connection, msg, err)
    connection.send_result(msg["id"], card.summary())


@command({vol.Required("type"): "aac/card/remove", vol.Required("card"): str})
async def ws_card_remove(hass, connection, msg) -> None:
    """Take a card out."""
    await _core(hass).domain.remove_card(msg["card"])
    connection.send_result(msg["id"], {})


@command({vol.Required("type"): "aac/values"})
async def ws_values(hass, connection, msg) -> None:
    """The value pool: what templates can use now ({{ <card>.<value> }}, {{ audio.… }}), as plain JSON."""
    import json  # noqa: PLC0415

    connection.send_result(msg["id"], {"values": json.loads(json.dumps(_core(hass).domain.template_values(), default=str))})


@command({vol.Required("type"): "aac/card/update", vol.Required("card"): str, vol.Optional("title"): str,
          vol.Optional("settings"): dict})
async def ws_card_update(hass, connection, msg) -> None:
    """A card's name or settings."""
    card = await _core(hass).domain.update_card(msg["card"], msg.get("title"), msg.get("settings"))
    connection.send_result(msg["id"], card.summary())


@command({vol.Required("type"): "aac/card/call", vol.Required("card"): str, vol.Required("function"): str,
          vol.Optional("args", default={}): dict})
async def ws_card_call(hass, connection, msg) -> None:
    """Call one of a card's functions (the panel uses them like a model does: lists, buffers …)."""
    card = _core(hass).domain.cards.get(msg["card"])
    if card is None:
        return await _fail(connection, msg, ValueError("no such card"))
    try:
        connection.send_result(msg["id"], {"result": await card.call(msg["function"], msg["args"])})
    except (ValueError, KeyError) as err:
        await _fail(connection, msg, err)


@command({vol.Required("type"): "aac/card/data", vol.Required("card"): str})
async def ws_card_data(hass, connection, msg) -> None:
    """A card's own data (for its page: a Lists card's lists)."""
    card = _core(hass).domain.cards.get(msg["card"])
    connection.send_result(msg["id"], {"data": card.data if card else None})


@command({vol.Required("type"): "aac/wires", vol.Required("wires"): [dict]})
async def ws_wires(hass, connection, msg) -> None:
    """The owner's wires."""
    domain = _core(hass).domain
    domain.set_wires(msg["wires"])
    connection.send_result(msg["id"], {"wires": domain.wires})


@command({vol.Required("type"): "aac/positions", vol.Required("positions"): dict, vol.Optional("undoable", default=False): bool})
async def ws_positions(hass, connection, msg) -> None:
    """Where the cards sit in the grid (Arrange: undoable)."""
    domain = _core(hass).domain
    if msg["undoable"]:
        domain.undo = [*domain.undo, {**domain._snapshot(), "positions": deepcopy(domain.positions)}][-UNDO_STEPS:]
    domain.positions = {k: {"x": float(v.get("x", 0)), "y": float(v.get("y", 0))} for k, v in msg["positions"].items()
                        if k in domain.cards}
    domain.save()
    connection.send_result(msg["id"], {})


@command({vol.Required("type"): "aac/undo"})
async def ws_undo(hass, connection, msg) -> None:
    """Back to the setup before the last change."""
    connection.send_result(msg["id"], {"undone": await _core(hass).domain.undo_last()})


@command({vol.Required("type"): "aac/slot", vol.Required("slot"): vol.In(["1", "2", "3"]), vol.Required("action"): vol.In(["save", "load"])})
async def ws_slot(hass, connection, msg) -> None:
    """Save the setup to a slot, or load it."""
    domain = _core(hass).domain
    if msg["action"] == "save":
        domain.save_slot(msg["slot"])
        return connection.send_result(msg["id"], {"saved": True})
    connection.send_result(msg["id"], {"loaded": await domain.load_slot(msg["slot"])})


@command({vol.Required("type"): "aac/setup/export"})
async def ws_setup_export(hass, connection, msg) -> None:
    """The running domain's setup, to save as a file."""
    connection.send_result(msg["id"], {"setup": _core(hass).domain.export()})


@command({vol.Required("type"): "aac/setup/import", vol.Required("setup"): dict})
async def ws_setup_import(hass, connection, msg) -> None:
    """Load a setup file into the running domain (undoable)."""
    try:
        await _core(hass).domain.import_setup(msg["setup"])
    except ValueError as err:
        return await _fail(connection, msg, err)
    connection.send_result(msg["id"], {})


@command({vol.Required("type"): "aac/stop"})
async def ws_stop(hass, connection, msg) -> None:
    """Send stop: every card drops what it is doing."""
    await _core(hass).domain.stop()
    connection.send_result(msg["id"], {})


@command({vol.Required("type"): "aac/monitor", vol.Required("wire"): str})
async def ws_monitor(hass, connection, msg) -> None:
    """The newest context that passed an output, and what passed it lately (10 minutes)."""
    flow = _core(hass).domain.flow
    connection.send_result(msg["id"], {"context": flow.last.get(msg["wire"]), "records": flow.records.get(msg["wire"], [])})


@command({vol.Required("type"): "aac/tags/add", vol.Required("tag"): str})
async def ws_tags_add(hass, connection, msg) -> None:
    """One of the owner's own tags (user.…)."""
    domain = _core(hass).domain
    tag = msg["tag"].strip()
    tag = tag if tag.startswith("user.") else f"user.{tag}"
    if tag not in domain.user_tags:
        domain.user_tags.append(tag)
        domain.save()
    connection.send_result(msg["id"], {"tags": domain.tag_pool()})


@command({vol.Required("type"): "aac/tags/remove", vol.Required("tag"): str})
async def ws_tags_remove(hass, connection, msg) -> None:
    """Take one of the owner's own tags away."""
    domain = _core(hass).domain
    domain.user_tags = [t for t in domain.user_tags if t != msg["tag"]]
    domain.save()
    connection.send_result(msg["id"], {"tags": domain.tag_pool()})


# ---------------------------------------------------------------------- card pages (a card's own .js)
@command({vol.Required("type"): "aac/nav"})
async def ws_nav(hass, connection, msg) -> None:
    """The sidebar: every card with a page, with its entries (chats, lists …)."""
    found = []
    for card in _core(hass).domain.cards.values():
        if card.ui_url:
            try:
                nav = card.nav()
            except Exception as err:  # noqa: BLE001 - one card must not break the sidebar
                nav = {"entries": [], "error": str(err)}
            found.append({"card": card.id, "title": card.name, "ui": card.ui_url, "element": f"aac-card-{card.type.replace('_', '-')}", "nav": nav})
    connection.send_result(msg["id"], {"nav": found})


@command({vol.Required("type"): "aac/card/ui", vol.Required("card"): str, vol.Required("action"): str,
          vol.Optional("args", default={}): dict})
async def ws_card_ui(hass, connection, msg) -> None:
    """A request from a card's own page."""
    card = _core(hass).domain.cards.get(msg["card"])
    if card is None:
        return await _fail(connection, msg, ValueError("no such card"))
    try:
        connection.send_result(msg["id"], {"result": await card.ui_call(msg["action"], msg["args"])})
    except (ValueError, KeyError) as err:
        await _fail(connection, msg, err)


@command({vol.Required("type"): "aac/chat/request", vol.Required("chat_id"): str})
async def ws_chat_request(hass, connection, msg) -> None:
    """What the models got for a chat's newest turn: each message with the cards its text came from, the tools."""
    found = [card.requests[msg["chat_id"]] for card in _core(hass).domain.cards.values()
             if msg["chat_id"] in getattr(card, "requests", {})]
    connection.send_result(msg["id"], {"requests": sorted(found, key=lambda r: r["at"])})


# ---------------------------------------------------------------------- models (core) and domains
@command({vol.Required("type"): "aac/models/save", vol.Required("model"): {
    vol.Optional("id"): str, vol.Required("name"): str, vol.Required("base_url"): str, vol.Required("model"): str,
    vol.Optional("api_key"): vol.Any(str, None), vol.Optional("tags"): [str], vol.Optional("parallel"): vol.All(vol.Coerce(int), vol.Range(1, 64))}})
async def ws_models_save(hass, connection, msg) -> None:
    """Add or change a model in the pool."""
    core = _core(hass)
    core.save_model(dict(msg["model"]))
    core.save()
    connection.send_result(msg["id"], {"models": core.pool.summary()})


@command({vol.Required("type"): "aac/models/delete", vol.Required("model_id"): str})
async def ws_models_delete(hass, connection, msg) -> None:
    """Take a model out of the pool."""
    core = _core(hass)
    core.delete_model(msg["model_id"])
    connection.send_result(msg["id"], {"models": core.pool.summary()})


@command({vol.Required("type"): "aac/models/probe", vol.Required("base_url"): str, vol.Optional("api_key"): vol.Any(str, None)})
async def ws_models_probe(hass, connection, msg) -> None:
    """What a server offers (to pick a model name)."""
    try:
        found = await Client(hass, {"base_url": msg["base_url"], "model": "", "api_key": msg.get("api_key")}).list_models()
    except ModelError as err:
        return await _fail(connection, msg, err)
    connection.send_result(msg["id"], {"models": found})


@command({vol.Required("type"): "aac/domain/clone", vol.Required("name"): vol.All(str, vol.Length(min=1, max=40))})
async def ws_domain_clone(hass, connection, msg) -> None:
    """A copy of the running domain."""
    connection.send_result(msg["id"], {"id": await _core(hass).clone(msg["name"])})


@command({vol.Required("type"): "aac/domain/switch", vol.Required("domain_id"): str})
async def ws_domain_switch(hass, connection, msg) -> None:
    """Run another domain."""
    domain = await _core(hass).switch(msg["domain_id"])
    connection.send_result(msg["id"], {"id": domain.id})


# ---------------------------------------------------------------------- audio (core)
@command({vol.Required("type"): "aac/appearance"})
async def ws_appearance(hass, connection, msg) -> None:
    """The skins and the one in use (shared by every device)."""
    from .skins import list_skins  # noqa: PLC0415

    connection.send_result(msg["id"], {"skins": await hass.async_add_executor_job(list_skins, hass),
                                       "skin": _core(hass).data.get("skin", "")})


@command({vol.Required("type"): "aac/appearance/set", vol.Required("skin"): str})
async def ws_appearance_set(hass, connection, msg) -> None:
    """Use a skin ("" = Home Assistant's look)."""
    core = _core(hass)
    core.data["skin"] = msg["skin"]
    core.save()
    core.notify("appearance")
    connection.send_result(msg["id"], {})


@command({vol.Required("type"): "aac/audio"})
async def ws_audio(hass, connection, msg) -> None:
    """Satellites (live levels), engines, volume."""
    connection.send_result(msg["id"], _core(hass).audio.summary())


@command({vol.Required("type"): "aac/audio/settings", vol.Optional("stt"): [str], vol.Optional("tts"): vol.Any(str, None),
          vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(0, 100)), vol.Optional("voices"): dict})
async def ws_audio_settings(hass, connection, msg) -> None:
    """Change the engines and the volume."""
    core = _core(hass)
    core.audio.settings.update({k: v for k, v in msg.items() if k in ("stt", "tts", "volume", "voices")})
    core.save()
    connection.send_result(msg["id"], core.audio.summary())


@command({vol.Required("type"): "aac/audio/forget", vol.Required("source"): str})
async def ws_audio_forget(hass, connection, msg) -> None:
    """Forget a satellite that is gone (its media player goes too)."""
    core = _core(hass)
    core.audio.forget(msg["source"])
    connection.send_result(msg["id"], core.audio.summary())


@command({vol.Required("type"): "aac/audio/say", vol.Required("source"): str, vol.Required("text"): str})
async def ws_audio_say(hass, connection, msg) -> None:
    """Speak a test sentence at a satellite."""
    connection.send_result(msg["id"], {"spoken": await _core(hass).audio.say(msg["source"], msg["text"])})
