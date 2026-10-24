"""Skins: CSS files that restyle the panel. The base look is Home Assistant's own (its theme's colors); a skin
overrides HA's theme variables inside the panel (`:host { --primary-color: …; }`) and may style anything else.

Built-in skins live in `www/skins/` (served at /aac_static/skins), own or downloaded ones in `<config>/aac/skins/`
(served at /aac_skins). A skin starts with a comment header:
    /*
    name: Neon
    description: …
    author: …
    */
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant

from .const import PANEL_URL

BUILTIN_DIR = Path(__file__).parent / "www" / "skins"
USER_URL = "/aac_skins"
HOME_ASSISTANT = {"id": "", "name": "Home Assistant", "description": "Your Home Assistant theme, as it is.",
                  "author": "AAC", "url": None, "source": "built-in"}
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


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
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.css")):
        if _ID.match(path.stem):
            head = _header(path.read_text(encoding="utf-8", errors="replace")[:2000])
            found.append({"id": path.stem, "name": head.get("name", path.stem), "description": head.get("description", ""),
                          "author": head.get("author", ""), "url": f"{url}/{path.name}", "source": source})
    return found


def list_skins(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Every skin (blocking: run it in the executor); an own skin wins over a built-in one with the same id."""
    found = {s["id"]: s for s in _scan(BUILTIN_DIR, f"{PANEL_URL}/skins", "built-in")}
    found.update({s["id"]: s for s in _scan(user_dir(hass), USER_URL, "own")})
    return [HOME_ASSISTANT, *found.values()]
