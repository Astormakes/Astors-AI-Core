"""The panel's websocket API (admin only)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.loader import async_get_integration

from .const import DOMAIN, SIGNAL
from .const import MODEL_TAGS, ModelError
from .home import catalog
from .permissions import LEVELS

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
    layer = await core.layer_ask("panel", {"type": "aac/state"})  # the running domain, the card types
    connection.send_result(msg["id"], {
        "version": str((await async_get_integration(hass, DOMAIN)).version), **layer,
        "domains": core.data["domains"], "active": core.data["active"], "models": core.pool.summary(), "model_tags": MODEL_TAGS})


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


@command({vol.Required("type"): "aac/view/watch", vol.Required("cards"): [str]})
async def ws_view_watch(hass, connection, msg) -> None:
    """An open Cards page: which cards' views it shows (again every few seconds; live wires go only while it is open)."""
    _core(hass).watch(msg["cards"])
    connection.send_result(msg["id"], {})


@command({vol.Required("type"): "aac/permissions"})
async def ws_permissions(hass, connection, msg) -> None:
    """The running domain's sheet, every thing it can be about, and whether the copy slot holds one."""
    core = _core(hass)
    connection.send_result(msg["id"], {"levels": core.sheet, "things": catalog(hass), "levels_all": list(LEVELS),
                                       "copied": core.data.get("permissions_copied") is not None})


@command({vol.Required("type"): "aac/permissions/set", vol.Required("levels"): {str: vol.In(LEVELS)}})
async def ws_permissions_set(hass, connection, msg) -> None:
    """The owner changes the running domain's sheet (hidden takes a thing off it)."""
    connection.send_result(msg["id"], {"levels": await _core(hass).set_permissions(msg["levels"])})


@command({vol.Required("type"): "aac/permissions/copy"})
async def ws_permissions_copy(hass, connection, msg) -> None:
    """The running domain's sheet into the copy slot (one slot, AAC-wide)."""
    core = _core(hass)
    core.data["permissions_copied"] = deepcopy(core.sheet)
    core.save()
    connection.send_result(msg["id"], {"copied": True})


@command({vol.Required("type"): "aac/permissions/paste"})
async def ws_permissions_paste(hass, connection, msg) -> None:
    """The copy slot's sheet replaces the running domain's."""
    core = _core(hass)
    if (copied := core.data.get("permissions_copied")) is None:
        return await _fail(connection, msg, ValueError("nothing copied yet"))
    connection.send_result(msg["id"], {"levels": await core.set_permissions({**{t: "hidden" for t in core.sheet}, **copied})})


@command({vol.Required("type"): "aac/asks"})
async def ws_asks(hass, connection, msg) -> None:
    """What waits for the owner: the asks (a yes for one action, every domain) and the running domain's requests."""
    core = _core(hass)
    connection.send_result(msg["id"], {"asks": list(core.asks.waiting.values()), "requests": core.requests})


@command({vol.Required("type"): "aac/ask/answer", vol.Required("ask"): str, vol.Required("yes"): bool})
async def ws_ask_answer(hass, connection, msg) -> None:
    """The owner's yes or no to one action (False: nothing waits under that id any more)."""
    connection.send_result(msg["id"], {"answered": _core(hass).asks.answer(msg["ask"], msg["yes"])})


@command({vol.Required("type"): "aac/request/decide", vol.Required("request"): str, vol.Required("allowed"): [str]})
async def ws_request_decide(hass, connection, msg) -> None:
    """The owner's answer to a card's request: the things allowed (all, some, none)."""
    try:
        granted = await _core(hass).decide(msg["request"], msg["allowed"])
    except ValueError as err:
        return await _fail(connection, msg, err)
    connection.send_result(msg["id"], {"granted": granted})



