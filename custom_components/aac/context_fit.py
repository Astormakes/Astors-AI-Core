"""Fit a request into the model's context window.

AAC keeps the whole chat, but a model holds only so many tokens (llama.cpp: n_ctx, e.g.
8192 on a small server). Before a request the messages are estimated (characters per
token, corrected by what the server reported last time) and, when they would not fit,
the oldest turns are left out first, then long tool results of the current turn are
shortened. The newest question and the system prompt always stay.
"""

from __future__ import annotations

import json
from typing import Any

CHARS_PER_TOKEN = 3.5  # rough for English/German text and JSON; corrected per model by `factor`
TOOL_RESULT_KEEP = 800  # characters of an older tool result that stay when space is short


def estimate(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, factor: float = 1.0) -> int:
    """Tokens the request will take, roughly (factor = real / estimated, learned per model)."""
    chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
    chars += len(json.dumps(tools, ensure_ascii=False)) if tools else 0
    return int(chars / CHARS_PER_TOKEN * factor)


def fit(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, budget: int, factor: float = 1.0
) -> tuple[list[dict[str, Any]], int, int]:
    """Messages that fit into `budget` tokens: (messages, messages left out, estimated tokens)."""
    if estimate(messages, tools, factor) <= budget:
        return messages, 0, estimate(messages, tools, factor)
    head = 0
    while head < len(messages) and messages[head]["role"] == "system":
        head += 1
    current = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=len(messages))
    older, tail = messages[head:current], messages[current:]

    # 1. leave out whole older turns (a user message and everything up to the next one)
    dropped = 0
    while older:
        if estimate([*messages[:head], *older, *tail], tools, factor) <= budget:
            break
        nxt = next((i for i, m in enumerate(older) if i > 0 and m["role"] == "user"), len(older))
        dropped += nxt
        older = older[nxt:]
    # 2. shorten tool results of this turn, oldest first (the newest one stays whole)
    tail = list(tail)
    tool_idx = [i for i, m in enumerate(tail) if m["role"] == "tool"][:-1]
    for i in tool_idx:
        if estimate([*messages[:head], *older, *tail], tools, factor) <= budget:
            break
        content = str(tail[i].get("content") or "")
        if len(content) > TOOL_RESULT_KEEP:
            tail[i] = {**tail[i], "content": content[:TOOL_RESULT_KEEP] + " … (shortened, the context is full)"}
    system = list(messages[:head])
    if dropped and system:
        system[-1] = {**system[-1], "content": f"{system[-1]['content']}\n\n(The first {dropped} messages of "
                      "this chat are left out: they do not fit into your context any more.)"}
    result = [*system, *older, *tail]
    return result, dropped, estimate(result, tools, factor)
