"""The broker: every request to a model goes through here (spec/streams.md).

Owner, 2026-10-21: "the master with pulling together context and all should be called the broker".
It takes requests (a chat, a spoken line, a trigger, the heartbeat), builds their context, asks the pool
for a model of the request's tier, lets the agent answer with its tools and records the run. It does
not think; the model does. "Master" now means only the main model (the master role in the pool).

On the stream bus it is the core's node "broker": `broker.request` in, `broker.answer` out. Any module
(heartbeat, later triggers, companion, wake word) asks by writing a request; the node turns it into an
automatic run: through the job queue (or at once with "now"), with the context built when the run really
starts (so a request that had to wait sees the current home), and the result posted to a chat, or nothing
when the model answers NOTHING (little noise is a core principle).

A request (plain JSON):
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
     "now": false}              # true: no queue, emit returns the run (the owner pressed "Run once now")
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .capabilities import parse_time
from .chats import AAC_CHAT_ID
from .const import LOGGER
from .context import home_snapshot
from .jobs import Job
from .pools import role_configured
from .request import ANSWER, REQUEST, ModelRequest, async_prepare, source_of, start
from .runs import Run, current_run
from .streams import Node

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


# The settings of an added model node (the panel builds the form from them, like a manifest's settings)
MODEL_SETTINGS = {
    "tier": {"type": "select", "title": "Model (pool request)", "default": "master", "options_from": "tiers",
             "help": "Which kind of model it asks the pool for."},
    "prompt": {"type": "longtext", "title": "Extra instructions", "default": "",
               "help": "Added to the system prompt after the chain's parts (e.g. how this model should work)."},
}


def answering_model(entry: AACConfigEntry, domain: Domain, source: str = "chat") -> Node | None:
    """The model node wired at the end of a source's request chain (the standard one, or an added one)."""
    bus = entry.runtime_data.bus
    tail = bus.chain(domain.id, *start(entry, domain.id, source))[1]
    nodes = bus.nodes_in(domain.id)
    return next((nodes[b] for a, ap, b, bp in bus.wires(domain.id)
                 if (a, ap) == tail and nodes[b].inputs.get(bp) == REQUEST), None)


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
) -> tuple[Run, Answer | None]:
    """One request to a domain's model, recorded as a run: the request record runs through its chain
    (request.py), the model node answers (model_node.py), readers of model.answer get it (memory's write path)."""
    domain = domain or entry.runtime_data.domain()
    runs = domain.runs
    request = ModelRequest(
        domain=domain, text=text, kind=kind, trigger=trigger, title=title, chat_id=chat_id,
        conversation_id=conversation_id or ulid_now(), user_id=user_id, own_words=own_words,
        history=list(history or []), tier=tier, allow_tools=allow_tools, language=language or hass.config.language,
        device_id=device_id, instructions=extra_system_prompt or "", source=source_of(kind, trigger),
    )
    run = runs.start(kind, trigger, title, input=text, chat_id=chat_id, context=extra_system_prompt or "",
                     allow_tools=allow_tools, tier=tier, history=history or [], own_words=own_words)
    token = current_run.set(run)
    model = None
    try:
        await async_prepare(hass, entry, request)
        if (model := answering_model(entry, domain, request.source)) is None:
            raise HomeAssistantError("No model node is wired at the end of the request chain (SETTINGS → Nodes).")
        if model.type:  # an added model node: its own tier and instructions
            request.tier = model.settings.get("tier") or request.tier
            if prompt := (model.settings.get("prompt") or "").strip():
                request.system.append(prompt)
            run.tier = request.tier
            run.steps.append({"type": "note", "text": f"answered by the model node \"{model.title}\""})
        if request.context:
            run.context = "\n\n".join(filter(None, [run.context, *request.context]))
        speech, extra = await entry.runtime_data.model_node.async_answer(request, run)
    except Exception as err:  # noqa: BLE001 - a failed run must not kill the caller
        if not isinstance(err, HomeAssistantError):
            LOGGER.exception("AAC: request %s failed", run.id)
        runs.finish(run, "failed", str(err))
        return run, None
    finally:
        current_run.reset(token)
    if kind == "auto" and is_silent(speech):
        runs.finish(run, "silent", speech)
    else:
        runs.finish(run, "done", speech)
    # The answer on the bus: e.g. memory keeps what was worth keeping (in the background, nobody waits)
    entry.async_create_background_task(hass, entry.runtime_data.bus.emit(domain.id, model.id, "model_answers", {
        "request": request, "run": run, "text": speech, **extra}), f"aac answer {run.id}")
    return run, Answer(speech, request.conversation_id)


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


async def async_auto_run(
    hass: HomeAssistant, entry: AACConfigEntry, domain: Domain, trigger: str, title: str, text: str, prompt: str,
    allow_tools: bool = True, tier: str = "master", chat: str = "aac", chat_prefix: str | None = None,
) -> Run:
    """One automatic run; what the model says goes to a chat (chat "own": a chat of its own)."""
    run, result = await async_run_request(
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


class BrokerNode:
    """The core's broker node."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.node = Node(id="broker", title="Model", inputs={"requests": "broker.request", "model": "model.request"},
                         outputs={"answers": "broker.answer", "model_answers": "model.answer"}, run=self._async_request,
                         make=self._instance, settings_schema=MODEL_SETTINGS)

    @staticmethod
    def _instance(inst_id: str, title: str, settings: dict[str, Any]) -> Node:
        """Another model node in a domain (spec/v1.md): its own tier and prompt; it answers the requests that
        reach it, i.e. when it is wired at the end of the request chain instead of the standard one."""
        return Node(id=inst_id, title=title, inputs={"model": REQUEST}, outputs={"model_answers": ANSWER}, settings=settings)

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
            await data.bus.emit(domain.id, "broker", "answers", {
                "run_id": run.id, "trigger": trigger, "status": run.status, "result": run.result, "chat_id": run.chat_id})
            return run

        if req.get("now"):
            return await start(req.get("_note") or "")
        await data.jobs.async_submit(self._job(domain, req, start))
        return None

    def _job(self, domain: Domain, req: dict[str, Any], start) -> Job:
        trigger = req.get("trigger") or "request"
        due = req.get("due_minutes")
        return Job(
            domain, req.get("title") or trigger, req.get("tier") or "master", req.get("priority") or "normal", start,
            due=dt_util.utcnow() + timedelta(minutes=int(due)) if due else None,
            critical=bool(req.get("critical")), fallback=req.get("fallback") or "",
            tags=list(dict.fromkeys([trigger, *(req.get("tags") or [])])),
            saved={"kind": "request", "title": req.get("title") or trigger, "request": {**req, "now": False}} if req.get("keep") else None,
        )

    def restore(self, domain: Domain, item: dict[str, Any]) -> Job | None:
        """A request that waited before a restart, queued again (the job queue keeps its times)."""
        req = item.get("request") or {}

        async def start(note: str = "") -> Run | None:
            # The same path as a fresh request, at once (it already waited its turn)
            return await self._async_request(domain.id, "requests", {**req, "now": True, "coalesce": False, "_note": note})

        job = self._job(domain, req, start)
        job.reason = "waited before Home Assistant restarted"
        return job
