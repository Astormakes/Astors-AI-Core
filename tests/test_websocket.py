"""Panel WebSocket API tests."""

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant

from .conftest import BASE_URL, MODEL, WORKER, setup_aac

LLAMA_URL = "http://llama.test/v1"


def _reply(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


async def test_state_fresh(hass: HomeAssistant, hass_ws_client, mock_entry):
    """Fresh install: setup steps are open, version is reported."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/state"})
    msg = await ws.receive_json()
    assert msg["success"]
    state = msg["result"]
    assert state["version"] == "0.3.0"
    assert state["agent_entity_id"] == "conversation.aac"
    steps = {s["id"]: s for s in state["setup"]}
    assert not steps["worker"]["done"]
    assert not steps["master"]["done"]
    assert "pipeline" not in steps  # HA's voice setup is not AAC's business


async def test_worker_master_chat_flow(
    hass: HomeAssistant, hass_ws_client, aioclient_mock: AiohttpClientMocker, mock_entry
):
    """Test a server, save a worker, make it master, chat with it."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    aioclient_mock.get(f"{BASE_URL}/models", json={"data": [{"id": MODEL}]})

    await ws.send_json_auto_id(
        {"type": "aac/server/models", "base_url": BASE_URL, "api_key": "k"}
    )
    assert (await ws.receive_json())["result"] == {"models": [MODEL]}

    await ws.send_json_auto_id(
        {"type": "aac/worker/save", "name": "Box", "base_url": BASE_URL,
         "api_key": "k", "model": MODEL}
    )
    worker_id = (await ws.receive_json())["result"]["id"]

    await ws.send_json_auto_id({"type": "aac/master/update", "worker": worker_id})
    state = (await ws.receive_json())["result"]
    steps = {s["id"]: s for s in state["setup"]}
    assert steps["worker"]["done"] and steps["master"]["done"]

    await ws.send_json_auto_id({"type": "aac/workers"})
    workers = (await ws.receive_json())["result"]
    assert workers["manual"][0]["online"] is True
    assert workers["manual"][0]["has_key"] is True
    assert "api_key" not in workers["manual"][0]
    # The same model is not listed twice (manual wins over auto)
    assert workers["auto"][0]["workers"] == []

    aioclient_mock.post(f"{BASE_URL}/chat/completions", json=_reply("Hi from AAC"))
    await ws.send_json_auto_id({"type": "aac/chat", "text": "hello"})
    chat = (await ws.receive_json())["result"]
    assert chat["speech"] == "Hi from AAC"
    assert chat["error"] is False
    assert chat["conversation_id"]


async def test_auto_workers_from_ha(
    hass: HomeAssistant, hass_ws_client, aioclient_mock: AiohttpClientMocker, mock_entry
):
    """A llama.cpp integration in HA shows up as auto workers and can be master."""
    MockConfigEntry(
        domain="llama_cpp", title="Server", data={"base_url": LLAMA_URL, "api_key": "lk"}
    ).add_to_hass(hass)
    await setup_aac(hass, mock_entry)
    aioclient_mock.get(f"{LLAMA_URL}/models", json={"data": [{"id": "qwen3-8b"}]})
    ws = await hass_ws_client(hass)

    await ws.send_json_auto_id({"type": "aac/workers"})
    auto = (await ws.receive_json())["result"]["auto"]
    assert auto[0]["online"] and auto[0]["label"] == "llama.cpp: Server"
    worker_id = auto[0]["workers"][0]["id"]

    await ws.send_json_auto_id({"type": "aac/master/update", "worker": worker_id})
    await ws.receive_json()
    aioclient_mock.post(f"{LLAMA_URL}/chat/completions", json=_reply("auto ok"))
    await ws.send_json_auto_id({"type": "aac/chat", "text": "hello"})
    assert (await ws.receive_json())["result"]["speech"] == "auto ok"
    assert aioclient_mock.mock_calls[-1][2]["model"] == "qwen3-8b"
    assert aioclient_mock.mock_calls[-1][3] == {"Authorization": "Bearer lk"}


async def test_offline_server(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured
):
    """An offline server is reported, not raised."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.get(f"{BASE_URL}/models", exc=TimeoutError)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/workers"})
    result = (await ws.receive_json())["result"]
    assert result["manual"][0]["online"] is False
    assert result["auto"][0]["online"] is False


async def test_delete_master_worker(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """Deleting the master's worker unsets the master."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/worker/delete", "worker_id": WORKER["id"]})
    assert (await ws.receive_json())["success"]
    assert mock_entry.runtime_data.store.master["worker"] is None


async def test_audio_overview(hass: HomeAssistant, hass_ws_client, mock_entry):
    """AAC's own audio pipeline: planned stages and sources found in HA (read only)."""
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen satellite"})
    hass.states.async_set("camera.door", "idle", {"friendly_name": "Door"})
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/audio"})
    audio = (await ws.receive_json())["result"]
    assert audio["built"] is False
    assert [s["id"] for s in audio["stages"]][0] == "source"
    assert audio["stages"][-1]["id"] == "master"
    sources = {s["id"]: s for s in audio["sources"]}
    assert sources["assist_satellite.kitchen"]["kind"] == "voice satellite"
    assert sources["camera.door"]["name"] == "Door"
    assert "browser" in sources
    assert not any(s["connected"] for s in audio["sources"])


async def test_admin_only(hass: HomeAssistant, hass_ws_client, hass_read_only_access_token, mock_entry):
    """Non-admins get nothing."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json_auto_id({"type": "aac/state"})
    msg = await ws.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "unauthorized"
