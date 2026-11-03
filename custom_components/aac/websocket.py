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
from .models import MODEL_TAGS, ModelError
from .permissions import LEVELS, catalog

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


@command({vol.Required("type"): "aac/card/remove", vol.Required("cards"): [str], vol.Optional("drop_data", default=False): bool})
async def ws_card_remove(hass, connection, msg) -> None:
    """Take cards out (several: one Undo step); drop_data: their data goes now, not when Undo forgets them."""
    await _core(hass).domain.remove_card(*msg["cards"], drop_data=msg["drop_data"])
    connection.send_result(msg["id"], {})


@command({vol.Required("type"): "aac/view/watch", vol.Required("cards"): [str]})
async def ws_view_watch(hass, connection, msg) -> None:
    """An open Cards page: which cards' views it shows (again every few seconds; live wires go only while it is open)."""
    _core(hass).watch(msg["cards"])
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
        connection.send_result(msg["id"], {"result": await card.run(msg["function"], msg["args"])})
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


@command({vol.Required("type"): "aac/wire/unblock", vol.Required("from"): str, vol.Required("to"): str})
async def ws_wire_unblock(hass, connection, msg) -> None:
    """A red wire (the storm stop blocked it) carries again."""
    domain = _core(hass).domain
    connection.send_result(msg["id"], {"unblocked": domain.unblock(msg["from"], msg["to"]), "red": domain.red})


@command({vol.Required("type"): "aac/permissions"})
async def ws_permissions(hass, connection, msg) -> None:
    """The running domain's sheet, every thing it can be about, and whether the copy slot holds one."""
    core = _core(hass)
    connection.send_result(msg["id"], {"levels": core.domain.permissions, "things": catalog(hass), "levels_all": list(LEVELS),
                                       "copied": core.data.get("permissions_copied") is not None})


@command({vol.Required("type"): "aac/permissions/set", vol.Required("levels"): {str: vol.In(LEVELS)}})
async def ws_permissions_set(hass, connection, msg) -> None:
    """The owner changes the running domain's sheet (hidden takes a thing off it)."""
    domain = _core(hass).domain
    await domain.set_permissions(msg["levels"])
    connection.send_result(msg["id"], {"levels": domain.permissions})


@command({vol.Required("type"): "aac/permissions/copy"})
async def ws_permissions_copy(hass, connection, msg) -> None:
    """The running domain's sheet into the copy slot (one slot, AAC-wide)."""
    core = _core(hass)
    core.data["permissions_copied"] = deepcopy(core.domain.permissions)
    core.save()
    connection.send_result(msg["id"], {"copied": True})


@command({vol.Required("type"): "aac/permissions/paste"})
async def ws_permissions_paste(hass, connection, msg) -> None:
    """The copy slot's sheet replaces the running domain's."""
    core = _core(hass)
    if (copied := core.data.get("permissions_copied")) is None:
        return await _fail(connection, msg, ValueError("nothing copied yet"))
    await core.domain.set_permissions({**{t: "hidden" for t in core.domain.permissions}, **copied})
    connection.send_result(msg["id"], {"levels": core.domain.permissions})


@command({vol.Required("type"): "aac/asks"})
async def ws_asks(hass, connection, msg) -> None:
    """What waits for the owner: the asks (a yes for one action, every domain) and the running domain's requests."""
    core = _core(hass)
    connection.send_result(msg["id"], {"asks": list(core.asks.waiting.values()), "requests": core.domain.requests})


@command({vol.Required("type"): "aac/ask/answer", vol.Required("ask"): str, vol.Required("yes"): bool})
async def ws_ask_answer(hass, connection, msg) -> None:
    """The owner's yes or no to one action (False: nothing waits under that id any more)."""
    connection.send_result(msg["id"], {"answered": _core(hass).asks.answer(msg["ask"], msg["yes"])})


@command({vol.Required("type"): "aac/request/decide", vol.Required("request"): str, vol.Required("allowed"): [str]})
async def ws_request_decide(hass, connection, msg) -> None:
    """The owner's answer to a card's request: the things allowed (all, some, none)."""
    try:
        granted = await _core(hass).domain.decide(msg["request"], msg["allowed"])
    except ValueError as err:
        return await _fail(connection, msg, err)
    connection.send_result(msg["id"], {"granted": granted})


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


@command({vol.Required("type"): "aac/redo"})
async def ws_redo(hass, connection, msg) -> None:
    """Forward again to what the last Undo took back."""
    connection.send_result(msg["id"], {"redone": await _core(hass).domain.redo_last()})


@command({vol.Required("type"): "aac/slot", vol.Required("slot"): vol.In(["1", "2", "3", "4"]), vol.Required("action"): vol.In(["save", "load"])})
async def ws_slot(hass, connection, msg) -> None:
    """Save the setup to a slot, or load it."""
    domain = _core(hass).domain
    if msg["action"] == "save":
        domain.save_slot(msg["slot"])
        return connection.send_result(msg["id"], {"saved": True})
    connection.send_result(msg["id"], {"loaded": await domain.load_slot(msg["slot"])})


