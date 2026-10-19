"""Memory write path: after each message, keep what is worth keeping.

Framework section 2 ("write path"). Small models often answer nicely but
forget to call remember ("81.6 kg today" was answered, never stored). So
after every user message a short background call extracts lasting facts,
preferences, measurements/events (with numbers) and plans, skipping what
memory already holds. General behavior, nothing topic-specific.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .const import LOGGER
from .memory import KINDS
from .memory_api import fact_args
from .provider import OpenAICompatProvider, ProviderError

if TYPE_CHECKING:
    from .memory import MemoryStore

EXTRACT_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "memories",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "kind": {"type": "string", "enum": list(KINDS)},
                            "subject": {"type": "string"},
                            "attribute": {"type": "string"},
                            "value": {"type": "string"},
                            "unit": {"type": "string"},
                        },
                        "required": ["text", "kind", "subject", "attribute", "value", "unit"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    },
}

PROMPT = (
    "You keep the long-term memory of a home assistant. From the user's message below, "
    "extract only what is worth remembering for weeks or months:\n"
    "- facts about the user's life, people, things and where they are (kind fact)\n"
    "- likes and dislikes (kind preference)\n"
    "- measurements and events, always with the number and unit (kind event)\n"
    "- what the user wants to achieve or do (kind plan)\n"
    "Not: questions, commands to devices, small talk, the assistant's own words, or anything "
    "already in memory (listed below). Each item is one self-contained sentence about the user. "
    "If it has a value (measurement, place, rating, count), also fill subject, attribute, value "
    "and unit (e.g. user / weight / 81.6 / kg); otherwise leave those empty. "
    "Usually there is nothing: then return an empty list."
)


async def async_extract(
    memory: MemoryStore,
    provider: OpenAICompatProvider,
    model: str,
    user_text: str,
    stored_this_turn: list[str],
) -> list[str]:
    """Store new memories from one user message. Returns the stored texts."""
    similar = await memory.async_recall(user_text, limit=5, min_score=0.5)
    known = [m.text for m in similar] + stored_this_turn
    content = (
        f"{PROMPT}\n\nAlready in memory:\n"
        + ("\n".join(f"- {t}" for t in known) if known else "- (nothing related)")
        + f"\n\nUser's message: {user_text}"
    )
    try:
        reply = await provider.chat(
            model,
            [{"role": "user", "content": content}],
            None,
            response_format=EXTRACT_FORMAT,
            temperature=0.1,
            max_tokens=300,
            chat_template_kwargs={"enable_thinking": False},
        )
        items = json.loads(reply.get("content") or "{}").get("items", [])
    except (ProviderError, json.JSONDecodeError, AttributeError) as err:
        LOGGER.debug("Memory extraction skipped: %s", err)
        return []
    stored = []
    for item in items[:3]:
        text = str(item.get("text", "")).strip()
        kind = item.get("kind") if item.get("kind") in KINDS else "note"
        if not text or any(text.lower() == k.lower() for k in known) or await memory.async_is_known(text):
            continue
        await memory.async_remember(text, kind, "user said", fact_args(item))
        stored.append(text)
    return stored
