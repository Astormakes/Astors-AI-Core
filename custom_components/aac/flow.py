"""Messages through the graph (spec/graph.md; owner, 2026-10-23).

A message (a document, request.py) starts at a node's output and follows the wires. Every node it reaches
does its part (`Node.handle`: writes into the document) and passes it on:
- a round output: every wire gets it (a copy each when there are several),
- a square output: the node picks one wire. A model card's output is square: the nodes wired to it are its
  tools; calling one sends the document there and the model waits until it comes back (a loop). Its plain
  answer goes to whoever called it, else `reply_to`, else where the message started, else on.
- an endpoint (chat, voice, Assist) delivers what comes back to whoever waits.
Automatic messages wait for a model in the job queue (priority, due, critical, coalesce, keep).
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
from .request import TEXT, Message
from .runs import Run, current_run
from .streams import Node

if TYPE_CHECKING:
    from . import AACConfigEntry
    from .domains import Domain

MAX_STEPS = 80  # nodes one message may visit (a loop in the wiring must not run forever)
MODEL = "model"  # the standard model node

# The settings of an added model card (the standard one's are the domain's)
MODEL_SETTINGS = {
    "tier": {"type": "select", "title": "Model (pool request)", "default": "master", "options_from": "tiers",
             "help": "Which kind of model it asks the pool for."},
    "prompt": {"type": "longtext", "title": "Its own instructions", "default": "",
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
    """What came home: the message, the last model run, and whether an endpoint delivered it."""

    message: Message | None = None
    run: Run | None = None
    replied: bool = False


@dataclass
class _Walk:
    """One message's way: the result future, the last model answer, steps visited, models waiting for loops."""

    future: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    last: FlowResult = field(default_factory=FlowResult)
    steps: int = 0
    waiting: dict[tuple[str, str], asyncio.Future] = field(default_factory=dict)  # (message id, model) → future


