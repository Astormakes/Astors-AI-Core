"""AAC helps set itself up: setup tools for the master, in the owner's chats only.

Framework: once one model is wired in, AAC should aid in setting up everything
else together with the owner. The panel stays the one place where settings
live; these tools change the same settings, but only while the owner is in
the conversation (an admin user in a chat run, never an automatic run).
Policy stays owner-only: modules marked owner_only (the charter) cannot be changed here.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

import probatio

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .languages import NAMES, code_of, home_languages
from .audio import audio_overview
from .const import CONF_BASE_URL, CONF_MODEL, DOMAIN
from .models import async_list_models, async_overview, model_kind, resolve_model
from .provider import ProviderError, ProviderNotFoundError
from .runs import current_run
from .pools import async_set_role, is_entity
from .capabilities import parse_weekdays

if TYPE_CHECKING:
    from .domains import Domain
    from . import AACConfigEntry

API_ID = "aac_setup"
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

API_PROMPT = (
    "You can help the owner set up AAC itself (models, memory, audio, modules such as the heartbeat). Start with "
    "setup_status. Go one step at a time, explain in a sentence why a step is useful, and ask "
    "before you change anything. The charter and what you may do on your own are the owner's "
    "to set in the panel; you cannot change them. Settings change while you talk (the owner may fix things "
    "in the panel meanwhile): when asked to check again, call setup_status again instead of repeating earlier answers. "
    "Ids (m_…, rule ids) are only for the tools: to the owner, call models and machines by their names."
)


async def owner_present(hass: HomeAssistant, user_id: str | None) -> bool:
    """Setup tools only for an admin in a chat (never automatic runs or anonymous voice)."""
    run = current_run.get()
    if run and run.kind != "chat":
        return False
    if not user_id or not (user := await hass.auth.async_get_user(user_id)):
        return False
    return user.is_admin


_ROLE_NAMES = {"master": "master", "memory": "memory", "speech": "stt", "voice": "tts"}


class _SetupTool(llm.Tool):
    """Common checks."""

    integration = DOMAIN

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize (models and pools are shared; the heartbeat belongs to the domain)."""
        self._entry = entry
        self._domain = domain

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Refuse unless the owner is present."""
        user_id = llm_context.context.user_id if llm_context.context else None
        if not await owner_present(hass, user_id):
            return llm.ToolResult(data={"error": "Setup changes need the owner in a chat."}, error=True)
        return await self._async_run(hass, tool_input.tool_args)

    async def _async_run(self, hass: HomeAssistant, args: dict):
        raise NotImplementedError


class SetupStatusTool(_SetupTool):
    """What is set up and what is open."""

    name = "setup_status"
    title = "Setup status"
    description = "See what of AAC is set up: models and pools, modules, audio, languages."
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)

    async def _async_run(self, hass, args):
        store = self._entry.runtime_data.store
        data = self._entry.runtime_data
        audio = audio_overview(hass, data.audio, store.audio)
        return llm.ToolResult(
            data={
                "domain": self._domain.name,
                "pools": {
                    name: {"list": [f"{i['id']} ({i['state']})" for i in data.pool.status()["roles"][role]["items"]],
                           "in_use_now": data.pool.status()["roles"][role]["active"]}
                    for name, role in _ROLE_NAMES.items()
                },
                "requests_now": data.pool.status()["by_tier"],
                "availability_rules": {r: data.pool.rule(r) for r in data.pool.candidates("master") if data.pool.rule(r)},
                "presence_entities": [
                    {"entity_id": st.entity_id, "name": st.attributes.get("friendly_name", st.entity_id),
                     "kind": "zone" if st.domain == "zone" else "person" if st.domain == "person"
                     else f"tracker ({st.attributes.get('source_type', 'unknown source')})",
                     "state": st.state}
                    for st in hass.states.async_all(("person", "device_tracker", "zone"))
                ],
                "languages": {"list": home_languages(hass, store), "set_by_owner": store.data["languages"] is not None,
                              "heard": store.audio.get("languages_seen") or {}},
                "modules": _modules_overview(self._entry, self._domain.id),
                "audio": {
                    "connected_sources": [{"id": c["id"], "name": c["name"], "area": c["area"]} for c in audio["connected"]],
                    "sources_seen_before": [k["name"] for k in store.audio["known"]],
                    "speech_to_text_now": audio["engines"]["stt"],
                    "ha_stt_engines": audio["engines"]["stt_options"],
                    "text_to_speech_now": audio["engines"]["tts"], "ha_tts_engines": audio["engines"]["tts_options"],
                    "volume": store.audio.get("volume", 40),
                    "hearing": "the audio cards in the node view (Microphone → Pause cut → Speech to text)",
                    "how_to_add_a_source": "Run the AAC satellite program (satellite/ in the AAC repository) on the "
                    "machine with the microphone; see its README.",
                },
            }
        )


class ListModelsTool(_SetupTool):
    """The models AAC can use, with live state."""

    name = "list_models"
    title = "List models"
    description = "List the model servers and models AAC knows, whether they are online, and whether they look like chat, embedding or speech-to-text models."
    annotations = llm.ToolAnnotations(read_only=True, open_world=True)

    async def _async_run(self, hass, args):
        overview = await async_overview(hass, self._entry.runtime_data.store)
        models = [
            {"id": m["id"], "name": m["name"], "model": m["model"], "online": m["online"], "kind": "manual"}
            for m in overview["manual"]
        ]
        for server in overview["auto"]:
            models.extend(
                {"id": m["id"], "name": f"{server['label']}: {m['model']}", "model": m["model"],
                 "online": server["online"], "kind": "found"}
                for m in server["models"]
            )
        run = current_run.get()
        on = {a["id"]: mach["name"] for mach in overview["machines"] for a in mach["models"]}
        for m in models:
            m["looks_like"] = model_kind(m["model"])
            m["machine"] = on.get(m["id"])
            if run and run.model == m["id"]:
                m["this_is_you"] = True  # the model answering right now (so it is online)
        return llm.ToolResult(data={"models": models})


class AddModelTool(_SetupTool):
    """Connect a model server."""

    name = "add_model"
    title = "Add model"
    description = (
        "Connect an OpenAI-compatible model server (llama.cpp, Ollama, vLLM, ...). Without 'model' "
        "it only tests the server and lists its models."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=True)
    parameters = probatio.Schema(
        {
            probatio.Required("base_url", description="e.g. http://192.168.1.20:8080/v1"): str,
            probatio.Optional("model", description="Model name on that server."): str,
            probatio.Optional("name", description="Short name for the panel."): str,
            probatio.Optional("api_key"): str,
        }
    )

    async def _async_run(self, hass, args):
        base_url = args["base_url"].strip().rstrip("/")
        try:
            available = await async_list_models(hass, base_url, args.get("api_key") or None)
        except ProviderNotFoundError:
            available = None  # reachable, but it cannot list its models (e.g. whisper.cpp)
        except (ProviderError, TimeoutError) as err:
            return llm.ToolResult(data={"error": f"Cannot reach the server: {err or 'timeout'}"}, error=True)
        if not args.get("model"):
            return llm.ToolResult(data={"reachable": True, "models": available if available is not None
                                        else "the server cannot list them; give the model name"})
        if available is not None and args["model"] not in available:
            return llm.ToolResult(data={"error": "The server does not offer that model.", "models": available}, error=True)
        cfg = await self._entry.runtime_data.store.async_save_model(
            {"name": args.get("name") or args["model"], CONF_BASE_URL: base_url,
             CONF_MODEL: args["model"], CONF_API_KEY: args.get("api_key") or None}
        )
        return llm.ToolResult(data={"added": cfg["id"], "name": cfg["name"]})


class UseModelTool(_SetupTool):
    """Give a model a role."""

    name = "use_model"
    title = "Use model"
    description = (
        "Put a model (id from list_models) or an HA engine (stt.* / tts.*) into a role's pool. Roles: "
        "master (thinks and answers), memory (embeddings, one model), speech (speech to text), voice "
        "(text to speech). position 'first' = preferred, 'last' = fallback when the others are "
        "unreachable (e.g. a PC that is not always on first, the server's model last). remove=true takes it out."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema(
        {
            probatio.Required("role"): probatio.In(list(_ROLE_NAMES)),
            probatio.Required("model_id"): str,
            probatio.Optional("position"): probatio.In(["first", "last"]),
            probatio.Optional("remove"): bool,
        }
    )

    async def _async_run(self, hass, args):
        data = self._entry.runtime_data
        role, resource = _ROLE_NAMES[args["role"]], args["model_id"]
        items = [r for r in data.store.pools.get(role, []) if r != resource]
        if not args.get("remove"):
            if is_entity(resource):
                if not resource.startswith(f"{'stt' if role == 'stt' else 'tts'}.") or hass.states.get(resource) is None:
                    return llm.ToolResult(data={"error": "Unknown engine for this role; see setup_status."}, error=True)
            elif role == "tts" or resolve_model(hass, data.store, resource) is None:
                return llm.ToolResult(data={"error": "Unknown model id (use list_models); voice takes HA engines."}, error=True)
            items = [*items, resource] if args.get("position") == "last" else [resource, *items]
        await async_set_role(hass, self._entry, role, items)
        status = data.pool.status()["roles"][role]
        return llm.ToolResult(data={"role": args["role"], "pool": [i["id"] for i in status["items"]],
                                    "in_use_now": status["active"]})


class TagModelTool(_SetupTool):
    """The owner's own tags for a model."""

    name = "tag_model"
    title = "Tag model"
    description = (
        "Give a master model the owner's own tags, e.g. 'efficient' for energy-saving hardware. Events and "
        "agents can then request a model by tag. fast/smart/small are worked out automatically."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("model_id"): str, probatio.Required("tags"): [str]})

    async def _async_run(self, hass, args):
        pool = self._entry.runtime_data.pool
        if args["model_id"] not in pool.candidates("master"):
            return llm.ToolResult(data={"error": "Tags are for models in the master pool."}, error=True)
        await pool.async_set_tags(args["model_id"], args["tags"])
        return llm.ToolResult(data={"traits": pool.traits(args["model_id"]), "by_tier": pool.status()["by_tier"]})


