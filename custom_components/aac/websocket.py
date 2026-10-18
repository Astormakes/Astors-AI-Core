"""WebSocket API for the AAC panel (admin only)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_BASE_URL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DOMAIN,
)
from .provider import ProviderAuthError, ProviderError
from .audio import audio_overview
from .master import async_run_master
from .runs import SIGNAL_UPDATE
from .status import aac_agent_entity_id, setup_steps
from .store import CONF_WORKER, AACStore
from .workers import async_list_models, async_overview


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register all aac/* commands."""
    for command in (
        ws_state,
        ws_master_update,
        ws_workers,
        ws_worker_save,
        ws_worker_delete,
        ws_server_models,
        ws_setup_skip,
        ws_audio,
        ws_chat,
        ws_chats,
        ws_chat_get,
        ws_chat_new,
        ws_chat_delete,
        ws_runs,
        ws_run_get,
        ws_heartbeat_update,
        ws_heartbeat_run,
        ws_trigger_save,
        ws_trigger_delete,
        ws_subscribe,
    ):
        websocket_api.async_register_command(hass, command)


def _entry(hass: HomeAssistant, connection, msg):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "AAC is not loaded")
        return None
    return entries[0]


def _loaded(hass: HomeAssistant, connection, msg) -> tuple[AACStore, str] | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "AAC is not loaded")
        return None
    return entries[0].runtime_data.store, entries[0].entry_id


def _state(hass: HomeAssistant, store: AACStore, entry_id: str) -> dict[str, Any]:
    entry = hass.config_entries.async_get_entry(entry_id)
    return {
        "version": entry.runtime_data.version if entry else None,
        "agent_entity_id": aac_agent_entity_id(hass, entry_id),
        "master": store.master,
        "setup": setup_steps(hass, store),
        "heartbeat": store.heartbeat,
        "triggers": store.triggers,
        "llm_apis": [{"id": api.id, "name": api.name} for api in llm.async_get_apis(hass)],
    }


@websocket_api.websocket_command({vol.Required("type"): "aac/state"})
@websocket_api.require_admin
@callback
def ws_state(hass: HomeAssistant, connection, msg) -> None:
    """Version, master settings, setup steps."""
    if loaded := _loaded(hass, connection, msg):
        connection.send_result(msg["id"], _state(hass, *loaded))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aac/master/update",
        vol.Optional(CONF_WORKER): vol.Any(str, None),
        vol.Optional(CONF_PROMPT): str,
        vol.Optional(CONF_LLM_HASS_API): [str],
        vol.Optional(CONF_TEMPERATURE): vol.All(vol.Coerce(float), vol.Range(0, 2)),
        vol.Optional(CONF_MAX_TOKENS): vol.All(vol.Coerce(int), vol.Range(16, 131072)),
        vol.Optional(CONF_THINKING): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_master_update(hass: HomeAssistant, connection, msg) -> None:
    """Change master agent settings."""
    if not (loaded := _loaded(hass, connection, msg)):
        return
    store, entry_id = loaded
    changes = {k: v for k, v in msg.items() if k not in ("id", "type")}
    await store.async_update_master(changes)
    connection.send_result(msg["id"], _state(hass, store, entry_id))