class Step:
    """What a node gets with a message: where it can send it, how to wait for a loop, how to deliver."""

    def __init__(self, flow: Flow, walk: _Walk, node: Node, port: str) -> None:
        """Initialize."""
        self.flow, self.walk, self.node, self.port = flow, walk, node, port

    def targets(self, message: Message, port: str | None = None) -> list[tuple[Node, str]]:
        """The nodes (and their input) wired to one of this node's text outputs (all of them: None)."""
        return self.flow.targets(message.domain.id, self.node, port)

    def leads_back(self, message: Message, start: Node) -> bool:
        """Does a message sent into `start` come back into this node by the wires?"""
        return self.flow.leads_back(message.domain.id, start.id, self.node.id)

    async def call(self, target: Node, port: str, message: Message) -> Message | None:
        """Send the message into a node and wait until it comes back into this one (None: it did not)."""
        key = (message.id, self.node.id)
        future = asyncio.get_running_loop().create_future()
        self.walk.waiting[key] = future
        task = asyncio.ensure_future(self.flow._enter(self.walk, target, port, message))  # noqa: SLF001
        try:
            await asyncio.wait({future, task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.walk.waiting.pop(key, None)
        return future.result() if future.done() else None

    def deliver(self, message: Message) -> None:
        """An endpoint: whoever waits for this message gets it."""
        self.flow._deliver(self.walk, FlowResult(message, self.walk.last.run, replied=True))  # noqa: SLF001

    @property
    def run(self) -> Run | None:
        """The last model run on this way."""
        return self.walk.last.run


def message_dict(message: Message) -> dict[str, Any]:
    """A message as plain JSON (to wait across a restart)."""
    data = {f.name: deepcopy(getattr(message, f.name)) for f in fields(message) if f.name != "domain"}
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

    # ------------------------------------------------------------------ the wires
    def targets(self, domain_id: str, node: Node, port: str | None = None) -> list[tuple[Node, str]]:
        """The nodes (and their text input) wired to a node's text output (any: None)."""
        nodes = self.bus.nodes_in(domain_id)
        found = {(b, bp): (nodes[b], bp) for a, ap, b, bp in self.bus.wires(domain_id)
                 if a == node.id and (port is None or ap == port) and node.outputs.get(ap) == TEXT
                 and nodes[b].inputs.get(bp) == TEXT}
        return list(found.values())

    def _single(self, domain_id: str) -> set[str]:
        """Round outputs the owner set to one wire ("node.port")."""
        return set(self.entry.runtime_data.store.graph(domain_id).get("single") or [])

    def leads_back(self, domain_id: str, start: str, home: str) -> bool:
        """Is there a way by text wires from node `start` into node `home`? (Not through an endpoint: what
        comes into the chat does not leave at its output.)"""
        nodes = self.bus.nodes_in(domain_id)
        wires = [(a, b) for a, ap, b, bp in self.bus.wires(domain_id) if nodes[a].outputs.get(ap) == TEXT]
        seen, todo = set(), [start]
        while todo:
            at = todo.pop()
            if at == home:
                return True
            if at in seen or nodes[at].role == "endpoint":
                continue
            seen.add(at)
            todo += [b for a, b in wires if a == at]
        return False

    # ------------------------------------------------------------------ walking
    async def async_send(self, message: Message, start: str | None = None, port: str = "out",
                         wait: bool = True) -> FlowResult:
        """Send a message from its origin's output (or from `start`); with wait, until it comes home."""
        walk = _Walk()
        start = start or message.origin
        if not any(a == start and ap == port for a, ap, *_ in self.bus.wires(message.domain.id)):
            # an origin without wires (its module off, wired by hand before it existed): the panel chat's way
            start, port = "chat", "out"
        node = self.bus.nodes_in(message.domain.id).get(start)
        message.trace.append({"node": node.id, "title": node.title, "role": node.role})  # where it starts
        task = self.hass.async_create_task(self._out(walk, node, message, [port]))
        if not wait:  # automatic: until it waits in the queue (or has gone its whole way)
            await task
            return walk.last
        await asyncio.wait({walk.future, task}, return_when=asyncio.FIRST_COMPLETED)
        if walk.future.done():
            return walk.future.result()
        return walk.last  # every way ended without an endpoint: the last answer counts

    async def _out(self, walk: _Walk, node: Node, message: Message, ports: list | None) -> None:
        """On from a node: its round outputs (None: all of them) or the wire it picked ((port, node id))."""
        domain_id = message.domain.id
        sends: list[tuple[Node, str]] = []
        for item in ports if ports is not None else [p for p, k in node.outputs.items() if k == TEXT and p not in node.square]:
            port, only = item if isinstance(item, tuple) else (item, None)
            self.bus.record(domain_id, node.id, port, {"id": message.id, "trigger": message.trigger,
                                                       "source": message.origin, "text": message.text[:200]})
            found = [(n, p) for n, p in self.targets(domain_id, node, port) if only is None or n.id == only]
            if only is None and f"{node.id}.{port}" in self._single(domain_id):
                found = found[:1]  # the owner set this output to one wire (no copies)
            sends += found
        sends = list({(n.id, p): (n, p) for n, p in sends}.values())
        if message.calls and ports is None and node.role != "model":
            # an open call: back toward the model that asked (a node wired into several models answers only that one)
            caller = message.calls[-1]["model"]
            if back := [(n, p) for n, p in sends if n.id == caller] \
                    or [(n, p) for n, p in sends if n.role != "model" and self.leads_back(domain_id, n.id, caller)]:
                sends = back
        if sends:
            await asyncio.gather(*(self._enter(walk, n, p, message if len(sends) == 1 else message.branch())
                                   for n, p in sends))

    async def _enter(self, walk: _Walk, node: Node, port: str, message: Message) -> None:
        if (future := walk.waiting.get((message.id, node.id))) is not None and not future.done():
            future.set_result(message)  # back from a loop: the model that sent it goes on
            return
        walk.steps += 1
        if walk.steps > MAX_STEPS:
            LOGGER.warning("AAC: a message visited %s nodes; the wiring may loop (stopped at %s)", MAX_STEPS, node.id)
            return
        before = {e["id"] for e in message.entries}
        step = {"node": node.id, "title": node.title, "role": node.role}
        message.trace.append(step)
        ctx = Step(self, walk, node, port)
        try:
            if node.role == "model":
                go = await self._model(walk, node, message, ctx)
            elif node.handle is not None:
                go = await node.handle(message, ctx)
            else:
                go = None  # a node without a part of its own passes it on
        except Exception as err:  # noqa: BLE001 - one broken node must not lose the message silently
            LOGGER.exception("AAC: node %s failed on a message", node.id)
            step["error"] = str(err)
            go = []
            if pending_call(message, node.id) is not None:  # asked by a model: it gets the error and goes on
                message.add(node.id, "result", f"{node.title} failed: {err}", error=True)
                go = None
        if added := [_short(e) for e in message.entries if e["id"] not in before]:
            step["added"] = added
        if go != []:
            await self._out(walk, node, message, None if go is None else go if isinstance(go, list) else [go])

    def _deliver(self, walk: _Walk, result: FlowResult) -> None:
        if not walk.future.done():
            walk.future.set_result(result)

    # ------------------------------------------------------------------ model
    async def _model(self, walk: _Walk, node: Node, message: Message, ctx: Step) -> list | None:
        """Answer now, or (automatic) through the job queue."""
        if message.kind == "auto" and not message.now and message.due_minutes is not None and not message.calls:
            if not (message.coalesce and self._busy(message)):  # one waiting or running is enough (a heartbeat)
                await self.entry.runtime_data.jobs.async_submit(self._job(walk, node, message))
            return []
        return await self._answer(walk, node, message, ctx)

    def _job(self, walk: _Walk, node: Node, message: Message, saved: dict | None = None) -> Job:
        """The job of an automatic message waiting at a model card (also rebuilt after a restart: `saved`)."""

        async def start(note: str = "") -> Run | None:
            if note:
                message.add("queue", "note", note)
            ctx = Step(self, walk, node, "in")
            go = await self._answer(walk, node, message, ctx)
            if go != []:
                await self._out(walk, node, message, go)
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

    def _busy(self, message: Message) -> bool:
        data = self.entry.runtime_data
        if any(j.domain is message.domain and message.trigger in j.tags for j in data.jobs.waiting):
            return True
        return any(r.trigger == message.trigger for r in message.domain.runs.running("auto"))

    def restore(self, domain: Domain, item: dict[str, Any]) -> Job | None:
        """A message that waited before a restart, queued again at its model card."""
        node = self.bus.nodes_in(domain.id).get(item.get("node") or MODEL)
        if node is None or node.role != "model":
            return None
        known = Message.__dataclass_fields__
        message = Message(domain=domain, **{k: v for k, v in (item.get("message") or {}).items()
                                            if k in known and k != "domain"})
        return self._job(_Walk(), node, message, saved=item)

    def answer_to(self, message: Message, node: Node, ctx: Step) -> list[tuple[Node, str]]:
        """Where a model's plain answer goes: the model that called it, else reply_to, else the origin, else
        every wired node that does not lead back into it (no other endpoint)."""
        targets = ctx.targets(message)
        for want in ([message.calls[-1]["model"]] if message.calls else []) + [message.reply_to, message.origin]:
            if want and (found := [(n, p) for n, p in targets if n.id == want]):
                return found
        # endpoints only take what is theirs (found above)
        return [(n, p) for n, p in targets if n.role != "endpoint" and not ctx.leads_back(message, n)]

    async def _answer(self, walk: _Walk, node: Node, message: Message, ctx: Step) -> list | None:
        """One model turn (with its loops), recorded as a run; on to where the answer goes."""
        from .broker import async_context, is_silent  # noqa: PLC0415 - avoid an import cycle

        domain, data = message.domain, self.entry.runtime_data
        if not message.trace or message.trace[-1].get("node") != node.id:  # started by the queue
            message.trace.append({"node": node.id, "title": node.title, "role": node.role})
        # its pool: the dotted wire, else its own setting, else what the message asks for
        pooled = next((a for a, ap, b, bp in self.bus.wires(domain.id) if b == node.id and node.inputs.get(bp) == "pool"), None)
        tier = (pooled[len("pool_"):] if pooled and pooled.startswith("pool_") and message.tier == "master" else None) \
            or (node.settings.get("tier") if node.type else None) or message.tier
        if node.type and (prompt := (node.settings.get("prompt") or "").strip()):
            message.put(node.id, "system", prompt)
        home = self.answer_to(message, node, ctx)
        going = {(n.id, p) for n, p in home}
        # its tools: every other wired node (endpoints only take answers)
        tools = [(n, p) for n, p in ctx.targets(message) if (n.id, p) not in going and n.role != "endpoint"] \
            if message.allow_tools else []
        user = message.last("user")
        run = domain.runs.start(message.kind, message.trigger, message.title, input=user["text"] if user else "",
                                chat_id=message.chat_id, allow_tools=message.allow_tools, tier=tier,
                                own_words=message.words, source=message.origin)
        run.model_node = node.id
        walk.last = FlowResult(message, run)
        token = current_run.set(run)
        try:
            if message.context_parts:  # built now, when the model really starts (a message that waited sees today's home)
                built = await async_context(self.hass, self.entry, domain, message.context_parts)
                message.context_parts = []
                if built.strip():
                    user = message.last("user")
                    note = message.add("context", "note", built.strip())
                    if user:  # before the question it belongs to
                        message.entries.remove(note)
                        message.entries.insert(message.entries.index(user), note)
            run.context = "\n\n".join(e["text"] for e in message.entries if e["kind"] == "note" and not e["data"].get("call_id"))
            run.trace = list(message.trace)
            message = await data.model_node.async_answer(message, run, node, tier, tools, ctx)
            ctx.walk.last = FlowResult(message, run)
        except Exception as err:  # noqa: BLE001 - a failed run must not kill the caller
            if not isinstance(err, HomeAssistantError):
                LOGGER.exception("AAC: run %s failed", run.id)
            domain.runs.finish(run, "failed", str(err))
            message.trace[-1].update({"error": str(err), "run": run.id})
            run.trace = list(message.trace)
            walk.last = FlowResult(message, run)
            if message.kind != "auto":
                self._deliver(walk, FlowResult(message, run, replied=False))
            return []
        finally:
            current_run.reset(token)
        text = message.answer or ""
        domain.runs.finish(run, "silent" if message.kind == "auto" and is_silent(text) else "done", text)
        message.trace[-1].update({"run": run.id, "model": run.model})
        run.trace = list(message.trace)
        walk.last = FlowResult(message, run)
        if not home:
            return []
        # one wire for the answer (the model chose by answering); several: a copy each
        return [(port_of(node, "out"), n.id) for n, _ in home]

    # ------------------------------------------------------------------ decide
    async def decide(self, node: Node, message: Message) -> str:
        """Pick one of a Decide card's outputs: a short call whose answer the server's grammar limits to their names."""
        outputs = [o for o in (node.settings.get("outputs") or []) if o.get("name") in node.outputs]
        names = [o["name"] for o in outputs] or list(node.outputs)
        default = node.settings.get("default") if node.settings.get("default") in names else names[-1]
        step = message.trace[-1] if message.trace else {}
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
                # what it decides on: the newest turn with the notes around it (a buffer's lines, a recall)
                recent = [e for e in message.entries if e["kind"] in ("user", "answer", "note")][-8:]
                text = "\n\n".join(e["text"] if e["kind"] != "note" else f"[{e['text']}]" for e in recent) or message.text
                reply = await provider.chat(endpoint.model, [{"role": "system", "content": system},
                                                             {"role": "user", "content": text}], None,
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
        return picked


def port_of(node: Node, side: str) -> str:
    """A node's text port on one side."""
    return next((p for p, k in (node.outputs if side == "out" else node.inputs).items() if k == TEXT), side)


def pending_call(message: Message, node_id: str) -> dict[str, Any] | None:
    """The open call this node is asked to answer (the newest call, if it is for this node)."""
    if not message.calls or message.calls[-1]["node"] != node_id:
        return None
    call_id = message.calls[-1]["call_id"]
    found = next((e for e in reversed(message.entries) if e["kind"] == "call" and e["data"].get("call_id") == call_id), None)
    if found is None or any(e["kind"] == "result" and e["by"] == node_id and e["data"].get("call_id") == call_id
                            for e in message.entries):
        return None  # already answered by this node (it passes it on)
    return found


def _short(item: dict[str, Any]) -> dict[str, Any]:
    """An entry for the trace (the node view shows each node's part)."""
    text = item["text"]
    return {"kind": item["kind"], "text": text if len(text) <= 600 else text[:600] + "…", "tags": item["tags"]}
