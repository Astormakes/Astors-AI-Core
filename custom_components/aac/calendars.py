"""AAC's calendar (spec/cards.md "The calendar"; v1's design): the core keeps no entries, only who offers them. A card
offers its entries with `core.calendar.offer(card, query, create=None, change=None, remove=None)`; asking collects from
every card at that moment, so nothing is copied and a removed card's entries are gone. Repeating is the card's job: it
gives the occurrences of the period asked. Home Assistant's calendar (calendar.aac_calendar), the Calendar card and
models ask here, never a card.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .cards import Card
    from .core import Core

Query = Callable[[datetime, datetime], list[dict[str, Any]]]  # → [{key, summary, start, end?, description?}]


def parse(value: Any) -> date | datetime | None:
    """A date ("YYYY-MM-DD") or a time (local) from text."""
    if not value:
        return None
    if isinstance(value, datetime):
        return dt_util.as_local(value)
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    when = dt_util.parse_datetime(text)
    return dt_util.as_local(when) if when else None


def moment(value: date | datetime) -> datetime:
    """A date as the start of that day (local)."""
    return value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time(), dt_util.get_default_time_zone())


class Calendars:
    """Who offers calendar entries (the cards of the running domain)."""

    def __init__(self, core: Core) -> None:
        """Initialize."""
        self.core = core
        self._offers: dict[Card, dict[str, Any]] = {}

    def offer(self, card: Card, query: Query, create: Callable[[dict[str, Any]], Awaitable[str]] | None = None,
              change: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
              remove: Callable[[str], Awaitable[None]] | None = None) -> None:
        """A card's entries are on AAC's calendar (until it unloads). query(start, end) gives the entries of a period,
        each with its own `key`; create(entry) stores a new one and gives its key; change(key, changes) and
        remove(key) work on one. What a card leaves out it does not offer."""
        self._offers[card] = {"query": query, "create": create, "change": change, "remove": remove}

    def forget(self, card: Card) -> None:
        """The card unloaded: its entries are no longer on the calendar."""
        self._offers.pop(card, None)

    def entries(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Every card's entries between two times, by start: {uid ("<card>:<key>"), summary, start, end, description,
        source (the card), all_day, start_v, end_v (dates or times), can (change, remove)}. A card that fails is skipped."""
        out = []
        for card, offer in list(self._offers.items()):
            try:
                found = offer["query"](start, end)
            except Exception as err:  # noqa: BLE001 - one card must not hide the others
                card.log(f"Its calendar entries failed: {err!r}", "error", what="calendar")
                continue
            for e in found:
                s = parse(e.get("start"))
                if s is None:
                    continue
                all_day = not isinstance(s, datetime)
                en = parse(e.get("end")) or (s + (timedelta(days=1) if all_day else timedelta(hours=1)))
                if moment(en) <= moment(s):
                    en = s + (timedelta(days=1) if all_day else timedelta(minutes=30))
                if moment(en) > start and moment(s) < end:
                    out.append({"uid": f"{card.id}:{e['key']}", "summary": str(e.get("summary") or ""), "start": e.get("start"),
                                "end": e.get("end"), "description": e.get("description") or "", "source": card.id,
                                "all_day": all_day, "start_v": s, "end_v": en, "can": [x for x in ("change", "remove") if offer[x]]})
        return sorted(out, key=lambda e: moment(e["start_v"]))

    async def add(self, entry: dict[str, Any]) -> str:
        """A new entry ({summary, start, end?, description?}) into the first card that takes new ones; its uid."""
        for card, offer in self._offers.items():
            if offer["create"]:
                key = await offer["create"](entry)
                self.core.notify("calendar")
                return f"{card.id}:{key}"
        raise ValueError("No card takes new calendar entries (a Calendar card does)")

    def _find(self, uid: str, what: str) -> tuple[Callable[..., Awaitable[None]], str]:
        card_id, _, key = str(uid).partition(":")
        for card, offer in self._offers.items():
            if card.id == card_id:
                if not offer[what]:
                    raise ValueError(f"{card.name}'s entries cannot be {'changed' if what == 'change' else 'removed'} here (do it on its page)")
                return offer[what], key
        raise ValueError(f"No calendar entry {uid}")

    async def change(self, uid: str, changes: dict[str, Any]) -> None:
        """Change an entry where it lives (summary, start, end, description)."""
        do, key = self._find(uid, "change")
        await do(key, {k: v for k, v in changes.items() if k in ("summary", "start", "end", "description") and v is not None})
        self.core.notify("calendar")

    async def remove(self, uid: str) -> None:
        """Remove an entry where it lives."""
        do, key = self._find(uid, "remove")
        await do(key)
        self.core.notify("calendar")
