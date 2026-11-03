"""What the card layer reaches outside AAC besides Home Assistant's entities (docs/v2 page 6): the web, AAC's shared
files, Home Assistant's notifications; and its own data folder (<config>/aac/data: each domain's log, each card's
files), which is the card layer's own and needs no permission. The core's side of the Card's helpers (`fetch`, `read_file`, `write_file`,
`notification`): plain data in and out, so they answer over the worker's channel too, and each holds the card layer
to the domain's sheet (the owner's cap); the Card checks its own May use and asks the owner at ask.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
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

    # ------------------------------------------------------------------ the card layer's own data (<config>/aac/data)
    def _data(self, parts: list[str]) -> Path:
        """A path under <config>/aac/data from plain names (no folders in a name, no way up)."""
        if not parts or any(not re.fullmatch(r"[\w\-. ]+", p) or p.strip(".") == "" for p in parts):
            raise ValueError(f"not a plain name: {parts}")
        return Path(self.hass.config.path("aac", "data", *parts))

    async def data_read(self, parts: list[str]) -> bytes | None:
        """A file's bytes (None: there is none)."""
        path = self._data(parts)
        return await self.hass.async_add_executor_job(lambda: path.read_bytes() if path.is_file() else None)

    async def data_write(self, parts: list[str], data: bytes, append: bool = False) -> None:
        """Write a file (append: at its end; else whole, put in place at once)."""
        path = self._data(parts)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with path.open("ab") as f:
                    f.write(data)
                return
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        await self.hass.async_add_executor_job(write)

    async def data_list(self, parts: list[str], suffix: str = "") -> list[str]:
        """The files in a folder (with that ending), oldest first."""
        path = self._data(parts)

        def names() -> list[str]:
            if not path.is_dir():
                return []
            return [p.name for p in sorted((p for p in path.iterdir() if p.is_file() and p.name.endswith(suffix)),
                                           key=lambda p: p.stat().st_mtime)]
        return await self.hass.async_add_executor_job(names)

    async def data_remove(self, parts: list[str]) -> None:
        """A file or a folder goes."""
        path = self._data(parts)

        def remove() -> None:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        await self.hass.async_add_executor_job(remove)

    async def data_move(self, parts: list[str], to: list[str]) -> None:
        """A folder (or file) moves; what was at the new place is gone."""
        src, dst = self._data(parts), self._data(to)

        def move() -> None:
            if src.exists():
                if dst.is_dir():
                    shutil.rmtree(dst, ignore_errors=True)
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
        await self.hass.async_add_executor_job(move)
