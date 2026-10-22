"""Charter module: the owner's values, first in every agent's system prompt (framework section 3b).

A module since 0.48 (owner, 2026-10-21: "charter is also a module, a really simple one that just adds
context to a passing query"). It is a prompt part: stable text at the very start of the system prompt,
so the server's prompt cache keeps working. Owner only: the setup tools may neither change nor switch it
(manifest "owner_only"). Empty = the built-in draft (the manifest's placeholder).
"""

from __future__ import annotations

from .. import ModuleContext


async def async_setup_module(ctx: ModuleContext) -> None:
    """The prompt part."""
    builtin = ctx.module.manifest["settings"]["text"]["placeholder"]

    def charter(domain_id: str) -> str:
        return (ctx.setting(domain_id, "text") or "").strip() or builtin

    # Added charters (another chain, e.g. for voice or a work assistant) have their own text
    ctx.add_prompt(charter, order=0, instance_part=lambda settings: (settings.get("text") or "").strip() or builtin)
