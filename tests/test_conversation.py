"""Conversation agent tests with a mocked model server."""

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent

from .conftest import BASE_URL, setup_aac


def _reply(message: dict) -> dict:
    return {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}


async def test_setup_and_unload(hass: HomeAssistant, mock_entry, mock_panel):
    """Entry loads, creates the agent entity and the panel, and unloads."""
    await setup_aac(hass, mock_entry)
    assert hass.states.get("conversation.aac") is not None
    assert mock_panel["register"].called
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    assert mock_panel["remove"].called


async def test_no_master_yet(hass: HomeAssistant, mock_entry):
    """Without a master model the agent says where to set it up."""
    await setup_aac(hass, mock_entry)
    result = await conversation.async_converse(
        hass, "hi", None, Context(), agent_id=mock_entry.entry_id
    )
    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert "AAC panel" in result.response.speech["plain"]["speech"]


async def test_simple_answer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry, configured
):
    """Plain text answer, prompt is sent as system message."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.post(
        f"{BASE_URL}/chat/completions",
        json=_reply({"role": "assistant", "content": "Hello there"}),
    )
    result = await conversation.async_converse(
        hass, "hi", None, Context(), agent_id=mock_entry.entry_id
    )
    assert result.response.speech["plain"]["speech"] == "Hello there"

    assert aioclient_mock.mock_calls[-1][3] == {"Authorization": "Bearer secret"}
    payload = aioclient_mock.mock_calls[-1][2]
    assert payload["model"] == "test-model"
    assert payload["messages"][0]["role"] == "system"
    assert "You are a test." in payload["messages"][0]["content"]
    assert payload["messages"][-1] == {"role": "user", "content": "hi"}
    assert "tools" not in payload


async def test_tool_call_round_trip(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry, configured_control
):
    """Model calls an HA tool, gets the result, then answers."""
    await setup_aac(hass, mock_entry)
    replies = iter(
        [
            _reply(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "homeassistant__GetLiveContext", "arguments": "{}"},
                        }
                    ],
                }
            ),
            _reply({"role": "assistant", "content": "All quiet."}),
        ]
    )

    async def side_effect(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import (
            AiohttpClientMockResponse,
        )

        return AiohttpClientMockResponse(method, url, json=next(replies))

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    result = await conversation.async_converse(
        hass, "what's up?", None, Context(), agent_id=mock_entry.entry_id
    )
    assert result.response.speech["plain"]["speech"] == "All quiet."

    second = aioclient_mock.mock_calls[-1][2]
    assert any(t["function"]["name"] == "homeassistant__GetLiveContext" for t in second["tools"])
    assert second["messages"][-2]["tool_calls"][0]["id"] == "call_1"
    assert second["messages"][-1]["role"] == "tool"
    assert second["messages"][-1]["tool_call_id"] == "call_1"
    # The tool really ran (no entities exposed in the test instance)
    assert "expose entities" in second["messages"][-1]["content"]


async def test_server_error(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """A failing server gives an error response, not a crash."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.post(f"{BASE_URL}/chat/completions", status=500, text="boom")
    result = await conversation.async_converse(
        hass, "hi", None, Context(), agent_id=mock_entry.entry_id
    )
    assert result.response.response_type is intent.IntentResponseType.ERROR
