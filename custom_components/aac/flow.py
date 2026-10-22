"""Messages through the graph (spec/v1.md; owner, 2026-10-22).

A message starts at a source node and follows the wires:
- a transform (charter, instructions, context, the agent's prompt) adds its part and passes it on,
- a model answers it: the cards attached to it (the dotted lines: tool sets, memory) give their tools and maybe
  context, the answer becomes the message's text, and it goes on (into more cards, another model, a Decide),
- Decide picks one of its named outputs (the choice is forced by the server's grammar, a default if anything fails),
- Reply brings it home: to the chat, the microphone or HA it came from (the message carries its origin, so the
  wires only ever point forward), an automatic finding to its chat,
- a sink reads along (the memory keeper).
Several wires out of one port = several branches. Automatic messages wait for a model in the job queue
(priority, due, critical, coalesce, keep); everything else is answered at once.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, fields
from datetime import timedelta
import json
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import LOGGER
from .jobs import Job
from .provider import OpenAICompatProvider, ProviderError
from .request import MESSAGE, TOOL, ModelRequest
from .runs import Run, current_run
from .streams import Node

if TYPE_CHECKING:
    from . import AACConfigEntry
    from .domains import Domain

MAX_STEPS = 60  # nodes one message may visit (a loop in the wiring must not run forever)
MODEL = "model"  # the standard model node
REPLY = "reply"

# The settings of a model node (the standard one's are the domain's; an added one has its own)
MODEL_SETTINGS = {
    "tier": {"type": "select", "title": "Model (pool request)", "default": "master", "options_from": "tiers",
             "help": "Which kind of model it asks the pool for."},
    "prompt": {"type": "longtext", "title": "Extra instructions", "default": "",
               "help": "Added to the system prompt (e.g. how this model should work)."},
}
DECIDE_SETTINGS = {
    "outputs": {"type": "outputs", "title": "Outputs", "default": [{"name": "yes", "description": "it applies"},
                                                                  {"name": "no", "description": "it does not apply"}],
                "help": "Each output with what it is for, in plain words; the model picks one per message."},
    "question": {"type": "longtext", "title": "What to decide", "default": "",
                 "help": "e.g. Does the message mention the number 69?"},
    "default": {"type": "text", "title": "Default output", "default": "",
                "help": "Where the message goes when the decision fails (empty: the last output)."},
    "tier": {"type": "select", "title": "Model (pool request)", "default": "fast", "options_from": "tiers"},
}


@dataclass
class FlowResult:
    """What came home: the message (its text = the answer), the last model run, and whether a Reply delivered it."""

    message: ModelRequest | None = None
    run: Run | None = None
    replied: bool = False


@dataclass
class _Walk:
    """One message's way: the result future, the last model answer (for a flow without Reply), steps visited."""

    future: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    last: FlowResult = field(default_factory=FlowResult)
    steps: int = 0


def message_dict(message: ModelRequest) -> dict[str, Any]:
    """A message as plain JSON (to wait across a restart)."""
    data = {f.name: deepcopy(getattr(message, f.name)) for f in fields(message) if f.name not in ("domain", "meta")}
    data["domain"] = message.domain.id
    return data


