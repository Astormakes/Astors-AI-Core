"""Model provider: a minimal client for OpenAI-compatible chat APIs.

Works with llama.cpp (llama-server), Ollama, vLLM, LM Studio and most cloud
APIs that speak the OpenAI chat completions format. This is the first
"model provider" from the framework design; more can be added later behind
the same small interface (list_models, chat, embed, transcribe).
"""

from __future__ import annotations

from collections.abc import Callable
import json
import math
import time
from typing import Any

import aiohttp


class ProviderError(Exception):
    """Generic provider error."""


class ProviderConnectionError(ProviderError):
    """The server could not be reached."""


class ProviderAuthError(ProviderError):
    """The server rejected the API key."""


class ProviderNotFoundError(ProviderError):
    """The server has no such endpoint (e.g. a speech server without /models)."""


class ProviderContextError(ProviderError):
    """The request is longer than the model's context (llama.cpp: exceed_context_size_error)."""

    def __init__(self, text: str, n_ctx: int | None, n_prompt: int | None) -> None:
        """Keep what the server reported."""
        super().__init__(text)
        self.n_ctx = n_ctx
        self.n_prompt = n_prompt


def _http_error(status: int, text: str) -> ProviderError:
    """The error for a failed response (a too long request gets its own, with the sizes)."""
    if status == 400 and ("context" in text and ("exceed" in text or "too long" in text)):
        try:
            err = json.loads(text).get("error") or {}
        except (ValueError, AttributeError):
            err = {}
        return ProviderContextError(f"HTTP {status}: {text}", err.get("n_ctx"), err.get("n_prompt_tokens"))
    return ProviderError(f"HTTP {status}: {text}")


