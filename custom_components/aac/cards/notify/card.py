"""The Notify card (docs/v2 page 2): Sends a notification through Home Assistant (your phone's notify service). In the
line: the newest answer."""

from __future__ import annotations

from typing import Any

from custom_components.aac.api import Card, Context, register

VERSION = "1.3.2"  # this card's file (a newer file replaces an older one in <config>/aac/cards)


@register
class Notify(Card):
    """A phone notification."""

    type = "notify"
    title = "Notify"
    category = "Home Assistant"
    help = "Sends a notification through Home Assistant (your phone's notify service). In the line: the newest answer."
    settings_schema = {
        "service": {"type": "text", "title": "Notify service", "default": "notify.notify", "help": "e.g. notify.mobile_app_my_phone"},
        "in_line": {"type": "bool", "title": "In the line: send the newest answer", "default": True},
        "title": {"type": "text", "title": "Title", "default": "AAC"},
    }
    functions = {"send": {"help": "Send a notification.", "args": {"message": {"type": "string", "help": "The text.", "required": True},
                                                                  "title": {"type": "string", "help": "A title."}}}}

    async def _send(self, text: str, title: str | None = None) -> dict[str, Any]:
        domain, _, service = str(self.settings.get("service") or "notify.notify").partition(".")
        if not self.hass.services.has_service(domain, service):
            return {"error": f"There is no service {domain}.{service}"}
        await self.hass.services.async_call(domain, service, {"message": text, "title": title or self.settings.get("title") or "AAC"}, blocking=True)
        return {"sent": True}

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        if self.settings.get("in_line", True) and context.answer.strip() and context.answer.strip().lower() not in ("nothing", "nothing."):
            await self._send(context.answer.strip())
        return None

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "send":
            return await self._send(str(args.get("message") or ""), args.get("title"))
        return await super().call(function, args, context)

