"""AAC – Astor's AI Core.

Home Assistant is the body (world layer), AAC is the brain on top.
This is the integration side: for now it provides a conversation agent
backed by any OpenAI-compatible model server.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BASE_URL
from .provider import OpenAICompatProvider, ProviderAuthError, ProviderError

PLATFORMS = (Platform.CONVERSATION,)

type AACConfigEntry = ConfigEntry[OpenAICompatProvider]


async def async_setup_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Set up AAC from a config entry."""
    provider = OpenAICompatProvider(
        async_get_clientsession(hass),
        entry.data[CONF_BASE_URL],
        entry.data.get(CONF_API_KEY),
    )
    try:
        await provider.list_models()
    except ProviderAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ProviderError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = provider
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: AACConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
