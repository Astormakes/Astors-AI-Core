"""AAC helps set itself up: setup tools for the master, in the owner's chats only.

Framework: once one model is wired in, AAC should aid in setting up everything
else together with the owner. The panel stays the one place where settings
live; these tools change the same settings, but only while the owner is in
the conversation (an admin user in a chat run, never an automatic run).
Policy stays owner-only: the charter and autonomy are not offered here.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

import probatio

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .audio import audio_overview
from .const import CONF_BASE_URL, CONF_MODEL, DOMAIN
from .models import async_list_models, async_overview, model_kind, resolve_model
from .provider import ProviderError, ProviderNotFoundError
from .runs import current_run
from .status import setup_steps
from .pools import async_set_role, is_entity
from .schedule_api import parse_weekdays

if TYPE_CHECKING:
    from .domains import Domain
    from . import AACConfigEntry

API_ID = "aac_setup"
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

API_PROMPT = (
    "You can help the owner set up AAC itself (models, memory, heartbeat, audio). Start with "
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
    description = "See what of AAC is set up and which steps are still open."
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)

    async def _async_run(self, hass, args):
        store = self._entry.runtime_data.store
        hb = self._domain.settings.heartbeat
        data = self._entry.runtime_data
        audio = audio_overview(hass, data.audio, store.audio)
        return llm.ToolResult(
            data={
                "steps": [
                    {"id": s["id"], "title": s["title"], "why": s["hint"], "optional": s["optional"],
                     "state": "done" if s["done"] else "skipped" if s["skipped"] else "open"}
                    for s in setup_steps(hass, store, self._domain.settings, self._entry)
                ],
                "domain": self._domain.name,
                "pools": {
                    name: {"list": [f"{i['id']} ({i['state']})" for i in data.broker.status()["roles"][role]["items"]],
                           "in_use_now": data.broker.status()["roles"][role]["active"]}
                    for name, role in _ROLE_NAMES.items()
                },
                "requests_now": data.broker.status()["by_tier"],
                "availability_rules": {r: data.broker.rule(r) for r in data.broker.candidates("master") if data.broker.rule(r)},
                "presence_entities": [
                    {"entity_id": st.entity_id, "name": st.attributes.get("friendly_name", st.entity_id),
                     "kind": "zone" if st.domain == "zone" else "person" if st.domain == "person"
                     else f"tracker ({st.attributes.get('source_type', 'unknown source')})",
                     "state": st.state}
                    for st in hass.states.async_all(("person", "device_tracker", "zone"))
                ],
                "heartbeat": {k: hb[k] for k in ("enabled", "interval", "quiet_start", "quiet_end")},
                "triggers": len(store.triggers),
                "audio": {
                    "connected_sources": [{"id": c["id"], "name": c["name"], "area": c["area"]} for c in audio["connected"]],
                    "sources_seen_before": [k["name"] for k in store.audio["known"]],
                    "speech_to_text_now": audio["engines"]["stt"],
                    "ha_stt_engines": audio["engines"]["stt_options"],
                    "text_to_speech_now": audio["engines"]["tts"], "ha_tts_engines": audio["engines"]["tts_options"],
                    "language": store.audio["language"] or "detect per sentence",
                    "names_that_wake_you": store.audio["names"],
                    "follow_up_seconds": store.audio["follow_up"],
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
        for m in models:
            m["looks_like"] = model_kind(m["model"])
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
        status = data.broker.status()["roles"][role]
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
        broker = self._entry.runtime_data.broker
        if args["model_id"] not in broker.candidates("master"):
            return llm.ToolResult(data={"error": "Tags are for models in the master pool."}, error=True)
        await broker.async_set_tags(args["model_id"], args["tags"])
        return llm.ToolResult(data={"traits": broker.traits(args["model_id"]), "by_tier": broker.status()["by_tier"]})


_CONDITION = probatio.Any(
    probatio.Schema({probatio.Required("type"): "state", probatio.Required("entity_id"): str,
                     probatio.Required("state"): probatio.Any(str, [str])}),
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
        "entity that tracks the OWNER (a person entity, or their phone; never a computer or another device), "
        "where 'away/at work' = not_home or a work zone; and/or a time window (after/before HH:MM, weekdays). "
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
        broker = self._entry.runtime_data.broker
        resource = args["model_id"]
        if resolve_model(hass, self._entry.runtime_data.store, resource) is None:
            return llm.ToolResult(data={"error": "Unknown model id; use list_models."}, error=True)
        if args.get("clear"):
            await broker.async_set_rule(resource, None)
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
        await broker.async_set_rule(resource, rule)
        return llm.ToolResult(data={"rule": rule, "allowed_for_background_jobs_now": broker.usable(resource, True)})


class MeasureModelsTool(_SetupTool):
    """Measure speeds."""

    name = "measure_models"
    title = "Measure models"
    description = "Measure the speed (tokens per second) of every reachable master model, so 'fast' requests pick the right one."
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=True)

    async def _async_run(self, hass, args):
        broker = self._entry.runtime_data.broker
        speeds = {r: await broker.async_measure(r) for r in broker.candidates("master") if broker.usable(r)}
        return llm.ToolResult(data={"tokens_per_second": speeds, "by_tier": broker.status()["by_tier"]})


class HeartbeatTool(_SetupTool):
    """Heartbeat settings."""

    name = "set_heartbeat"
    title = "Set heartbeat"
    description = "Turn the heartbeat on/off (AAC looks at the home every few minutes and only speaks when it matters), set its interval and quiet hours."
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema(
        {
            probatio.Optional("enabled"): bool,
            probatio.Optional("interval_minutes"): int,
            probatio.Optional("quiet_start", description="HH:MM"): str,
            probatio.Optional("quiet_end", description="HH:MM"): str,
        }
    )

    async def _async_run(self, hass, args):
        changes: dict = {}
        if "enabled" in args:
            changes["enabled"] = bool(args["enabled"])
        if "interval_minutes" in args:
            changes["interval"] = min(1440, max(1, int(args["interval_minutes"])))
        for key in ("quiet_start", "quiet_end"):
            if key in args:
                if not _TIME_RE.match(args[key]):
                    return llm.ToolResult(data={"error": f"{key} must be HH:MM"}, error=True)
                changes[key] = args[key]
        dom = self._domain
        await dom.settings.async_update_heartbeat(changes)
        dom.triggers.async_start()
        hb = dom.settings.heartbeat
        return llm.ToolResult(data={k: hb[k] for k in ("enabled", "interval", "quiet_start", "quiet_end")})


class AudioTool(_SetupTool):
    """Audio pipeline settings."""

    name = "set_audio"
    title = "Set audio"
    description = (
        "Audio settings: the names that wake you by voice, how many seconds a follow-up needs no name, "
        "and the language (empty = detect per sentence). Engines are pools: use_model with role speech/voice."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema(
        {
            probatio.Optional("language", description="e.g. 'de'; empty = detect"): str,
            probatio.Optional("names", description="e.g. ['computer', 'glados']"): [str],
            probatio.Optional("follow_up_seconds"): int,
        }
    )

    async def _async_run(self, hass, args):
        data = self._entry.runtime_data
        changes: dict = {}
        if "language" in args:
            changes["language"] = args["language"].strip() or None
        if "names" in args:
            names = [n.strip() for n in args["names"] if n.strip()]
            if not names:
                return llm.ToolResult(data={"error": "Keep at least one name."}, error=True)
            changes["names"] = names
        if "follow_up_seconds" in args:
            changes["follow_up"] = min(120, max(0, int(args["follow_up_seconds"])))
        await data.store.async_update_audio(changes)
        cfg = data.store.audio
        return llm.ToolResult(data={k: cfg[k] for k in ("language", "names", "follow_up")})


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


class SkipStepTool(_SetupTool):
    """Skip an optional setup step."""

    name = "skip_setup_step"
    title = "Skip setup step"
    description = "Skip an optional setup step the owner does not want now (it can be done later)."
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("step", description="Step id from setup_status."): str})

    async def _async_run(self, hass, args):
        store = self._entry.runtime_data.store
        step = next((s for s in setup_steps(hass, store, self._domain.settings, self._entry) if s["id"] == args["step"]), None)
        if step is None or not step["optional"]:
            return llm.ToolResult(data={"error": "Only optional steps can be skipped."}, error=True)
        await store.async_set_skipped(step["id"], True)
        return llm.ToolResult(data={"skipped": step["id"]})


@dataclass(kw_only=True)
class AACSetupAPI(llm.API):
    """The setup tools as an LLM API."""

    entry: AACConfigEntry
    domain: Domain

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Tools + prompt."""
        tools = [SetupStatusTool, ListModelsTool, AddModelTool, UseModelTool, TagModelTool, AvailabilityTool, MeasureModelsTool, HeartbeatTool, AudioTool, SpeakTestTool, SkipStepTool]
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=[tool(self.entry, self.domain) for tool in tools],
        )
