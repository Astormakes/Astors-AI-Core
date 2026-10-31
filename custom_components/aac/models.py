"""The model pool (core, shared by every domain) and the client for OpenAI-compatible servers (llama.cpp first;
Ollama, vLLM, LM Studio and cloud APIs speak the same)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import json
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import LOGGER, SIGNAL

DOWN_FOR = 60  # seconds a model that did not answer is skipped


class ModelError(Exception):
    """A model server failed."""


class ModelUnreachable(ModelError):
    """The machine does not answer (it may simply be off)."""


class Client:
    """One model on one server."""

    def __init__(self, hass: HomeAssistant, model: dict[str, Any]) -> None:
        """Initialize from a pool entry {id, name, base_url, model, api_key}."""
        self.session = async_get_clientsession(hass)
        self.model = model
        self.base = model["base_url"].rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.model['api_key']}"} if self.model.get("api_key") else {}

    async def list_models(self) -> list[str]:
        """What the server offers."""
        try:
            async with self.session.get(f"{self.base}/models", headers=self._headers(),
                                        timeout=aiohttp.ClientTimeout(total=8)) as resp:
                resp.raise_for_status()
                return [m.get("id") for m in (await resp.json()).get("data", [])]
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ModelUnreachable(str(err)) from err

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                   on_delta: Callable[[str, str], None] | None = None, **params: Any) -> dict[str, Any]:
        """One answer, streamed (on_delta gets the text and the thinking so far). Returns {"content", "tool_calls",
        "thinking", "finish" (why it ended: stop, length, tool_calls …), "usage" ({prompt_tokens, completion_tokens,
        total_tokens} when the server tells)}. A kept-alive connection the server had already closed is tried once
        more on a fresh one (it fails before anything comes)."""
        try:
            return await self._chat(messages, tools, on_delta, **params)
        except ModelUnreachable as err:
            if not isinstance(err.__cause__, (aiohttp.ServerDisconnectedError, aiohttp.ClientOSError)) or getattr(err, "started", False):
                raise
        return await self._chat(messages, tools, on_delta, **params)

    async def following(self) -> str:
        """The model to ask for: its own, or (it follows a router) whatever the router has loaded now; with none loaded
        the one it used last (the router loads it)."""
        if not self.model.get("follow"):
            return self.model["model"]
        try:
            found = (await self._request("GET", "/models", seconds=5)).get("data") or []
        except ModelError:
            return self.model["model"]
        loaded = next((m["id"] for m in found if (m.get("status") or {}).get("value") in ("loaded", "loading", "sleeping")), None)
        if loaded:
            self.model["model"] = loaded  # the pool's entry remembers it (shown, and used when nothing is loaded)
        return self.model["model"]

    async def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
                    on_delta: Callable[[str, str], None] | None, **params: Any) -> dict[str, Any]:
        body = {"model": await self.following(), "messages": messages, "stream": True,
                "stream_options": {"include_usage": True}, **params}
        if tools:
            body["tools"] = tools
        content, thinking, calls, finish, usage = "", "", {}, None, None
        try:
            async with self.session.post(f"{self.base}/chat/completions", json=body, headers=self._headers(),
                                         timeout=aiohttp.ClientTimeout(total=600, sock_connect=4)) as resp:
                if resp.status >= 400:
                    raise ModelError(f"HTTP {resp.status}: {(await resp.text())[:300]}")
                if "event-stream" not in resp.headers.get("Content-Type", ""):  # the server ignored "stream"
                    data = await resp.json()
                    choice = data["choices"][0]
                    msg = choice["message"]
                    if on_delta and msg.get("content"):
                        on_delta(msg["content"], msg.get("reasoning_content") or "")
                    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or [],
                            "thinking": msg.get("reasoning_content") or "", "finish": choice.get("finish_reason"),
                            "usage": data.get("usage")}
                async for raw in resp.content:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:") or line == "data: [DONE]":
                        continue
                    try:
                        chunk = json.loads(line[5:])
                    except ValueError:
                        continue
                    usage = chunk.get("usage") or usage  # the last piece tells the tokens
                    choice = (chunk.get("choices") or [{}])[0]
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}
                    if delta.get("content") or delta.get("reasoning_content"):
                        content += delta.get("content") or ""
                        thinking += delta.get("reasoning_content") or ""
                        if on_delta:
                            on_delta(content, thinking)
                    for tc in delta.get("tool_calls") or []:
                        call = calls.setdefault(tc.get("index", 0), {"id": tc.get("id"), "type": "function",
                                                                      "function": {"name": "", "arguments": ""}})
                        fn = tc.get("function") or {}
                        call["id"] = call["id"] or tc.get("id")
                        call["function"]["name"] += fn.get("name") or ""
                        call["function"]["arguments"] += fn.get("arguments") or ""
        except (aiohttp.ClientConnectionError, TimeoutError) as err:  # off, unreachable, dropped
            unreachable = ModelUnreachable(str(err) or type(err).__name__)
            unreachable.started = bool(content or calls)  # something came already: not asked again
            raise unreachable from err
        except aiohttp.ClientError as err:
            raise ModelError(str(err)) from err
        return {"content": content, "tool_calls": [calls[i] for i in sorted(calls)], "thinking": thinking, "finish": finish,
                "usage": usage}

    async def context_size(self) -> int | None:
        """How many tokens the server's context holds for this model (llama-server tells); None when it does not."""
        try:
            props = await self._request("GET", "/props", seconds=5, params={"model": await self.following(), "autoload": "false"})
        except ModelError:
            return None
        return ((props or {}).get("default_generation_settings") or {}).get("n_ctx") if isinstance(props, dict) else None

    # -------------------------------------------------- llama-server's router (one server, many models, see spec)
    @property
    def root(self) -> str:
        """The server's own address: the router's endpoints sit beside /v1."""
        return self.base[:-3] if self.base.endswith("/v1") else self.base

    async def _request(self, method: str, path: str, text_only: bool = False, seconds: float = 10, **kwargs: Any) -> Any:
        try:
            async with self.session.request(method, f"{self.root}{path}", headers=self._headers(),
                                            timeout=aiohttp.ClientTimeout(total=seconds), **kwargs) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    try:
                        text = json.loads(text)["error"]["message"]
                    except (ValueError, KeyError, TypeError):
                        pass
                    raise ModelError(f"HTTP {resp.status}: {str(text)[:300]}")
                if text_only:
                    return text
                return json.loads(text) if text.strip() else {}
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ModelUnreachable(str(err) or type(err).__name__) from err

    async def router(self, known: dict[str, int] | None = None) -> list[dict[str, Any]] | None:
        """What a llama-server router offers, each with its state and, once loaded, what it is and how fast it ran.
        None: the server is no router (one fixed model). `known`: the last progress per download / load (kept by the
        caller), used when the live events said nothing this time (else it showed 0 % now and then)."""
        try:
            found = (await self._request("GET", "/models")).get("data") or []
        except ModelError as err:
            if str(err).startswith("HTTP 404"):  # a server without the list (whisper.cpp and the like)
                return None
            raise
        if not found or "status" not in found[0]:
            return None
        loaded = [m["id"] for m in found if m["status"].get("value") == "loaded"]
        meta = {}
        if loaded:
            meta = {m["id"]: m.get("meta") or {} for m in (await self._request("GET", "/v1/models")).get("data", [])}
        busy = {m["id"] for m in found if m["status"].get("value") in ("downloading", "loading") and not m["status"].get("progress")}
        progress = await self._live_progress(busy) if busy else {}
        out = []
        for m in found:
            status, args = m["status"], m["status"].get("args") or []
            entry = {"id": m["id"], "status": status.get("value"), "failed": bool(status.get("failed")),
                     "can_remove": bool(m.get("can_remove")), "source": m.get("source"),
                     "context_max": m.get("context_length"), "inputs": (m.get("architecture") or {}).get("input_modalities") or [],
                     "context": int(args[args.index("--ctx-size") + 1]) if "--ctx-size" in args else None}
            key = f"{self.root} {m['id']}"
            if files := status.get("progress"):
                entry["progress"] = _percent(files)
            elif m["id"] in progress:
                entry["progress"] = progress[m["id"]]
            elif known is not None and m["id"] in busy and key in known:
                entry["progress"] = known[key]
            if known is not None:
                if entry["status"] in ("downloading", "loading") and "progress" in entry:
                    known[key] = entry["progress"]
                else:
                    known.pop(key, None)
            if m["id"] in loaded:
                info = meta.get(m["id"], {})
                entry.update(params=info.get("n_params"), size=info.get("size"), quant=info.get("ftype"), context=info.get("n_ctx") or entry["context"])
                try:
                    props = await self._request("GET", "/props", params={"model": m["id"], "autoload": "false"})
                    entry["tools"] = bool((props.get("chat_template_caps") or {}).get("supports_tools"))
                    entry["build"] = props.get("build_info")
                    metrics = await self._request("GET", "/metrics", text_only=True, params={"model": m["id"], "autoload": "false"})
                except (ModelError, ValueError):
                    metrics = ""
                numbers = dict(line.split()[:2] for line in metrics.splitlines() if line.startswith("llamacpp:") and len(line.split()) >= 2)
                entry["speed"] = {"read": round(float(numbers.get("llamacpp:prompt_tokens_seconds", 0))),
                                  "write": round(float(numbers.get("llamacpp:predicted_tokens_seconds", 0)))}
            out.append(entry)
        return out

    async def _live_progress(self, models: set[str]) -> dict[str, int]:
        """How far downloads and loads are, from the router's live events (its list has no progress): read a moment."""
        out: dict[str, int] = {}
        try:
            async with self.session.get(f"{self.root}/models/sse", headers=self._headers(),
                                        timeout=aiohttp.ClientTimeout(total=1.5)) as resp:
                async for raw in resp.content:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    if event.get("model") not in models:
                        continue
                    data = event.get("data") or {}
                    if event.get("event") == "download_progress":
                        out[event["model"]] = _percent(data.get("progress") or {})
                    elif event.get("event") in ("status_change", "model_status") and data.get("status") == "loading" and (load := data.get("progress")):
                        stages = load.get("stages") or [load.get("current")]  # each stage counts the same
                        done = stages.index(load.get("current")) if load.get("current") in stages else 0
                        out[event["model"]] = round(100 * (done + float(load.get("value") or 0)) / len(stages))
                    if set(out) >= models:
                        break
        except (aiohttp.ClientError, TimeoutError, ValueError):
            pass
        return out

    async def template(self, body: dict[str, Any]) -> str | None:
        """A request as the model reads it: llama-server writes it with the model's chat template (no answer is made);
        None when the server cannot."""
        try:
            found = await self._request("POST", "/apply-template", json={"model": await self.following(), **body})
        except ModelError:
            return None
        return found.get("prompt") if isinstance(found, dict) else None

    async def bench(self, name: str) -> dict[str, Any]:
        """A short fixed test of one of the server's models: it reads a long text (no cache) and writes 256 tokens;
        how fast each went, as the server measured it."""
        body = {"model": name, "messages": [{"role": "user", "content": BENCH_TEXT}], "max_tokens": 256, "temperature": 0,
                "ignore_eos": True, "cache_prompt": False, "stream": False, "chat_template_kwargs": {"enable_thinking": False}}
        timings = (await self._request("POST", "/v1/chat/completions", seconds=600, json=body)).get("timings") or {}
        if not timings:
            raise ModelError("the server measured nothing (not a llama-server?)")
        return {"read": round(timings.get("prompt_per_second") or 0), "write": round(timings.get("predicted_per_second") or 0),
                "prompt": timings.get("prompt_n"), "written": timings.get("predicted_n"),
                "seconds": round(((timings.get("prompt_ms") or 0) + (timings.get("predicted_ms") or 0)) / 1000, 1)}

    async def router_do(self, action: str, name: str, sidecars: list[str] | None = None) -> None:
        """load / unload one of the router's models, download one from Hugging Face ("user/repo:quant", sidecars like
        "mmproj", "mtp"), or delete a downloaded one."""
        if action in ("load", "unload"):
            await self._request("POST", f"/models/{action}", json={"model": name})
        elif action == "download":
            await self._request("POST", "/models", json={"model": name, "sidecars": sidecars or []})
        elif action == "delete":
            await self._request("DELETE", "/models", params={"model": name})
        else:
            raise ModelError(f"unknown action {action}")


