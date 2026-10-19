"""Running the master agent, for chats and for automatic wake-ups."""

from __future__ import annotations

import re

from typing import TYPE_CHECKING, Any

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant

from .runs import Run, current_run

if TYPE_CHECKING:
    from .domains import Domain
    from . import AACConfigEntry

SILENT = "NOTHING"


# Small models often say "nothing" in other words instead of the exact NOTHING.
# Interim heuristic until there is a real interjection gate (framework section 5).
_NOTHING_RE = re.compile(
    r"^\W*(nothing\b|none\W*$|n/?a\W*$|no\W*$|no (new |current |active )?(alerts?|issues?|problems?|updates?|news)\b"
    r"|(there is |there's )?nothing (new |unusual |important )?to (report|say|mention)"
    r"|all (systems )?(is |are |seems |look |looks )?(good|fine|normal|quiet|ok)\b"
    r"|everything (is |seems |looks )?(fine|normal|ok|good)\b"
    r"|no[,.!]? (everything|nothing|all)\b)",
    re.IGNORECASE,
)


def is_silent(text: str) -> bool:
    """The master answers NOTHING (or an obvious equivalent) when it has nothing to say."""
    text = text.strip()
    return not text or bool(_NOTHING_RE.match(text)) or text.strip(".!").upper() == SILENT


async def async_run_master(
    hass: HomeAssistant,
    entry: AACConfigEntry,
    text: str,
    *,
    kind: str,
    trigger: str,
    title: str,
    chat_id: str | None = None,
    conversation_id: str | None = None,
    extra_system_prompt: str | None = None,
    user_id: str | None = None,
    allow_tools: bool = True,
    tier: str = "master",
    domain: Domain | None = None,
    history: list[dict[str, Any]] | None = None,
) -> tuple[Run, conversation.ConversationResult | None]:
    """Run a domain's master once and record it as a run (steps come from the agent)."""
    domain = domain or entry.runtime_data.domain()
    runs = domain.runs
    run = runs.start(
        kind,
        trigger,
        title,
        input=text,
        chat_id=chat_id,
        context=extra_system_prompt or "",
        allow_tools=allow_tools,
        tier=tier,
        history=history or [],
    )
    token = current_run.set(run)
    try:
        result = await conversation.async_converse(
            hass,
            text,
            conversation_id,
            Context(user_id=user_id),
            agent_id=domain.entity_id(hass, entry.entry_id) or entry.entry_id,
            extra_system_prompt=extra_system_prompt,
        )
    except Exception as err:  # noqa: BLE001 - a failed run must not kill the caller
        runs.finish(run, "failed", str(err))
        return run, None
    finally:
        current_run.reset(token)

    speech = result.response.speech.get("plain", {}).get("speech", "")
    if result.response.response_type.value == "error":
        runs.finish(run, "failed", speech)
    elif kind == "auto" and is_silent(speech):
        runs.finish(run, "silent", speech)
    else:
        runs.finish(run, "done", speech)
    return run, result
