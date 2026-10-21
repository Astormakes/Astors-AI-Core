"""Voices: who is talking (owner, 2026-10-19). Every spoken line gets a voice print from the voice
pool's "voices" role (e.g. the AAC senses service) and is matched against the voices AAC knows.
A new voice gets a provisional id ("voice #3"); the owner can say who it is, also "someone from a
video", so AAC learns to ignore it. Prints stay in HA's storage; unnamed voices are forgotten after
`keep_days` without being heard (design: no permanent print of a non-household person without OK).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import probatio
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ...streams import Node
from ...const import DOMAIN
from .. import ModuleContext

KINDS = ["unknown", "person", "media"]
NEW_VOICE_SECONDS = 2.5  # shorter lines never start a new voice (their prints are too noisy)


class Voices:
    """The known voices (shared by all domains: a voice is the same whoever listens)."""

    def __init__(self, store: Store) -> None:
        """Initialize."""
        self._store = store
        self.voices: list[dict[str, Any]] = []
        self.next = 1

    async def async_load(self) -> None:
        """Read from storage."""
        data = await self._store.async_load() or {}
        self.voices, self.next = data.get("voices", []), data.get("next", 1)

    def save(self, keep_days: float) -> None:
        """Forget unnamed voices not heard for keep_days, then save (delayed)."""
        cutoff = (dt_util.utcnow() - timedelta(days=keep_days)).isoformat()
        self.voices = [v for v in self.voices if v["named"] or v["last"] >= cutoff]
        self._store.async_delay_save(lambda: {"voices": self.voices, "next": self.next}, 10)

    def match(self, embedding: list[float], threshold: float, new_below: float | None = None,
              may_add: bool = True) -> tuple[dict[str, Any] | None, float, bool]:
        """The known voice closest to a print (cosine; prints are normalized), or a new one.
        ≥ threshold: that voice (it learns the line); new_below … threshold: probably that voice (no learning);
        below new_below: a new voice when may_add (a long enough line), else None."""
        new_below = threshold if new_below is None else new_below
        scored = sorted(((sum(a * b for a, b in zip(v["print"], embedding, strict=False)), v) for v in self.voices),
                        key=lambda x: x[0], reverse=True)
        score, best = scored[0] if scored else (-1.0, None)
        if len(scored) > 1 and scored[1][0] >= threshold:
            # this line fits two voices: they are one (the same person was split earlier, e.g. on noisy lines)
            best = self.merge(best, scored[1][1])
        now = dt_util.utcnow().isoformat()
        if best is not None and score >= threshold:
            if score >= threshold + 0.05:  # sure enough: let the print learn this line (running mean)
                n = min(best["n"], 50)
                mixed = [(a * n + b) / (n + 1) for a, b in zip(best["print"], embedding, strict=False)]
                norm = sum(x * x for x in mixed) ** 0.5 or 1
                best["print"], best["n"] = [round(x / norm, 5) for x in mixed], best["n"] + 1
            best["last"], best["heard"] = now, best.get("heard", 0) + 1
            return best, score, False
        if best is not None and score >= new_below:
            return best, score, False  # probably this voice; too unsure to learn from
        if not may_add:
            return None, score, False
        voice = {"id": f"v{self.next}", "name": f"voice #{self.next}", "kind": "unknown", "note": "",
                 "named": False, "print": embedding, "n": 1, "first": now, "last": now, "heard": 1}
        self.next += 1
        self.voices.append(voice)
        return voice, score, True

    def merge(self, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        """Join two voices; a named one keeps its name (two named ones are left alone)."""
        if a["named"] and b["named"]:
            return a
        keep, gone = (b, a) if b["named"] and not a["named"] else (a, b)
        n1, n2 = keep["n"], gone["n"]
        mixed = [(x * n1 + y * n2) / (n1 + n2) for x, y in zip(keep["print"], gone["print"], strict=False)]
        norm = sum(x * x for x in mixed) ** 0.5 or 1
        keep.update(print=[round(x / norm, 5) for x in mixed], n=n1 + n2, heard=keep.get("heard", 0) + gone.get("heard", 0),
                    first=min(keep["first"], gone["first"]), last=max(keep["last"], gone["last"]))
        keep.setdefault("merged", []).append(gone["name"])
        self.voices.remove(gone)
        return keep

    def get(self, ref: str) -> dict[str, Any] | None:
        """By id or name."""
        ref = ref.strip().lower()
        return next((v for v in self.voices if v["id"] == ref or v["name"].lower() == ref), None)


def public(voice: dict[str, Any]) -> dict[str, Any]:
    """Without the print."""
    return {k: v for k, v in voice.items() if k not in ("print", "n")}


class _VoiceTool(llm.Tool):
    integration = DOMAIN

    def __init__(self, voices: Voices, keep) -> None:
        self.voices, self.keep = voices, keep


class ListVoicesTool(_VoiceTool):
    name = "list_voices"
    description = "The voices AAC knows: id, name, kind (person / media = from a video or TV / unknown), note, when heard."
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)

    async def async_call(self, hass, tool_input, llm_context):
        return llm.ToolResult(data={"voices": [public(v) for v in self.voices.voices]})


class NameVoiceTool(_VoiceTool):
    name = "name_voice"
    description = ("Say who a voice is, only when the owner tells you: use the voice the line carries "
                   "(\"[spoken … by voice #8]\" — \"that's me\" means that voice), never guess from list_voices. A "
                   "name, the kind (person in or visiting the home, media = from a video, the TV, the radio) and a short note.")
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("voice"): str, probatio.Required("name"): str,
                                  probatio.Required("kind"): probatio.In(KINDS), probatio.Optional("note"): str})

    async def async_call(self, hass, tool_input, llm_context):
        args = tool_input.tool_args
        if (voice := self.voices.get(args["voice"])) is None:
            return llm.ToolResult(data={"error": "No such voice; see list_voices."}, error=True)
        voice.update(name=args["name"].strip(), kind=args["kind"], note=args.get("note", voice["note"]), named=True)
        self.voices.save(self.keep())
        return llm.ToolResult(data={"voice": public(voice)})


class ForgetVoiceTool(_VoiceTool):
    name = "forget_voice"
    description = "Delete a voice and its voice print (e.g. when the owner asks, or a voice was a mistake)."
    annotations = llm.ToolAnnotations(read_only=False, destructive=True, open_world=False)
    parameters = probatio.Schema({probatio.Required("voice"): str})

    async def async_call(self, hass, tool_input, llm_context):
        if (voice := self.voices.get(tool_input.tool_args["voice"])) is None:
            return llm.ToolResult(data={"error": "No such voice; see list_voices."}, error=True)
        self.voices.voices.remove(voice)
        self.voices.save(self.keep())
        return llm.ToolResult(data={"forgotten": voice["name"]})


class MergeVoicesTool(_VoiceTool):
    name = "merge_voices"
    description = "Two voices are the same person (only when the owner says so): join them into the first one."
    # counts as destructive: only with the owner in a chat, never from an anonymous voice
    annotations = llm.ToolAnnotations(read_only=False, destructive=True, open_world=False)
    parameters = probatio.Schema({probatio.Required("voice"): str, probatio.Required("same_as"): str})

    async def async_call(self, hass, tool_input, llm_context):
        a, b = self.voices.get(tool_input.tool_args["voice"]), self.voices.get(tool_input.tool_args["same_as"])
        if a is None or b is None or a is b:
            return llm.ToolResult(data={"error": "Give two different voices from list_voices."}, error=True)
        if a["named"] and b["named"]:
            return llm.ToolResult(data={"error": "Both have names; forget the wrong one instead."}, error=True)
        keep = self.voices.merge(a, b)
        self.voices.save(self.keep())
        return llm.ToolResult(data={"voice": public(keep)})


@dataclass(kw_only=True)
class VoicesAPI(llm.API):
    """The voice tools."""

    voices: Voices
    keep: Any

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        tools = [ListVoicesTool, NameVoiceTool, MergeVoicesTool, ForgetVoiceTool]
        return llm.APIInstance(api=self, api_prompt="", llm_context=llm_context,
                               tools=[t(self.voices, self.keep) for t in tools])


async def async_setup_module(ctx: ModuleContext) -> None:
    """Load the voices, register the stage and the tools."""
    first = next(d for d in ctx.domains.values() if d.first)
    voices = Voices(ctx.storage(first, "voices"))
    await voices.async_load()

    def setting(key: str) -> Any:
        return ctx.setting(ctx.entry.runtime_data.voice_domain.id, key)

    def keep() -> float:
        return float(setting("keep_days") or 7)

    async def run(source, pcm: bytes, line: dict[str, Any]) -> dict[str, Any] | None:
        if line.get("kind", "speech") != "speech" or line["seconds"] < float(setting("min_seconds") or 1.5):
            return None  # sounds (whisper's *crunch*) and very short lines get no voice
        found = await ctx.audio.async_senses("voices", "/audio/voiceprint", pcm[-20 * 32000:])
        if not found or not found.get("embedding"):
            return None
        sure = float(setting("match") or 80) / 100
        voice, score, new = voices.match(found["embedding"], sure, float(setting("new_below") or 65) / 100,
                                         may_add=line["seconds"] >= NEW_VOICE_SECONDS)
        voices.save(keep())
        if voice is None:
            return None
        return {"speaker": {"id": voice["id"], "name": voice["name"], "kind": voice["kind"], "note": voice["note"],
                            "score": round(score, 2), "new": new, "sure": new or score >= sure}}

    ctx.hass.data.setdefault(f"{DOMAIN}_voices", {})[ctx.entry.entry_id] = (voices, keep)
    ctx.add_ws_commands(ws_voices, ws_voice_change)
    ctx.add_node(Node(id="voices", title="Voices", inputs={"speech": "audio.segment"}, outputs={"speech": "audio.segment"}, adds=["speaker"], order=20, annotate=run))
    ctx.add_llm_api(lambda dom, api_id, name: VoicesAPI(hass=ctx.hass, id=api_id, name=name, voices=voices, keep=keep))


def _voices(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return hass.data.get(f"{DOMAIN}_voices", {}).get(entries[0].entry_id) if entries else None


@websocket_api.websocket_command({vol.Required("type"): "aac/voices", vol.Optional("domain"): vol.Any(str, None)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_voices(hass: HomeAssistant, connection, msg) -> None:
    """The known voices for the panel (without prints)."""
    if (found := _voices(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "The Voices module is not loaded")
        return
    connection.send_result(msg["id"], {"voices": [public(v) for v in found[0].voices]})


@websocket_api.websocket_command({
    vol.Required("type"): "aac/voices/change", vol.Optional("domain"): vol.Any(str, None),
    vol.Required("action"): vol.In(["name", "forget", "forget_all"]), vol.Optional("voice"): str,
    vol.Optional("name"): str, vol.Optional("kind"): vol.In(KINDS), vol.Optional("note"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_voice_change(hass: HomeAssistant, connection, msg) -> None:
    """The owner names or forgets voices (or all of them: a fresh start)."""
    if (found := _voices(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "The Voices module is not loaded")
        return
    voices, keep = found
    if msg["action"] == "forget_all":
        voices.voices = []
    elif (voice := voices.get(msg.get("voice", ""))) is None:
        connection.send_error(msg["id"], "not_found", "No such voice")
        return
    elif msg["action"] == "forget":
        voices.voices.remove(voice)
    else:
        voice.update(name=(msg.get("name") or voice["name"]).strip(), kind=msg.get("kind", voice["kind"]),
                     note=msg.get("note", voice["note"]), named=True)
    voices.save(keep())
    connection.send_result(msg["id"], {"voices": [public(v) for v in voices.voices]})
