"""Relay: passes the message on (its round output copies it to every wire) and can add a text where its setting says:
nothing (a pure relay), the start (system prompt, cached by the server), a note before the turn, or after the turn.
It can also add a text to entries picked by tag. It never holds a stream back."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template

from ..message import Message
from . import Card, register

KIND = {"start": "system", "note": "note", "after": "trailer"}


@register
class Relay(Card):
    """Passes on, adds a text."""

    type = "relay"
    title = "Relay"
    category = "Text"
    help = ("Passes the message on to every wire. With a text it also adds it: at the start (the system prompt, e.g. a "
            "charter), as a note before the turn, or after the turn (read last, e.g. the time).")
    tags = {"text": "its text"}
    settings_schema = {
        "text": {"type": "longtext", "title": "Text", "default": "",
                 "help": "Templates work: {{ now().strftime('%H:%M') }}, {{ states('sensor.x') }}, and other cards' values: "
                    "{{ lists.total }}, {{ calendar.today | join(', ') }} (each card lists its values)."},
        "position": {"type": "select", "title": "Where", "default": "nothing",
                     "options": [["nothing", "nothing (only pass on)"], ["start", "start (system prompt)"],
                                 ["note", "a note before the turn"], ["after", "after the turn (read last)"]]},
        "replace": {"type": "bool", "title": "Replace", "default": True,
                    "help": "Ticked: one entry, replaced each time. Unticked: a new one each time. At the start keep it "
                            "ticked, after the turn unticked: then the conversation only grows at the end (the server's "
                            "cache keeps working, answers stay fast)."},
        "add_tag": {"type": "tag", "title": "Also add text to entries tagged", "default": ""},
        "add_where": {"type": "select", "title": "… at", "default": "end", "options": [["start", "their start"], ["end", "their end"]]},
        "add_text": {"type": "text", "title": "… this text", "default": ""},
    }
    functions = {"text": {"help": "This card's text (e.g. the time from a Relay with the time)."}}

    def render(self) -> str:
        """Its text through HA's template engine (an error shows in the text)."""
        text = (self.settings.get("text") or "").strip()
        if "{" not in text:
            return text
        try:
            return str(Template(text, self.hass).async_render(self.domain.template_values(), parse_result=False)).strip()
        except TemplateError as err:
            return f"{text}\n[template error: {err}]"

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        position = self.settings.get("position") or "nothing"
        if position in KIND:
            message.put(self.id, KIND[position], self.render(), "text", replace_it=bool(self.settings.get("replace")))
        if (tag := self.settings.get("add_tag")) and (extra := self.settings.get("add_text")):
            for e in message.tagged(tag):
                e["text"] = f"{extra}{e['text']}" if self.settings.get("add_where") == "start" else f"{e['text']}{extra}"
        return None

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
        if function == "text":
            return self.render()
        return await super().call(function, args, message)
