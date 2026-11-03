"""Asking the owner (docs/v2 page 6): a card whose thing is at `ask` waits for a yes before every action; and a card
(a model through it) may ask for more than it has, with a reason.

An ask shows in every open panel (Allow / Deny) and, when AAC's settings name one, as a phone notification with the
same two buttons (Home Assistant's companion app); the first answer counts, no answer in time is a no. A request waits
in the domain until the owner allows all of it, some or none (the Permissions page; Home Assistant notifies).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, callback

from .context import new_id
from .permissions import LEVELS, rank

if TYPE_CHECKING:
    from .cards import Card
    from .core import Core

ASK_WAIT = 120  # seconds an ask waits for the owner (then it is a no)
PHONE_ACTION = "mobile_app_notification_action"  # the companion app's event when a notification's button is pressed


class Asks:
    """The asks waiting for the owner now (not kept over a restart: a card waiting is gone then too)."""

    def __init__(self, core: Core) -> None:
        self.core, self.hass = core, core.hass
        self.waiting: dict[str, dict[str, Any]] = {}  # id → {id, domain, card, name, thing, action, since}
        self._answers: dict[str, asyncio.Future] = {}
        self._unlisten = self.hass.bus.async_listen(PHONE_ACTION, self._phone)

    def unload(self) -> None:
        self._unlisten()
        for future in self._answers.values():
            if not future.done():
                future.set_result(False)

    async def ask(self, card: Card, thing: str, what: str) -> bool:
        """Wait for the owner's yes to one action; True: allowed."""
        ask_id = new_id()
        item = {"id": ask_id, "domain": card.domain.id, "card": card.id, "name": card.name, "thing": thing, "action": what,
                "since": self.hass.loop.time()}
        self.waiting[ask_id] = item
        self._answers[ask_id] = future = self.hass.loop.create_future()
        card.log(f"Asks the owner: {what}", "info", what="ask")
        self.core.notify("ask", **item)
        await self._to_phone(item)
        try:
            yes = await asyncio.wait_for(asyncio.shield(future), ASK_WAIT)
        except TimeoutError:
            yes = False
        finally:
            self.waiting.pop(ask_id, None)
            self._answers.pop(ask_id, None)
            self.core.notify("asked", id=ask_id)
            await self._clear_phone(ask_id)
        card.log(f"The owner {'allowed' if yes else 'did not allow'}: {what}", "info" if yes else "warning", what="ask")
        return yes

    def answer(self, ask_id: str, yes: bool) -> bool:
        """The owner's answer (a panel, the phone); False: nothing waits under that id (answered already, too late)."""
        future = self._answers.get(ask_id)
        if future is None or future.done():
            return False
        future.set_result(bool(yes))
        return True

    @callback
    def _phone(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        for word, yes in (("AAC_YES_", True), ("AAC_NO_", False)):
            if action.startswith(word):
                self.answer(action[len(word):], yes)

    def _service(self) -> str | None:
        found = str((self.core.data.get("settings") or {}).get("ask_notify") or "").removeprefix("notify.")
        return found if found and self.hass.services.has_service("notify", found) else None

    async def _to_phone(self, item: dict[str, Any]) -> None:
        if (service := self._service()) is None:
            return
        try:
            await self.hass.services.async_call("notify", service, {
                "title": f"AAC: {item['name']} asks", "message": item["action"],
                "data": {"tag": f"aac_ask_{item['id']}", "actions": [{"action": f"AAC_YES_{item['id']}", "title": "Allow"},
                                                                    {"action": f"AAC_NO_{item['id']}", "title": "Deny"}]}},
                blocking=True)
        except Exception as err:  # noqa: BLE001 - the panel still asks
            self.core.log("asks", "warning", f"The ask could not go to the phone: {err}")

    async def _clear_phone(self, ask_id: str) -> None:
        if (service := self._service()) is None:
            return
        with contextlib.suppress(Exception):
            await self.hass.services.async_call("notify", service, {"message": "clear_notification",
                                                                    "data": {"tag": f"aac_ask_{ask_id}"}}, blocking=True)


def request_note(core: Any, domain: Any, request: dict[str, Any]) -> None:
    """Home Assistant tells the owner a request waits."""
    core.note(f"aac_request_{request['id']}", f"AAC ({domain.name}): a card asks for more",
              f"{request['name']} asks for {', '.join(f'{t} ({lvl})' for t, lvl in request['things'].items())}: "
              f"{request['reason']}\n\nAnswer on AAC's Permissions page (the domain menu).")


def request_done(core: Any, request_id: str) -> None:
    core.unnote(f"aac_request_{request_id}")


def asked_for(levels: dict[str, str]) -> dict[str, str]:
    """A request's things with a level each (only real levels above hidden)."""
    return {str(t): lvl for t, lvl in levels.items() if lvl in LEVELS and rank(lvl) > 0}
