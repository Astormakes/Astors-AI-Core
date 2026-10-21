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
from .memory import KINDS, MemoryStore, summarize, summary_text


def parse_value(value):
    """'81.6' → 81.6; anything else stays text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return str(value).strip() if value is not None else None


def fact_args(args: dict) -> dict | None:
    """The optional structured part of a remember call."""
    if not (args.get("subject") and args.get("attribute")) or args.get("value") in (None, ""):
        return None
    return {"subject": args["subject"], "attribute": args["attribute"],
            "value": parse_value(args["value"]), "unit": args.get("unit")}

API_ID = "aac_memory"

API_PROMPT = (
    "You have a long-term memory. Use recall before answering questions about the "
    "user's life, preferences, things, places or earlier conversations; never say you "
    "don't know or can't help with such a question before you called recall. Use remember "
    "when the user tells you something worth keeping (facts, preferences, where things "
    "are). Every memory keeps its date, so remember measurements and events as they "
    "happen (e.g. 'Weight 82.4 kg'); recall them later to see how things develop. "
    "Remember what the user wants to achieve or do as kind 'plan'; forget a plan when it "
    "is done or dropped. If you only conclude something yourself, remember it with "
    "source 'agent believes'; the user confirms it later. Do not remember small talk.\n"
    "For anything with a value (a measurement, a place, a rating, a count), also give "
    "subject, attribute, value and unit, e.g. subject 'user', attribute 'weight', value 81.6, "
    "unit 'kg'. Use lookup_facts to get exact values and their history."
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
            probatio.Optional(
                "kind", description="fact, preference, event, note, or plan (something the user wants to achieve or do)"
            ): probatio.In(list(KINDS)),
            probatio.Optional(
                "source",
                description="'user said' when the user told you, 'agent believes' when you concluded it yourself",
            ): probatio.In(["user said", "agent believes"]),
            probatio.Optional("subject", description="Structured: who/what it is about, e.g. 'user', 'drill'."): str,
            probatio.Optional("attribute", description="Structured: which property, e.g. 'weight', 'location'."): str,
            probatio.Optional("value", description="Structured: the value, a number or a short text."): str,
            probatio.Optional("unit", description="Structured: unit if any, e.g. 'kg'."): str,
        }
    )

    def __init__(self, memory: MemoryStore) -> None:
        """Initialize."""
        self._memory = memory

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Store it."""
        args = tool_input.tool_args
        mem = await self._memory.async_remember(
            args["text"], args.get("kind", "note"), args.get("source", "user said"), fact_args(args)
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


class LookupFactsTool(llm.Tool):
    """Exact structured facts with history."""

    name = "lookup_facts"
    title = "Look up facts"
    description = (
        "Exact stored values with their history, by subject and/or attribute "
        "(e.g. subject 'user', attribute 'weight'). Numbers come with first, latest and change."
    )
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Optional("subject", description="Who/what, e.g. 'user'."): str,
            probatio.Optional("attribute", description="Which property, e.g. 'weight'."): str,
        }
    )

    def __init__(self, memory: MemoryStore) -> None:
        """Initialize."""
        self._memory = memory

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Group by subject/attribute, summarize numbers."""
        args = tool_input.tool_args
        facts = await self._memory.async_facts(args.get("subject"), args.get("attribute"))
        groups: dict[tuple[str, str], list[dict]] = {}
        for fact in facts:
            groups.setdefault((fact["subject"], fact["attribute"]), []).append(fact)
        return llm.ToolResult(
            data={
                "series": [
                    {
                        "summary": summary_text(summarize(items)),
                        "values": [{"value": f["value"], "unit": f["unit"], "date": f["time"][:10]} for f in items[-20:]],
                    }
                    for items in groups.values()
                ]
                or "nothing stored for that"
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
        self._tools = [
            RememberTool(self.memory),
            RecallTool(self.memory),
            LookupFactsTool(self.memory),
            ForgetTool(self.memory),
        ]

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Tools + prompt."""
        return llm.APIInstance(
            api=self, api_prompt=API_PROMPT, llm_context=llm_context, tools=self._tools
        )