class Flow:
    """Moves the messages of one AAC entry through its domains' graphs."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry

    @property
    def bus(self):
        return self.entry.runtime_data.bus

    # ------------------------------------------------------------------ walking
    async def async_send(self, message: ModelRequest, start: str | None = None, port: str = "out",
                         wait: bool = True) -> FlowResult:
        """Send a message from its source node (or from `start`); with wait, until it comes home."""
        walk = _Walk()
        start = start or message.source
        if not any(a == start and ap == port for a, ap, *_ in self.bus.wires(message.domain.id)):
            # a source without wires (its module off, wired by hand before it existed): the panel chat's way
            start, port = "chat", "out"
        task = self.hass.async_create_task(self._from(walk, message, start, port))
        if not wait:  # automatic: until it waits in the queue (or has gone its whole way)
            await task
            return walk.last
        done, _ = await asyncio.wait({walk.future, task}, return_when=asyncio.FIRST_COMPLETED)
        if walk.future.done():
            return walk.future.result()
        await task  # every branch ended without a Reply: the last answer counts
        return walk.future.result() if walk.future.done() else walk.last

    async def _from(self, walk: _Walk, message: ModelRequest, node_id: str, port: str) -> None:
        nodes, wires = self.bus.nodes_in(message.domain.id), self.bus.wires(message.domain.id)
        self.bus.record(message.domain.id, node_id, port, {"id": message.id, "trigger": message.trigger,
                                                           "source": message.source, "text": message.text[:200]})
        targets = [(nodes[b], bp) for a, ap, b, bp in wires if (a, ap) == (node_id, port) and nodes[b].inputs.get(bp) == MESSAGE]
        if not targets:
            return
        await asyncio.gather(*(self._enter(walk, node, bp, message if len(targets) == 1 else message.branch())
                               for node, bp in targets))

    async def _enter(self, walk: _Walk, node: Node, port: str, message: ModelRequest) -> None:
        walk.steps += 1
        if walk.steps > MAX_STEPS:
            LOGGER.warning("AAC: a message visited %s nodes; the wiring may loop (stopped at %s)", MAX_STEPS, node.id)
            return
        try:
            if node.role == "model":
                if (answered := await self._model(walk, node, message)) is not None:
                    await self._from(walk, answered, node.id, _port(node, "out"))
            elif node.role == "decide":
                out = await self._decide(node, message)
                await self._from(walk, message, node.id, out)
            elif node.role == "reply":
                await self._reply(walk, node, message)
            elif node.role == "sink":
                if node.run:
                    await node.run(message.domain.id, port, {"message": message, "run": walk.last.run, **_meta(message)})
            elif node.annotate is not None:  # a transform
                await self._transform(node, message)
                await self._from(walk, message, node.id, _port(node, "out"))
        except Exception as err:  # noqa: BLE001 - one broken node must not lose the message silently
            LOGGER.exception("AAC: node %s failed on a message", node.id)
            message.trace.append({"node": node.id, "title": node.title, "error": str(err)})

    async def _transform(self, node: Node, message: ModelRequest) -> None:
        before = (len(message.system), len(message.context), len(message.tools))
        step: dict[str, Any] = {"node": node.id, "title": node.title}
        try:
            await node.annotate(message)
        except Exception as err:  # noqa: BLE001
            LOGGER.exception("AAC: node %s failed on a message", node.id)
            step["error"] = str(err)
        _added(message, before, step)
        message.trace.append(step)

    # ------------------------------------------------------------------ model
    def attached(self, domain_id: str, node: Node) -> list[Node]:
        """The cards attached to a model (wired into its tool input)."""
        nodes = self.bus.nodes_in(domain_id)
        return [nodes[a] for a, ap, b, bp in self.bus.wires(domain_id)
                if b == node.id and node.inputs.get(bp) == TOOL and nodes[a].provide]

    async def _model(self, walk: _Walk, node: Node, message: ModelRequest) -> ModelRequest | None:
        """Answer now, or (automatic) through the job queue; returns the answered message, or None (it waits)."""
        if message.kind == "auto" and not message.now and message.due_minutes is not None:
            return await self._queue(walk, node, message)
        return await self._answer(walk, node, message)

    async def _queue(self, walk: _Walk, node: Node, message: ModelRequest) -> None:
        if message.coalesce and self._busy(message):
            return None  # one waiting or running is enough (a heartbeat)
        await self.entry.runtime_data.jobs.async_submit(self._job(walk, node, message))
        return None

    def _job(self, walk: _Walk, node: Node, message: ModelRequest, saved: dict | None = None) -> Job:
        """The job of an automatic message waiting at a model node (also rebuilt after a restart: `saved`)."""

        async def start(note: str = "") -> Run | None:
            if note:
                message.instructions = f"{message.instructions}\n\n{note}".strip()
            answered = await self._answer(walk, node, message)
            if answered is not None:
                await self._from(walk, answered, node.id, _port(node, "out"))
            return walk.last.run

        job = Job(message.domain, message.title or message.trigger, message.tier, message.priority, start,
                  due=dt_util.utcnow() + timedelta(minutes=int(message.due_minutes)) if message.due_minutes else None,
                  critical=message.critical, fallback=message.fallback,
                  tags=list(dict.fromkeys([message.trigger, *message.tags])),
                  saved={"kind": "message", "title": message.title, "node": node.id, "message": message_dict(message)}
                  if message.keep else None)
        if saved:  # back after a restart (the queue keeps its original times)
            job.reason = "waited before Home Assistant restarted"
        return job

    def _busy(self, message: ModelRequest) -> bool:
        data = self.entry.runtime_data
        if any(j.domain is message.domain and message.trigger in j.tags for j in data.jobs.waiting):
            return True
        return any(r.trigger == message.trigger for r in message.domain.runs.running("auto"))

    def restore(self, domain: Domain, item: dict[str, Any]) -> Job | None:
        """A message that waited before a restart, queued again at its model node."""
        node = self.bus.nodes_in(domain.id).get(item.get("node") or MODEL)
        if node is None or node.role != "model":
            return None
        known = ModelRequest.__dataclass_fields__
        message = ModelRequest(domain=domain, **{k: v for k, v in (item.get("message") or {}).items()
                                                 if k in known and k not in ("domain", "meta")})
        return self._job(_Walk(), node, message, saved=item)

    async def _answer(self, walk: _Walk, node: Node, message: ModelRequest) -> ModelRequest | None:
        """One model call, recorded as a run."""
        from .broker import async_context, is_silent  # noqa: PLC0415 - avoid an import cycle

        domain, data = message.domain, self.entry.runtime_data
        if node.type:  # an added model node: its own tier and instructions
            message.tier = node.settings.get("tier") or message.tier
            if prompt := (node.settings.get("prompt") or "").strip():
                message.system.append(prompt)
        run = domain.runs.start(message.kind, message.trigger, message.title, input=message.text, chat_id=message.chat_id,
                                context=message.instructions, allow_tools=message.allow_tools, tier=message.tier,
                                history=message.history, own_words=message.own_words, source=message.source)
        run.model_node = node.id
        walk.last = FlowResult(message, run)
        token = current_run.set(run)
        try:
            if message.context_parts:  # built now, when the model really starts (a message that waited sees today's home)
                built = await async_context(self.hass, self.entry, domain, message.context_parts)
                intro, _, rest = message.instructions.partition("\n\n")  # the situation first, then what it sees
                message.instructions = "\n\n".join(filter(None, [intro, built.strip(), rest]))
                message.context_parts = []
            # The attached cards: their tools, and what they hand along (memory)
            for card in self.attached(domain.id, node):
                before = (len(message.system), len(message.context), len(message.tools))
                step: dict[str, Any] = {"node": card.id, "title": card.title, "attached": node.id}
                try:
                    await card.provide(message)
                except Exception as err:  # noqa: BLE001
                    LOGGER.exception("AAC: card %s failed", card.id)
                    step["error"] = str(err)
                _added(message, before, step)
                message.trace.append(step)
            if message.context:
                run.context = "\n\n".join(filter(None, [run.context, *message.context]))
            run.trace = list(message.trace)
            text, extra = await data.model_node.async_answer(message, run)
        except Exception as err:  # noqa: BLE001 - a failed run must not kill the caller
            if not isinstance(err, HomeAssistantError):
                LOGGER.exception("AAC: run %s failed", run.id)
            domain.runs.finish(run, "failed", str(err))
            message.trace.append({"node": node.id, "title": node.title, "role": "model", "error": str(err), "run": run.id})
            run.trace = list(message.trace)
            walk.last = FlowResult(message, run)
            if message.kind != "auto":
                self._deliver(walk, FlowResult(message, run, replied=False))
            return None
        finally:
            current_run.reset(token)
        domain.runs.finish(run, "silent" if message.kind == "auto" and is_silent(text) else "done", text)
        message.trace.append({"node": node.id, "title": node.title, "role": "model", "run": run.id, "model": run.model})
        run.trace = list(message.trace)
        message.meta = {"question": message.words, **extra}  # for the sinks (memory keeper: the person's own words)
        message.text, message.answered = text, True
        walk.last = FlowResult(message, run)
        return message

    # ------------------------------------------------------------------ decide
    async def _decide(self, node: Node, message: ModelRequest) -> str:
        """Pick one of the node's outputs: a short call whose answer the server's grammar limits to their names."""
        outputs = [o for o in (node.settings.get("outputs") or []) if o.get("name") in node.outputs]
        names = [o["name"] for o in outputs] or list(node.outputs)
        default = node.settings.get("default") if node.settings.get("default") in names else names[-1]
        step: dict[str, Any] = {"node": node.id, "title": node.title, "role": "decide"}
        picked = default
        pool = self.entry.runtime_data.pool
        if (found := pool.endpoint_for(node.settings.get("tier") or "fast", message.kind == "auto")) is not None:
            model_id, endpoint = found
            provider = OpenAICompatProvider(async_get_clientsession(self.hass), endpoint.base_url, endpoint.api_key,
                                            timeout=60, connect_timeout=3)
            options = "\n".join(f"- {o['name']}: {o.get('description') or ''}" for o in outputs) or "\n".join(f"- {n}" for n in names)
            system = ("You sort messages. Read the message and pick exactly one output.\n"
                      f"{(node.settings.get('question') or '').strip()}\nOutputs:\n{options}\n"
                      'Reply only with JSON: {"output": "<one of the names>"}')
            schema = {"type": "json_schema", "json_schema": {"name": "decision", "strict": True, "schema": {
                "type": "object", "properties": {"output": {"type": "string", "enum": names}},
                "required": ["output"], "additionalProperties": False}}}
            try:
                reply = await provider.chat(endpoint.model, [{"role": "system", "content": system},
                                                             {"role": "user", "content": message.text}], None,
                                            temperature=0, max_tokens=60, response_format=schema,
                                            chat_template_kwargs={"enable_thinking": False})
                choice = json.loads(reply.get("content") or "{}").get("output")
                if choice in names:
                    picked = choice
                else:
                    step["note"] = f"no valid choice ({choice!r}): the default"
                pool.mark_up(model_id)
            except (ProviderError, ValueError) as err:
                step["note"] = f"decision failed ({err}): the default"
        else:
            step["note"] = "no model free: the default"
        step["output"] = picked
        message.trace.append(step)
        return picked

    # ------------------------------------------------------------------ reply
    async def _reply(self, walk: _Walk, node: Node, message: ModelRequest) -> None:
        """Home: an automatic finding to its chat (silence stays silent); everything else to whoever waits."""
        from .broker import is_silent, short  # noqa: PLC0415
        from .chats import AAC_CHAT_ID  # noqa: PLC0415

        message.trace.append({"node": node.id, "title": node.title, "role": "reply"})
        run = walk.last.run
        if run is not None:
            run.trace = list(message.trace)
        if message.kind == "auto" and message.answered and message.text and not is_silent(message.text):
            domain = message.domain
            # A finding with a chat of its own (owner, 2026-10-20, heartbeat): a reply is about that one thing
            chat_id = (f"{message.chat_prefix or message.trigger}_{dt_util.utcnow().strftime('%Y%m%d%H%M%S')}"
                       if message.chat == "own" else AAC_CHAT_ID)
            if chat_id != AAC_CHAT_ID:
                domain.chats.create(f"{message.title} · {short(message.text)}", chat_id)
            if run is not None:
                run.chat_id = chat_id
            domain.chats.add_message(chat_id, "aac", message.text, proactive=True, run_id=run.id if run else None,
                                     trigger=message.title)
        self._deliver(walk, FlowResult(message, run, replied=True))

    def _deliver(self, walk: _Walk, result: FlowResult) -> None:
        if not walk.future.done():
            walk.future.set_result(result)


def _port(node: Node, side: str) -> str:
    """A node's message port on one side."""
    return next((p for p, k in (node.outputs if side == "out" else node.inputs).items() if k == MESSAGE), side)


def _added(message: ModelRequest, before: tuple[int, int, int], step: dict[str, Any]) -> None:
    """What a node added to the message, for the trace (the node view shows the way and each node's part)."""
    for key, n in zip(("system", "context", "tools"), before):
        if added := getattr(message, key)[n:]:
            step[key] = [a if key == "tools" else (a if len(a) <= 600 else a[:600] + "…") for a in added]


def _meta(message: ModelRequest) -> dict[str, Any]:
    return dict(message.meta)
