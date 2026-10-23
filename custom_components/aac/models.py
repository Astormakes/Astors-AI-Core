"""The model pool (core, shared by every domain) and the client for OpenAI-compatible servers (llama.cpp first;
Ollama, vLLM, LM Studio and cloud APIs speak the same)."""

from __future__ import annotations

from collections.abc import Callable
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


class ModelPool:
    """Every model AAC may use (core: shared by the domains); a model that does not answer is skipped a minute."""

    def __init__(self, hass: HomeAssistant, models: list[dict[str, Any]]) -> None:
        """Initialize with the stored list."""
        self.hass = hass
        self.models = models
        self._down: dict[str, float] = {}

    def get(self, model_id: str) -> dict[str, Any] | None:
        """A model by id."""
        return next((m for m in self.models if m["id"] == model_id), None)

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
                 "up": self.usable(m["id"])} for m in self.models]
