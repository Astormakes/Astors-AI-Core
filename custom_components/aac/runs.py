"""Agent runs: every time the master (later: any agent) works on something.

A run records what woke it (a chat message, the heartbeat, an HA event),
the tool calls it made and the result. The panel shows chat runs inline in
the chat and automatic runs under WORKERS → AUTO. This is the start of the
audit log from the framework design.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

SIGNAL_UPDATE = f"{DOMAIN}_update"
MAX_RUNS = 100

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

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return asdict(self)


@callback
def async_notify(hass: HomeAssistant, what: str, **data: Any) -> None:
    """Tell open panels that something changed."""
    async_dispatcher_send(hass, SIGNAL_UPDATE, {"what": what, **data})


class RunRegistry:
    """Keeps the last runs (persisted) and tells the panel about changes."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass
        self._store: Store[list[dict[str, Any]]] = Store(hass, 1, f"{DOMAIN}.runs")
        self.runs: list[Run] = []

    async def async_load(self) -> None:
        """Load finished runs; anything still 'running' was cut off by a restart."""
        for data in await self._store.async_load() or []:
            run = Run(**data)
            if run.status == "running":
                run.status, run.result = "failed", "interrupted by a restart"
            self.runs.append(run)

    def _save(self) -> None:
        self.runs = self.runs[-MAX_RUNS:]
        self._store.async_delay_save(lambda: [r.as_dict() for r in self.runs], 2)

    @callback
    def start(self, kind: str, trigger: str, title: str, **kwargs: Any) -> Run:
        """Begin a run."""
        run = Run(kind=kind, trigger=trigger, title=title, **kwargs)
        self.runs.append(run)
        self._save()
        async_notify(self.hass, "run", run=run.as_dict())
        return run

    @callback
    def finish(self, run: Run, status: str, result: str) -> None:
        """End a run."""
        run.status = status
        run.result = result
        run.finished = dt_util.utcnow().isoformat()
        self._save()
        async_notify(self.hass, "run", run=run.as_dict())

    def get(self, run_id: str) -> Run | None:
        """Find a run."""
        return next((r for r in self.runs if r.id == run_id), None)

    def running(self, kind: str | None = None) -> list[Run]:
        """Runs still in progress."""
        return [r for r in self.runs if r.status == "running" and kind in (None, r.kind)]
