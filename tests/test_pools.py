"""Pools: ordered lists per role, the broker falls back when a machine is off."""

import aiohttp

from homeassistant.core import HomeAssistant

from custom_components.aac.store import STORAGE_KEY

from .conftest import MODEL_CFG, setup_aac
from .test_planning import _chat, _queue, _text, _tool_reply

PC = {"id": "m_pc000001", "name": "PC", "base_url": "http://pc.test/v1", "api_key": None, "model": "big"}


def _with_pc(hass_storage) -> None:
    data = hass_storage[STORAGE_KEY]["data"]
    data["models"].append(dict(PC))
    data["pools"] = {"master": [PC["id"], MODEL_CFG["id"]], "memory": [], "stt": [], "tts": []}


async def test_old_single_choices_migrate(hass: HomeAssistant, hass_storage, mock_entry):
    """0.11 had one model per role; they become the first entry of each pool."""
    hass_storage[STORAGE_KEY] = {"version": 1, "key": STORAGE_KEY, "data": {
        "models": [dict(MODEL_CFG)], "master": {"model": MODEL_CFG["id"]},
        "memory": {"enabled": True, "embed_model": "m_x"}, "audio": {"stt": "stt.whisper", "tts": None}}}
    await setup_aac(hass, mock_entry)
    store = mock_entry.runtime_data.store
    assert store.pools == {"master": [MODEL_CFG["id"]], "memory": ["m_x"], "stt": ["stt.whisper"], "tts": []}
    assert "model" not in store.master and "embed_model" not in store.memory and "stt" not in store.audio


async def test_master_falls_back_when_pc_is_off(hass: HomeAssistant, hass_ws_client, hass_storage, aioclient_mock, mock_entry, configured):
    """The PC model is first; when its machine does not answer, the server model answers."""
    _with_pc(hass_storage)
    await setup_aac(hass, mock_entry)
    aioclient_mock.post("http://pc.test/v1/chat/completions", exc=aiohttp.ClientConnectionError("off"))
    payloads = _queue(aioclient_mock, _text("from the server"), _text("again"))
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "hi")
    assert message["text"] == "from the server"
    assert any("switched to m_test0001" in s.get("text", "") for s in message["steps"])
    status = mock_entry.runtime_data.broker.status()["roles"]["master"]
    assert status["active"] == MODEL_CFG["id"] and status["fallback"]
    assert status["items"][0]["state"] == "down"
    pc_calls = sum(1 for c in aioclient_mock.mock_calls if "pc.test" in str(c[1]))
    await _chat(ws, "hi again")  # within the minute: the PC is not tried again
    assert sum(1 for c in aioclient_mock.mock_calls if "pc.test" in str(c[1])) == pc_calls
    assert len(payloads) == 2


async def test_setup_tool_orders_the_pool(hass: HomeAssistant, hass_ws_client, hass_storage, aioclient_mock, mock_entry, configured):
    """use_model with position first/last builds the fallback order; remove takes one out."""
    hass_storage[STORAGE_KEY]["data"]["models"].append(dict(PC))
    await setup_aac(hass, mock_entry)
    _queue(
        aioclient_mock,
        _tool_reply("aac-setup__use_model", f'{{"role": "master", "model_id": "{PC["id"]}", "position": "first"}}'),
        _text("done"),
        _tool_reply("aac-setup__use_model", '{"role": "speech", "model_id": "tts.nope"}'),
        _text("no"),
    )
    ws = await hass_ws_client(hass)
    await _chat(ws, "Use the PC model first, the server one when the PC is off.")
    assert mock_entry.runtime_data.store.pools["master"] == [PC["id"], MODEL_CFG["id"]]
    aioclient_mock.post("http://pc.test/v1/chat/completions", exc=aiohttp.ClientConnectionError("off"))
    message = await _chat(ws, "Use tts.nope for speech.")
    assert next(s for s in message["steps"] if s["type"] == "tool_result")["error"]


async def test_pools_ws(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """aac/pools lists roles with state; aac/pool/set replaces a list (no duplicates)."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/pools"})
    pools = (await ws.receive_json())["result"]
    assert set(pools["pools"]) == {"thinking", "voice", "vision"}
    assert pools["roles"]["master"]["items"] == [{"id": MODEL_CFG["id"], "state": "ok", "active": True, "error": None}]
    await ws.send_json_auto_id({"type": "aac/pool/set", "role": "stt", "items": ["m_test0001", "m_test0001", "stt.gone"]})
    stt = (await ws.receive_json())["result"]["roles"]["stt"]
    assert [(i["id"], i["state"]) for i in stt["items"]] == [("m_test0001", "ok"), ("stt.gone", "missing")]
