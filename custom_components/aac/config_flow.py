"""Config flow for AAC – Astor's AI Core.

Deliberately empty: adding AAC is one click, everything else happens in
the AAC panel (see docs on the panel design).
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DEFAULT_NAME, DOMAIN


class AACConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add AAC."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then create the (single) entry."""
        if user_input is not None:
            return self.async_create_entry(title=DEFAULT_NAME, data={})
        return self.async_show_form(step_id="user")