class OpenAICompatProvider:
    """Talks to an OpenAI-compatible `/v1` endpoint."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 300,
        connect_timeout: float | None = None,
    ) -> None:
        """Initialize the provider (connect_timeout: give up early on a machine that is off)."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = aiohttp.ClientTimeout(total=timeout, sock_connect=connect_timeout)

    async def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None, timeout=None, data=None
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                data=data,
                headers=self._headers,
                timeout=timeout or self._timeout,
            ) as resp:
                if resp.status in (401, 403):
                    raise ProviderAuthError(await resp.text())
                if resp.status == 404:
                    raise ProviderNotFoundError(f"HTTP 404: {path}")
                if resp.status >= 400:
                    raise _http_error(resp.status, await resp.text())
                return await resp.json(content_type=None)
        except (aiohttp.ClientConnectionError, TimeoutError) as err:
            raise ProviderConnectionError(str(err) or type(err).__name__) from err
        except aiohttp.ClientError as err:
            raise ProviderError(str(err)) from err

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str, str | None], None] | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """Like chat, but streamed (server-sent events): on_delta(text so far, tool being called or None)
        is called while the answer arrives. Returns the same message dict as chat."""
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True,
                                   "stream_options": {"include_usage": True}, **params}
        if tools:
            payload["tools"] = tools
        started = time.monotonic()
        content, reasoning = "", ""
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        timings: dict[str, Any] = {}
        try:
            # llama.cpp closes the connection after a stream; reusing it would fail the next request
            async with self._session.post(f"{self._base_url}/chat/completions", json=payload,
                                          headers={**self._headers, "Connection": "close"}, timeout=self._timeout) as resp:
                if resp.status in (401, 403):
                    raise ProviderAuthError(await resp.text())
                if resp.status == 404:
                    raise ProviderNotFoundError("HTTP 404: /chat/completions")
                if resp.status >= 400:
                    raise _http_error(resp.status, await resp.text())
                if "event-stream" not in (resp.headers.get("Content-Type") or ""):
                    # The server ignored "stream": an ordinary answer
                    data = await resp.json(content_type=None)
                    try:
                        message = data["choices"][0]["message"]
                    except (KeyError, IndexError, TypeError) as err:
                        raise ProviderError(f"Unexpected response: {data}") from err
                    if on_delta and message.get("content"):
                        on_delta(message["content"], None)
                    tokens = (data.get("usage") or {}).get("completion_tokens")
                    speed = (data.get("timings") or {}).get("predicted_per_second")
                    if speed is None and tokens:
                        speed = tokens / max(time.monotonic() - started, 0.001)
                    return {**message, "_stats": {"completion_tokens": tokens, "tokens_per_second": speed}}
                async for raw in resp.content:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        continue  # read to the end, so the connection is clean for the next request
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    usage = chunk.get("usage") or usage
                    timings = chunk.get("timings") or timings
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):
                            reasoning += delta["reasoning_content"]
                        if delta.get("content"):
                            content += delta["content"]
                            if on_delta:
                                on_delta(content, None)
                        for tc in delta.get("tool_calls") or []:
                            call = calls.setdefault(tc.get("index", len(calls)),
                                                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            call["id"] = tc.get("id") or call["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                call["function"]["name"] += fn["name"]
                                if on_delta:
                                    on_delta(content, call["function"]["name"])
                            call["function"]["arguments"] += fn.get("arguments") or ""
        except (aiohttp.ClientConnectionError, TimeoutError) as err:
            raise ProviderConnectionError(str(err) or type(err).__name__) from err
        except aiohttp.ClientError as err:
            raise ProviderError(str(err)) from err
        tokens = usage.get("completion_tokens")
        speed = timings.get("predicted_per_second")
        if speed is None and tokens:
            speed = tokens / max(time.monotonic() - started, 0.001)
        message: dict[str, Any] = {"role": "assistant", "content": content or None}
        if reasoning:
            message["reasoning_content"] = reasoning
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        return {**message, "_stats": {"completion_tokens": tokens, "tokens_per_second": speed}}

    async def models_meta(self) -> dict[str, dict[str, Any]]:
        """Model id → metadata the server gives (llama.cpp: n_params, size, n_ctx_train, …)."""
        data = await self._request("GET", "/models", timeout=aiohttp.ClientTimeout(total=10))
        return {m["id"]: m.get("meta") or {} for m in data.get("data", []) if "id" in m}

    async def audio_post(self, path: str, wav: bytes) -> dict[str, Any]:
        """POST a WAV to an audio endpoint (AAC senses: /audio/tags, /audio/voiceprint)."""
        return await self._request("POST", path, data=wav, timeout=aiohttp.ClientTimeout(total=30, sock_connect=3))

    async def context_size(self) -> int | None:
        """The context the server runs with (llama.cpp /props), None when it does not say."""
        root = self._base_url[:-3] if self._base_url.endswith("/v1") else self._base_url
        try:
            async with self._session.get(f"{root}/props", headers=self._headers,
                                         timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001 - only a hint: any failure means "not known"
            return None
        n_ctx = (data.get("default_generation_settings") or {}).get("n_ctx") if isinstance(data, dict) else None
        return int(n_ctx) if isinstance(n_ctx, int) and n_ctx > 0 else None

    async def list_models(self) -> list[str]:
        """Return the model IDs the server offers."""
        data = await self._request(
            "GET", "/models", timeout=aiohttp.ClientTimeout(total=10)
        )
        return [m["id"] for m in data.get("data", []) if "id" in m]

    async def transcribe(
        self, model: str, wav: bytes, language: str | None = None, prompt: str | None = None
    ) -> dict[str, Any]:
        """Speech to text (OpenAI /audio/transcriptions, verbose_json when the server has it).

        Returns {"text", "language", "confidence" (0..1 or None), "words" ([{word, start, end}]),
        "segments" ([{text, start, end}]: the model's sentences with times, when the server gives them)}.
        Without a language the server detects it (multilingual models: German and English alike).
        prompt = what was said just before (context: keeps short pieces in the right language and topic).
        """
        def form(verbose: bool) -> aiohttp.FormData:
            fd = aiohttp.FormData()
            fd.add_field("file", wav, filename="speech.wav", content_type="audio/wav")
            fd.add_field("model", model)
            fd.add_field("response_format", "verbose_json" if verbose else "json")
            if verbose:
                fd.add_field("timestamp_granularities[]", "word")
                fd.add_field("timestamp_granularities[]", "segment")
            if language:
                fd.add_field("language", language)
            if prompt:
                fd.add_field("prompt", prompt[-500:])
            return fd

        try:
            data = await self._request("POST", "/audio/transcriptions", data=form(True))
        except ProviderAuthError:
            raise
        except ProviderConnectionError:
            raise
        except ProviderError:  # some servers only know plain json
            data = await self._request("POST", "/audio/transcriptions", data=form(False))
        if not isinstance(data, dict) or "text" not in data:
            raise ProviderError(f"Unexpected response: {str(data)[:200]}")
        segments = data.get("segments") or []
        logprobs = [s["avg_logprob"] for s in segments if isinstance(s, dict) and "avg_logprob" in s]
        confidence = None
        if logprobs:
            confidence = round(math.exp(sum(logprobs) / len(logprobs)), 3)
        return {
            "text": str(data["text"]).strip(),
            "language": data.get("language"),
            "confidence": confidence,
            "words": [
                {"word": w.get("word", ""), "start": w.get("start"), "end": w.get("end")}
                for w in data.get("words") or [] if isinstance(w, dict)
            ],
            "segments": [
                {"text": str(s.get("text", "")).strip(), "start": s.get("start"), "end": s.get("end")}
                for s in segments if isinstance(s, dict) and isinstance(s.get("start"), (int, float))
                and isinstance(s.get("end"), (int, float))
            ],
        }

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Embedding vectors for texts (OpenAI /embeddings)."""
        data = await self._request("POST", "/embeddings", json={"model": model, "input": texts})
        try:
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            return [item["embedding"] for item in items]
        except (KeyError, TypeError) as err:
            raise ProviderError(f"Unexpected response: {str(data)[:200]}") from err

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """Run one chat completion and return the first choice's message."""
        payload: dict[str, Any] = {"model": model, "messages": messages, **params}
        if tools:
            payload["tools"] = tools
        started = time.monotonic()
        data = await self._request("POST", "/chat/completions", json=payload)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as err:
            raise ProviderError(f"Unexpected response: {data}") from err
        # Speed for the pool: llama.cpp reports generation speed exactly, else tokens / time
        tokens = (data.get("usage") or {}).get("completion_tokens")
        speed = (data.get("timings") or {}).get("predicted_per_second")
        if speed is None and tokens:
            speed = tokens / max(time.monotonic() - started, 0.001)
        return {**message, "_stats": {"completion_tokens": tokens, "tokens_per_second": speed}}
