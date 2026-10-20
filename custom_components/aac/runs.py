"""Agent runs: every time the master (later: any agent) works on something.

A run records what woke it (a chat message, the heartbeat, an HA event),
the tool calls it made and the result. The panel shows chat runs inline in
the chat and automatic runs under CHATS → Auto. This is the start of the
audit log from the framework design. What the model got (the last request of
a run: system prompt, messages, tools) is kept apart for the newest runs.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

SIGNAL_UPDATE = f"{DOMAIN}_update"
MAX_RUNS = 100
MAX_REQUESTS = 40  # what the model got, for the newest runs (each is a few KB)
MAX_MESSAGE = 20000  # characters per message in a recorded request

# The run the current task works for (set around a master call)
current_run: ContextVar[Run | None] = ContextVar("aac_current_run", default=None)


@dataclass
class Run:
    """One piece of agent work."""

    kind: str  # "chat" or "auto"
    trigger: str  # e.g. "chat", "heartbeat", "state: binary_sensor.door"
    title: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "running"  # running / done / silent / failed
    started: str = field(default_factory=lambda: dt_util.utcnow().isoformat())
    finished: str | None = None
    input: str = ""
    # What the agent was told on top of its instructions (context recipe)
    context: str = ""
    result: str = ""
    chat_id: str | None = None
    # False = the model only gets its context, no tools (looking, not acting)
    allow_tools: bool = True
    model: str | None = None  # the model that answered (resource id)
    tier: str = "master"  # pool request: master (the owner's order), fast, smart, small or an own tag
    steps: list[dict[str, Any]] = field(default_factory=list)
    request: bool = False  # what the model got is recorded (RunRegistry.request)
    # The chat so far ({role, text}); used when HA forgot the conversation (it does after 5 idle minutes)
    history: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def as_dict(self) -> dict[str, Any]:
        """Serialize (without the chat history, which lives in the chat)."""
        data = asdict(self)
        data.pop("history", None)
        return data


@callback
def async_notify(hass: HomeAssistant, what: str, **data: Any) -> None:
    """Tell open panels that something changed."""
    async_dispatcher_send(hass, SIGNAL_UPDATE, {"what": what, **data})


class RunRegistry:
    """Keeps the last runs (persisted) and tells the panel about changes."""

    def __init__(self, hass: HomeAssistant, domain_id: str | None = None, suffix: str = "") -> None:
        """Initialize (suffix: storage per AAC domain; the first domain has none)."""
        self.hass = hass
        self.domain_id = domain_id
        self._store: Store[list[dict[str, Any]]] = Store(hass, 1, f"{DOMAIN}.runs{suffix}")
        self._req_store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.requests{suffix}")
        self.runs: list[Run] = []
        self.requests: dict[str, dict[str, Any]] = {}  # run id → the last request of that run
        self.prompts: dict[str, str] = {}  # system prompts by hash (they repeat: charter, instructions)

    async def async_load(self) -> None:
        """Load finished runs; anything still 'running' was cut off by a restart."""
        for data in await self._store.async_load() or []:
            run = Run(**data)
            if run.status == "running":
                run.status, run.result = "failed", "interrupted by a restart"
            self.runs.append(run)
        data = await self._req_store.async_load() or {}
        self.requests, self.prompts = data.get("requests", {}), data.get("prompts", {})

    @property
    def store(self):
        """The HA storage behind it (to remove a domain's data)."""
        return self._store

    @property
    def request_store(self):
        """The storage of the recorded requests (to remove a domain's data)."""
        return self._req_store

    @callback
    def record_request(self, run: Run, model: str, messages: list[dict[str, Any]],
                       tools: list[dict[str, Any]] | None, **extra: Any) -> None:
        """What the model got in the run's latest call (each call replaces the one before: the last one
        holds everything, the earlier tool calls and results included)."""
        system = "\n\n".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
        key = hashlib.sha1(system.encode()).hexdigest()[:12]
        self.prompts[key] = system
        self.requests.pop(run.id, None)
        self.requests[run.id] = {
            "model": model, "system": key, "at": dt_util.utcnow().isoformat(),
            "tools": [t.get("function", {}).get("name", "?") for t in tools or []],
            "messages": [_message(m) for m in messages if m.get("role") != "system"], **extra,
        }
        run.request = True
        for old in list(self.requests)[:-MAX_REQUESTS]:
            del self.requests[old]
        used = {r["system"] for r in self.requests.values()}
        self.prompts = {k: v for k, v in self.prompts.items() if k in used}
        self._req_store.async_delay_save(lambda: {"requests": self.requests, "prompts": self.prompts}, 5)

    def request(self, run_id: str) -> dict[str, Any] | None:
        """A recorded request with its system prompt written out."""
        if (req := self.requests.get(run_id)) is None:
            return None
        return {**req, "system": self.prompts.get(req["system"], "")}

    def _save(self) -> None:
        self.runs = self.runs[-MAX_RUNS:]
        self._store.async_delay_save(lambda: [r.as_dict() for r in self.runs], 2)

    @callback
    def start(self, kind: str, trigger: str, title: str, **kwargs: Any) -> Run:
        """Begin a run."""
        run = Run(kind=kind, trigger=trigger, title=title, **kwargs)
        self.runs.append(run)
        self._save()
        async_notify(self.hass, "run", run=run.as_dict(), domain=self.domain_id)
        return run

    @callback
    def finish(self, run: Run, status: str, result: str) -> None:
        """End a run."""
        run.status = status
        run.result = result
        run.finished = dt_util.utcnow().isoformat()
        self._save()
        async_notify(self.hass, "run", run=run.as_dict(), domain=self.domain_id)

    def get(self, run_id: str) -> Run | None:
        """Find a run."""
        return next((r for r in self.runs if r.id == run_id), None)

    def running(self, kind: str | None = None) -> list[Run]:
        """Runs still in progress."""
        return [r for r in self.runs if r.status == "running" and kind in (None, r.kind)]


def _text(content: Any) -> str:
    """A message's content as text (some are lists of parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return "" if content is None else json.dumps(content)


def _message(m: dict[str, Any]) -> dict[str, Any]:
    """One message of a request, for reading (long ones cut)."""
    text = _text(m.get("content"))
    out: dict[str, Any] = {"role": m.get("role"), "content": text[:MAX_MESSAGE] + (" …" if len(text) > MAX_MESSAGE else "")}
    if m.get("tool_calls"):
        out["tool_calls"] = [{"name": (c.get("function") or {}).get("name"), "args": (c.get("function") or {}).get("arguments")}
                             for c in m["tool_calls"]]
    return out
