"""Card editor: lets a model see and change the setup (cards and wires) of its domain, with your permission settings;
every change can be undone (Cards → Undo)."""

from __future__ import annotations

import json

from typing import Any

from custom_components.aac.api import CARD_TYPES, RULES, Card, Context, register

VERSION = "2.1.1"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)


SHORT = 80  # longer settings are shortened in the list of cards (characters)


def setup_help(kind: Any) -> str:
    """A card type's setup help, from what every card declares (its help, ports, settings, functions): the same as
    the ? panel shows."""
    t = kind.describe()
    ports = t.get("ports_help") or {}
    lines = [f"{t['title']} (type {t['type']}, version {t['version']}): {t['help']}"]
    for title, found in (("Inputs", t["inputs"]), ("Outputs", t["outputs"])):
        if found:
            lines.append(f"{title}:")
            lines += [f"- {p} ({k}){f': {ports[p]}' if ports.get(p) else ''}" for p, k in found.items()]
    if ports.get("*"):
        lines.append(f"Other ports: {ports['*']}")
    if t["settings"]:
        lines.append("Settings:")
        for key, f in t["settings"].items():
            lines.append(f"- {key} ({f.get('title') or key}, default {json.dumps(f.get('default'), ensure_ascii=False)[:200]})"
                         + (f": {f['help']}" if f.get("help") else ""))
    if t["functions"]:
        lines.append("Functions:")
        lines += [f"- {n}({', '.join(a + ('' if x.get('required') else '?') for a, x in (f.get('args') or {}).items())}): {f.get('help', '')}"
                  for n, f in t["functions"].items()]
    return "\n".join(lines)

@register
class Builder(Card):
    """A model builds."""

    type = "builder"
    title = "Card editor"
    category = "Other"
    help = ("Wired from a model and back: the model can look at and change the cards and wires of this domain (e.g. "
            "\"set up a reminder card\"). Every change can be undone on the Cards page. While a wire is red (the storm "
            "stop blocked it: too many contexts went through it at once), every answer of this card says so, and the "
            "model can unblock it once the wiring is fixed.")
    needs = {"setup"}  # read: look at the cards and wires; full: add, change, wire (never a permission: the owner's)
    settings_schema = {
        "may_remove": {"type": "bool", "title": "May remove cards", "default": False, "permission": True},
    }
    functions = {
        "rules": {"help": "How AAC works: the few rules every card follows (entries, contexts, when a card acts and "
                          "sends, calls, stop). Read them before building or wiring."},
        "cards": {"help": "An overview of this domain: each card's id, type, name, inputs and outputs and settings (long "
                          "ones shortened: see card), and the wires."},
        "card": {"help": "One card with all its settings.", "args": {"card": {"type": "string", "help": "Its id.", "required": True}}},
        "types": {"help": "The card types that can be added, one line each (help gives one type in full)."},
        "help": {"help": "Everything about one card type, as its ? help shows it: what it does, its inputs and outputs, "
                         "its settings, its functions (a Relay's: how to write its script). Read it before adding or "
                         "setting up a card of that type.",
                 "args": {"type": {"type": "string", "help": "A type from types (e.g. relay).", "required": True}}},
        "add_card": {"help": "Add a card.", "args": {"type": {"type": "string", "help": "A type from types.", "required": True},
                                                     "name": {"type": "string", "help": "Its name."},
                                                     "settings": {"type": "object", "help": "Its settings."}}},
        "change_card": {"help": "Change a card's name or settings.", "args": {"card": {"type": "string", "help": "Its id.", "required": True},
                                                                            "name": {"type": "string", "help": "New name."},
                                                                            "settings": {"type": "object", "help": "Settings to change."}}},
        "remove_card": {"help": "Remove a card.", "args": {"card": {"type": "string", "help": "Its id.", "required": True}}},
        "wire": {"help": "Wire an output to an input ('card.port').", "args": {"from": {"type": "string", "help": "e.g. chat.said", "required": True},
                                                                              "to": {"type": "string", "help": "e.g. model.in", "required": True}}},
        "unwire": {"help": "Remove a wire.", "args": {"from": {"type": "string", "help": "card.port", "required": True},
                                                      "to": {"type": "string", "help": "card.port", "required": True}}},
        "unblock": {"help": "Let a red wire carry again (the storm stop blocked it: more contexts went through it at once "
                              "than a turn sends, the wiring loops). Fix the loop first.",
                    "args": {"from": {"type": "string", "help": "card.port", "required": True},
                             "to": {"type": "string", "help": "card.port", "required": True}}},
    }

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        """Every answer says which wires are red right now (when it may look at the setup); what it may not do is told
        as an error (the interface refuses it, not this card)."""
        try:
            result = await self._call(function, args, context)
        except (PermissionError, ValueError) as err:
            result = {"error": str(err)}
        if self.may("setup", "read") and (red := self.read_setup()["red"]):
            return {**(result if isinstance(result, dict) else {"result": result}), "red_wires": red,
                    "note": "These wires are blocked (red): the storm stop found a loop there. Tell the owner; fix the "
                            "wiring, then unblock them."}
        return result

    async def _call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "rules":
            return RULES
        if function == "types":
            return [f"{t.type} ({t.title}): {t.help}" for t in CARD_TYPES.values()]
        if function == "help":
            kind = CARD_TYPES.get(str(args.get("type") or ""))
            return setup_help(kind) if kind else {"error": f"no type {args.get('type')} (see types)"}
        if function == "cards":  # an overview that fits: long settings shortened (`card` gives one card in full)
            setup = self.read_setup()
            def short(v: Any) -> Any:
                if isinstance(v, (dict, list)) and len(json.dumps(v)) > SHORT:
                    return f"({len(v)} entries: see card)"
                return v[:SHORT] + "…" if isinstance(v, str) and len(v) > SHORT else v
            red = set(setup["red"])
            return {"count": len(setup["cards"]), "cards": [{"id": i, "type": c["type"], "name": c["name"], "in": list(c["inputs"]),
                                                             "out": list(c["outputs"]), "settings": {k: short(v) for k, v in c["settings"].items()}}
                                                            for i, c in setup["cards"].items()],
                    "wires": [f"{w['from']} -> {w['to']}" + (" (red: blocked)" if f"{w['from']} -> {w['to']}" in red else "")
                              for w in setup["wires"]]}
        if function == "card":
            found = self.read_setup()["cards"].get(str(args.get("card") or ""))
            return {"id": args.get("card"), **found} if found else {"error": "no such card (see cards)"}
        if function == "remove_card":
            if not self.settings.get("may_remove"):
                return {"error": "Removing cards is not allowed (the Card editor card's settings)."}
            await self.remove_card(str(args.get("card")))
            return {"removed": args.get("card")}
        if function == "add_card":
            return await self.add_card(str(args.get("type")), str(args.get("name") or ""), args.get("settings") or {})
        if function == "change_card":
            return await self.change_card(str(args.get("card")), args.get("name"), args.get("settings"))
        if function == "unblock":
            return {"unblocked": True} if await self.unblock(str(args.get("from")), str(args.get("to"))) else {"error": "that wire is not red"}
        if function in ("wire", "unwire"):
            wire = {"from": str(args.get("from")), "to": str(args.get("to"))}
            wires = [w for w in self.read_setup()["wires"] if w != wire] + ([wire] if function == "wire" else [])
            now = await self.set_wires(wires)
            return {"wired": wire in now} if function == "wire" else {"unwired": wire not in now}
        return await super().call(function, args, context)
