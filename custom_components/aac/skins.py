"""Panel skins: CSS files that restyle the panel (the neon look is the base).

Built-in skins live in `www/skins/`, downloaded or own ones in `<config>/aac/skins/`
(served at /aac_skins). A skin starts with a comment header:
    /*
    name: Home Assistant
    description: …
    author: …
    */
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant

BUILTIN_DIR = Path(__file__).parent / "www" / "skins"
BUILTIN_URL = "/aac_static/skins"
USER_URL = "/aac_skins"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

NEON = {"id": "neon", "name": "Neon", "description": "The original: dark, thin glowing lines, cyberpunk.",
        "author": "AAC", "url": None, "source": "built-in"}


def user_dir(hass: HomeAssistant) -> Path:
    """Where own and downloaded skins go."""
    return Path(hass.config.path("aac", "skins"))


def _header(text: str) -> dict[str, str]:
    match = re.match(r"\s*/\*(.*?)\*/", text, re.S)
    fields = {}
    for line in (match.group(1) if match else "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    return fields


def _scan(folder: Path, url: str, source: str) -> list[dict[str, Any]]:
    skins = []
    if not folder.is_dir():
        return skins
    for path in sorted(folder.glob("*.css")):
        if not _ID_RE.match(path.stem):
            continue
        head = _header(path.read_text(encoding="utf-8", errors="replace")[:2000])
        skins.append({"id": path.stem, "name": head.get("name", path.stem), "description": head.get("description", ""),
                      "author": head.get("author", ""), "url": f"{url}/{path.name}", "source": source})
    return skins


def list_skins(hass: HomeAssistant) -> list[dict[str, Any]]:
    """All skins (blocking: run in the executor). Own skins win over built-in ones with the same id."""
    found = {s["id"]: s for s in _scan(BUILTIN_DIR, BUILTIN_URL, "built-in")}
    found.update({s["id"]: s for s in _scan(user_dir(hass), USER_URL, "own")})
    found.pop("neon", None)
    return [NEON, *found.values()]