BENCH_TEXT = ("Read this text, then write on about foxes and dogs for as long as you can.\n\n"
              + "The quick brown fox jumps over the lazy dog, and the dog sleeps on in the warm afternoon sun. " * 75)


def _percent(files: dict[str, Any]) -> int:
    """A download's progress over its files ({url: {done, total}})."""
    done, total = sum(f.get("done", 0) for f in files.values()), sum(f.get("total", 0) for f in files.values())
    return round(100 * done / total) if total else 0


MODEL_TAGS = ["text", "tools", "images", "audio in", "audio out", "video", "embeddings", "speech to text", "sound tags",
              "voice prints"]  # what a model can do; a card offers only the models with the tag it needs


def guess_tags(model: dict[str, Any]) -> list[str]:
    """What a model probably does, from its name (the owner corrects it on the Models page)."""
    name = f"{model.get('model', '')} {model.get('name', '')}".lower()
    if any(w in name for w in ("embed", "bge", "e5-", "gte-", "nomic")):
        return ["embeddings"]
    if any(w in name for w in ("whisper", "transcri", "parakeet")):
        return ["speech to text"]
    if "senses" in name:
        return ["sound tags", "voice prints"]
    return ["text", "tools", *(["images"] if any(w in name for w in ("-vl", "vision", "llava", "omni")) else [])]


