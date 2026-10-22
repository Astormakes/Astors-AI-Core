"""The broker: every request to a model enters here (spec/v1.md).

Owner, 2026-10-21: "the master with pulling together context and all should be called the broker"; 2026-10-22:
"answer and request is the same thing". A request becomes a message (request.py) that starts at its source node
and runs through the graph (flow.py): transforms add their part, a model answers with the cards attached to it,
Reply brings the answer home. This file is the entrance (async_run_request for whoever waits for an answer,
async_send for automatic ones from the heartbeat and the triggers) and the helpers they share.

An automatic request (plain JSON, from the heartbeat and the triggers):
    {"trigger": "heartbeat", "title": "Heartbeat", "text": "Check-in: anything I should know right now?",
     "intro": "Heartbeat: a scheduled check-in, nobody asked you anything.",
     "context": ["time", "home", "open_loops"], "instructions": "…",
     "tier": "fast", "tools": false, "priority": "low", "due_minutes": 30, "tags": ["heartbeat"],
     "coalesce": true,          # skip while one of this trigger waits or runs
     "chat": "own",             # "aac" = the From AAC chat (default), "own" = a chat of its own per finding
     "chat_prefix": "hb",       # its id starts with this (default: the trigger)
     "critical": false,         # still reach the owner when no model came before it was due …
     "fallback": "…",           # … with this text (a plain notification, no model)
     "keep": false,             # wait across a restart (saved with the queue)
     "now": false}              # true: no queue, the run comes back (the owner pressed "Run once now")
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .capabilities import parse_time
from .context import home_snapshot
from .pools import role_configured
from .request import PRIORITY, ModelRequest, source_of
from .runs import Run

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
    """The model answers NOTHING (or an obvious equivalent) when it has nothing to say."""
    text = text.strip()
    return not text or bool(_NOTHING_RE.match(text)) or text.strip(".!").upper() == SILENT


@dataclass
class Answer:
    """What a request returns besides its run."""

    text: str
    conversation_id: str


async def async_run_request(
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
    own_words: str | None = None,
    language: str | None = None,
    device_id: str | None = None,
    source: str | None = None,
) -> tuple[Run, Answer | None]:
    """A message from someone who waits for the answer (a chat, a spoken line, HA Assist, Try it): it starts at
    its source node and goes through the graph; the answer that comes home (or the last model's) is returned with
    the run of the model that gave it."""
    domain = domain or entry.runtime_data.domain()
    source = source or source_of(kind, trigger)
    message = ModelRequest(
        domain=domain, text=text, kind=kind, trigger=trigger, title=title, chat_id=chat_id,
        conversation_id=conversation_id or ulid_now(), user_id=user_id, own_words=own_words,
        history=list(history or []), tier=tier, allow_tools=allow_tools, language=language or hass.config.language,
        device_id=device_id, instructions=extra_system_prompt or "", source=source, priority=PRIORITY.get(source, "high"),
    )
    result = await entry.runtime_data.flow.async_send(message)
    run = result.run
    if run is None:  # it never reached a model
        run = domain.runs.start(kind, trigger, title, input=text, chat_id=chat_id, source=source)
        run.trace = list(message.trace)
        domain.runs.finish(run, "failed", "No model node on this message's way (SETTINGS → Nodes).")
        return run, None
    if run.status == "failed":
        return run, None
    answer = result.message.text if result.message else run.result
    return run, Answer(answer, message.conversation_id)


async def async_send(hass: HomeAssistant, entry: AACConfigEntry, domain: Domain, req: dict[str, Any],
                     source: str) -> Run | None:
    """An automatic request (the heartbeat's, a trigger's) from its source node: through the graph, waiting for a
    model in the queue unless `now`; with now, the run comes back (None: no model, or one is already running)."""
    trigger = req.get("trigger") or source
    if not role_configured(hass, entry.runtime_data.store, "master"):
        return None
    message = ModelRequest(
        domain=domain, text=req.get("text") or "", kind="auto", trigger=trigger, title=req.get("title") or trigger,
        instructions="\n\n".join(filter(None, [req.get("intro") or "Nobody asked you anything.", req.get("instructions") or ""])),
        tier=req.get("tier") or "master", allow_tools=bool(req.get("tools", True)), source=source,
        priority=req.get("priority") or "normal", due_minutes=req.get("due_minutes") or 15, critical=bool(req.get("critical")),
        fallback=req.get("fallback") or "", coalesce=bool(req.get("coalesce")), keep=bool(req.get("keep")),
        tags=list(req.get("tags") or []), context_parts=list(req.get("context") or []), chat=req.get("chat") or "aac",
        chat_prefix=req.get("chat_prefix"), now=bool(req.get("now")),
    )
    flow = entry.runtime_data.flow
    if message.now:
        if message.coalesce and flow._busy(message):  # noqa: SLF001
            return None
        return (await flow.async_send(message)).run
    await flow.async_send(message, wait=False)
    return None


def short(text: str, n: int = 40) -> str:
    """The start of a text for a title, cut at a word."""
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


async def async_open_loops(entry: AACConfigEntry, domain: Domain) -> str:
    """What modules add (memory: open plans; triggers: AAC's own schedules) and what is coming up, for
    automatic runs."""
    lines = await entry.runtime_data.capabilities.async_open_loops(domain.id)
    # Coming up: everything the calendar providers know for the next 24 hours
    now = dt_util.now()
    upcoming = await entry.runtime_data.capabilities.async_calendar(domain.id, now, now + timedelta(hours=24))
    lines += [
        f"- coming up: {e['name']}" + (f" ({dt_util.as_local(parse_time(e['start'])).strftime('%a %H:%M')})" if parse_time(e.get('start')) else "")
        + f" [{e['source']}]"
        for e in upcoming if not (e.get("ref") or {}).get("open_loop")  # listed above by their module
    ][:15]
    return ("\n\nOpen loops (what the user wants to achieve, your own plans, what is coming up):\n" + "\n".join(lines)) if lines else ""


async def async_context(hass: HomeAssistant, entry: AACConfigEntry, domain: Domain, parts: list[str]) -> str:
    """The named context parts, built now."""
    text = ""
    if "time" in parts:
        text += f"Local time: {dt_util.now().strftime('%A %Y-%m-%d %H:%M')}.\n"
    if "home" in parts:
        text += f"Current state of the home (area | name | state):\n{home_snapshot(hass, domain.settings)}"
    if "open_loops" in parts:
        text += await async_open_loops(entry, domain)
    return text


