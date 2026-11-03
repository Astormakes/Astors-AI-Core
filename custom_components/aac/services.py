"""What the card layer reaches outside AAC besides Home Assistant's entities (docs/v2 page 6): the web, AAC's shared
files, Home Assistant's notifications. The core's side of the Card's helpers (`fetch`, `read_file`, `write_file`,
`notification`): plain data in and out, so they answer over the worker's channel too, and each holds the card layer
to the domain's sheet (the owner's cap); the Card checks its own May use and asks the owner at ask.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

import aiohttp

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .permissions import rank

if TYPE_CHECKING:
    from .core import Core


class Services:
    """The web, the shared files and the notifications, as far as the running domain allows."""

    def __init__(self, core: Core) -> None:
        self.core, self.hass = core, core.hass

    def _need(self, thing: str, level: str) -> None:
        if self.core.domain is None or rank(self.core.domain.allows(thing)) < rank(level):
            raise PermissionError(f"the domain does not allow {thing} at {level}")

    # ------------------------------------------------------------------ the web
    async def fetch(self, url: str, method: str = "GET", params: dict[str, Any] | None = None, body: Any = None,
                    headers: dict[str, str] | None = None, seconds: float = 20) -> dict[str, Any]:
        """One HTTP request: {status, text}."""
        self._need("web", "read")
        try:
            async with async_get_clientsession(self.hass).request(method, url, params=params, json=body, headers=headers,
                                                                  timeout=aiohttp.ClientTimeout(total=seconds)) as resp:
                return {"status": resp.status, "text": await resp.text()}
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ConnectionError(f"{type(err).__name__}: {err}") from err

    # ------------------------------------------------------------------ the shared files (<config>/aac/files)
    def _file(self, name: str) -> Path:
        return Path(self.hass.config.path("aac", "files")) / self.file_name(name)

    async def read_file(self, name: str) -> str:
        """A file's text ("" when there is none)."""
        self._need("files", "read")
        path = self._file(name)
        return await self.hass.async_add_executor_job(lambda: path.read_text(encoding="utf-8") if path.exists() else "")

    async def write_file(self, name: str, text: str, append: bool = False) -> str:
        """Write (or append to) a file; the whole file afterwards."""
        self._need("files", "ask")
        path = self._file(name)

        def write() -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a" if append else "w", encoding="utf-8") as f:
                f.write(text)
            return path.read_text(encoding="utf-8")
        return await self.hass.async_add_executor_job(write)

    @staticmethod
    def file_name(name: str) -> str:
        """A plain name in that folder (no folders)."""
        return re.sub(r"[^\w\- .]", "", name).strip(" .") or "notes"

    # ------------------------------------------------------------------ Home Assistant's notifications
    async def notification(self, service: str, message: str, title: str) -> None:
        """Through one of Home Assistant's notify services."""
        self._need("notify", "ask")
        if not self.hass.services.has_service("notify", service):
            raise ValueError(f"Home Assistant has no notify service {service}")
        await self.hass.services.async_call("notify", service, {"message": message, "title": title}, blocking=True)
