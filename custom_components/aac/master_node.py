"""The master as a node on the stream bus (spec/streams.md): `master.request` in, `master.answer` out.

Any module (heartbeat, later triggers, companion, wake word) asks the master by writing a request;
this node turns it into an automatic run: through the job queue (or at once with "now"), with the
context built when the run really starts (so a request that had to wait sees the current home), and
the result posted to a chat, or nothing when the master answers NOTHING (little noise is a core principle).

A request (plain JSON):
    {"trigger": "heartbeat", "title": "Heartbeat", "text": "Check-in: anything I should know right now?",
     "intro": "Heartbeat: a scheduled check-in, nobody asked you anything.",
     "context": ["time", "home", "open_loops"], "instructions": "…",
     "tier": "fast", "tools": false, "priority": "low", "due_minutes": 30, "tags": ["heartbeat"],
     "coalesce": true,          # skip while one of this trigger waits or runs
     "chat": "own",             # "aac" = the From AAC chat (default), "own" = a chat of its own per finding
     "chat_prefix": "hb",       # its id starts with this (default: the trigger)
     "now": false}              # true: no queue, emit returns the run (the owner pressed "Run once now")
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .capabilities import parse_time
from .chats import AAC_CHAT_ID
from .const import LOGGER
from .context import home_snapshot
from .jobs import Job
from .master import async_run_master
from .pools import role_configured
from .streams import Node

if TYPE_CHECKING:
    from . import AACConfigEntry
    from .domains import Domain
    from .runs import Run


def short(text: str, n: int = 40) -> str:
    """The start of a text for a title, cut at a word."""
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


async def async_open_loops(entry: AACConfigEntry, domain: Domain) -> str:
    """Open plans (memory kind 'plan'), AAC's own schedules and what is coming up, for automatic runs."""
    settings = domain.settings
    lines = []
    if (memory := domain.memory) and settings.memory["enabled"]:
        lines += [f"- plan: {m.text} (since {m.created[:10]})" for m in await memory.async_plans()]
    lines += [
        f"- your own scheduled check-in: {t['name']} ({t['when'].get('date') or 'repeating'} at {t['when'].get('at')})"
        for t in settings.triggers
        if t.get("created_by") == "agent" and t.get("enabled") and t["when"]["type"] == "time"
    ]
    # Coming up: everything the calendar providers know for the next 24 hours
    now = dt_util.now()
    upcoming = await entry.runtime_data.capabilities.async_calendar(domain.id, now, now + timedelta(hours=24))
    lines += [
        f"- coming up: {e['name']}" + (f" ({dt_util.as_local(parse_time(e['start'])).strftime('%a %H:%M')})" if parse_time(e.get('start')) else "")
        + f" [{e['source']}]"
        for e in upcoming if (e.get("ref") or {}).get("module") != "core"  # own plans are listed above
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


async def async_auto_run(
    hass: HomeAssistant, entry: AACConfigEntry, domain: Domain, trigger: str, title: str, text: str, prompt: str,
    allow_tools: bool = True, tier: str = "master", chat: str = "aac", chat_prefix: str | None = None,
) -> Run:
    """One automatic run; what the master says goes to a chat (chat "own": a chat of its own)."""
    run, result = await async_run_master(
        hass, entry, text, kind="auto", trigger=trigger, title=title,
        extra_system_prompt=prompt, allow_tools=allow_tools, tier=tier, domain=domain,
    )
    if result is not None and run.status == "done" and run.result:
        # A finding with a chat of its own (owner, 2026-10-20, heartbeat): a reply is about that one
        # thing, and findings don't pile up as context in one long chat
        chat_id = f"{chat_prefix or trigger}_{dt_util.utcnow().strftime('%Y%m%d%H%M%S')}" if chat == "own" else AAC_CHAT_ID
        if chat_id != AAC_CHAT_ID:
            domain.chats.create(f"{title} · {short(run.result)}", chat_id)
        run.chat_id = chat_id
        domain.chats.add_message(chat_id, "aac", run.result, proactive=True, run_id=run.id, trigger=title)
    LOGGER.debug("Automatic run %s (%s): %s", run.id, trigger, run.status)
    return run


class MasterNode:
    """The core's master node."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.node = Node(id="master", title="Master", inputs={"requests": "master.request"},
                         outputs={"answers": "master.answer"}, run=self._async_request)

    def _busy(self, domain: Domain, trigger: str) -> bool:
        if any(j.domain is domain and trigger in j.tags for j in self.entry.runtime_data.jobs.waiting):
            return True  # one waiting is enough
        return any(r.trigger == trigger for r in domain.runs.running("auto"))

    async def _async_request(self, domain_id: str, port: str, req: dict[str, Any]) -> Run | None:
        data = self.entry.runtime_data
        domain = data.domain(domain_id)
        trigger = req.get("trigger") or "request"
        if domain is None or not role_configured(self.hass, data.store, "master"):
            return None
        if req.get("coalesce") and self._busy(domain, trigger):
            return None
        title = req.get("title") or trigger
        tier = req.get("tier") or "master"

        async def start(note: str = "") -> Run:
            prompt = (
                f"{req.get('intro') or 'Nobody asked you anything.'}\n"
                f"{await async_context(self.hass, self.entry, domain, req.get('context') or [])}\n\n"
                f"{req.get('instructions') or ''}"
                f"{chr(10) * 2 + note if note else ''}"
            )
            run = await async_auto_run(self.hass, self.entry, domain, trigger, title, req.get("text") or "", prompt,
                                       allow_tools=bool(req.get("tools", True)), tier=tier, chat=req.get("chat") or "aac",
                                       chat_prefix=req.get("chat_prefix"))
            await data.bus.emit(domain.id, "master", "answers", {
                "run_id": run.id, "trigger": trigger, "status": run.status, "result": run.result, "chat_id": run.chat_id})
            return run

        if req.get("now"):
            return await start()
        due = req.get("due_minutes")
        await data.jobs.async_submit(Job(
            domain, title, tier, req.get("priority") or "normal", start,
            due=dt_util.utcnow() + timedelta(minutes=int(due)) if due else None,
            tags=list(dict.fromkeys([trigger, *(req.get("tags") or [])])),
        ))
        return None
