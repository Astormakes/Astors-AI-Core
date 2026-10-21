"""The model request record and its chain (spec/streams.md → Target model, spec/v1.md).

A request to a model is a structured record, not one long text, so every node writes to the right place
and the server's prompt cache keeps working:
- `system`: stable parts, first in the system prompt (the charter, the agent's own prompt)
- `instructions`: system text for this request only (an automatic run's situation, the voice rules)
- `context`: goes along with the message itself (memory recalls)
- `tools`: the LLM API ids the model may use (each tool set a node adds)

On the way to the model node it runs through the annotating nodes of type `model.request` on the stream
bus, lowest order first: each adds its part (`annotate(request)`). The model node then sends it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .const import LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import AACConfigEntry
    from .domains import Domain

REQUEST = "model.request"
ANSWER = "model.answer"


@dataclass
class ModelRequest:
    """One request to a domain's model."""

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
    system: list[str] = field(default_factory=list)
    instructions: str = ""
    context: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    @property
    def words(self) -> str:
        """The person's own words (for memory)."""
        return self.own_words or self.text

    def add_tools(self, api_id: str | None) -> None:
        """A tool set for this request (once)."""
        if api_id and api_id not in self.tools:
            self.tools.append(api_id)


async def async_prepare(hass: HomeAssistant, entry: AACConfigEntry, request: ModelRequest) -> ModelRequest:
    """Run the request through its chain: every annotating node of `model.request` that is on in its domain."""
    for node in entry.runtime_data.bus.annotators(request.domain.id, REQUEST):
        if node.annotate is None:
            continue
        try:
            await node.annotate(request)
        except Exception:  # noqa: BLE001 - one broken node must not stop the request
            LOGGER.exception("AAC: node %s failed on a request", node.id)
    return request
