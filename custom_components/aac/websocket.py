"""The panel's websocket API (admin only)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.loader import async_get_integration

from .cards import CARD_TYPES, CATEGORIES
from .const import DOMAIN, SIGNAL
from .models import Client, ModelError

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
        "types": [t.describe() for t in CARD_TYPES.values()], "categories": CATEGORIES, "models": core.pool.summary()})


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


@command({vol.Required("type"): "aac/positions", vol.Required("positions"): dict})
async def ws_positions(hass, connection, msg) -> None:
    """Where the cards sit in the grid."""
    domain = _core(hass).domain
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


@command({vol.Required("type"): "aac/stop"})
async def ws_stop(hass, connection, msg) -> None:
    """Send stop: every card drops what it is doing."""
    await _core(hass).domain.stop()
    connection.send_result(msg["id"], {})


@command({vol.Required("type"): "aac/monitor", vol.Required("wire"): str})
async def ws_monitor(hass, connection, msg) -> None:
    """The newest message that passed an output."""
    connection.send_result(msg["id"], {"message": _core(hass).domain.flow.last.get(msg["wire"])})


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


# ---------------------------------------------------------------------- chats
def _chat_card(hass, card_id: str):
    card = _core(hass).domain.cards.get(card_id)
    if card is None or card.type != "chat":
        raise ValueError("no such chat card")
    return card


@command({vol.Required("type"): "aac/chat/send", vol.Required("card"): str, vol.Required("text"): vol.All(str, vol.Length(min=1)),
          vol.Optional("chat_id"): vol.Any(str, None)})
async def ws_chat_send(hass, connection, msg) -> None:
    """You wrote something in a chat."""
    try:
        chat_id, answer = await _chat_card(hass, msg["card"]).send(msg["text"], msg.get("chat_id"))
    except ValueError as err:
        return await _fail(connection, msg, err)
    connection.send_result(msg["id"], {"chat_id": chat_id, "answer": answer})


@command({vol.Required("type"): "aac/chat/list", vol.Required("card"): str})
async def ws_chat_list(hass, connection, msg) -> None:
    """A chat card's chats."""
    connection.send_result(msg["id"], {"chats": _chat_card(hass, msg["card"]).chats()})


@command({vol.Required("type"): "aac/chat/get", vol.Required("card"): str, vol.Required("chat_id"): str})
async def ws_chat_get(hass, connection, msg) -> None:
    """One chat with its messages and its document (the expert view)."""
    connection.send_result(msg["id"], {"chat": _chat_card(hass, msg["card"]).get(msg["chat_id"])})


@command({vol.Required("type"): "aac/chat/delete", vol.Required("card"): str, vol.Required("chat_id"): str})
async def ws_chat_delete(hass, connection, msg) -> None:
    """Delete a chat."""
    await _chat_card(hass, msg["card"]).delete(msg["chat_id"])
    connection.send_result(msg["id"], {})


# ---------------------------------------------------------------------- models (core) and domains
@command({vol.Required("type"): "aac/models/save", vol.Required("model"): {
    vol.Optional("id"): str, vol.Required("name"): str, vol.Required("base_url"): str, vol.Required("model"): str,
    vol.Optional("api_key"): vol.Any(str, None)}})
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
