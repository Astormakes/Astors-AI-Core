"""WebSocket API for the AAC panel (admin only)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, llm
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .languages import NAMES, from_ha, home_languages
from .const import (
    CONF_BASE_URL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DOMAIN,
)
from .provider import ProviderAuthError, ProviderError, ProviderNotFoundError
from .audio import audio_overview
from .broker import async_run_request
from .runs import SIGNAL_UPDATE, async_notify
from .status import aac_agent_entity_id, setup_steps
from .pools import ROLES, async_set_role
from .modules import access as module_access, async_set_access, async_set_settings, setting as module_setting
from .entities import all_entities, async_set_access as async_set_entity_access, async_set_visible
from .satellite_app import async_prepare as async_prepare_satellite
from .skins import list_skins, user_dir
from .domains import Domain, async_remove_domain_data
from .store import AACStore
from .streams import STREAM_TYPES
from .models import async_list_models, async_overview


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register all aac/* commands."""
    for command in (
        ws_state,
        ws_master_update,
        ws_models,
        ws_machine_set,
        ws_model_save,
        ws_model_delete,
        ws_server_models,
        ws_setup_skip,
        ws_audio,
        ws_audio_settings,
        ws_languages,
        ws_languages_set,
        ws_audio_say,
        ws_audio_live,
        ws_chat,
        ws_chats,
        ws_chat_rewind,
        ws_chat_edit,
        ws_chat_get,
        ws_chat_new,
        ws_chat_delete,
        ws_chat_archive,
        ws_runs,
        ws_run_get,
        ws_run_request,
        ws_pool_tool_mode,
        ws_subscribe,
        ws_pools,
        ws_skins,
        ws_satellite_app,
        ws_entities,
        ws_entities_set,
        ws_domain_visible,
        ws_domain_visible_set,
        ws_requests,
        ws_request_decide,
        ws_modules,
        ws_nodes,
        ws_node_containers,
        ws_module_settings,
        ws_module_access,
        ws_jobs,
        ws_jobs_settings,
        ws_domain_create,
        ws_domain_rename,
        ws_domain_delete,
        ws_domain_voice,
        ws_skin_set,
        ws_pool_set,
        ws_pool_tags,
        ws_pool_measure,
        ws_pool_rule,
    ):
        websocket_api.async_register_command(hass, command)


def aac_command(schema: dict):
    """An aac/* command; every one may name the AAC domain it is about (default: the first)."""
    return websocket_api.websocket_command({**schema, vol.Optional("domain"): vol.Any(str, None)})


def _d(entry, msg) -> Domain:
    """The domain a message is about (unknown or missing: the first)."""
    return entry.runtime_data.domain(msg.get("domain")) or entry.runtime_data.domain()


def _ctx(hass: HomeAssistant, msg) -> tuple:
    entry = hass.config_entries.async_loaded_entries(DOMAIN)[0]
    return entry, _d(entry, msg)


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


def _state(hass: HomeAssistant, entry, dom: Domain) -> dict[str, Any]:
    """Everything the panel needs about one domain, plus the shared parts."""
    store, settings = entry.runtime_data.store, dom.settings
    return {
        "version": entry.runtime_data.version,
        "domain": {"id": dom.id, "name": dom.name, "first": dom.first},
        "domains": [{"id": d.id, "name": d.name, "first": d.first, "agent_entity_id": d.entity_id(hass, entry.entry_id)}
                    for d in entry.runtime_data.domains.values()],
        "voice_domain": store.data["voice_domain"],
        "agent_entity_id": dom.entity_id(hass, entry.entry_id),
        "master": settings.master,
        "setup": setup_steps(hass, store, settings, entry),
        "ui": store.data["ui"],
        "modules": [m.summary(entry, dom.id) for m in entry.runtime_data.modules],
        "requests_open": len(entry.runtime_data.requests.pending(dom.id)),
        "pools": entry.runtime_data.pool.status(),
        "llm_apis": [{"id": api.id, "name": api.name} for api in llm.async_get_apis(hass)
                     if not api.id.startswith("aac_")],
    }


@aac_command({vol.Required("type"): "aac/state"})
@websocket_api.require_admin
@callback
def ws_state(hass: HomeAssistant, connection, msg) -> None:
    """Version, master settings, setup steps."""
    if loaded := _loaded(hass, connection, msg):
        connection.send_result(msg["id"], _state(hass, *_ctx(hass, msg)))


