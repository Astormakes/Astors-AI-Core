"""Optional tests against a real model server.

Run with: pytest -m live -s
Uses AAC_LIVE_URL (default http://127.0.0.1:8080/v1) and AAC_LIVE_KEY.
"""

import os
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aac.const import CONF_BASE_URL, CONF_MODEL, DOMAIN
from homeassistant.components import conversation
from homeassistant.components.llm.llm import GetDateTimeTool
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import Context, HomeAssistant

LIVE_URL = os.environ.get("AAC_LIVE_URL", "http://127.0.0.1:8080/v1")

pytestmark = [
    pytest.mark.live,
    pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"]),
]


async def _ask(hass: HomeAssistant, question: str, **options) -> str:
    data = {CONF_BASE_URL: LIVE_URL, CONF_MODEL: "any"}
    if key := os.environ.get("AAC_LIVE_KEY"):
        data[CONF_API_KEY] = key
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options={CONF_PROMPT: "Answer with one short sentence.", **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await conversation.async_converse(
        hass, question, None, Context(), agent_id=entry.entry_id
    )
    speech = result.response.speech["plain"]["speech"]
    print(f"\nModel said: {speech}")
    return speech


async def test_live_answer(hass: HomeAssistant, socket_enabled):
    """Ask the real model one question."""
    speech = await _ask(hass, "What is the capital of France?")
    assert "paris" in speech.lower()


async def test_live_tool_call(hass: HomeAssistant, socket_enabled):
    """The real model uses an HA tool (GetDateTime) to answer."""
    original = GetDateTimeTool.async_call
    with patch.object(
        GetDateTimeTool, "async_call", autospec=True, side_effect=original
    ) as spy:
        speech = await _ask(
            hass, "What time is it right now? Use a tool.", **{CONF_LLM_HASS_API: ["assist"]}
        )
    assert spy.called, f"model did not call the tool, said: {speech}"