@websocket_api.websocket_command({vol.Required("type"): "aac/workers"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_workers(hass: HomeAssistant, connection, msg) -> None:
    """Manual + auto workers with live state."""
    if loaded := _loaded(hass, connection, msg):
        connection.send_result(msg["id"], await async_overview(hass, loaded[0]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aac/worker/save",
        vol.Optional("worker_id"): str,
        vol.Required("name"): vol.All(str, vol.Length(min=1)),
        vol.Required(CONF_BASE_URL): vol.All(str, vol.Length(min=1)),
        vol.Optional(CONF_API_KEY): vol.Any(str, None),
        vol.Required(CONF_MODEL): vol.All(str, vol.Length(min=1)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_worker_save(hass: HomeAssistant, connection, msg) -> None:
    """Add or change a manual worker."""
    if not (loaded := _loaded(hass, connection, msg)):
        return
    worker = {k: msg[k] for k in ("name", CONF_BASE_URL, CONF_MODEL)}
    if "worker_id" in msg:
        worker["id"] = msg["worker_id"]
    if CONF_API_KEY in msg:
        worker[CONF_API_KEY] = msg[CONF_API_KEY]
    saved = await loaded[0].async_save_worker(worker)
    connection.send_result(msg["id"], {"id": saved["id"]})


@websocket_api.websocket_command(
    {vol.Required("type"): "aac/worker/delete", vol.Required("worker_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_worker_delete(hass: HomeAssistant, connection, msg) -> None:
    """Remove a manual worker."""
    if loaded := _loaded(hass, connection, msg):
        await loaded[0].async_delete_worker(msg["worker_id"])
        connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aac/server/models",
        vol.Required(CONF_BASE_URL): str,
        vol.Optional(CONF_API_KEY): vol.Any(str, None),
        vol.Optional("worker_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_server_models(hass: HomeAssistant, connection, msg) -> None:
    """Test a server and list its models (for the worker form)."""
    if not (loaded := _loaded(hass, connection, msg)):
        return
    api_key = msg.get(CONF_API_KEY)
    if not api_key and (worker := loaded[0].get_worker(msg.get("worker_id", ""))):
        api_key = worker.get(CONF_API_KEY)
    try:
        models = await async_list_models(hass, msg[CONF_BASE_URL], api_key, timeout=10)
    except ProviderAuthError:
        connection.send_error(msg["id"], "invalid_auth", "The server rejected the API key")
    except (ProviderError, TimeoutError) as err:
        connection.send_error(msg["id"], "cannot_connect", str(err) or "timeout")
    else:
        connection.send_result(msg["id"], {"models": models})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aac/setup/skip",
        vol.Required("step"): str,
        vol.Required("skipped"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_setup_skip(hass: HomeAssistant, connection, msg) -> None:
    """Skip (or un-skip) an optional setup step."""
    if not (loaded := _loaded(hass, connection, msg)):
        return
    store, entry_id = loaded
    await store.async_set_skipped(msg["step"], msg["skipped"])
    connection.send_result(msg["id"], _state(hass, store, entry_id))


@websocket_api.websocket_command({vol.Required("type"): "aac/audio"})
@websocket_api.require_admin
@callback
def ws_audio(hass: HomeAssistant, connection, msg) -> None:
    """AAC's own audio pipeline: stages and candidate sources."""
    if _loaded(hass, connection, msg):
        connection.send_result(msg["id"], audio_overview(hass))


def _run_summary(run) -> dict[str, Any]:
    data = run.as_dict()
    data["steps"] = len(run.steps)
    return data


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aac/chat",
        vol.Required("text"): vol.All(str, vol.Length(min=1)),
        vol.Optional("chat_id"): vol.Any(str, None),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_chat(hass: HomeAssistant, connection, msg) -> None:
    """Send a message in a chat (a new chat if none given) and get the answer."""
    if not (entry := _entry(hass, connection, msg)):
        return
    chats = entry.runtime_data.chats
    chat = chats.get(msg.get("chat_id") or "") or chats.create()
    chats.add_message(chat["id"], "user", msg["text"])
    run, result = await async_run_master(
        hass,
        entry,
        msg["text"],
        kind="chat",
        trigger="chat",
        title=chat["title"],
        chat_id=chat["id"],
        conversation_id=chat["conversation_id"],
        user_id=connection.user.id,
    )
    if result is not None:
        chats.set_conversation(chat["id"], result.conversation_id)
    message = chats.add_message(
        chat["id"],
        "aac",
        run.result or "(no answer)",
        error=run.status == "failed",
        run_id=run.id,
        steps=run.steps,
    )
    connection.send_result(msg["id"], {"chat_id": chat["id"], "message": message})


@websocket_api.websocket_command({vol.Required("type"): "aac/chats"})
@websocket_api.require_admin
@callback
def ws_chats(hass: HomeAssistant, connection, msg) -> None:
    """All chats (without messages) + automatic runs for the WORKERS segment."""
    if entry := _entry(hass, connection, msg):
        connection.send_result(msg["id"], {"chats": entry.runtime_data.chats.list()})


@websocket_api.websocket_command({vol.Required("type"): "aac/chat/get", vol.Required("chat_id"): str})
@websocket_api.require_admin
@callback
def ws_chat_get(hass: HomeAssistant, connection, msg) -> None:
    """One chat with its messages (marks it read)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    chats = entry.runtime_data.chats
    if (chat := chats.get(msg["chat_id"])) is None:
        connection.send_error(msg["id"], "not_found", "Chat not found")
        return
    chats.mark_read(msg["chat_id"])
    connection.send_result(msg["id"], chat)


@websocket_api.websocket_command({vol.Required("type"): "aac/chat/new"})
@websocket_api.require_admin
@callback
def ws_chat_new(hass: HomeAssistant, connection, msg) -> None:
    """Start an empty chat."""
    if entry := _entry(hass, connection, msg):
        connection.send_result(msg["id"], entry.runtime_data.chats.create())


@websocket_api.websocket_command({vol.Required("type"): "aac/chat/delete", vol.Required("chat_id"): str})
@websocket_api.require_admin
@callback
def ws_chat_delete(hass: HomeAssistant, connection, msg) -> None:
    """Delete a chat."""
    if entry := _entry(hass, connection, msg):
        entry.runtime_data.chats.delete(msg["chat_id"])
        connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {vol.Required("type"): "aac/runs", vol.Optional("kind"): vol.In(["chat", "auto"])}
)
@websocket_api.require_admin
@callback
def ws_runs(hass: HomeAssistant, connection, msg) -> None:
    """Runs, newest first (steps counted, not included)."""
    if entry := _entry(hass, connection, msg):
        runs = [r for r in entry.runtime_data.runs.runs if msg.get("kind") in (None, r.kind)]
        connection.send_result(msg["id"], [_run_summary(r) for r in reversed(runs)])


@websocket_api.websocket_command({vol.Required("type"): "aac/run/get", vol.Required("run_id"): str})
@websocket_api.require_admin
@callback
def ws_run_get(hass: HomeAssistant, connection, msg) -> None:
    """One run with all steps."""
    if not (entry := _entry(hass, connection, msg)):
        return
    if (run := entry.runtime_data.runs.get(msg["run_id"])) is None:
        connection.send_error(msg["id"], "not_found", "Run not found")
        return
    connection.send_result(msg["id"], run.as_dict())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aac/heartbeat/update",
        vol.Optional("enabled"): bool,
        vol.Optional("interval"): vol.All(vol.Coerce(int), vol.Range(1, 1440)),
        vol.Optional("quiet_start"): vol.Match(r"^\d{1,2}:\d{2}$"),
        vol.Optional("quiet_end"): vol.Match(r"^\d{1,2}:\d{2}$"),
        vol.Optional("instructions"): vol.All(str, vol.Length(min=1)),
        vol.Optional("tools"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_heartbeat_update(hass: HomeAssistant, connection, msg) -> None:
    """Change heartbeat settings (applies immediately)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    await entry.runtime_data.store.async_update_heartbeat(
        {k: v for k, v in msg.items() if k not in ("id", "type")}
    )
    entry.runtime_data.triggers.async_start()
    connection.send_result(msg["id"], _state(hass, entry.runtime_data.store, entry.entry_id))


@websocket_api.websocket_command({vol.Required("type"): "aac/heartbeat/run"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_heartbeat_run(hass: HomeAssistant, connection, msg) -> None:
    """Run one heartbeat now (ignores quiet hours and the on/off switch)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    run_id = await entry.runtime_data.triggers.async_heartbeat(force=True)
    if run_id is None:
        connection.send_error(msg["id"], "skipped", "No master model, or a heartbeat is already running")
        return
    connection.send_result(msg["id"], {"run_id": run_id})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aac/trigger/save",
        vol.Optional("trigger_id"): str,
        vol.Required("name"): str,
        vol.Required("entity_id"): vol.All(str, vol.Length(min=3)),
        vol.Optional("to", default=""): str,
        vol.Optional("instruction", default=""): str,
        vol.Optional("cooldown", default=300): vol.All(vol.Coerce(int), vol.Range(0, 86400)),
        vol.Optional("enabled", default=True): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_trigger_save(hass: HomeAssistant, connection, msg) -> None:
    """Add or change an event trigger."""
    if not (entry := _entry(hass, connection, msg)):
        return
    trig = {k: msg[k] for k in ("name", "entity_id", "to", "instruction", "cooldown", "enabled")}
    if "trigger_id" in msg:
        trig["id"] = msg["trigger_id"]
    saved = await entry.runtime_data.store.async_save_trigger(trig)
    entry.runtime_data.triggers.async_start()
    connection.send_result(msg["id"], saved)


@websocket_api.websocket_command({vol.Required("type"): "aac/trigger/delete", vol.Required("trigger_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_trigger_delete(hass: HomeAssistant, connection, msg) -> None:
    """Remove an event trigger."""
    if not (entry := _entry(hass, connection, msg)):
        return
    await entry.runtime_data.store.async_delete_trigger(msg["trigger_id"])
    entry.runtime_data.triggers.async_start()
    connection.send_result(msg["id"])


@websocket_api.websocket_command({vol.Required("type"): "aac/subscribe"})
@websocket_api.require_admin
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg) -> None:
    """Push changes (runs, chats) to the panel."""

    @callback
    def forward(update: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], update))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_UPDATE, forward)
    connection.send_result(msg["id"])