_CONDITION = probatio.Any(
    probatio.Schema({probatio.Required("type"): "state", probatio.Required("entity_id"): str,
                     probatio.Required("state"): probatio.Any(str, [str]), probatio.Optional("not"): bool}),
    probatio.Schema({probatio.Required("type"): "time", probatio.Optional("after"): str, probatio.Optional("before"): str,
                     probatio.Optional("weekdays"): [probatio.Any(str, int)]}),
)


class AvailabilityTool(_SetupTool):
    """The owner's rule for when a model may be used."""

    name = "set_availability"
    title = "Set availability"
    description = (
        "Write down the OWNER's rule for when a model may be used, e.g. 'use my PC for heavy stuff only while "
        "I am at work'. Only when the owner said so. Conditions (all must hold): a state condition on an "
        "entity that tracks the OWNER (a person entity, their phone, or what they do, e.g. the game they play; "
        "never a computer or another device), where 'away/at work' = not_home or a work zone; state may be a "
        "list, and not=true means 'not one of these'. Check the entity's current state first: an empty sensor "
        "is often 'unknown', so name 'unknown' when you mean empty. And/or a time window (after/before HH:MM, weekdays). "
        "setup_status lists presence entities with name, kind and state. If none tracks the owner reliably "
        "(state unknown or unavailable), do not guess: propose a time window and ask the owner first. "
        "applies_to 'background' (default) limits only automatic jobs, 'all' also the owner's chats. "
        "clear=true removes the rule. Afterwards say plainly what the rule depends on."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema(
        {
            probatio.Required("model_id"): str,
            probatio.Optional("conditions"): [_CONDITION],
            probatio.Optional("applies_to"): probatio.In(["background", "all"]),
            probatio.Optional("note", description="The owner's words, e.g. 'only while I am at work'."): str,
            probatio.Optional("clear"): bool,
        }
    )

    async def _async_run(self, hass, args):
        pool = self._entry.runtime_data.pool
        resource = args["model_id"]
        if resolve_model(hass, self._entry.runtime_data.store, resource) is None:
            return llm.ToolResult(data={"error": "Unknown model id; use list_models."}, error=True)
        if args.get("clear"):
            await pool.async_set_rule(resource, None)
            return llm.ToolResult(data={"cleared": resource})
        conditions = []
        for cond in args.get("conditions") or []:
            if cond["type"] == "state":
                if hass.states.get(cond["entity_id"]) is None:
                    return llm.ToolResult(data={"error": f"{cond['entity_id']} does not exist."}, error=True)
                conditions.append(dict(cond))
            else:
                for key in ("after", "before"):
                    if key in cond and not _TIME_RE.match(cond[key]):
                        return llm.ToolResult(data={"error": f"{key} must be HH:MM"}, error=True)
                conditions.append({**cond, "weekdays": parse_weekdays(cond.get("weekdays"))})
        if not conditions:
            return llm.ToolResult(data={"error": "Give at least one condition (or clear=true)."}, error=True)
        rule = {"applies_to": args.get("applies_to", "background"), "when": conditions, "note": args.get("note", "")}
        await pool.async_set_rule(resource, rule)
        now = {"chats": pool.allowed(resource, False), "background_jobs": pool.allowed(resource, True)}
        states = {c["entity_id"]: (st.state if (st := hass.states.get(c["entity_id"])) else None)
                  for c in conditions if c["type"] == "state"}
        data = {"rule": rule, "allowed_now": now, "entity_states_now": states}
        if not now["background_jobs"]:
            data["check"] = ("With this rule the model may NOT be used right now"
                             f"{' (states now: ' + ', '.join(f'{e} = {v!r}' for e, v in states.items()) + ')' if states else ''}. "
                             "If that is not what the owner means, fix the rule now.")
        return llm.ToolResult(data=data)


class MeasureModelsTool(_SetupTool):
    """Measure speeds."""

    name = "measure_models"
    title = "Measure models"
    description = "Measure the speed (tokens per second) of every reachable master model, so 'fast' requests pick the right one."
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=True)

    async def _async_run(self, hass, args):
        pool = self._entry.runtime_data.pool
        speeds = {r: await pool.async_measure(r) for r in pool.candidates("master") if pool.usable(r)}
        return llm.ToolResult(data={"tokens_per_second": speeds, "by_tier": pool.status()["by_tier"]})


