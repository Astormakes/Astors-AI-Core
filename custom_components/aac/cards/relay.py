"""Relay: passes the context on (its round output copies it to every wire) and can add a text where its setting says:
nothing (a pure relay), the start (system prompt, cached by the server), a note before the turn, or after the turn.
It can also add a text to entries picked by tag. It never holds a stream back."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template

from custom_components.aac.api import Card, Context, register

VERSION = "1.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

KIND = {"start": "system", "note": "note", "after": "trailer"}


@register
class Relay(Card):
    """Passes on, adds a text."""

    type = "relay"
    title = "Relay"
    category = "Text"
    help = ("Passes the context on to every wire. With a text it also adds it: at the start (the system prompt, e.g. a "
            "charter), as a note before the turn, or after the turn (read last, e.g. the time). With a dotted wire into "
            "'reads' it starts from what it reads (e.g. a chat's conversation) and puts the arriving lines on top.")
    inputs = {"in": "text", "reads": "read"}
    ports_help = {"in": "a context arrives: the Relay runs",
                  "reads": "dotted: read when a context arrives, never starts anything. Wired from a Chat's 'said': the "
                           "arriving lines go on top of that chat's conversation, and the answer files into that chat",
                  "out": "the context, with its text added"}
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
        "add_tag": {"type": "tag", "title": "Also add text to the newest entry tagged", "default": ""},
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

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        if port != "in":
            return []
        if step and (read := step.read("reads")):  # start from what it reads (a chat's context), the new lines on top
            base, new = read[0], [e for e in context.entries if e["kind"] not in ("system", "signal")]
            context.entries[:] = [*base.entries, *new]
            context.origin, context.chat_id = base.origin, base.chat_id
        position = self.settings.get("position") or "nothing"
        if position in KIND:
            context.put(self.id, KIND[position], self.render(), "text", replace_it=bool(self.settings.get("replace")))
        if (tag := self.settings.get("add_tag")) and (extra := self.settings.get("add_text")):
            for e in context.tagged(tag)[-1:]:  # the newest one (earlier turns stay as they were)
                e["text"] = f"{extra}{e['text']}" if self.settings.get("add_where") == "start" else f"{e['text']}{extra}"
        return None

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "text":
            return self.render()
        return await super().call(function, args, context)
