"""Shared fixtures."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aac.const import DOMAIN
from homeassistant.core import HomeAssistant

BASE_URL = "http://llm.local:8080/v1"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom integrations."""
    return


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """The AAC entry."""
    return MockConfigEntry(domain=DOMAIN, title="AAC", data={}, version=3)


def v1_files(tmp_config: str, lists: list[dict[str, Any]] | None = None) -> None:
    """What v1 left behind in .storage (models, charter, entity access, lists)."""
    from pathlib import Path

    storage = Path(tmp_config) / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "aac.config").write_text(json.dumps({"version": 1, "key": "aac.config", "data": {
        "models": [{"id": "m_big", "name": "Big", "base_url": BASE_URL, "model": "big"},
                   {"id": "m_small", "name": "Small", "base_url": BASE_URL, "model": "small"}],
        "pools": {"master": ["m_big", "m_small"]},
        "graphs": {"assistant": {"modules": {"charter": {"settings": {"text": "Be kind."}}}}},
        "domains": [{"id": "assistant", "visible": ["sensor.outside"]}],
        "entity_access": {"switch.lamp": {"read": True, "write": True}}}}))
    (storage / "aac.mod.lists.lists").write_text(json.dumps({"version": 1, "key": "aac.mod.lists.lists", "data": {
        "lists": lists or [{"id": "l1", "name": "Project Portfolio", "view": "checklist", "created": "", "updated": "",
                            "items": [{"id": "i1", "text": "Car Projects", "done": False, "fields": {}, "children": [
                                {"id": "i2", "text": "ESP32 on the CAN bus", "done": False, "fields": {"status": "Parts there"}}]}]}]}}))


async def setup_aac(hass: HomeAssistant, entry: MockConfigEntry):
    """Set up AAC; returns the core."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def reply(text: str = "", calls: list[tuple[str, dict]] | None = None) -> dict[str, Any]:
    """A model's (non-streamed) answer."""
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                                 for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": message}]}


def model_answers(aioclient_mock, *replies: dict[str, Any]) -> list[dict[str, Any]]:
    """The model server answers in turn (the last one repeats); returns what was asked."""
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    asked, queue = [], list(replies)

    async def side_effect(method, url, data):
        asked.append(data)
        return AiohttpClientMockResponse(method, url, json=queue.pop(0) if len(queue) > 1 else queue[0])

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    return asked
