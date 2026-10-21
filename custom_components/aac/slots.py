"""Slots (spec/streams.md): interfaces with exactly one provider, asked on demand.

Owner design (2026-10-21): "everything not core becomes a module, even memory". The core asks a slot,
never a module: what to hand the model along with a message (memory recalls), what to keep after one.
Without a provider (or with its module switched off in a domain) a slot answers empty and AAC works
without it. The first slot is `memory`; its default provider is the memory module (modules/memory).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .domains import Domain
    from .provider import OpenAICompatProvider


class MemorySlot(Protocol):
    """What the core asks of a memory provider (all per AAC domain)."""

    async def async_context(self, domain_id: str, text: str) -> str | None:
        """Text to hand the model along with a message ("Memories that may help: …"), or None."""

    async def async_after_message(self, domain_id: str, text: str, stored_now: list[str],
                                  provider: OpenAICompatProvider, model: str) -> None:
        """The person said `text` in a chat (the model already stored `stored_now`): keep what is worth it.
        `provider`/`model` = the model that answered, for a short extraction call."""

    async def async_model_changed(self) -> None:
        """The embedding model (pool role "memory") changed."""

    async def async_remove_domain(self, domain: Domain) -> None:
        """A domain is deleted: its memory goes too."""


class Slots:
    """The registry: slot name → (provider, module that provides it)."""

    def __init__(self) -> None:
        """Initialize."""
        self._providers: dict[str, tuple[Any, str | None]] = {}
        # Is a module on in a domain? fn(domain_id, module_id) → bool (set by the module loader)
        self.module_enabled: Callable[[str, str], bool] = lambda domain_id, module_id: True

    def provide(self, slot: str, provider: Any, module: str | None = None) -> Callable[[], None]:
        """Become a slot's provider (the last one wins); returns the function that gives it up again."""
        self._providers[slot] = (provider, module)

        def remove() -> None:
            if self._providers.get(slot, (None,))[0] is provider:
                del self._providers[slot]

        return remove

    def get(self, slot: str, domain_id: str | None = None) -> Any:
        """A slot's provider (in a domain: only while its module is on there), or None."""
        provider, module = self._providers.get(slot, (None, None))
        if provider is not None and module and domain_id and not self.module_enabled(domain_id, module):
            return None
        return provider

    def memory(self, domain_id: str | None = None) -> MemorySlot | None:
        """The memory provider."""
        return self.get("memory", domain_id)
