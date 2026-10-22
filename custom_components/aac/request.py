"""The message (spec/streams.md → Target model, spec/v1.md).

Owner, 2026-10-22: "answer and request is the same thing". A message is a structured record, not one long
text, so every node writes to the right place and the server's prompt cache keeps working:
- `text`: what is being asked now (after a model: its answer, so another model can work on it)
- `system`: stable parts, first in the system prompt (the charter, the agent's own prompt)
- `instructions`: system text for this message only (an automatic run's situation, the voice rules)
- `context`: goes along with the message itself (memory recalls, a context card)
- `tools`: the LLM API ids the model may use (filled by the cards attached to it, when it answers)
- where it came from (`source`, chat, user, …), so Reply brings the answer home however many models it passed
- `priority` (set by a source's output), and for automatic ones how long it may wait (`due_minutes`, …)

flow.py moves it through the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any
import uuid

if TYPE_CHECKING:
    from .domains import Domain

MESSAGE = "message"
TOOL = "tool"

SOURCES = {"chat": "Panel chat", "voice": "Voice", "assist": "HA Assist"}
PRIORITY = {"chat": "high", "voice": "high", "assist": "high"}


def source_of(kind: str, trigger: str) -> str:
    """Which source node a message comes from."""
    if trigger == "heartbeat":
        return "heartbeat"
    if kind == "auto":
        return "triggers"
    if trigger.startswith("voice"):
        return "voice"
    return "assist" if trigger == "assist" else "chat"


@dataclass
class ModelRequest:
    """A message (the name stays from v0's requests: one record for asking and answering)."""

    domain: Domain
    text: str
    kind: str = "chat"  # chat (someone waits) · auto (nobody asked)
    trigger: str = "chat"
    title: str = ""
    chat_id: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None
    own_words: str | None = None  # what the person said themselves (a voice line comes with the room's lines)
    history: list[dict[str, Any]] = field(default_factory=list)  # [{"role": "user"|"aac", "text": …}]
    tier: str = "master"
    allow_tools: bool = True
    language: str | None = None
    device_id: str | None = None
    source: str = "chat"  # the node it comes from: chat (panel), voice, assist (HA), auto (heartbeat, triggers)
    priority: str = "high"  # high · normal · low (a source's output sets it)
    system: list[str] = field(default_factory=list)
    instructions: str = ""
    context: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)  # the way through the graph, each node's part
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    # Automatic messages (heartbeat, triggers): how they wait for a model (jobs.py) and where a finding goes
    due_minutes: int | None = None
    critical: bool = False
    fallback: str = ""
    coalesce: bool = False
    keep: bool = False
    tags: list[str] = field(default_factory=list)
    context_parts: list[str] = field(default_factory=list)  # built when a model really starts: time, home, open_loops
    chat: str = "aac"  # an automatic finding: "aac" = the From AAC chat, "own" = a chat of its own
    chat_prefix: str | None = None
    answered: bool = False  # a model has answered it (text = the answer)
    now: bool = False  # automatic but at once, without the queue ("Run once now")
    meta: dict[str, Any] = field(default_factory=dict, repr=False)  # the last answer's details for sinks (memory)

    @property
    def words(self) -> str:
        """The person's own words (for memory)."""
        return self.own_words or self.text

    def add_tools(self, api_id: str | None) -> None:
        """A tool set for this message (once)."""
        if api_id and api_id not in self.tools:
            self.tools.append(api_id)

    def branch(self) -> ModelRequest:
        """A copy for one branch of the graph (lists copied, the way so far kept)."""
        return replace(self, system=list(self.system), context=list(self.context), tools=list(self.tools),
                       trace=list(self.trace), history=list(self.history), tags=list(self.tags),
                       context_parts=list(self.context_parts), meta=dict(self.meta))


Message = ModelRequest