@command({vol.Required("type"): "aac/trial", vol.Required("action"): vol.In(["keep", "back"])})
async def ws_trial(hass, connection, msg) -> None:
    """A model's switch that waits for keep: the owner keeps it, or takes it back now."""
    domain = _core(hass).domain
    done = domain.keep() if msg["action"] == "keep" else await domain.go_back(by="you")
    connection.send_result(msg["id"], {"done": done})


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
    """The newest context that passed an output, and what passed it lately (from AAC's log; off: the card's Log setting
    writes nothing going out)."""
    domain = _core(hass).domain
    card_id, _, port = msg["wire"].partition(".")
    card = domain.cards.get(card_id)
    off = card is None or card.settings.get("log") == "nothing" or card.settings.get("log_at") not in ("out", "both")
    records = [{"time": e["time"], "text": e["text"]} for e in domain.log.read(card=card_id, port=port, what="out", last=20)]
    connection.send_result(msg["id"], {"context": domain.flow.last.get(msg["wire"]), "records": records, "off": off})


@command({vol.Required("type"): "aac/log", vol.Optional("card"): str, vol.Optional("part"): str,
          vol.Optional("levels"): [str], vol.Optional("search"): str, vol.Optional("before"): int, vol.Optional("last"): int})
async def ws_log(hass, connection, msg) -> None:
    """AAC's log of the running domain (Settings → Logs): the newest entries that match."""
    filters = {k: msg[k] for k in ("card", "part", "levels", "search", "before") if k in msg}
    connection.send_result(msg["id"], {"entries": _core(hass).domain.log.read(**filters, last=max(1, min(msg.get("last", 300), 5000)))})


@command({vol.Required("type"): "aac/log/clear"})
async def ws_log_clear(hass, connection, msg) -> None:
    """Every entry of the running domain's log goes."""
    await _core(hass).domain.log.clear()
    connection.send_result(msg["id"], {})


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


# ---------------------------------------------------------------------- models (core) and domains
@command({vol.Required("type"): "aac/models/save", vol.Required("model"): {
    vol.Optional("id"): str, vol.Required("name"): str, vol.Optional("server"): str, vol.Optional("base_url"): str,
    vol.Required("model"): str, vol.Optional("api_key"): vol.Any(str, None), vol.Optional("tags"): [str],
    vol.Optional("parallel"): vol.All(vol.Coerce(int), vol.Range(1, 64)), vol.Optional("follow"): bool}})
async def ws_models_save(hass, connection, msg) -> None:
    """Add or change one of AAC's models (on a server)."""
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


@command({vol.Required("type"): "aac/servers/save", vol.Required("server"): {
    vol.Optional("id"): str, vol.Required("name"): vol.All(str, vol.Length(min=1, max=40)), vol.Required("base_url"): str,
    vol.Optional("api_key"): vol.Any(str, None)}})
async def ws_servers_save(hass, connection, msg) -> None:
    """Add or change a model server."""
    connection.send_result(msg["id"], {"id": _core(hass).save_server(dict(msg["server"]))["id"]})


@command({vol.Required("type"): "aac/servers/delete", vol.Required("server_id"): str})
async def ws_servers_delete(hass, connection, msg) -> None:
    """Take a server and its models out of AAC."""
    core = _core(hass)
    core.delete_server(msg["server_id"])
    connection.send_result(msg["id"], {"models": core.pool.summary()})


@command({vol.Required("type"): "aac/models/servers"})
async def ws_models_servers(hass, connection, msg) -> None:
    """Every server with what it offers now (a llama-server router: its models, their state, numbers)."""
    connection.send_result(msg["id"], {"servers": await _core(hass).pool.offers()})


@command({vol.Required("type"): "aac/models/router", vol.Required("server_id"): str,
          vol.Required("action"): vol.In(["load", "unload", "download", "delete", "bench"]), vol.Required("name"): str,
          vol.Optional("sidecars"): [str]})
async def ws_models_router(hass, connection, msg) -> None:
    """On a router: load, unload, download or delete one of its models, or benchmark one (the result is kept)."""
    core = _core(hass)
    server = core.pool.server_of(msg["server_id"])
    if server is None:
        return await _fail(connection, msg, ValueError("no such server"))
    try:
        if msg["action"] == "bench":
            await core.pool.bench(server["client"], msg["name"])
            core.save()
        else:
            await server["client"].router_do(msg["action"], msg["name"], msg.get("sidecars"))
            if msg["action"] == "delete":
                core.pool.forget(server["client"], msg["name"])
    except ModelError as err:
        return await _fail(connection, msg, err)
    connection.send_result(msg["id"], {"models": core.pool.summary()})


@command({vol.Required("type"): "aac/domain/clone", vol.Required("name"): vol.All(str, vol.Length(min=1, max=40))})
async def ws_domain_clone(hass, connection, msg) -> None:
    """A copy of the running domain."""
    connection.send_result(msg["id"], {"id": await _core(hass).clone(msg["name"])})


