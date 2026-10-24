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

from .const import LOGGER

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
                   on_delta: Callable[[str], None] | None = None, **params: Any) -> dict[str, Any]:
        """One answer, streamed (on_delta gets the text so far). Returns {"content", "tool_calls"}."""
        body = {"model": self.model["model"], "messages": messages, "stream": True, **params}
        if tools:
            body["tools"] = tools
        content, calls = "", {}
        try:
            async with self.session.post(f"{self.base}/chat/completions", json=body, headers=self._headers(),
                                         timeout=aiohttp.ClientTimeout(total=600, sock_connect=4)) as resp:
                if resp.status >= 400:
                    raise ModelError(f"HTTP {resp.status}: {(await resp.text())[:300]}")
                if "event-stream" not in resp.headers.get("Content-Type", ""):  # the server ignored "stream"
                    data = await resp.json()
                    msg = data["choices"][0]["message"]
                    if on_delta and msg.get("content"):
                        on_delta(msg["content"])
                    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}
                async for raw in resp.content:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:") or line == "data: [DONE]":
                        continue
                    try:
                        delta = json.loads(line[5:])["choices"][0].get("delta") or {}
                    except (ValueError, KeyError, IndexError):
                        continue
                    if delta.get("content"):
                        content += delta["content"]
                        if on_delta:
                            on_delta(content)
                    for tc in delta.get("tool_calls") or []:
                        call = calls.setdefault(tc.get("index", 0), {"id": tc.get("id"), "type": "function",
                                                                      "function": {"name": "", "arguments": ""}})
                        fn = tc.get("function") or {}
                        call["id"] = call["id"] or tc.get("id")
                        call["function"]["name"] += fn.get("name") or ""
                        call["function"]["arguments"] += fn.get("arguments") or ""
        except (aiohttp.ClientConnectionError, TimeoutError) as err:  # off, unreachable, dropped
            raise ModelUnreachable(str(err) or type(err).__name__) from err
        except aiohttp.ClientError as err:
            raise ModelError(str(err)) from err
        return {"content": content, "tool_calls": [calls[i] for i in sorted(calls)]}


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

    def __init__(self, hass: HomeAssistant, models: list[dict[str, Any]]) -> None:
        """Initialize with the stored list."""
        self.hass = hass
        self.models = models
        self._down: dict[str, float] = {}
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

    def mark_down(self, model_id: str) -> None:
        """Skip it for a while."""
        self._down[model_id] = time.monotonic() + DOWN_FOR
        LOGGER.info("AAC: model %s unreachable, skipped for %s s", model_id, DOWN_FOR)

    def mark_up(self, model_id: str) -> None:
        """It answered."""
        self._down.pop(model_id, None)

    def summary(self) -> list[dict[str, Any]]:
        """For the panel (no keys)."""
        return [{**{k: v for k, v in m.items() if k != "api_key"}, "has_key": bool(m.get("api_key")),
                 "up": self.usable(m["id"]), "tags": self.tags(m["id"]), "busy": self._busy.get(m["id"], 0),
                 "waiting": len(self._queues.get(m["id"], []))} for m in self.models]


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
            except ModelUnreachable:
                pool.mark_down(model_id)
            except ModelError as err:
                LOGGER.info("AAC: model %s failed: %s", model_id, err)
            else:
                pool.mark_up(model_id)
                return result, False
            tried.add(model_id)

