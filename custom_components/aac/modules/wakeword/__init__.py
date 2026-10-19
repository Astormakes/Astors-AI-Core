"""Wake word: AAC answers when its name is said, the classic way (owner, 2026-10-19).

The core only hears (audio_hub → hearing listeners); this module decides: one of AAC's
names in the line (core audio setting) or a follow-up right after it spoke → optionally a
quick attention check (a video saying the name is ignored) → the master answers by voice,
with the lines said just before as context (look back).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import time
from typing import Any

from ...capabilities import Hearing
from .. import ModuleContext


async def async_setup_module(ctx: ModuleContext) -> None:
    """Listen for AAC's name."""

    async def heard(source, line: dict[str, Any], earlier: list[dict[str, Any]]) -> None:
        hub = ctx.audio
        domain_id = ctx.entry.runtime_data.voice_domain.id
        follow_up = float(ctx.setting(domain_id, "follow_up") or 0)
        if not (hub.mentions(line["text"]) or (source.last_answer and time.monotonic() - source.last_answer < follow_up)):
            return
        line["addressed"] = True
        if ctx.setting(domain_id, "attention") and not await hub.async_attention(
            line["text"], [x["text"] for x in earlier[-6:]]
        ):
            line["addressed"], line["gate"] = False, "not meant for AAC"
            hub.changed()
            return
        hub.changed()
        seconds = float(ctx.setting(domain_id, "look_back") or 0)
        since = datetime.fromisoformat(line["time"]) - timedelta(seconds=seconds)
        before = [x["text"] for x in earlier if seconds and datetime.fromisoformat(x["time"]) >= since]
        await hub.async_answer(source, line["text"], before)

    ctx.add_hearing(Hearing(id="wakeword", title="Wake word", heard=heard))