class ModelPool:
    """Every model AAC may use (core: shared by the domains). A model runs a set number of requests at once (its
    "runs at once", default 1); a card goes through its list of models: a free one takes the context, a busy one
    queues it when the entry says so, and the first that gets free takes it (it leaves the other queues). A model
    that does not answer is skipped a minute."""

    def __init__(self, hass: HomeAssistant, models: list[dict[str, Any]], benches: dict[str, Any] | None = None,
                 servers: list[dict[str, Any]] | None = None) -> None:
        """Initialize with the stored lists: models, the last benchmarks, the servers the models are on."""
        self.hass = hass
        self.models = models
        self.benches = benches if benches is not None else {}
        self.server_list = servers if servers is not None else []
        self._down: dict[str, float] = {}
        self.progress: dict[str, int] = {}  # "<server> <model>" → its last download / load progress (router())
        self.heard: dict[str, dict[str, Any]] = {}  # server id → when it last answered / failed (since AAC started)
        self.failing: set[str] = set()  # models that did not answer last time (until they answer again): the panel warns
        self._busy: dict[str, int] = {}  # requests running per model
        self._queues: dict[str, list[asyncio.Future]] = {}  # contexts waiting per model (one queue, every card and domain)

    def get(self, model_id: str) -> dict[str, Any] | None:
        """A model by id."""
        return next((m for m in self.models if m["id"] == model_id), None)

    def tags(self, model_id: str) -> list[str]:
        """What a model can do (its tags, else a guess from its name)."""
        model = self.get(model_id)
        return (model.get("tags") or guess_tags(model)) if model else []

    def entries(self, wanted: list[dict[str, Any]] | None, tag: str) -> list[dict[str, Any]]:
        """A card's list of models ({model, queue}) that exist and do the job; empty: every model with the tag, queued."""
        if not wanted:
            return [{"model": m["id"], "queue": True} for m in self.models if tag in self.tags(m["id"])]
        return [e for e in wanted if self.get(e.get("model", "")) and tag in self.tags(e["model"])]

    def _free(self, model_id: str) -> bool:
        model = self.get(model_id) or {}
        return self._busy.get(model_id, 0) < max(1, int(model.get("parallel") or 1))

    async def _take(self, entries: list[dict[str, Any]]) -> str | None:
        """Down the list: a free model now, else wait in the queues it may join; None: all busy, none to queue at."""
        joined: list[tuple[str, asyncio.Future]] = []
        for e in entries:
            model_id = e["model"]
            if not self.usable(model_id):
                continue
            if self._free(model_id):
                self._busy[model_id] = self._busy.get(model_id, 0) + 1
                self._leave(joined)
                return model_id
            if e.get("queue"):
                waiter = asyncio.get_running_loop().create_future()
                self._queues.setdefault(model_id, []).append(waiter)
                joined.append((model_id, waiter))
        if not joined:
            return None
        try:
            await asyncio.wait([w for _, w in joined], return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            self._leave(joined)
            raise
        won = next(m for m, w in joined if w.done() and not w.cancelled())
        self._leave([(m, w) for m, w in joined if m != won])
        return won

    def _leave(self, joined: list[tuple[str, asyncio.Future]]) -> None:
        """Out of these queues; a slot that came at the same time goes to the next in line."""
        for model_id, waiter in joined:
            if waiter.done() and not waiter.cancelled():
                self._release(model_id)
            else:
                waiter.cancel()
                if waiter in self._queues.get(model_id, []):
                    self._queues[model_id].remove(waiter)

    def _release(self, model_id: str) -> None:
        """A request ended: the slot goes to the next one waiting, else it is free."""
        queue = self._queues.get(model_id, [])
        while queue:
            waiter = queue.pop(0)
            if not waiter.done():
                waiter.set_result(model_id)
                return
        self._busy[model_id] = max(0, self._busy.get(model_id, 0) - 1)

    @asynccontextmanager
    async def slot(self, entries: list[dict[str, Any]]) -> AsyncIterator[str | None]:
        """A model from the list for one request (None: every model busy and none to queue at, or none usable)."""
        model_id = await self._take(entries)
        try:
            yield model_id
        finally:
            if model_id:
                self._release(model_id)

    def usable(self, model_id: str) -> bool:
        """Not marked down."""
        return time.monotonic() >= self._down.get(model_id, 0)

    def mark_down(self, model_id: str, error: str = "") -> None:
        """Skip it for a while."""
        self.heard_from(model_id, error or "no answer")
        self._down[model_id] = time.monotonic() + DOWN_FOR
        LOGGER.warning("AAC: model %s does not answer, skipped for %s s", model_id, DOWN_FOR)
        if model_id not in self.failing:
            self.failing.add(model_id)
            async_dispatcher_send(self.hass, SIGNAL, {"what": "models"})

    def heard_from(self, model_id: str, error: str | None = None) -> None:
        """A request to a model ended: its server answered (error None) or not (mark_up / mark_down say it too); the
        Models page shows when."""
        if server := (self.get(model_id) or {}).get("server"):
            now = dt_util.utcnow().isoformat()
            self.heard[server] = {**self.heard.get(server, {}), **({"failed": now, "error": error} if error else {"answered": now})}

    def mark_up(self, model_id: str) -> None:
        """It answered."""
        self.heard_from(model_id)
        self._down.pop(model_id, None)
        if model_id in self.failing:
            self.failing.discard(model_id)
            async_dispatcher_send(self.hass, SIGNAL, {"what": "models"})

    def summary(self) -> list[dict[str, Any]]:
        """For the panel (no keys)."""
        return [{**{k: v for k, v in m.items() if k != "api_key"}, "has_key": bool(m.get("api_key")),
                 "up": self.usable(m["id"]), "failing": m["id"] in self.failing, "tags": self.tags(m["id"]), "busy": self._busy.get(m["id"], 0),
                 "waiting": len(self._queues.get(m["id"], []))} for m in self.models]

    def servers(self) -> list[dict[str, Any]]:
        """The model servers, each with a client for it and the pool's models on it (a router serves many models)."""
        out = []
        for record in self.server_list:
            client = Client(self.hass, {"base_url": record["base_url"], "api_key": record.get("api_key"), "model": ""})
            out.append({"record": record, "address": client.root, "client": client,
                        "uses": [m for m in self.models if m.get("server") == record["id"]]})
        return out

    def server_of(self, server_id: str) -> dict[str, Any] | None:
        """One server (with its client), by id."""
        return next((s for s in self.servers() if s["record"]["id"] == server_id), None)

    async def bench(self, client: Client, name: str) -> dict[str, Any]:
        """Benchmark one model of a server and keep the result (the newest per server and model)."""
        result = {**await client.bench(name), "at": dt_util.utcnow().isoformat()}
        self.benches[f"{client.root} {name}"] = result
        return result

    def find(self, name: str) -> dict[str, Any] | None:
        """A pool model by id or name (as a model or a Relay's script writes it)."""
        name = (name or "").strip().lower()
        return next((m for m in self.models if name in (m["id"].lower(), m["name"].lower())), None)

    async def offers(self) -> list[dict[str, Any]]:
        """Every server with what it offers right now (a router: its models and their state; else only the pool's
        models on it). For the panel and the Models card; no keys."""
        async def one(server: dict[str, Any]) -> dict[str, Any]:
            record = server["record"]
            out = {"id": record["id"], "name": record["name"], "base_url": record["base_url"], "has_key": bool(record.get("api_key")),
                   "address": server["address"], "heard": self.heard.get(record["id"], {}),
                   "uses": [{"id": m["id"], "name": m["name"], "model": m["model"], "follow": bool(m.get("follow")),
                             "tags": self.tags(m["id"]), "parallel": m.get("parallel") or 1} for m in server["uses"]]}
            try:
                out["models"] = await server["client"].router(self.progress)
                out["router"] = out["models"] is not None
                if not out["router"]:  # one fixed model (or a few): what its /v1/models says, if it says anything
                    try:
                        out["offered"] = await server["client"].list_models()
                    except ModelError:
                        out["offered"] = []
            except ModelError as err:
                out.update(router=False, models=None, error=str(err))
            for m in out["models"] or []:
                if bench := self.benches.get(f"{server['address']} {m['id']}"):
                    m["bench"] = bench
            loaded = next((m["id"] for m in out["models"] or [] if m["status"] in ("loaded", "loading", "sleeping")), None)
            for model, use in zip(server["uses"], out["uses"]):
                if model.get("follow") and loaded:  # a model that follows the server uses what it has loaded
                    model["model"] = use["model"] = loaded
            return out
        return list(await asyncio.gather(*(one(s) for s in self.servers())))


async def run(pool: ModelPool, entries: list[dict[str, Any]], job: Callable[[dict[str, Any]], Any]) -> tuple[Any, bool]:
    """One request down a card's list (free model, else queues); a model that does not answer or fails is skipped.
    Returns (the job's result or None, dropped: every model was busy and none queues)."""
    tried: set[str] = set()
    while True:
        left = [e for e in entries if e["model"] not in tried]
        async with pool.slot(left) as model_id:
            if model_id is None:
                return None, any(pool.usable(e["model"]) for e in left)
            try:
                result = await job(pool.get(model_id))
            except ModelUnreachable as err:
                pool.mark_down(model_id, str(err))
            except ModelError as err:
                LOGGER.info("AAC: model %s failed: %s", model_id, err)
                pool.heard_from(model_id, str(err))
            else:
                pool.mark_up(model_id)
                return result, False
            tried.add(model_id)

