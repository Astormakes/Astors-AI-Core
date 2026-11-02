"""The Model servers card (docs/v2 page 2): The servers behind AAC's models and what they offer now. A llama-server in
router mode serves many models and loads one at a time: a model or a Relay's script can see them and switch (the same
as the Models page)."""

from __future__ import annotations

from typing import Any

from custom_components.aac.api import Card, Context, ModelError, register

VERSION = "1.4.1"  # this card's file (a newer file replaces an older one in <config>/aac/cards)


@register
class ModelServers(Card):
    """What the model servers offer, and switching it."""

    type = "model_servers"
    title = "Model servers"
    category = "Models"
    help = ("The servers behind AAC's models and what they offer now. A llama-server in router mode serves many models "
            "and loads one at a time: a model or a Relay's script can see them and switch (the same as the Models page).")
    needs = {"servers"}  # read: see them; full: bench, use, load, unload
    functions = {
        "servers": {"help": "Every model server: AAC's models on it (and which server model each uses), and what it offers "
                            "with each one's state (loaded, unloaded, loading, downloading)."},
        "use": {"help": "Let one of AAC's models use another model of its server from now on (it is loaded at once, the "
                        "one before is unloaded).",
                "args": {"aac_model": {"type": "string", "help": "AAC's model, by its name (as servers lists it).", "required": True},
                         "model": {"type": "string", "help": "The server's model (as servers lists it).", "required": True}}},
        "load": {"help": "Load a model on the server of one of AAC's models (without switching what that one uses).",
                 "args": {"aac_model": {"type": "string", "help": "AAC's model whose server it is.", "required": True},
                          "model": {"type": "string", "help": "The server's model.", "required": True}}},
        "bench": {"help": "Measure how fast a model of a server reads and writes (a short fixed test, ~10–60 s; it is "
                          "loaded first if needed). The result is kept and shown on the Models page.",
                  "args": {"aac_model": {"type": "string", "help": "AAC's model whose server it is.", "required": True},
                           "model": {"type": "string", "help": "The server's model.", "required": True}}},
        "unload": {"help": "Unload a model on the server of one of AAC's models (frees its memory).",
                   "args": {"aac_model": {"type": "string", "help": "AAC's model whose server it is.", "required": True},
                            "model": {"type": "string", "help": "The server's model.", "required": True}}},
    }

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function not in self.functions:
            return await super().call(function, args, context)
        try:
            if function == "servers":
                return [{"address": s["address"], "aac_models": [f"{u['name']} (uses {u['model']})" for u in s["uses"]],
                         "offers": [{"model": m["id"], "state": m["status"], **({"progress": f"{m['progress']} %"} if "progress" in m else {})}
                                    for m in s["models"]] if s.get("models") else (s.get("error") or "one fixed model (no router)")}
                        for s in await self.servers()]
            return await self.server(function, str(args.get("aac_model") or ""), str(args.get("model") or ""))
        except (PermissionError, ValueError, ModelError) as err:
            return {"error": str(err)}


