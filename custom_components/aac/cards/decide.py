"""Decide: a model picks one of the named outputs (e.g. "for AAC" / "not for AAC"); the context goes on there."""

from __future__ import annotations


from custom_components.aac.api import Card, Client, Context, register, run

VERSION = "2.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

LOOK_BACK = 8  # entries the deciding model reads


@register
class Decide(Card):
    """A model picks an output."""

    type = "decide"
    title = "Decide"
    category = "Logic"
    help = ("A model reads the newest entries and picks one of the outputs you name (with what each is for); the context "
            "goes on there, every wire of it (a fast small model is enough).")
    tags = {"choice": "what it picked (no text: data.choice)"}
    settings_schema = {
        "outputs": {"type": "outputs", "title": "Outputs", "default": [{"name": "yes", "description": "it applies"},
                                                                       {"name": "no", "description": "anything else"}],
                    "help": "One per line: name: what it is for."},
        "question": {"type": "longtext", "title": "What to decide", "default": "Is the newest line meant for AAC?"},
        "models": {"type": "modellist", "tag": "text", "title": "Models (in order)", "default": [], "help": "The list is run through from the top: a free model takes it; a busy one queues it if its Queue is ticked, then the next is tried. Empty: every model with the tag, queued."},
    }

    def ports(self) -> tuple[dict[str, str], dict[str, str]]:
        names = [o["name"] for o in self.settings.get("outputs") or [] if o.get("name")]
        return dict(self.inputs), {n: "text" for n in names}

    async def handle(self, context: Context, port: str) -> None:
        outs = [o for o in self.settings.get("outputs") or [] if o.get("name")]
        if port != "in" or not outs or context.streaming or context.waiting:
            return
        choice = await self._choose(context, outs)
        context.add(self.tag("choice"), "", choice=choice)
        await self.send(context, choice)

    async def _choose(self, context: Context, outs: list[dict[str, str]]) -> str:
        lines = [f"{e['tag']}: {e['text']}" for e in context.conversation if e["text"].strip()][-LOOK_BACK:]
        options = "\n".join(f"- {o['name']}: {o.get('description', '')}" for o in outs)
        prompt = (f"{self.settings.get('question') or ''}\n\nOptions:\n{options}\n\nAnswer with exactly one option name, "
                  "nothing else.")
        async def ask(model):
            return await Client(self.hass, model).chat(
                [{"role": "system", "content": prompt}, {"role": "user", "content": "\n".join(lines) or "(empty)"}],
                temperature=0, max_tokens=16, chat_template_kwargs={"enable_thinking": False})
        reply, _ = await run(self.core.pool, self.core.pool.entries(self.settings.get("models"), "text"), ask)
        if reply is None:
            return outs[-1]["name"]  # no model: the last output (usually "anything else")
        said = reply["content"].strip().strip(".").lower()
        return next((o["name"] for o in outs if o["name"].lower() == said), None) or \
            next((o["name"] for o in outs if o["name"].lower() in said), outs[-1]["name"])