def _modules_overview(entry: AACConfigEntry, domain_id: str) -> list[dict]:
    """Every module: on in this domain?, its settings (values and what they take)."""
    from .modules import access, setting  # noqa: PLC0415

    return [{"module": m.id, "name": m.manifest.get("name", m.id), "on": access(entry, m.id, domain_id)["enabled"],
             "settings": {k: {"value": setting(entry, m.manifest, domain_id, k), "type": f.get("type"), "title": f.get("title", k)}
                          for k, f in m.manifest.get("settings", {}).items()}}
            for m in entry.runtime_data.modules]


class ModuleSettingTool(_SetupTool):
    """A module on/off and its settings, for any module (the manifest says what a setting takes)."""

    name = "module_setting"
    title = "Set up a module"
    description = (
        "Switch a module on or off in this domain and change its settings (see setup_status → modules for the "
        "modules, their settings and current values). E.g. the heartbeat: module 'heartbeat', on true, "
        "settings {\"interval\": 15, \"quiet_start\": \"23:00\"}."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema(
        {
            probatio.Required("module"): str,
            probatio.Optional("on"): bool,
            probatio.Optional("settings", description="setting key → new value"): dict,
        }
    )

    async def _async_run(self, hass, args):
        from .modules import async_set_access, async_set_settings  # noqa: PLC0415

        entry, domain_id = self._entry, self._domain.id
        module = next((m for m in entry.runtime_data.modules if m.id == args["module"]), None)
        if module is None:
            return llm.ToolResult(data={"error": f"No module {args['module']!r}",
                                        "modules": [m.id for m in entry.runtime_data.modules]}, error=True)
        if module.manifest.get("owner_only"):  # the charter: the owner's values, never changed by an agent
            return llm.ToolResult(data={"error": f"Only the owner changes {module.id}, in the panel (MODULES)."}, error=True)
        schema, values = module.manifest.get("settings", {}), {}
        for key, value in (args.get("settings") or {}).items():
            field = schema.get(key)
            if field is None:
                return llm.ToolResult(data={"error": f"{module.id} has no setting {key!r}", "settings": list(schema)}, error=True)
            kind = field.get("type")
            try:
                if kind == "number":
                    value = float(value) if "." in str(value) else int(value)
                elif kind == "bool":
                    value = value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "on", "1")
                elif kind == "time" and not _TIME_RE.match(str(value)):
                    raise ValueError("HH:MM")
                elif kind in ("select", "entity") and (source := field.get("options_from") or field.get("options")):
                    choices = [o[0] for o in (entry.runtime_data.capabilities.options[source](domain_id)
                                              if isinstance(source, str) else source)]
                    if value not in choices:
                        raise ValueError(f"one of {choices}")
            except (ValueError, TypeError, KeyError) as err:
                return llm.ToolResult(data={"error": f"{key}: {err}"}, error=True)
            values[key] = value
        if "on" in args:
            await async_set_access(entry, module.id, domain_id, {"enabled": bool(args["on"])})
        if values:
            await async_set_settings(entry, module, domain_id, values)
        return llm.ToolResult(data=next(m for m in _modules_overview(entry, domain_id) if m["module"] == module.id))


