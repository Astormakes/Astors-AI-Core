"""Conditional: "if … then …" without a model. Rules are checked from the top; the first that matches acts (or goes
on checking). If: which entries (by tag, kind or card; the newest / all) and a test. Then: edit the text, add a tag,
call a function of a wired card, send stop or a signal, pick an output."""

from __future__ import annotations

import re
from typing import Any

from ..message import Message, entry
from . import Card, register

TESTS = ["contains", "is", "starts", "ends", "matches", "has_tag", "count_more", "empty", "always"]
ACTIONS = ["replace", "insert", "remove", "remove_entries", "cut", "split", "join", "case", "trim", "tag", "call",
           "stop", "signal", "output"]


def _select(message: Message, rule: dict[str, Any]) -> list[dict[str, Any]]:
    pick = rule.get("select") or {}
    by, value = pick.get("by", "kind"), pick.get("value", "said")
    found = [e for e in message.entries if e["kind"] != "signal" and (
        by == "any" or (by == "tag" and value in e["tags"]) or (by == "kind" and e["kind"] == value)
        or (by == "card" and e["by"] == value))]
    return found[-1:] if pick.get("which", "newest") == "newest" else found


def _test(entries: list[dict[str, Any]], rule: dict[str, Any]) -> bool:
    test, value = rule.get("test", "always"), str(rule.get("value", ""))
    if test == "always":
        return True
    if test == "empty":
        return not entries or all(not e["text"].strip() for e in entries)
    if test == "count_more":
        return len(entries) > int(value or 0)
    low = value.lower()
    for e in entries:
        text = e["text"]
        if ((test == "contains" and low in text.lower()) or (test == "is" and text.strip().lower() == low)
                or (test == "starts" and text.lower().startswith(low)) or (test == "ends" and text.lower().endswith(low))
                or (test == "has_tag" and value in e["tags"])):
            return True
        if test == "matches":
            try:
                if re.search(value, text, re.IGNORECASE):
                    return True
            except re.error:
                return False
    return False


@register
class Conditional(Card):
    """If … then …"""

    type = "conditional"
    title = "Conditional"
    category = "Logic"
    help = ("If … then … without a model: checks the message, edits it (replace, cut, split …), tags it, calls a function "
            "of a card wired to it, stops, or sends it to one of its outputs.")
    tags = {"split": "entries made by splitting", "call": "its function calls", "match": "which rule matched",
            "stop": "a stop it sent"}
    settings_schema = {
        "outputs": {"type": "list", "title": "Outputs", "default": [],
                    "help": "Names of extra outputs a rule can send the message to ('out' is always there)."},
        "rules": {"type": "rules", "title": "Rules", "default": [], "help": "Checked from the top; the first that matches acts."},
    }

    def ports(self) -> tuple[dict[str, str], dict[str, str], set[str]]:
        names = [n for n in self.settings.get("outputs") or [] if n and n != "out"]
        return {"in": "text"}, {"out": "text", **{n: "text" for n in names}}, {"out", *names}

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        chosen: str | None = None
        for n, rule in enumerate(self.settings.get("rules") or []):
            entries = _select(message, rule)
            if not _test(entries, rule):
                continue
            message.add(self.id, "signal", f"rule {n + 1}: {rule.get('name') or ''}".strip(), ["match"])  # the trace
            for action in rule.get("actions") or []:
                chosen = await self._do(action, entries, message, step) or chosen
            if not rule.get("go_on"):
                break
        # its tools (cards wired from it and back) only take calls; the message goes on to the others
        tools = {c.id for c in step.tool_cards()}
        port_out = chosen if chosen in self.ports()[1] else "out"
        return [(port_out, c.id) for c, _ in step.targets(port_out) if c.id not in tools]

    async def _do(self, action: dict[str, Any], entries: list[dict[str, Any]], message: Message, step) -> str | None:
        do, a, b = action.get("do"), str(action.get("a", "")), str(action.get("b", ""))
        for e in entries:
            if do == "replace" and a:
                e["text"] = re.sub(re.escape(a), lambda _: b, e["text"], flags=re.IGNORECASE)
            elif do == "insert":
                e["text"] = f"{a}{e['text']}" if b == "start" else f"{e['text']}{a}"
            elif do == "remove" and a:
                e["text"] = re.sub(re.escape(a), "", e["text"], flags=re.IGNORECASE)
            elif do == "cut":
                text = e["text"]
                start = text.lower().find(a.lower()) + len(a) if a and a.lower() in text.lower() else 0
                end = text.lower().find(b.lower(), start) if b and b.lower() in text.lower()[start:] else len(text)
                e["text"] = text[start:end].strip()
            elif do == "case":
                e["text"] = {"upper": e["text"].upper(), "lower": e["text"].lower(), "title": e["text"].title()}.get(a, e["text"])
            elif do == "trim":
                e["text"] = e["text"].strip()
            elif do == "tag" and a:
                tag = a if "." in a else f"user.{a}"
                e["tags"] = [*e["tags"], tag] if tag not in e["tags"] else e["tags"]
        if do == "remove_entries":
            message.entries[:] = [e for e in message.entries if e not in entries]
        elif do == "split" and a:
            for e in entries:
                parts = [p.strip() for p in e["text"].split(a) if p.strip()]
                at = message.entries.index(e)
                message.entries[at:at + 1] = [{**entry(self.id, e["kind"], p, [*e["tags"], self.tag("split")])} for p in parts]
        elif do == "join" and len(entries) > 1:
            first = entries[0]
            first["text"] = (b or "\n").join(e["text"] for e in entries)
            message.entries[:] = [e for e in message.entries if e is first or e not in entries]
        elif do == "call":
            card = self.domain.cards.get(action.get("card", ""))
            if card and card in step.tool_cards():  # only cards wired from it and back (like a model's tools)
                text = entries[-1]["text"] if entries else ""
                args = {k: (v.replace("{text}", text) if isinstance(v, str) else v) for k, v in (action.get("args") or {}).items()}
                result = await card.call(action.get("function", ""), args, message)
                message.add(self.id, "call", "", ["call"], function=f"{card.id}.{action.get('function')}", args=args)
                message.add(card.id, "result", str(result)[:4000])
        elif do == "stop":
            message.add(self.id, "signal", "stop", [self.tag("stop")])
        elif do == "signal" and a:
            message.add(self.id, "signal", a, [a if "." in a else f"user.{a}"])
        elif do == "output" and a:
            return a
        return None
