"""WebSocket API for the AAC panel (admin only)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import conversation, websocket_api
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import llm

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
    ):
        websocket_api.async_register_command(hass, command)


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


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aac/chat",
        vol.Required("text"): str,
        vol.Optional("conversation_id"): vol.Any(str, None),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_chat(hass: HomeAssistant, connection, msg) -> None:
    """Send one message to the master agent."""
    if not (loaded := _loaded(hass, connection, msg)):
        return
    result = await conversation.async_converse(
        hass,
        msg["text"],
        msg.get("conversation_id"),
        Context(user_id=connection.user.id),
        agent_id=loaded[1],
    )
    response = result.response
    connection.send_result(
        msg["id"],
        {
            "conversation_id": result.conversation_id,
            "speech": response.speech.get("plain", {}).get("speech", ""),
            "error": response.response_type.value == "error",
        },
    )