# ---------------------------------------------------------------------- the running domain: the card layer answers (panel.py)
FORWARDED = [  # handed on as they came; the answer is the card layer's
    {vol.Required("type"): "aac/card/add", vol.Required("card_type"): str, vol.Optional("title"): str,
          vol.Optional("settings"): dict, vol.Optional("x"): vol.Coerce(float), vol.Optional("y"): vol.Coerce(float)},
    {vol.Required("type"): "aac/card/remove", vol.Required("cards"): [str], vol.Optional("drop_data", default=False): bool},
    {vol.Required("type"): "aac/values"},
    {vol.Required("type"): "aac/card/update", vol.Required("card"): str, vol.Optional("title"): str,
          vol.Optional("settings"): dict},
    {vol.Required("type"): "aac/card/call", vol.Required("card"): str, vol.Required("function"): str,
          vol.Optional("args", default={}): dict},
    {vol.Required("type"): "aac/card/data", vol.Required("card"): str},
    {vol.Required("type"): "aac/wires", vol.Required("wires"): [dict]},
    {vol.Required("type"): "aac/wire/unblock", vol.Required("from"): str, vol.Required("to"): str},
    {vol.Required("type"): "aac/positions", vol.Required("positions"): dict, vol.Optional("undoable", default=False): bool},
    {vol.Required("type"): "aac/undo"},
    {vol.Required("type"): "aac/redo"},
    {vol.Required("type"): "aac/slot", vol.Required("slot"): vol.In(["1", "2", "3", "4"]), vol.Required("action"): vol.In(["save", "load"])},
    {vol.Required("type"): "aac/trial", vol.Required("action"): vol.In(["keep", "back"])},
    {vol.Required("type"): "aac/setup/export"},
    {vol.Required("type"): "aac/setup/import", vol.Required("setup"): dict},
    {vol.Required("type"): "aac/stop"},
    {vol.Required("type"): "aac/monitor", vol.Required("wire"): str},
    {vol.Required("type"): "aac/log", vol.Optional("card"): str, vol.Optional("part"): str,
          vol.Optional("levels"): [str], vol.Optional("search"): str, vol.Optional("before"): int, vol.Optional("last"): int},
    {vol.Required("type"): "aac/log/clear"},
    {vol.Required("type"): "aac/tags/add", vol.Required("tag"): str},
    {vol.Required("type"): "aac/tags/remove", vol.Required("tag"): str},
    {vol.Required("type"): "aac/nav"},
    {vol.Required("type"): "aac/card/ui", vol.Required("card"): str, vol.Required("action"): str,
          vol.Optional("args", default={}): dict},
]


def _forward(schema: dict) -> None:
    @command(schema)
    async def handler(hass, connection, msg) -> None:
        try:
            result = await _core(hass).layer_ask("panel", {k: v for k, v in msg.items() if k != "id"})
        except (ValueError, KeyError, PermissionError) as err:
            return await _fail(connection, msg, err)
        connection.send_result(msg["id"], result)


for _schema in FORWARDED:
    _forward(_schema)

# ---------------------------------------------------------------------- (card pages: forwarded above)
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
    connection.send_result(msg["id"], {"id": _core(hass).running.get("id")})


@command({vol.Required("type"): "aac/domain/switch", vol.Required("domain_id"): str})
async def ws_domain_switch(hass, connection, msg) -> None:
    """Run another domain."""
    connection.send_result(msg["id"], {"id": await _core(hass).switch(msg["domain_id"])})


# ---------------------------------------------------------------------- AAC's settings (core)
@command({vol.Required("type"): "aac/settings"})
async def ws_settings(hass, connection, msg) -> None:
    """AAC's own settings (every domain): the storm limit; and the rules every card follows (How AAC works)."""
    from .rules import RULES  # noqa: PLC0415

    core = _core(hass)
    connection.send_result(msg["id"], {"storm": core.storm, "ask_notify": (core.data.get("settings") or {}).get("ask_notify") or "",
                                       "rules": RULES, "cards_run": core.cards_run()})


@command({vol.Required("type"): "aac/settings/set", vol.Optional("storm"): vol.All(int, vol.Range(min=1, max=10000)),
          vol.Optional("ask_notify"): str})
async def ws_settings_set(hass, connection, msg) -> None:
    """Change AAC's settings: the storm limit; the notify service that asks the owner on the phone (empty: only panels)."""
    core = _core(hass)
    for key in ("storm", "ask_notify"):
        if key in msg:
            core.data.setdefault("settings", {})[key] = msg[key]
    core.save()
    core.share()
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
    core.share()
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
