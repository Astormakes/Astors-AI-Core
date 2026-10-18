"""Shared fixtures."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aac.const import CONF_BASE_URL, CONF_MODEL, DOMAIN
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

BASE_URL = "http://llm.test/v1"
MODEL = "test-model"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
async def setup_ha_core(hass: HomeAssistant) -> None:
    """Conversation needs the core `homeassistant` component (exposed entities)."""
    assert await async_setup_component(hass, "homeassistant", {})


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """A config entry without HA control."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"AAC ({MODEL})",
        data={CONF_BASE_URL: BASE_URL, CONF_MODEL: MODEL},
        options={CONF_PROMPT: "You are a test."},
    )


@pytest.fixture
def mock_entry_control(mock_entry) -> MockConfigEntry:
    """A config entry with the Assist API enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=mock_entry.title,
        data=dict(mock_entry.data),
        options={**mock_entry.options, CONF_LLM_HASS_API: ["assist"]},
    )
