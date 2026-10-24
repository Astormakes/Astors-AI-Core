"""Decide: a model picks one of the named outputs (e.g. "for AAC" / "not for AAC"); the context goes on there."""

from __future__ import annotations

from typing import Any

from ..const import LOGGER
from ..context import Context
from ..models import Client, ModelError
from . import Card, register
from .pool import pool_ids

LOOK_BACK = 8  # entries the deciding model reads


@register
class Decide(Card):
    """A model picks an output."""

    type = "decide"
    title = "Decide"
    category = "Logic"
    help = ("A model reads the newest entries and picks one of the outputs you name (with what each is for); the context "
            "goes on there. Wire a Pool into its dotted input (a fast small model is enough).")
    inputs = {"in": "text", "pool": "pool"}
    tags = {"choice": "what it picked"}
    settings_schema = {
        "outputs": {"type": "outputs", "title": "Outputs", "default": [{"name": "yes", "description": "it applies"},
                                                                       {"name": "no", "description": "anything else"}],
                    "help": "One per line: name: what it is for."},
        "question": {"type": "longtext", "title": "What to decide", "default": "Is the newest line meant for AAC?"},
    }

    def ports(self) -> tuple[dict[str, str], dict[str, str], set[str]]:
        names = [o["name"] for o in self.settings.get("outputs") or [] if o.get("name")]
        return dict(self.inputs), {n: "text" for n in names}, set(names)

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        if port != "in":
            return []
        outs = [o for o in self.settings.get("outputs") or [] if o.get("name")]
        if not outs:
            return []
        choice = await self._choose(context, outs, step)
        context.add(self.id, "signal", f"decided: {choice}", ["choice"])
        return [(choice, c.id) for c, _ in step.targets(choice)]

    async def _choose(self, context: Context, outs: list[dict[str, str]], step) -> str:
        lines = [f"{e['by']} ({e['kind']}): {e['text']}" for e in context.entries
                 if e["kind"] in ("said", "answer", "note", "trailer") and e["text"].strip()][-LOOK_BACK:]
        options = "\n".join(f"- {o['name']}: {o.get('description', '')}" for o in outs)
        prompt = (f"{self.settings.get('question') or ''}\n\nOptions:\n{options}\n\nAnswer with exactly one option name, "
                  "nothing else.")
        for model_id in pool_ids(self, step):
            if not (model := self.core.pool.get(model_id)) or not self.core.pool.usable(model_id):
                continue
            try:
                reply = await Client(self.hass, model).chat(
                    [{"role": "system", "content": prompt}, {"role": "user", "content": "\n".join(lines) or "(empty)"}],
                    temperature=0, max_tokens=16, chat_template_kwargs={"enable_thinking": False})
            except ModelError as err:
                LOGGER.info("AAC: Decide %s: %s failed (%s)", self.id, model_id, err)
                self.core.pool.mark_down(model_id)
                continue
            said = reply["content"].strip().strip(".").lower()
            return next((o["name"] for o in outs if o["name"].lower() == said), None) or \
                next((o["name"] for o in outs if o["name"].lower() in said), outs[-1]["name"])
        return outs[-1]["name"]  # no model: the last output (usually "anything else")
