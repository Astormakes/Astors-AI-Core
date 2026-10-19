"""Requests: AAC asks, the owner decides, outside the chat.

Owner design (2026-10-19): some things AAC may only do once the owner is OK with it
(e.g. seeing or switching more entities). AAC files a request with a reason; it waits
in the panel (with a badge) and as an HA notification until the owner approves all,
some or none of its items. Only then is it applied, by the handler of its kind, and
AAC is told the outcome in "From AAC". Any part of AAC (and later modules) can add a
kind with `register_kind`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .chats import AAC_CHAT_ID
from .runs import async_notify

if TYPE_CHECKING:
    from . import AACConfigEntry

# kind → apply(entry, domain_id, request, approved item ids)
Apply = Callable[[Any, str, dict[str, Any], list[str]], Awaitable[None]]


class Requests:
    """All requests of the entry (they are kept in the AAC config)."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.kinds: dict[str, Apply] = {}

    @property
    def all(self) -> list[dict[str, Any]]:
        """Newest last."""
        return self.entry.runtime_data.store.data["requests"]

    def register_kind(self, kind: str, apply: Apply) -> None:
        """What happens when a request of this kind is approved."""
        self.kinds[kind] = apply

    def pending(self, domain_id: str | None = None) -> list[dict[str, Any]]:
        """Waiting for the owner."""
        return [r for r in self.all if r["status"] == "pending" and (domain_id is None or r["domain"] == domain_id)]

    async def async_file(self, domain_id: str, kind: str, title: str, reason: str,
                         items: list[dict[str, Any]], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """A new request (items: [{id, label, detail}]); the owner is told in the panel and by notification."""
        request = {"id": uuid.uuid4().hex[:10], "domain": domain_id, "kind": kind, "title": title, "reason": reason,
                   "items": items, "payload": payload or {}, "status": "pending",
                   "created": dt_util.utcnow().isoformat(), "decided": None, "approved": []}
        self.all.append(request)
        del self.all[:-100]
        await self.entry.runtime_data.store.async_save()
        persistent_notification.async_create(
            self.hass,
            f"{title}\n\nWhy: {reason}\n\nDecide in the AAC panel (Requests).",
            title="AAC asks for your OK",
            notification_id=f"aac_request_{request['id']}",
        )
        async_notify(self.hass, "requests", domain=domain_id)
        return request

    async def async_decide(self, request_id: str, approve: list[str] | bool) -> dict[str, Any]:
        """approve = True (all), False (none) or the item ids the owner allows."""
        request = next((r for r in self.all if r["id"] == request_id), None)
        if request is None or request["status"] != "pending":
            raise LookupError("No such open request")
        ids = [i["id"] for i in request["items"]]
        approved = ids if approve is True else [] if approve is False else [i for i in approve if i in ids]
        if approved and (apply := self.kinds.get(request["kind"])):
            await apply(self.entry, request["domain"], request, approved)
        request["approved"] = approved
        request["status"] = "approved" if len(approved) == len(ids) else "partly" if approved else "rejected"
        request["decided"] = dt_util.utcnow().isoformat()
        await self.entry.runtime_data.store.async_save()
        persistent_notification.async_dismiss(self.hass, f"aac_request_{request_id}")
        if dom := self.entry.runtime_data.domain(request["domain"]):
            labels = {i["id"]: i["label"] for i in request["items"]}
            outcome = {"approved": "The owner approved", "partly": "The owner approved part of", "rejected": "The owner declined"}[request["status"]]
            text = f"{outcome} my request: {request['title']}"
            if request["status"] == "partly":
                text += " (allowed: " + ", ".join(labels[i] for i in approved) + ")"
            dom.chats.add_message(AAC_CHAT_ID, "aac", text + ".", proactive=False, trigger="Request decided")
        async_notify(self.hass, "requests", domain=request["domain"])
        return request
