"""AAC memory as an LLM API: remember / recall / forget.

The master always gets these tools (when memory is on). Because it is a
normal HA LLM API, HA's own agents could select it too; AAC never forces
it on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import probatio

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .const import DOMAIN
from .memory import KINDS, MemoryStore

API_ID = "aac_memory"

API_PROMPT = (
    "You have a long-term memory. Use recall before answering questions about the "
    "user's life, preferences, things, places or earlier conversations. Use remember "
    "when the user tells you something worth keeping (facts, preferences, where things "
    "are). If you only conclude something yourself, remember it with source "
    "'agent believes'; the user confirms it later. Do not remember small talk."
)


class RememberTool(llm.Tool):
    """Store something in AAC's memory."""

    name = "remember"
    title = "Remember"
    description = "Store a lasting fact, preference, event or note in long-term memory."
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required("text", description="What to remember, one self-contained sentence."): str,
            probatio.Optional("kind", description="fact, preference, event or note"): probatio.In(list(KINDS)),
            probatio.Optional(
                "source",
                description="'user said' when the user told you, 'agent believes' when you concluded it yourself",
            ): probatio.In(["user said", "agent believes"]),
        }
    )

    def __init__(self, memory: MemoryStore) -> None:
        """Initialize."""
        self._memory = memory

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Store it."""
        args = tool_input.tool_args
        mem = await self._memory.async_remember(
            args["text"], args.get("kind", "note"), args.get("source", "user said")
        )
        return llm.ToolResult(data={"stored": mem.id, "confirmed": mem.confirmed})


class RecallTool(llm.Tool):
    """Search AAC's memory."""

    name = "recall"
    title = "Recall"
    description = "Search long-term memory by meaning and keywords. Returns the best matches."
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required("query", description="What you are looking for, in a few words."): str,
            probatio.Optional("limit", description="How many results (default 5)."): int,
        }
    )

    def __init__(self, memory: MemoryStore) -> None:
        """Initialize."""
        self._memory = memory

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Search."""
        args = tool_input.tool_args
        found = await self._memory.async_recall(args["query"], min(int(args.get("limit", 5)), 20))
        return llm.ToolResult(
            data={
                "memories": [
                    {"id": m.id, "text": m.text, "kind": m.kind, "source": m.source,
                     "confirmed": m.confirmed, "date": m.created[:10]}
                    for m in found
                ]
            }
        )


class ForgetTool(llm.Tool):
    """Delete a memory."""

    name = "forget"
    title = "Forget"
    description = "Delete a memory by id (only when the user asks to forget it or it is wrong)."
    annotations = llm.ToolAnnotations(read_only=False, destructive=True, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema({probatio.Required("id", description="The memory id from recall."): str})

    def __init__(self, memory: MemoryStore) -> None:
        """Initialize."""
        self._memory = memory

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Delete."""
        deleted = await self._memory.async_delete(tool_input.tool_args["id"])
        return llm.ToolResult(data={"deleted": deleted}, error=not deleted)


@dataclass(kw_only=True)
class AACMemoryAPI(llm.API):
    """The memory tools as an LLM API."""

    memory: MemoryStore
    _tools: list[llm.Tool] = field(init=False)

    def __post_init__(self) -> None:
        """Build the tools once."""
        self._tools = [RememberTool(self.memory), RecallTool(self.memory), ForgetTool(self.memory)]

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Tools + prompt."""
        return llm.APIInstance(
            api=self, api_prompt=API_PROMPT, llm_context=llm_context, tools=self._tools
        )