class AudioTool(_SetupTool):
    """Audio pipeline settings."""

    name = "set_audio"
    title = "Set audio"
    description = (
        "Audio settings: the volume of your voice. Engines are pools: use_model with role speech/voice. What you "
        "hear is up to the audio cards the owner wires in the node view."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema(
        {
            probatio.Optional("volume", description="0-100 % of AAC's voice; 0 = never speak aloud"): int,
        }
    )

    async def _async_run(self, hass, args):
        data = self._entry.runtime_data
        changes: dict = {}
        if "volume" in args:
            changes["volume"] = min(100, max(0, int(args["volume"])))
        await data.store.async_update_audio(changes)
        cfg = data.store.audio
        return llm.ToolResult(data={"volume": cfg.get("volume")})


class LanguagesTool(_SetupTool):
    """The home's languages."""

    name = "set_languages"
    title = "Set languages"
    description = (
        "The languages spoken in this home, the main one first (ISO codes like 'en', 'de'). Speech recognition, "
        "voices and modules use only these. Ask the owner; don't guess. setup_status shows the list and the "
        "languages heard so far. reset=true goes back to what HA says (its language and country)."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Optional("languages"): [str], probatio.Optional("reset"): bool})

    async def _async_run(self, hass, args):
        store = self._entry.runtime_data.store
        if args.get("reset"):
            await store.async_set_languages(None)
        else:
            codes = [c for c in (code_of(x) for x in args.get("languages") or []) if c in NAMES]
            if not codes:
                return llm.ToolResult(data={"error": "Give at least one known language code, e.g. ['en', 'de']."}, error=True)
            await store.async_set_languages(codes)
        return llm.ToolResult(data={"languages": [NAMES[c] for c in home_languages(hass, store)]})


class SpeakTestTool(_SetupTool):
    """Say something on a source."""

    name = "speak_test"
    title = "Speak test"
    description = "Say a short text on a connected audio source, to check its speaker and the voice."
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("source_id"): str, probatio.Required("text"): str})

    async def _async_run(self, hass, args):
        hub = self._entry.runtime_data.audio
        if (source := hub.sources.get(args["source_id"])) is None:
            return llm.ToolResult(data={"error": "That source is not connected."}, error=True)
        return llm.ToolResult(data={"spoken": await hub.async_say(source, args["text"])})


@dataclass(kw_only=True)
class AACSetupAPI(llm.API):
    """The setup tools as an LLM API."""

    entry: AACConfigEntry
    domain: Domain

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Tools + prompt."""
        tools = [SetupStatusTool, ListModelsTool, AddModelTool, UseModelTool, TagModelTool, AvailabilityTool, MeasureModelsTool, ModuleSettingTool, AudioTool, LanguagesTool, SpeakTestTool]
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=[tool(self.entry, self.domain) for tool in tools],
        )
