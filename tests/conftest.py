"""Shared fixtures."""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aac.const import DOMAIN
from custom_components.aac.store import STORAGE_KEY
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

BASE_URL = "http://llm.test/v1"
MODEL = "test-model"
MODEL_CFG = {
    "id": "m_test0001",
    "name": "Test server",
    "base_url": BASE_URL,
    "api_key": "secret",
    "model": MODEL,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
async def setup_ha_core(hass: HomeAssistant) -> None:
    """Conversation needs the core `homeassistant` component (exposed entities)."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "http", {})


@pytest.fixture(autouse=True)
def isolated_memory_db(tmp_path):
    """Each test gets its own memory database (HA's test config dir is shared)."""
    with patch("custom_components.aac.memory_path", side_effect=lambda hass, suffix="": tmp_path / f"memory{suffix}.db"):
        yield


@pytest.fixture(autouse=True)
def no_memory_extractor(request):
    """The extractor adds a model call per message; tests opt in with @pytest.mark.extractor."""
    if request.node.get_closest_marker("extractor"):
        yield
        return
    with patch("custom_components.aac.conversation.async_extract", new=AsyncMock(return_value=[])):
        yield


@pytest.fixture(autouse=True)
def mock_panel(hass: HomeAssistant) -> Generator[dict[str, MagicMock]]:
    """The real frontend needs half of HA; pretend it is loaded."""
    hass.config.components.update({"frontend", "panel_custom"})
    with (
        patch("custom_components.aac.panel_custom.async_register_panel", new=AsyncMock()) as reg,
        patch("custom_components.aac.frontend.async_remove_panel") as rem,
    ):
        yield {"register": reg, "remove": rem}


def store_data(tool_mode: str = "all", **master: Any) -> dict[str, Any]:
    """Stored AAC config with one manual model as master (all tools up front, unless tool_mode="menu")."""
    return {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {
            "models": [dict(MODEL_CFG)],
            "master": {"model": MODEL_CFG["id"], "prompt": "You are a test.",
                       "llm_hass_api": [], **master},
            "setup": {"skipped": []},
            "traits": {MODEL_CFG["id"]: {"tool_mode": tool_mode}},
        },
    }


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """The (only) AAC config entry."""
    return MockConfigEntry(domain=DOMAIN, title="AAC", data={}, version=2)


@pytest.fixture
def configured(hass_storage) -> None:
    """AAC with a master model, no HA control."""
    hass_storage[STORAGE_KEY] = store_data()


@pytest.fixture
def configured_menu(hass_storage) -> None:
    """AAC with a master model that gets its tools as a menu, and the Assist API."""
    hass_storage[STORAGE_KEY] = store_data(tool_mode="menu", llm_hass_api=["assist"])


@pytest.fixture
def configured_control(hass_storage) -> None:
    """AAC with a master model and the Assist API."""
    hass_storage[STORAGE_KEY] = store_data(llm_hass_api=["assist"])


async def setup_aac(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and load the entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