@aac_command(
    {
        vol.Required("type"): "aac/master/update",
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
    if not (entry := _entry(hass, connection, msg)):
        return
    changes = {k: v for k, v in msg.items() if k not in ("id", "type", "domain")}
    await _d(entry, msg).settings.async_update_master(changes)
    connection.send_result(msg["id"], _state(hass, *_ctx(hass, msg)))


@aac_command({vol.Required("type"): "aac/models"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_models(hass: HomeAssistant, connection, msg) -> None:
    """Manual + auto models with live state."""
    if loaded := _loaded(hass, connection, msg):
        overview = await async_overview(hass, loaded[0])
        if entry := _entry(hass, connection, msg):
            entry.runtime_data.pool.learn_from_overview(overview)  # real sizes for "smart"
        connection.send_result(msg["id"], overview)


@aac_command({vol.Required("type"): "aac/machine/set", vol.Required("host"): str, vol.Required("name"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_machine_set(hass: HomeAssistant, connection, msg) -> None:
    """Name a machine (empty = default name)."""
    if loaded := _loaded(hass, connection, msg):
        await loaded[0].async_set_machine(msg["host"], msg["name"])
        connection.send_result(msg["id"], {"machines": loaded[0].machines})


@aac_command(
    {
        vol.Required("type"): "aac/model/save",
        vol.Optional("model_id"): str,
        vol.Required("name"): vol.All(str, vol.Length(min=1)),
        vol.Required(CONF_BASE_URL): vol.All(str, vol.Length(min=1)),
        vol.Optional(CONF_API_KEY): vol.Any(str, None),
        vol.Required(CONF_MODEL): vol.All(str, vol.Length(min=1)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_model_save(hass: HomeAssistant, connection, msg) -> None:
    """Add or change a manual model."""
    if not (loaded := _loaded(hass, connection, msg)):
        return
    cfg = {k: msg[k] for k in ("name", CONF_BASE_URL, CONF_MODEL)}
    if "model_id" in msg:
        cfg["id"] = msg["model_id"]
    if CONF_API_KEY in msg:
        cfg[CONF_API_KEY] = msg[CONF_API_KEY]
    saved = await loaded[0].async_save_model(cfg)
    connection.send_result(msg["id"], {"id": saved["id"]})


@aac_command(
    {vol.Required("type"): "aac/model/delete", vol.Required("model_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_model_delete(hass: HomeAssistant, connection, msg) -> None:
    """Remove a manual model."""
    if loaded := _loaded(hass, connection, msg):
        await loaded[0].async_delete_model(msg["model_id"])
        connection.send_result(msg["id"])


@aac_command(
    {
        vol.Required("type"): "aac/server/models",
        vol.Required(CONF_BASE_URL): str,
        vol.Optional(CONF_API_KEY): vol.Any(str, None),
        vol.Optional("model_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_server_models(hass: HomeAssistant, connection, msg) -> None:
    """Test a server and list its models (for the model form)."""
    if not (loaded := _loaded(hass, connection, msg)):
        return
    api_key = msg.get(CONF_API_KEY)
    if not api_key and (cfg := loaded[0].get_model(msg.get("model_id", ""))):
        api_key = cfg.get(CONF_API_KEY)
    try:
        models = await async_list_models(hass, msg[CONF_BASE_URL], api_key, timeout=10)
    except ProviderAuthError:
        connection.send_error(msg["id"], "invalid_auth", "The server rejected the API key")
    except ProviderNotFoundError:
        # Reachable but cannot list models (e.g. whisper.cpp): the model name is typed by hand
        connection.send_result(msg["id"], {"models": [], "unlisted": True})
    except (ProviderError, TimeoutError) as err:
        connection.send_error(msg["id"], "cannot_connect", str(err) or "timeout")
    else:
        connection.send_result(msg["id"], {"models": models})


@aac_command(
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
    connection.send_result(msg["id"], _state(hass, *_ctx(hass, msg)))


@aac_command({vol.Required("type"): "aac/audio"})
@websocket_api.require_admin
@callback
def ws_audio(hass: HomeAssistant, connection, msg) -> None:
    """AAC's own audio pipeline: stages, live sources, transcript, settings."""
    if entry := _entry(hass, connection, msg):
        data = entry.runtime_data
        connection.send_result(msg["id"], audio_overview(hass, data.audio, data.store.audio))


@aac_command(
    {
        vol.Required("type"): "aac/audio/settings",
        vol.Optional("names"): [vol.All(str, vol.Length(min=1))],
        vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(0, 100)),
        vol.Optional("pause_ms"): vol.All(vol.Coerce(int), vol.Range(200, 3000)),
        vol.Optional("voices"): {str: {vol.Required("language"): str, vol.Required("voice"): str}},
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_audio_settings(hass: HomeAssistant, connection, msg) -> None:
    """Speech engines, AAC's names, follow-up window."""
    if not (entry := _entry(hass, connection, msg)):
        return
    data = entry.runtime_data
    await data.store.async_update_audio({k: v for k, v in msg.items() if k not in ("id", "type", "domain")})
    data.audio.apply_settings()
    connection.send_result(msg["id"], audio_overview(hass, data.audio, data.store.audio))


def _languages(hass: HomeAssistant, store) -> dict:
    return {"languages": home_languages(hass, store), "set_by_owner": store.data["languages"] is not None,
            "from_ha": from_ha(hass.config.language, hass.config.country),
            "ha": {"language": hass.config.language, "country": hass.config.country},
            "seen": store.audio.get("languages_seen") or {}, "names": NAMES}


@aac_command({vol.Required("type"): "aac/languages"})
@websocket_api.require_admin
@callback
def ws_languages(hass: HomeAssistant, connection, msg) -> None:
    """The home's languages (global), what HA says, what was heard, every known language."""
    if entry := _entry(hass, connection, msg):
        connection.send_result(msg["id"], _languages(hass, entry.runtime_data.store))


@aac_command({vol.Required("type"): "aac/languages/set", vol.Required("languages"): vol.Any(None, [vol.In(list(NAMES))])})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_languages_set(hass: HomeAssistant, connection, msg) -> None:
    """Set the list (first = primary); null or [] = as HA describes the home."""
    if entry := _entry(hass, connection, msg):
        await entry.runtime_data.store.async_set_languages(msg["languages"] or None)
        connection.send_result(msg["id"], _languages(hass, entry.runtime_data.store))


@aac_command(
    {vol.Required("type"): "aac/audio/say", vol.Required("source_id"): str, vol.Required("text"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_audio_say(hass: HomeAssistant, connection, msg) -> None:
    """Speak a text on a connected source (to test the speaker and the voice)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    hub = entry.runtime_data.audio
    if (source := hub.sources.get(msg["source_id"])) is None:
        connection.send_error(msg["id"], "not_found", "Source not connected")
        return
    if int(entry.runtime_data.store.audio.get("volume", 40)) <= 0:
        connection.send_error(msg["id"], "muted", "AAC's voice volume is 0 %: turn it up in the settings below.")
        return
    connection.send_result(msg["id"], {"spoken": await hub.async_say(source, msg["text"])})


@aac_command({vol.Required("type"): "aac/audio/live"})
@websocket_api.require_admin
@callback
def ws_audio_live(hass: HomeAssistant, connection, msg) -> None:
    """The live view: loudness and speech of the last seconds per source (the panel asks a few times a second)."""
    if entry := _entry(hass, connection, msg):
        connection.send_result(msg["id"], {"sources": entry.runtime_data.audio.live()})


def _run_summary(run) -> dict[str, Any]:
    data = run.as_dict()
    data["steps"] = len(run.steps)
    return data


@aac_command(
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
    chats = _d(entry, msg).chats
    chat = chats.get(msg.get("chat_id") or "") or chats.create()
    chats.add_message(chat["id"], "user", msg["text"])
    run, result = await async_run_request(
        hass,
        entry,
        msg["text"],
        kind="chat",
        trigger="chat",
        title=chat["title"],
        chat_id=chat["id"],
        conversation_id=chat["conversation_id"],
        user_id=connection.user.id,
        domain=_d(entry, msg),
        history=chats.history(chat["id"]),
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


@aac_command({vol.Required("type"): "aac/chat/rewind", vol.Required("chat_id"): str, vol.Required("index"): vol.All(int, vol.Range(0))})
@websocket_api.require_admin
@callback
def ws_chat_rewind(hass: HomeAssistant, connection, msg) -> None:
    """Cut a chat off before a message (edit / rerun); the next message gets the rest as context."""
    if entry := _entry(hass, connection, msg):
        chats = _d(entry, msg).chats
        chats.rewind(msg["chat_id"], msg["index"])
        connection.send_result(msg["id"], chats.get(msg["chat_id"]))


@aac_command({vol.Required("type"): "aac/chat/edit", vol.Required("chat_id"): str, vol.Required("index"): vol.All(int, vol.Range(0)),
              vol.Required("text"): vol.All(str, vol.Length(min=1))})
@websocket_api.require_admin
@callback
def ws_chat_edit(hass: HomeAssistant, connection, msg) -> None:
    """Change a message's text (also an answer of AAC's); the model sees the changed text from then on."""
    if entry := _entry(hass, connection, msg):
        chats = _d(entry, msg).chats
        chats.edit(msg["chat_id"], msg["index"], msg["text"])
        connection.send_result(msg["id"], chats.get(msg["chat_id"]))


@aac_command({vol.Required("type"): "aac/chats"})
@websocket_api.require_admin
@callback
def ws_chats(hass: HomeAssistant, connection, msg) -> None:
    """All chats (without messages) + automatic runs for the WORKERS segment."""
    if entry := _entry(hass, connection, msg):
        connection.send_result(msg["id"], {"chats": _d(entry, msg).chats.list()})


@aac_command({vol.Required("type"): "aac/chat/get", vol.Required("chat_id"): str})
@websocket_api.require_admin
@callback
def ws_chat_get(hass: HomeAssistant, connection, msg) -> None:
    """One chat with its messages (marks it read)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    chats = _d(entry, msg).chats
    if (chat := chats.get(msg["chat_id"])) is None:
        connection.send_error(msg["id"], "not_found", "Chat not found")
        return
    chats.mark_read(msg["chat_id"])
    connection.send_result(msg["id"], chat)


@aac_command({vol.Required("type"): "aac/chat/new"})
@websocket_api.require_admin
@callback
def ws_chat_new(hass: HomeAssistant, connection, msg) -> None:
    """Start an empty chat."""
    if entry := _entry(hass, connection, msg):
        connection.send_result(msg["id"], _d(entry, msg).chats.create())


@aac_command({vol.Required("type"): "aac/chat/archive", vol.Required("chat_id"): str, vol.Optional("archived", default=True): bool})
@websocket_api.require_admin
@callback
def ws_chat_archive(hass: HomeAssistant, connection, msg) -> None:
    """Archive a chat (or take it out of the archive)."""
    if entry := _entry(hass, connection, msg):
        _d(entry, msg).chats.archive(msg["chat_id"], msg["archived"])
        connection.send_result(msg["id"])


@aac_command({vol.Required("type"): "aac/chat/delete", vol.Required("chat_id"): str})
@websocket_api.require_admin
@callback
def ws_chat_delete(hass: HomeAssistant, connection, msg) -> None:
    """Delete a chat."""
    if entry := _entry(hass, connection, msg):
        _d(entry, msg).chats.delete(msg["chat_id"])
        connection.send_result(msg["id"])


@aac_command(
    {vol.Required("type"): "aac/runs", vol.Optional("kind"): vol.In(["chat", "auto"])}
)
@websocket_api.require_admin
@callback
def ws_runs(hass: HomeAssistant, connection, msg) -> None:
    """Runs, newest first (steps counted, not included)."""
    if entry := _entry(hass, connection, msg):
        runs = [r for r in _d(entry, msg).runs.runs if msg.get("kind") in (None, r.kind)]
        connection.send_result(msg["id"], [_run_summary(r) for r in reversed(runs)])


@aac_command({vol.Required("type"): "aac/run/get", vol.Required("run_id"): str})
@websocket_api.require_admin
@callback
def ws_run_get(hass: HomeAssistant, connection, msg) -> None:
    """One run with all steps."""
    if not (entry := _entry(hass, connection, msg)):
        return
    if (run := _d(entry, msg).runs.get(msg["run_id"])) is None:
        connection.send_error(msg["id"], "not_found", "Run not found")
        return
    connection.send_result(msg["id"], run.as_dict())


@aac_command({vol.Required("type"): "aac/run/request", vol.Required("run_id"): str})
@websocket_api.require_admin
@callback
def ws_run_request(hass: HomeAssistant, connection, msg) -> None:
    """What the model got in a run's last call (recorded for the newest runs)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    if (req := _d(entry, msg).runs.request(msg["run_id"])) is None:
        connection.send_error(msg["id"], "not_found", "Not recorded (only the newest runs keep it)")
        return
    connection.send_result(msg["id"], req)


@aac_command({vol.Required("type"): "aac/subscribe"})
@websocket_api.require_admin
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg) -> None:
    """Push changes (runs, chats) to the panel."""

    @callback
    def forward(update: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], update))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_UPDATE, forward)
    connection.send_result(msg["id"])


@aac_command({vol.Required("type"): "aac/domain/create", vol.Required("name"): vol.All(str, vol.Length(min=1, max=40)),
              vol.Optional("copy_from"): vol.Any(str, None)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_domain_create(hass: HomeAssistant, connection, msg) -> None:
    """A new domain (empty, or with another domain's settings; never its memory or chats). AAC reloads."""
    if not (entry := _entry(hass, connection, msg)):
        return
    created = await entry.runtime_data.store.async_create_domain(msg["name"], msg.get("copy_from"))
    connection.send_result(msg["id"], {"domain": created.id})
    hass.config_entries.async_schedule_reload(entry.entry_id)  # its agent, tools and modules


@aac_command({vol.Required("type"): "aac/domain/rename", vol.Required("domain_id"): str,
              vol.Required("name"): vol.All(str, vol.Length(min=1, max=40))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_domain_rename(hass: HomeAssistant, connection, msg) -> None:
    """Rename a domain (its id, storage and entity id stay)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    if msg["domain_id"] not in entry.runtime_data.store.domains:
        connection.send_error(msg["id"], "not_found", "No such domain")
        return
    await entry.runtime_data.store.async_rename_domain(msg["domain_id"], msg["name"])
    connection.send_result(msg["id"], {"domain": msg["domain_id"]})
    hass.config_entries.async_schedule_reload(entry.entry_id)  # the agent's name


@aac_command({vol.Required("type"): "aac/domain/delete", vol.Required("domain_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_domain_delete(hass: HomeAssistant, connection, msg) -> None:
    """Delete a domain with its memory, chats and runs (not the first one). AAC reloads."""
    if not (entry := _entry(hass, connection, msg)):
        return
    dom = entry.runtime_data.domains.get(msg["domain_id"])
    if dom is None or dom.first:
        connection.send_error(msg["id"], "not_allowed", "The first domain cannot be deleted")
        return
    await entry.runtime_data.store.async_delete_domain(dom.id)
    if memory := entry.runtime_data.slots.memory():
        await memory.async_remove_domain(dom)
    await async_remove_domain_data(hass, dom)
    if entity_id := dom.entity_id(hass, entry.entry_id):
        er.async_get(hass).async_remove(entity_id)
    connection.send_result(msg["id"], {"deleted": dom.id})
    hass.config_entries.async_schedule_reload(entry.entry_id)


@aac_command({vol.Required("type"): "aac/domain/voice", vol.Required("domain_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_domain_voice(hass: HomeAssistant, connection, msg) -> None:
    """The domain the microphones talk to."""
    if entry := _entry(hass, connection, msg):
        await entry.runtime_data.store.async_set_voice_domain(msg["domain_id"])
        connection.send_result(msg["id"], _state(hass, *_ctx(hass, msg)))


@aac_command({vol.Required("type"): "aac/jobs"})
@websocket_api.require_admin
@callback
def ws_jobs(hass: HomeAssistant, connection, msg) -> None:
    """Waiting and recent automatic jobs, queue settings."""
    if entry := _entry(hass, connection, msg):
        connection.send_result(msg["id"], entry.runtime_data.jobs.overview())


@aac_command({vol.Required("type"): "aac/jobs/settings", vol.Optional("notify_no_model"): bool,
              vol.Optional("postpone_notice"): vol.All(vol.Coerce(int), vol.Range(0, 1440))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_jobs_settings(hass: HomeAssistant, connection, msg) -> None:
    """Queue settings."""
    if not (entry := _entry(hass, connection, msg)):
        return
    store = entry.runtime_data.store
    store.data["queue"].update({k: v for k, v in msg.items() if k in ("notify_no_model", "postpone_notice")})
    await store.async_save()
    connection.send_result(msg["id"], entry.runtime_data.jobs.overview())


def _module_view(hass: HomeAssistant, entry, module, domain_id: str) -> dict[str, Any]:
    """A module with its settings schema (choices resolved) and this domain's values."""
    schema = {}
    for key, field_def in module.manifest.get("settings", {}).items():
        field_def = dict(field_def)
        if field_def.get("type") in ("entity", "entities") and field_def.get("domain"):
            field_def["options"] = [[st.entity_id, st.attributes.get("friendly_name", st.entity_id)]
                                    for st in hass.states.async_all(field_def["domain"])]
        if source := field_def.get("options_from"):
            resolver = entry.runtime_data.capabilities.options.get(source)
            field_def["options"] = [list(o) for o in resolver(domain_id)] if resolver else []
        schema[key] = field_def
    return {**module.summary(entry, domain_id), "settings": schema,
            "values": {k: module_setting(entry, module.manifest, domain_id, k) for k in schema},
            "access": module_access(entry, module.id, domain_id), "manifest": module.manifest,
            "default_name": module.manifest.get("name", module.id),
            "default_instructions": module.manifest.get("instructions", ""),
            "has_tools": bool(module.api_ids)}


@aac_command({vol.Required("type"): "aac/nodes"})
@websocket_api.require_admin
@callback
def ws_nodes(hass: HomeAssistant, connection, msg) -> None:
    """The domain's nodes and wires on the stream bus (the node view; spec/streams.md)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    dom = _d(entry, msg)
    summary = entry.runtime_data.bus.summary(dom.id)
    names = {m.id: m.manifest.get("name", m.id) for m in entry.runtime_data.modules}
    for node in summary["nodes"]:
        node["module_name"] = names.get(node["module"], "Core") if node["module"] else "Core"
    summary["types"] = STREAM_TYPES
    summary["containers"] = entry.runtime_data.store.data["node_view"].get(dom.id, {}).get("containers", [])
    connection.send_result(msg["id"], summary)


_CONTAINER = vol.Schema({
    vol.Required("id"): vol.All(str, vol.Length(min=1, max=40)),
    vol.Required("name"): vol.All(str, vol.Length(max=60)),
    vol.Optional("nodes", default=[]): [str],
    vol.Required("x"): vol.Coerce(float), vol.Required("y"): vol.Coerce(float),
    vol.Required("w"): vol.All(vol.Coerce(float), vol.Range(min=60, max=20000)),
    vol.Required("h"): vol.All(vol.Coerce(float), vol.Range(min=60, max=20000)),
})


@aac_command({vol.Required("type"): "aac/nodes/containers", vol.Required("containers"): [_CONTAINER]})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_node_containers(hass: HomeAssistant, connection, msg) -> None:
    """The node view's named containers of the domain (all of them; a node belongs to one at most)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    seen: set[str] = set()
    containers = []
    for c in msg["containers"]:
        c = {**c, "nodes": [n for n in dict.fromkeys(c["nodes"]) if n not in seen],
             **{k: round(c[k]) for k in ("x", "y", "w", "h")}}
        seen.update(c["nodes"])
        containers.append(c)
    store = entry.runtime_data.store
    store.data["node_view"].setdefault(_d(entry, msg).id, {})["containers"] = containers
    await store.async_save()
    connection.send_result(msg["id"], {"containers": containers})


@aac_command({vol.Required("type"): "aac/modules"})
@websocket_api.require_admin
@callback
def ws_modules(hass: HomeAssistant, connection, msg) -> None:
    """The loaded modules with their settings for the selected domain."""
    if entry := _entry(hass, connection, msg):
        dom = _d(entry, msg)
        connection.send_result(msg["id"], {"modules": [_module_view(hass, entry, m, dom.id) for m in entry.runtime_data.modules]})


@aac_command({vol.Required("type"): "aac/module/settings", vol.Required("module"): str, vol.Required("values"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_module_settings(hass: HomeAssistant, connection, msg) -> None:
    """Save a module's settings for the selected domain."""
    if not (entry := _entry(hass, connection, msg)):
        return
    module = next((m for m in entry.runtime_data.modules if m.id == msg["module"]), None)
    if module is None:
        connection.send_error(msg["id"], "not_found", "No such module")
        return
    dom = _d(entry, msg)
    await async_set_settings(entry, module, dom.id, msg["values"])
    async_notify(hass, "modules", domain=dom.id)
    connection.send_result(msg["id"], _module_view(hass, entry, module, dom.id))


@aac_command({vol.Required("type"): "aac/module/access", vol.Required("module"): str, vol.Required("values"): {
    vol.Optional("enabled"): bool, vol.Optional("name"): vol.Any(str, None), vol.Optional("instructions"): vol.Any(str, None),
    vol.Optional("access"): vol.In(["full", "read", "none"]), vol.Optional("auto"): bool,
    vol.Optional("destructive_needs_owner"): bool, vol.Optional("hidden"): bool}})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_module_access(hass: HomeAssistant, connection, msg) -> None:
    """A module's permissions and properties in the selected domain (empty name/instructions = default)."""
    if not (entry := _entry(hass, connection, msg)):
        return
    module = next((m for m in entry.runtime_data.modules if m.id == msg["module"]), None)
    if module is None:
        connection.send_error(msg["id"], "not_found", "No such module")
        return
    dom = _d(entry, msg)
    await async_set_access(entry, module.id, dom.id, msg["values"])
    async_notify(hass, "modules", domain=dom.id)
    connection.send_result(msg["id"], _module_view(hass, entry, module, dom.id))


@aac_command({vol.Required("type"): "aac/entities"})
@websocket_api.require_admin
@callback
def ws_entities(hass: HomeAssistant, connection, msg) -> None:
    """Every entity with read/write (for all domains and modules)."""
    if entry := _entry(hass, connection, msg):
        store = entry.runtime_data.store
        connection.send_result(msg["id"], {"entities": all_entities(hass, store), "chosen": store.entity_access is not None})


@aac_command({vol.Required("type"): "aac/entities/set",
              vol.Required("access"): {str: {vol.Optional("read"): bool, vol.Optional("write"): bool}}})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_entities_set(hass: HomeAssistant, connection, msg) -> None:
    """Change read/write of some entities (write needs read)."""
    if entry := _entry(hass, connection, msg):
        store = entry.runtime_data.store
        await async_set_entity_access(hass, store, msg["access"])
        async_notify(hass, "entities")
        connection.send_result(msg["id"], {"entities": all_entities(hass, store), "chosen": True})


@aac_command({vol.Required("type"): "aac/domain/visible"})
@websocket_api.require_admin
@callback
def ws_domain_visible(hass: HomeAssistant, connection, msg) -> None:
    """Every entity, ticked where the selected domain can see it."""
    if entry := _entry(hass, connection, msg):
        settings = _d(entry, msg).settings
        connection.send_result(msg["id"], {"entities": all_entities(hass, entry.runtime_data.store, settings),
                                           "chosen": settings.visible is not None})


@aac_command({vol.Required("type"): "aac/domain/visible/set", vol.Required("visible"): [str]})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_domain_visible_set(hass: HomeAssistant, connection, msg) -> None:
    """The selected domain's tick list (what it can see at all)."""
    if entry := _entry(hass, connection, msg):
        dom = _d(entry, msg)
        await async_set_visible(hass, dom.settings, msg["visible"])
        async_notify(hass, "entities", domain=dom.id)
        connection.send_result(msg["id"], {"entities": all_entities(hass, entry.runtime_data.store, dom.settings), "chosen": True})


@aac_command({vol.Required("type"): "aac/requests"})
@websocket_api.require_admin
@callback
def ws_requests(hass: HomeAssistant, connection, msg) -> None:
    """Open and recent requests of the selected domain."""
    if entry := _entry(hass, connection, msg):
        dom = _d(entry, msg)
        mine = [r for r in entry.runtime_data.requests.all if r["domain"] == dom.id]
        connection.send_result(msg["id"], {"requests": list(reversed(mine))[:30]})


@aac_command({vol.Required("type"): "aac/request/decide", vol.Required("request_id"): str,
              vol.Required("approve"): vol.Any(bool, [str])})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_request_decide(hass: HomeAssistant, connection, msg) -> None:
    """Approve all (true), none (false) or some (item ids) of a request."""
    if not (entry := _entry(hass, connection, msg)):
        return
    try:
        request = await entry.runtime_data.requests.async_decide(msg["request_id"], msg["approve"])
    except LookupError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], request)


@aac_command({vol.Required("type"): "aac/satellite/app"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_satellite_app(hass: HomeAssistant, connection, msg) -> None:
    """Prepare access for AAC's Home Assistant app (a local-only user + token in <config>/aac/satellite.json)."""
    if entry := _entry(hass, connection, msg):
        connection.send_result(msg["id"], await async_prepare_satellite(hass, entry))


@aac_command({vol.Required("type"): "aac/skins"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_skins(hass: HomeAssistant, connection, msg) -> None:
    """The skins (built-in + own) and the chosen one."""
    if entry := _entry(hass, connection, msg):
        skins = await hass.async_add_executor_job(list_skins, hass)
        connection.send_result(msg["id"], {"skins": skins, "skin": entry.runtime_data.store.data["ui"]["skin"],
                                           "folder": str(user_dir(hass))})


@aac_command({vol.Required("type"): "aac/skin/set", vol.Required("skin"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_skin_set(hass: HomeAssistant, connection, msg) -> None:
    """Choose the panel's skin."""
    if not (entry := _entry(hass, connection, msg)):
        return
    skins = await hass.async_add_executor_job(list_skins, hass)
    if msg["skin"] not in {s["id"] for s in skins}:
        connection.send_error(msg["id"], "not_found", "No such skin")
        return
    store = entry.runtime_data.store
    store.data["ui"]["skin"] = msg["skin"]
    await store.async_save()
    connection.send_result(msg["id"], {"skins": skins, "skin": msg["skin"], "folder": str(user_dir(hass))})


@aac_command({vol.Required("type"): "aac/pools"})
@websocket_api.require_admin
@callback
def ws_pools(hass: HomeAssistant, connection, msg) -> None:
    """Pools: per role the ordered list with live state."""
    if entry := _entry(hass, connection, msg):
        connection.send_result(msg["id"], entry.runtime_data.pool.status())


@aac_command(
    {vol.Required("type"): "aac/pool/tags", vol.Required("resource"): str, vol.Required("tags"): [str]}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_pool_tags(hass: HomeAssistant, connection, msg) -> None:
    """The owner's own tags for a model (e.g. efficient)."""
    if entry := _entry(hass, connection, msg):
        await entry.runtime_data.pool.async_set_tags(msg["resource"], msg["tags"])
        connection.send_result(msg["id"], entry.runtime_data.pool.status())


@aac_command({vol.Required("type"): "aac/pool/tool_mode", vol.Required("resource"): str,
              vol.Required("mode"): vol.In(["menu", "all"])})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_pool_tool_mode(hass: HomeAssistant, connection, msg) -> None:
    """How a model gets its tools: menu (areas, opened on demand) or all (every tool up front)."""
    if entry := _entry(hass, connection, msg):
        await entry.runtime_data.pool.async_set_tool_mode(msg["resource"], msg["mode"])
        connection.send_result(msg["id"], entry.runtime_data.pool.status())


@aac_command({vol.Required("type"): "aac/pool/rule", vol.Required("resource"): str,
              vol.Required("rule"): vol.Any(None, {vol.Optional("applies_to", default="background"): vol.In(["background", "all"]),
                                                    vol.Required("when"): [dict], vol.Optional("note", default=""): str})})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_pool_rule(hass: HomeAssistant, connection, msg) -> None:
    """Set or clear a model's availability rule (None = always usable)."""
    if entry := _entry(hass, connection, msg):
        await entry.runtime_data.pool.async_set_rule(msg["resource"], msg["rule"])
        connection.send_result(msg["id"], entry.runtime_data.pool.status())


@aac_command({vol.Required("type"): "aac/pool/measure"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_pool_measure(hass: HomeAssistant, connection, msg) -> None:
    """Measure the speed of every reachable model of the master role."""
    if not (entry := _entry(hass, connection, msg)):
        return
    pool = entry.runtime_data.pool
    for resource in pool.candidates("master"):
        if pool.usable(resource):
            await pool.async_measure(resource)
    connection.send_result(msg["id"], pool.status())


@aac_command(
    {
        vol.Required("type"): "aac/pool/set",
        vol.Required("role"): vol.In(list(ROLES)),
        vol.Required("items"): [str],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_pool_set(hass: HomeAssistant, connection, msg) -> None:
    """Set a role's ordered list."""
    if not (entry := _entry(hass, connection, msg)):
        return
    await async_set_role(hass, entry, msg["role"], msg["items"])
    connection.send_result(msg["id"], entry.runtime_data.pool.status())
