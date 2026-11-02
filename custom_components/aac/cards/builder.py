"""Card editor: lets a model see and change the setup (cards and wires) of its domain, with your permission settings;
every change can be undone (Cards → Undo)."""

from __future__ import annotations

import json

from typing import Any

from custom_components.aac.api import CARD_TYPES, RULES, Card, Context, register

VERSION = "2.1.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)


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

CHANGES = {"add_card", "change_card", "remove_card", "wire", "unwire", "unblock"}

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
        """Every answer says which wires are red right now (the core lists them; a wire unblocked is not mentioned)."""
        result = await self._call(function, args, context)
        if red := self.domain.red:
            wires = [f"{r['from']} -> {r['to']}" for r in red]
            return {**(result if isinstance(result, dict) else {"result": result}), "red_wires": wires,
                    "note": "These wires are blocked (red): the storm stop found a loop there. Tell the owner; fix the "
                            "wiring, then unblock them."}
        return result

    async def _call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        domain = self.domain
        if function == "rules":
            return RULES
        if function == "cards":  # an overview that fits: long settings shortened (`card` gives one card in full)
            if not self.may("setup", "read"):
                return self._refused("read")
            def short(v: Any) -> Any:
                if isinstance(v, (dict, list)) and len(json.dumps(v)) > SHORT:
                    return f"({len(v)} entries: see card)"
                return v[:SHORT] + "…" if isinstance(v, str) and len(v) > SHORT else v
            return {"count": len(domain.cards), "cards": [{"id": c.id, "type": c.type, "name": c.name, "in": list(c.ports()[0]), "out": list(c.ports()[1]),
                               "settings": {k: short(v) for k, v in c.settings.items()}} for c in domain.cards.values()],
                    "wires": [f"{w['from']} -> {w['to']}" + (" (red: blocked)" if domain.is_red(w["from"], w["to"]) else "")
                              for w in domain.wires]}
        if function == "card":
            if not self.may("setup", "read"):
                return self._refused("read")
            card = domain.cards.get(str(args.get("card") or ""))
            return card.summary() if card else {"error": "no such card (see cards)"}
        if function == "types":
            return [f"{t.type} ({t.title}): {t.help}" for t in CARD_TYPES.values()]
        if function == "help":
            kind = CARD_TYPES.get(str(args.get("type") or ""))
            return setup_help(kind) if kind else {"error": f"no type {args.get('type')} (see types)"}
        if function in CHANGES and not self.may("setup", "full"):
            return self._refused("full")
        if function == "remove_card":
            if not self.settings.get("may_remove"):
                return {"error": "Removing cards is not allowed (the Card editor card's settings)."}
            await domain.remove_card(str(args.get("card")))
            return {"removed": args.get("card")}
        if function == "add_card":
            kind = CARD_TYPES.get(str(args.get("type")))
            if kind is not None and (owners := _permissions(kind, args.get("settings") or {})):
                return self._owners(owners)
            card = await domain.add_card(str(args.get("type")), str(args.get("name") or ""), args.get("settings") or {})
            return card.summary()
        if function == "change_card":
            if (card := domain.cards.get(str(args.get("card")))) is None:
                return {"error": "no such card"}
            if owners := _permissions(type(card), args.get("settings") or {}, card.settings):
                return self._owners(owners)
            card = await domain.update_card(str(args["card"]), args.get("name"), args.get("settings"))
            return card.summary()
        if function == "unblock":
            a, b = str(args.get("from")), str(args.get("to"))
            return {"unblocked": domain.unblock(a, b, by=f"{self.name}, for a model")} if domain.is_red(a, b) else {"error": "that wire is not red"}
        if function in ("wire", "unwire"):
            wire = {"from": str(args.get("from")), "to": str(args.get("to"))}
            wires = [w for w in domain.wires if w != wire] + ([wire] if function == "wire" else [])
            domain.set_wires(wires)
            return {"wired" if function == "wire" else "unwired": wire in domain.wires if function == "wire" else wire not in domain.wires}
        return await super().call(function, args, context)

    def _refused(self, level: str) -> dict[str, str]:
        return {"error": f"{self.name} may not {'look at' if level == 'read' else 'change'} the setup (setup at "
                         f"{self.level('setup')}): the owner allows it on the Permissions page"}

    @staticmethod
    def _owners(fields: list[str]) -> dict[str, str]:
        return {"error": f"{', '.join(fields)}: permissions are the owner's to set (the Permissions page), not a model's"}


def _permissions(kind: type[Card], settings: dict[str, Any], now: dict[str, Any] | None = None) -> list[str]:
    """The settings a change would set that are permissions (fields marked so), unless they stay as they are."""
    return [k for k, v in settings.items() if (kind.settings_schema.get(k) or {}).get("permission")
            and (now is None or now.get(k) != v)]
