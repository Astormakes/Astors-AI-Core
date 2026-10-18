"""Model provider: a minimal client for OpenAI-compatible chat APIs.

Works with llama.cpp (llama-server), Ollama, vLLM, LM Studio and most cloud
APIs that speak the OpenAI chat completions format. This is the first
"model provider" from the framework design; more can be added later behind
the same small interface (list_models, chat).
"""

from __future__ import annotations

from typing import Any

import aiohttp


class ProviderError(Exception):
    """Generic provider error."""


class ProviderConnectionError(ProviderError):
    """The server could not be reached."""


class ProviderAuthError(ProviderError):
    """The server rejected the API key."""


class OpenAICompatProvider:
    """Talks to an OpenAI-compatible `/v1` endpoint."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 300,
    ) -> None:
        """Initialize the provider."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None, timeout=None
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=self._headers,
                timeout=timeout or self._timeout,
            ) as resp:
                if resp.status in (401, 403):
                    raise ProviderAuthError(await resp.text())
                if resp.status >= 400:
                    raise ProviderError(f"HTTP {resp.status}: {await resp.text()}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientConnectionError, TimeoutError) as err:
            raise ProviderConnectionError(str(err) or type(err).__name__) from err
        except aiohttp.ClientError as err:
            raise ProviderError(str(err)) from err

    async def list_models(self) -> list[str]:
        """Return the model IDs the server offers."""
        data = await self._request(
            "GET", "/models", timeout=aiohttp.ClientTimeout(total=10)
        )
        return [m["id"] for m in data.get("data", []) if "id" in m]

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
        data = await self._request("POST", "/chat/completions", json=payload)
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as err:
            raise ProviderError(f"Unexpected response: {data}") from err
