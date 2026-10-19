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
from .store import CONF_MASTER_MODEL

if TYPE_CHECKING:
    from . import AACConfigEntry

API_ID = "aac_setup"
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

API_PROMPT = (
    "You can help the owner set up AAC itself (models, memory, heartbeat, audio). Start with "
    "setup_status. Go one step at a time, explain in a sentence why a step is useful, and ask "
    "before you change anything. The charter and what you may do on your own are the owner's "
    "to set in the panel; you cannot change them."
)


async def owner_present(hass: HomeAssistant, user_id: str | None) -> bool:
    """Setup tools only for an admin in a chat (never automatic runs or anonymous voice)."""
    run = current_run.get()
    if run and run.kind != "chat":
        return False
    if not user_id or not (user := await hass.auth.async_get_user(user_id)):
        return False
    return user.is_admin


class _SetupTool(llm.Tool):
    """Common checks."""

    integration = DOMAIN

    def __init__(self, entry: AACConfigEntry) -> None:
        """Initialize."""
        self._entry = entry

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
        hb = store.heartbeat
        data = self._entry.runtime_data
        audio = audio_overview(hass, data.audio, store.audio)
        return llm.ToolResult(
            data={
                "steps": [
                    {"id": s["id"], "title": s["title"], "why": s["hint"], "optional": s["optional"],
                     "state": "done" if s["done"] else "skipped" if s["skipped"] else "open"}
                    for s in setup_steps(hass, store)
                ],
                "master_model": store.master[CONF_MASTER_MODEL],
                "memory_model": store.memory["embed_model"],
                "heartbeat": {k: hb[k] for k in ("enabled", "interval", "quiet_start", "quiet_end")},
                "triggers": len(store.triggers),
                "audio": {
                    "connected_sources": [{"id": c["id"], "name": c["name"], "area": c["area"]} for c in audio["connected"]],
                    "sources_seen_before": [k["name"] for k in store.audio["known"]],
                    "speech_to_text": audio["engines"]["stt"],
                    "stt_options": audio["engines"]["stt_options"] + ["or any speech model from list_models (multilingual whisper detects the language)"],
                    "text_to_speech": audio["engines"]["tts"], "tts_options": audio["engines"]["tts_options"],
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
        for m in models:
            m["looks_like"] = model_kind(m["model"])
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
        "Give a model (id from list_models) a role: master (thinks and answers), memory (embeddings) "
        "or speech (speech to text for the audio pipeline, e.g. a whisper model)."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema(
        {
            probatio.Required("role"): probatio.In(["master", "memory", "speech"]),
            probatio.Required("model_id"): str,
        }
    )

    async def _async_run(self, hass, args):
        data = self._entry.runtime_data
        if resolve_model(hass, data.store, args["model_id"]) is None:
            return llm.ToolResult(data={"error": "Unknown model id; use list_models."}, error=True)
        if args["role"] == "master":
            await data.store.async_update_master({CONF_MASTER_MODEL: args["model_id"]})
            return llm.ToolResult(data={"master": args["model_id"], "note": "Takes effect from the next message."})
        if args["role"] == "speech":
            await data.store.async_update_audio({"stt": args["model_id"]})
            return llm.ToolResult(data={"speech_to_text": args["model_id"]})
        changed = data.store.memory["embed_model"] != args["model_id"]
        await data.store.async_update_memory({"embed_model": args["model_id"]})
        if changed and data.memory:
            self._entry.async_create_background_task(hass, data.memory.async_reembed(), "aac memory re-embed")
        return llm.ToolResult(data={"memory_model": args["model_id"]})


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
        data = self._entry.runtime_data
        await data.store.async_update_heartbeat(changes)
        data.triggers.async_start()
        hb = data.store.heartbeat
        return llm.ToolResult(data={k: hb[k] for k in ("enabled", "interval", "quiet_start", "quiet_end")})


class AudioTool(_SetupTool):
    """Audio pipeline settings."""

    name = "set_audio"
    title = "Set audio"
    description = (
        "Audio settings: speech to text (an HA engine from setup_status, or a speech model id from list_models), "
        "text to speech (an HA engine), "
        "the names that wake you by voice, and how many seconds a follow-up needs no name."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema(
        {
            probatio.Optional("speech_to_text"): str,
            probatio.Optional("text_to_speech"): str,
            probatio.Optional("names", description="e.g. ['computer', 'glados']"): [str],
            probatio.Optional("follow_up_seconds"): int,
        }
    )

    async def _async_run(self, hass, args):
        data = self._entry.runtime_data
        changes: dict = {}
        for arg, key, domain in (("speech_to_text", "stt", "stt"), ("text_to_speech", "tts", "tts")):
            if arg in args:
                is_model = domain == "stt" and resolve_model(hass, data.store, args[arg]) is not None
                if not is_model and args[arg] not in hass.states.async_entity_ids(domain):
                    return llm.ToolResult(data={"error": f"Unknown {domain} engine; see setup_status."}, error=True)
                changes[key] = args[arg]
        if "names" in args:
            names = [n.strip() for n in args["names"] if n.strip()]
            if not names:
                return llm.ToolResult(data={"error": "Keep at least one name."}, error=True)
            changes["names"] = names
        if "follow_up_seconds" in args:
            changes["follow_up"] = min(120, max(0, int(args["follow_up_seconds"])))
        await data.store.async_update_audio(changes)
        cfg = data.store.audio
        return llm.ToolResult(data={k: cfg[k] for k in ("stt", "tts", "names", "follow_up")})


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
        step = next((s for s in setup_steps(hass, store) if s["id"] == args["step"]), None)
        if step is None or not step["optional"]:
            return llm.ToolResult(data={"error": "Only optional steps can be skipped."}, error=True)
        await store.async_set_skipped(step["id"], True)
        return llm.ToolResult(data={"skipped": step["id"]})


@dataclass(kw_only=True)
class AACSetupAPI(llm.API):
    """The setup tools as an LLM API."""

    entry: AACConfigEntry

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Tools + prompt."""
        tools = [SetupStatusTool, ListModelsTool, AddModelTool, UseModelTool, HeartbeatTool, AudioTool, SpeakTestTool, SkipStepTool]
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=[tool(self.entry) for tool in tools],
        )
