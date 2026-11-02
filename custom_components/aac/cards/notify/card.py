"""The Notify card (docs/v2 page 2): Sends a notification through Home Assistant (your phone's notify service). In the
line: the newest answer."""

from __future__ import annotations

from typing import Any

from custom_components.aac.api import Card, Context, register

VERSION = "2.1.0"  # this card's file (a newer file replaces an older one in <config>/aac/cards)


@register
class Notify(Card):
    """A phone notification."""

    type = "notify"
    title = "Notify"
    category = "Home Assistant"
    help = ("Sends a notification through Home Assistant (your phone's notify service). In the line: the newest entry's "
            "text, then the context goes on.")
    needs = {"notify"}
    settings_schema = {
        "service": {"type": "text", "title": "Notify service", "default": "notify.notify", "help": "e.g. notify.mobile_app_my_phone"},
        "in_line": {"type": "bool", "title": "In the line: send the newest entry's text", "default": True},
        "title": {"type": "text", "title": "Title", "default": "AAC"},
    }
    functions = {"send": {"help": "Send a notification.", "args": {"message": {"type": "string", "help": "The text.", "required": True},
                                                                  "title": {"type": "string", "help": "A title."}}}}

    async def _send(self, text: str, title: str | None = None) -> dict[str, Any]:
        service = str(self.settings.get("service") or "notify.notify").removeprefix("notify.")
        try:
            await self.notification(service, text, title or self.settings.get("title") or "AAC")
        except (PermissionError, ValueError) as err:
            return {"error": str(err)}
        return {"sent": True}

    async def handle(self, context: Context, port: str) -> None:
        newest = context.newest
        if not self.settings.get("in_line", True) or context.streaming or context.waiting or newest is None or not newest["text"].strip():
            return
        await self._send(newest["text"].strip())
        await self.send(context, "out")

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "send":
            return await self._send(str(args.get("message") or ""), args.get("title"))
        return await super().call(function, args, context)

