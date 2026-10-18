"""Optional tests against a real model server.

Run with: pytest -m live -s
Uses AAC_LIVE_URL (default http://127.0.0.1:8080/v1) and AAC_LIVE_KEY.
"""

import os
from unittest.mock import patch

import pytest

from custom_components.aac.store import STORAGE_KEY
from homeassistant.components import conversation
from homeassistant.components.llm.llm import GetDateTimeTool
from homeassistant.core import Context, HomeAssistant

from .conftest import setup_aac, store_data

LIVE_URL = os.environ.get("AAC_LIVE_URL", "http://127.0.0.1:8080/v1")

pytestmark = [
    pytest.mark.live,
    pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"]),
]


async def _ask(hass, hass_storage, entry, question: str, **master) -> str:
    data = store_data(prompt="Answer with one short sentence.", **master)
    data["data"]["workers"][0].update(
        base_url=LIVE_URL, api_key=os.environ.get("AAC_LIVE_KEY"), model="any"
    )
    hass_storage[STORAGE_KEY] = data
    await setup_aac(hass, entry)
    result = await conversation.async_converse(
        hass, question, None, Context(), agent_id=entry.entry_id
    )
    speech = result.response.speech["plain"]["speech"]
    print(f"\nModel said: {speech}")
    return speech


async def test_live_answer(hass: HomeAssistant, hass_storage, mock_entry, socket_enabled):
    """Ask the real model one question."""
    speech = await _ask(hass, hass_storage, mock_entry, "What is the capital of France?")
    assert "paris" in speech.lower()


async def test_live_tool_call(hass: HomeAssistant, hass_storage, mock_entry, socket_enabled):
    """The real model uses an HA tool (GetDateTime) to answer."""
    original = GetDateTimeTool.async_call
    with patch.object(
        GetDateTimeTool, "async_call", autospec=True, side_effect=original
    ) as spy:
        speech = await _ask(
            hass, hass_storage, mock_entry, "What time is it right now? Use a tool.",
            llm_hass_api=["assist"],
        )
    assert spy.called, f"model did not call the tool, said: {speech}"


@pytest.mark.parametrize("tools", [False, True])
async def test_live_heartbeat(hass: HomeAssistant, hass_storage, mock_entry, socket_enabled, tools):
    """A heartbeat on a quiet home should end silent, with or without tools."""
    data = store_data(llm_hass_api=["assist"])
    data["data"]["workers"][0].update(
        base_url=LIVE_URL, api_key=os.environ.get("AAC_LIVE_KEY"), model="any"
    )
    data["data"]["heartbeat"] = {"tools": tools}
    hass_storage[STORAGE_KEY] = data
    hass.states.async_set("light.desk", "off", {"friendly_name": "Desk lamp"})
    await setup_aac(hass, mock_entry)
    run_id = await mock_entry.runtime_data.triggers.async_heartbeat(force=True)
    run = mock_entry.runtime_data.runs.get(run_id)
    print(f"\nHeartbeat (tools={tools}): {run.status} after {len(run.steps)} steps: {run.result}")
    assert run.status == "silent", run.result


async def test_live_heartbeat_speaks_up(hass: HomeAssistant, hass_storage, mock_entry, socket_enabled):
    """Something really wrong (smoke) must not be swallowed."""
    data = store_data(llm_hass_api=["assist"])
    data["data"]["workers"][0].update(
        base_url=LIVE_URL, api_key=os.environ.get("AAC_LIVE_KEY"), model="any"
    )
    hass_storage[STORAGE_KEY] = data
    hass.states.async_set(
        "binary_sensor.kitchen_smoke", "on", {"friendly_name": "Kitchen smoke detector", "device_class": "smoke"}
    )
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    async_expose_entity(hass, "conversation", "binary_sensor.kitchen_smoke", True)
    await setup_aac(hass, mock_entry)
    run_id = await mock_entry.runtime_data.triggers.async_heartbeat(force=True)
    run = mock_entry.runtime_data.runs.get(run_id)
    print(f"\nHeartbeat with smoke: {run.status}: {run.result}")
    assert "smoke" in run.context.lower(), "smoke detector not in the snapshot"
    assert run.status == "done" and "smoke" in run.result.lower()
