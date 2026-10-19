"""Access for AAC's Home Assistant app (the satellite add-on next to a mic/speaker on the HA machine).

The app reads `<config>/aac/satellite.json` (url + token) instead of the owner copying a token.
AAC makes a separate, local-only HA user "AAC satellite" for it with a long-lived token, so
the access can be seen and revoked in HA's user list like any other.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from . import AACConfigEntry

USER_NAME = "AAC satellite"


def config_path(hass: HomeAssistant) -> Path:
    """Where the app finds its access."""
    return Path(hass.config.path("aac", "satellite.json"))


async def async_prepare(hass: HomeAssistant, entry: AACConfigEntry) -> dict[str, Any]:
    """Make (or keep) the app's user and token and write the access file."""
    path = config_path(hass)
    store = entry.runtime_data.store
    info = store.data.setdefault("satellite_app", {})
    user = await hass.auth.async_get_user(info["user_id"]) if info.get("user_id") else None
    if user is None:
        user = await hass.auth.async_create_user(USER_NAME, group_ids=[GROUP_ID_USER], local_only=True)
        info.clear()
        info["user_id"] = user.id
    exists = await hass.async_add_executor_job(path.exists)
    if not exists or not any(t.id == info.get("refresh_token_id") for t in user.refresh_tokens.values()):
        refresh = await hass.auth.async_create_refresh_token(
            user, client_name="AAC Home Assistant app", token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
            access_token_expiration=timedelta(days=3650),
        )
        token = hass.auth.async_create_access_token(refresh)
        info["refresh_token_id"] = refresh.id
        port = hass.http.server_port or 8123
        data = {"url": f"http://homeassistant:{port}", "token": token, "created": dt_util.utcnow().isoformat()}

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
            path.chmod(0o600)

        await hass.async_add_executor_job(write)
        await store.async_save()
    return {"user": USER_NAME, "file": str(path), "ready": True}
