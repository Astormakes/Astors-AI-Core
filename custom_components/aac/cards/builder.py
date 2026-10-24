"""Builder: lets a model see and change the setup (cards and wires) of its domain, with your permission settings;
every change can be undone (Nodes → Undo)."""

from __future__ import annotations

from typing import Any

from custom_components.aac.api import CARD_TYPES, Card, Context, register

VERSION = "1.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)


@register
class Builder(Card):
    """A model builds."""

    type = "builder"
    title = "Builder"
    category = "Other"
    help = ("Wired from a model and back: the model can look at and change the cards and wires of this domain (e.g. "
            "\"set up a reminder card\"). Every change can be undone in Nodes.")
    settings_schema = {
        "may_change": {"type": "bool", "title": "May add cards, change settings and wires", "default": True},
        "may_remove": {"type": "bool", "title": "May remove cards", "default": False},
    }
    functions = {
        "cards": {"help": "The cards of this domain: id, type, name, settings, inputs and outputs, and the wires."},
        "types": {"help": "The card types that can be added, with what they do and their settings."},
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
    }

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        domain = self.domain
        if function == "cards":
            return {"cards": [c.summary() for c in domain.cards.values()], "wires": domain.wires}
        if function == "types":
            return [{"type": t.type, "title": t.title, "help": t.help, "settings": {k: f.get("title") for k, f in t.settings_schema.items()},
                     "inputs": t.inputs, "outputs": t.outputs} for t in CARD_TYPES.values()]
        if function == "remove_card":
            if not self.settings.get("may_remove"):
                return {"error": "Removing cards is not allowed (the Builder card's settings)."}
            await domain.remove_card(str(args.get("card")))
            return {"removed": args.get("card")}
        if not self.settings.get("may_change", True):
            return {"error": "Changing the setup is not allowed (the Builder card's settings)."}
        if function == "add_card":
            card = await domain.add_card(str(args.get("type")), str(args.get("name") or ""), args.get("settings") or {})
            return card.summary()
        if function == "change_card":
            if args.get("card") not in domain.cards:
                return {"error": "no such card"}
            card = await domain.update_card(str(args["card"]), args.get("name"), args.get("settings"))
            return card.summary()
        if function in ("wire", "unwire"):
            wire = {"from": str(args.get("from")), "to": str(args.get("to"))}
            wires = [w for w in domain.wires if w != wire] + ([wire] if function == "wire" else [])
            domain.set_wires(wires)
            return {"wired" if function == "wire" else "unwired": wire in domain.wires if function == "wire" else wire not in domain.wires}
        return await super().call(function, args, context)
