"""The job queue: automatic work waits for a model instead of failing.

Owner design (2026-10-19): every automatic job (heartbeat, triggers, AAC's own
plans, later module jobs) carries classes like the models carry traits:
- priority: high (reminders, appointments) · normal (event reactions) · low (heartbeat, background)
- due: after this time the job is pointless. A time-critical job that is still
  waiting then reaches the owner anyway, without a model: its fallback text becomes
  a notification. Others expire, visibly.
When no allowed model is free the job waits (highest priority first). The owner
can be told once per outage, and a job that waited longer than the configured
time is told so when it finally runs. The owner's own chats never queue.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .chats import AAC_CHAT_ID
from .const import LOGGER
from .runs import async_notify

if TYPE_CHECKING:
    from . import AACConfigEntry
    from .domains import Domain

PRIORITIES = {"high": 0, "normal": 1, "low": 2}
CHECK_EVERY = timedelta(seconds=30)
NO_MODEL = "No master model reachable"  # marks a run that failed for lack of a model


@dataclass
class Job:
    """One piece of automatic work."""

    domain: Domain
    title: str
    tier: str
    priority: str
    start: Callable[[str], Awaitable[Any]]  # gets a note ("" or "this job was postponed …")
    due: datetime | None = None
    critical: bool = False  # still reach the owner when no model came in time
    fallback: str = ""  # the text that reaches the owner then
    tags: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created: datetime = field(default_factory=dt_util.utcnow)
    status: str = "queued"  # queued · running · done · expired · notified
    reason: str = ""

    def summary(self) -> dict[str, Any]:
        """For the panel."""
        return {"id": self.id, "domain": self.domain.id, "title": self.title, "tier": self.tier,
                "priority": self.priority, "due": self.due.isoformat() if self.due else None,
                "critical": self.critical, "tags": self.tags, "created": self.created.isoformat(),
                "status": self.status, "reason": self.reason}


class JobQueue:
    """Runs automatic jobs now, or keeps them until a model is free."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.waiting: list[Job] = []
        self.history: list[Job] = []  # the last finished/expired ones, for the panel
        self._unsub: CALLBACK_TYPE | None = None
        self._outage_told = False

    @property
    def settings(self) -> dict[str, Any]:
        """Owner settings: notify_no_model, postpone_notice (minutes)."""
        return self.entry.runtime_data.store.data["queue"]

    @callback
    def async_start(self) -> None:
        """Check the waiting jobs regularly."""
        self._unsub = async_track_time_interval(self.hass, self._tick, CHECK_EVERY)

    @callback
    def async_stop(self) -> None:
        """Stop checking."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _model_free(self, job: Job) -> bool:
        return self.entry.runtime_data.broker.endpoint_for(job.tier, background=True) is not None

    async def async_submit(self, job: Job) -> None:
        """Run now if a model is free, else wait."""
        if self._model_free(job):
            await self._async_run(job)
        else:
            self._enqueue(job, "no model available")

    def _enqueue(self, job: Job, reason: str) -> None:
        job.status, job.reason = "queued", reason
        if job not in self.waiting:
            self.waiting.append(job)
            self.waiting.sort(key=lambda j: (PRIORITIES.get(j.priority, 1), j.created))
        LOGGER.info("AAC job %s waits: %s", job.title, reason)
        if self.settings.get("notify_no_model") and not self._outage_told:
            self._outage_told = True
            self._tell(job.domain, "AAC has no model available right now; automatic jobs wait until one is back.",
                       "AAC: no model available")
        async_notify(self.hass, "jobs")

    async def _async_run(self, job: Job) -> None:
        waited = dt_util.utcnow() - job.created
        note = ""
        if waited >= timedelta(minutes=float(self.settings.get("postpone_notice", 10))):
            note = (f"Note: this job was postponed by {int(waited.total_seconds() // 60)} minutes because no model "
                    f"was available ({job.reason or 'busy'}). Take that into account (e.g. say it is late).")
        job.status = "running"
        async_notify(self.hass, "jobs")
        try:
            run = await job.start(note)
        except Exception as err:  # noqa: BLE001 - one job must not stop the queue
            LOGGER.warning("AAC job %s failed: %s", job.title, err)
            run = None
        if run is not None and getattr(run, "status", "") == "failed" and NO_MODEL in (run.result or ""):
            self._enqueue(job, "the model went away")  # it vanished between check and call
            return
        job.status = "done"
        self._finish(job)

    def _finish(self, job: Job) -> None:
        if job in self.waiting:
            self.waiting.remove(job)
        self.history = [job, *self.history][:30]
        if not self.waiting:
            self._outage_told = False
        async_notify(self.hass, "jobs")

    async def _tick(self, now: datetime | None = None) -> None:
        now = dt_util.utcnow()
        for job in list(self.waiting):
            if job.status != "queued":
                continue
            if job.due and now >= job.due:
                if job.critical:
                    job.status = "notified"
                    self._tell(job.domain, job.fallback or job.title, f"AAC: {job.title}")
                else:
                    job.status = "expired"
                self._finish(job)
                continue
            if self._model_free(job):
                await self._async_run(job)

    def _tell(self, domain: Domain, text: str, title: str) -> None:
        """Reach the owner without a model: HA notification + the domain's From AAC chat."""
        persistent_notification.async_create(self.hass, text, title=title, notification_id=f"aac_{uuid.uuid4().hex[:6]}")
        domain.chats.add_message(AAC_CHAT_ID, "aac", text, proactive=True, trigger="Without a model")

    def overview(self) -> dict[str, Any]:
        """For the panel."""
        return {"waiting": [j.summary() for j in self.waiting], "history": [j.summary() for j in self.history],
                "settings": self.settings}
