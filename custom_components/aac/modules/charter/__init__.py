"""Charter module: the owner's values, first in every agent's system prompt (framework section 3b).

A module since 0.48 (owner, 2026-10-21: "charter is also a module, a really simple one that just adds
context to a passing query"). It is a prompt part: stable text at the very start of the system prompt,
so the server's prompt cache keeps working. Owner only: the setup tools may neither change nor switch it
(manifest "owner_only"). Empty adds nothing; the panel offers the built-in draft (the manifest's placeholder) as a button.
"""

from __future__ import annotations

from .. import ModuleContext


async def async_setup_module(ctx: ModuleContext) -> None:
    """The prompt part."""
    def charter(domain_id: str) -> str:
        # empty adds nothing; the card's "Use the built-in charter draft" fills the draft in (owner, 2026-10-23: "I'd
        # rather have it ask … the automatic one is prone to be overlooked")
        return (ctx.setting(domain_id, "text") or "").strip()

    # Added charters (another chain, e.g. for voice or a work assistant) have their own text
    # Renamed Header (owner, 2026-10-23: "remove the header and rename the charter since they are basically the same")
    # a Header with no text adds nothing: a relay (owner, 2026-10-23)
    ctx.add_prompt(charter, order=0, instance_part=lambda settings: (settings.get("text") or "").strip(),
                   replace=lambda domain_id: ctx.setting(domain_id, "replace") is not False,
                   description="Text at the very start of the system prompt: by default your values (honest, sparing, your "
                               "decisions stay yours). Only you can change it, never an agent. An added Header has its own "
                               "text, e.g. a calmer one for voice; with no text it adds nothing and only passes the message "
                               "on: a relay (its round output gives every wire a copy).")
