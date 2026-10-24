"""Shared fixtures."""

from __future__ import annotations

import json
from pathlib import Path
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


BACKUPS = Path(__file__).parent / "backups"


@pytest.fixture(autouse=True, params=["fresh", "from_backup"])
def start(request, hass_storage):
    """Every test runs twice: on a fresh install, and on one that starts from the backup of this major version
    (tests/backups, see test_backups.py). A test that only makes sense on a fresh install says @pytest.mark.fresh."""
    if request.param == "from_backup":
        if request.node.get_closest_marker("fresh"):
            pytest.skip("only on a fresh install")
        saved = next((p for p in sorted(BACKUPS.iterdir()) if p.is_dir()), None) if BACKUPS.is_dir() else None
        if saved is None:
            pytest.skip("no backup yet")
        hass_storage.update(json.loads((saved / "storage.json").read_text()))
    return request.param


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """The AAC entry."""
    return MockConfigEntry(domain=DOMAIN, title="AAC", data={}, version=3)


async def setup_aac(hass: HomeAssistant, entry: MockConfigEntry):
    """Set up AAC; returns the core."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    core = entry.runtime_data
    for model_id, name in (("m_big", "big"), ("m_small", "small")):  # two models on the test server, a short charter
        core.save_model({"id": model_id, "name": name.title(), "base_url": BASE_URL, "model": name})
    await core.domain.update_card("charter", settings={"text": "Be kind."})
    return core


def reply(text: str = "", calls: list[tuple[str, dict]] | None = None) -> dict[str, Any]:
    """A model's (non-streamed) answer."""
    context: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        context["tool_calls"] = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                                 for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": context}]}


def model_answers(aioclient_mock, *replies: dict[str, Any]) -> list[dict[str, Any]]:
    """The model server answers in turn (the last one repeats); returns what was asked."""
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    asked, queue = [], list(replies)

    async def side_effect(method, url, data):
        asked.append(data)
        return AiohttpClientMockResponse(method, url, json=queue.pop(0) if len(queue) > 1 else queue[0])

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    return asked
