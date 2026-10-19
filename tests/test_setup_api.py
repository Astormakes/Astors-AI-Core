"""AAC helps set itself up: setup tools only with the owner present."""

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant

from .conftest import MODEL_CFG, setup_aac
from .test_planning import _chat, _queue, _text, _tool_reply

SETUP_TOOLS = {
    "aac-setup__setup_status", "aac-setup__list_models", "aac-setup__add_model",
    "aac-setup__use_model", "aac-setup__set_heartbeat", "aac-setup__skip_setup_step",
}


def _names(payload: dict) -> set[str]:
    return {t["function"]["name"] for t in payload.get("tools") or []}


async def test_owner_chat_sets_up_heartbeat(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """In the owner's chat the master gets the setup tools and can change settings."""
    await setup_aac(hass, mock_entry)
    payloads = _queue(
        aioclient_mock,
        _tool_reply("aac-setup__setup_status", "{}"),
        _tool_reply("aac-setup__set_heartbeat", '{"enabled": true, "interval_minutes": 15, "quiet_start": "23:00"}'),
        _text("The heartbeat is on, every 15 minutes."),
    )
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "Help me set up the rest of AAC.")
    assert SETUP_TOOLS <= _names(payloads[0])
    status = next(s for s in message["steps"] if s["type"] == "tool_result")
    assert '"open"' in status["data"]
    hb = mock_entry.runtime_data.store.heartbeat
    assert hb["enabled"] and hb["interval"] == 15 and hb["quiet_start"] == "23:00"


async def test_no_setup_tools_without_owner(hass: HomeAssistant, aioclient_mock, hass_admin_user, hass_read_only_user, mock_entry, configured):
    """Anonymous voice or a non-admin user: no setup tools."""
    await setup_aac(hass, mock_entry)
    payloads = _queue(aioclient_mock, _text("hi"), _text("hi"), _text("hi"))
    for user_id in (None, hass_read_only_user.id):
        await conversation.async_converse(hass, "hi", None, Context(user_id=user_id), agent_id=mock_entry.entry_id)
    await conversation.async_converse(hass, "hi", None, Context(user_id=hass_admin_user.id), agent_id=mock_entry.entry_id)
    assert not _names(payloads[0]) & SETUP_TOOLS
    assert not _names(payloads[1]) & SETUP_TOOLS
    assert SETUP_TOOLS <= _names(payloads[2])


async def test_no_setup_tools_in_automatic_runs(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """A heartbeat with tools still never gets the setup tools."""
    await setup_aac(hass, mock_entry)
    await mock_entry.runtime_data.store.async_update_heartbeat({"tools": True})
    payloads = _queue(aioclient_mock, _text("NOTHING"), _text('{"speak": false, "message": ""}'))
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/heartbeat/run"})
    await ws.receive_json()
    assert _names(payloads[0]) and not _names(payloads[0]) & SETUP_TOOLS


async def test_add_and_use_models(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """add_model checks the server; use_model gives a model a role."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.get("http://embed.test/v1/models", json={"data": [{"id": "bge-m3"}]})
    _queue(
        aioclient_mock,
        _tool_reply("aac-setup__add_model", '{"base_url": "http://embed.test/v1", "model": "nope"}'),
        _tool_reply("aac-setup__add_model", '{"base_url": "http://embed.test/v1/", "model": "bge-m3", "name": "Embeddings"}'),
        _tool_reply("aac-setup__use_model", '{"role": "memory", "model_id": "m_unknown"}'),
        _text("Which one?"),
    )
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "My embedding server is http://embed.test/v1, use bge-m3.")
    results = [s for s in message["steps"] if s["type"] == "tool_result"]
    assert results[0]["error"] and "bge-m3" in results[0]["data"]
    assert not results[1]["error"]
    assert results[2]["error"]
    store = mock_entry.runtime_data.store
    added = next(m for m in store.models if m["name"] == "Embeddings")
    assert added["base_url"] == "http://embed.test/v1" and added["model"] == "bge-m3"

    aioclient_mock.clear_requests()  # the first mock would still answer
    _queue(aioclient_mock, _tool_reply("aac-setup__use_model", f'{{"role": "memory", "model_id": "{added["id"]}"}}'), _text("Done."))
    await _chat(ws, "Yes, use it for memory.")
    assert store.pools["memory"] == [added["id"]]
    assert store.pools["master"] == [MODEL_CFG["id"]]