@command({vol.Required("type"): "aac/domain/create", vol.Required("name"): vol.All(str, vol.Length(min=1, max=40))})
async def ws_domain_create(hass, connection, msg) -> None:
    """A new, empty domain."""
    connection.send_result(msg["id"], {"id": await _core(hass).create(msg["name"])})


@command({vol.Required("type"): "aac/domain/rename", vol.Required("domain_id"): str,
          vol.Required("name"): vol.All(str, vol.Length(min=1, max=40))})
async def ws_domain_rename(hass, connection, msg) -> None:
    """A domain's new name (its id follows: the new one)."""
    connection.send_result(msg["id"], {"id": await _core(hass).rename(msg["domain_id"], msg["name"])})


@command({vol.Required("type"): "aac/domain/delete", vol.Required("domain_id"): str})
async def ws_domain_delete(hass, connection, msg) -> None:
    """A domain goes, with its cards' data."""
    try:
        await _core(hass).delete(msg["domain_id"])
    except (ValueError, StopIteration) as err:
        return await _fail(connection, msg, err)
    connection.send_result(msg["id"], {"id": _core(hass).domain.id})


@command({vol.Required("type"): "aac/domain/switch", vol.Required("domain_id"): str})
async def ws_domain_switch(hass, connection, msg) -> None:
    """Run another domain."""
    domain = await _core(hass).switch(msg["domain_id"])
    connection.send_result(msg["id"], {"id": domain.id})


# ---------------------------------------------------------------------- AAC's settings (core)
@command({vol.Required("type"): "aac/settings"})
async def ws_settings(hass, connection, msg) -> None:
    """AAC's own settings (every domain): the storm limit; and the rules every card follows (How AAC works)."""
    from .rules import RULES  # noqa: PLC0415

    core = _core(hass)
    connection.send_result(msg["id"], {"storm": core.storm, "ask_notify": (core.data.get("settings") or {}).get("ask_notify") or "",
                                       "rules": RULES})


@command({vol.Required("type"): "aac/settings/set", vol.Optional("storm"): vol.All(int, vol.Range(min=1, max=10000)),
          vol.Optional("ask_notify"): str})
async def ws_settings_set(hass, connection, msg) -> None:
    """Change AAC's settings: the storm limit; the notify service that asks the owner on the phone (empty: only panels)."""
    core = _core(hass)
    for key in ("storm", "ask_notify"):
        if key in msg:
            core.data.setdefault("settings", {})[key] = msg[key]
    core.save()
    connection.send_result(msg["id"], {"storm": core.storm, "ask_notify": core.data["settings"].get("ask_notify") or ""})


@command({vol.Required("type"): "aac/appearance"})
async def ws_appearance(hass, connection, msg) -> None:
    """The skins and the one in use (shared by every device)."""
    from .skins import list_skins  # noqa: PLC0415

    core = _core(hass)
    connection.send_result(msg["id"], {"skins": await hass.async_add_executor_job(list_skins, hass),
                                       "skin": core.data.get("skin", ""), "folds": core.data.get("folds", {})})


@command({vol.Required("type"): "aac/appearance/set", vol.Optional("skin"): str,
          vol.Optional("folds"): {str: vol.In(["auto", "arrow"])}})
async def ws_appearance_set(hass, connection, msg) -> None:
    """Use a skin ("" = Home Assistant's look); how each sidebar group folds (auto: open while in use; arrow)."""
    core = _core(hass)
    if "skin" in msg:
        core.data["skin"] = msg["skin"]
    if "folds" in msg:
        core.data["folds"] = {**core.data.get("folds", {}), **msg["folds"]}
    core.save()
    core.notify("appearance")
    connection.send_result(msg["id"], {})


@command({vol.Required("type"): "aac/audio"})
async def ws_audio(hass, connection, msg) -> None:
    """Satellites (live levels), engines, volume."""
    connection.send_result(msg["id"], _core(hass).audio.summary())


@command({vol.Required("type"): "aac/audio/settings", vol.Optional("stt"): [str], vol.Optional("tts"): vol.Any(str, None),
          vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(0, 100))})
async def ws_audio_settings(hass, connection, msg) -> None:
    """Change the engines and the volume."""
    core = _core(hass)
    core.audio.settings.update({k: v for k, v in msg.items() if k in ("stt", "tts", "volume")})
    core.save()
    connection.send_result(msg["id"], core.audio.summary())


@command({vol.Required("type"): "aac/languages/set", vol.Required("languages"): vol.Any([str], None)})
async def ws_languages_set(hass, connection, msg) -> None:
    """The home's languages (main first); None: as Home Assistant describes the home."""
    from .languages import code_of  # noqa: PLC0415

    core = _core(hass)
    codes = list(dict.fromkeys(c for c in (code_of(x) for x in msg["languages"] or []) if c))
    if codes:
        core.data["languages"] = codes
    else:
        core.data.pop("languages", None)
    core.save()
    core.notify("audio")
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
