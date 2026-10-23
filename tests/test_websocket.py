"""Panel WebSocket API tests."""

import json
from pathlib import Path

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant

from .conftest import BASE_URL, MODEL, MODEL_CFG, setup_aac

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
    manifest = json.loads((Path(__file__).parents[1] / "custom_components/aac/manifest.json").read_text())
    assert state["version"] == manifest["version"]
    assert state["agent_entity_id"] == "conversation.aac"
    steps = {s["id"]: s for s in state["setup"]}
    assert not steps["models"]["done"]
    assert not steps["master"]["done"]
    assert "pipeline" not in steps  # HA's voice setup is not AAC's business


async def test_model_master_chat_flow(
    hass: HomeAssistant, hass_ws_client, aioclient_mock: AiohttpClientMocker, mock_entry
):
    """Test a server, save a model, make it master, chat with it."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    aioclient_mock.get(f"{BASE_URL}/models", json={"data": [{"id": MODEL}]})

    await ws.send_json_auto_id(
        {"type": "aac/server/models", "base_url": BASE_URL, "api_key": "k"}
    )
    assert (await ws.receive_json())["result"] == {"models": [MODEL]}

    await ws.send_json_auto_id(
        {"type": "aac/model/save", "name": "Box", "base_url": BASE_URL,
         "api_key": "k", "model": MODEL}
    )
    model_id = (await ws.receive_json())["result"]["id"]

    await ws.send_json_auto_id({"type": "aac/pool/set", "role": "master", "items": [model_id]})
    await ws.receive_json()
    await ws.send_json_auto_id({"type": "aac/state"})
    state = (await ws.receive_json())["result"]
    steps = {s["id"]: s for s in state["setup"]}
    assert steps["models"]["done"] and steps["master"]["done"]

    await ws.send_json_auto_id({"type": "aac/models"})
    models = (await ws.receive_json())["result"]
    assert models["manual"][0]["online"] is True
    assert models["manual"][0]["has_key"] is True
    assert "api_key" not in models["manual"][0]
    # The same model is not listed twice (manual wins over auto)
    assert models["auto"][0]["models"] == []

    aioclient_mock.post(f"{BASE_URL}/chat/completions", json=_reply("Hi from AAC"))
    await ws.send_json_auto_id({"type": "aac/chat", "text": "hello"})
    chat = (await ws.receive_json())["result"]
    assert chat["message"]["text"] == "Hi from AAC"
    assert chat["message"]["error"] is False
    assert chat["chat_id"]


async def test_auto_models_from_ha(
    hass: HomeAssistant, hass_ws_client, aioclient_mock: AiohttpClientMocker, mock_entry
):
    """A llama.cpp integration in HA shows up as auto models and can be master."""
    MockConfigEntry(
        domain="llama_cpp", title="Server", data={"base_url": LLAMA_URL, "api_key": "lk"}
    ).add_to_hass(hass)
    await setup_aac(hass, mock_entry)
    aioclient_mock.get(f"{LLAMA_URL}/models", json={"data": [{"id": "qwen3-8b"}]})
    ws = await hass_ws_client(hass)

    await ws.send_json_auto_id({"type": "aac/models"})
    auto = (await ws.receive_json())["result"]["auto"]
    assert auto[0]["online"] and auto[0]["label"] == "llama.cpp: Server"
    model_id = auto[0]["models"][0]["id"]

    await ws.send_json_auto_id({"type": "aac/pool/set", "role": "master", "items": [model_id]})
    await ws.receive_json()
    aioclient_mock.post(f"{LLAMA_URL}/chat/completions", json=_reply("auto ok"))
    await ws.send_json_auto_id({"type": "aac/chat", "text": "hello"})
    assert (await ws.receive_json())["result"]["message"]["text"] == "auto ok"
    assert aioclient_mock.mock_calls[-1][2]["model"] == "qwen3-8b"
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == "Bearer lk"


async def test_offline_server(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured
):
    """An offline server is reported, not raised."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.get(f"{BASE_URL}/models", exc=TimeoutError)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/models"})
    result = (await ws.receive_json())["result"]
    assert result["manual"][0]["online"] is False
    assert result["auto"][0]["online"] is False


async def test_delete_master_model(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """Deleting the master's model takes it out of the pool."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/model/delete", "model_id": MODEL_CFG["id"]})
    assert (await ws.receive_json())["success"]
    assert mock_entry.runtime_data.store.pools["master"] == []


async def test_audio_overview(hass: HomeAssistant, hass_ws_client, mock_entry):
    """AAC's own audio pipeline: planned stages and sources found in HA (read only)."""
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen satellite"})
    hass.states.async_set("camera.door", "idle", {"friendly_name": "Door"})
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/audio"})
    audio = (await ws.receive_json())["result"]
    assert audio["built"] is True
    assert [s["id"] for s in audio["stages"]][0] == "source"
    assert audio["stages"][-1]["id"] == "tts"
    assert {s["id"]: s["state"] for s in audio["stages"]}["arbitration"] == "planned"
    candidates = {s["id"]: s for s in audio["candidates"]}
    assert candidates["assist_satellite.kitchen"]["kind"] == "voice satellite"
    assert candidates["camera.door"]["name"] == "Door"
    assert audio["connected"] == [] and audio["settings"]["names"] == ["computer"]


async def test_admin_only(hass: HomeAssistant, hass_ws_client, hass_read_only_access_token, mock_entry):
    """Non-admins get nothing."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json_auto_id({"type": "aac/state"})
    msg = await ws.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "unauthorized"


async def test_charter_reaches_every_agent(
    hass: HomeAssistant, hass_ws_client, hass_storage, aioclient_mock: AiohttpClientMocker, mock_entry, configured
):
    """The charter module comes first in the system prompt (built-in draft when empty); 0.47's own text moves
    over; the setup tools can neither change nor switch it."""
    from custom_components.aac.store import STORAGE_KEY

    hass_storage[STORAGE_KEY]["data"]["charter"] = "Be kind. Never lie."  # 0.47: core, flat config
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    assert data.store.module_record("assistant", "charter")["settings"] == {"text": "Be kind. Never lie."}
    assert "charter" not in data.store.default_domain.data
    aioclient_mock.post(f"{BASE_URL}/chat/completions", json=_reply("ok"))
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/chat", "text": "hi"})
    await ws.receive_json()
    system = aioclient_mock.mock_calls[-1][2]["messages"][0]["content"]
    assert system.startswith("Be kind. Never lie.")

    await ws.send_json_auto_id({"type": "aac/module/settings", "module": "charter", "values": {"text": ""}})
    await ws.receive_json()
    async def system() -> str:
        await ws.send_json_auto_id({"type": "aac/chat", "text": "hi"})
        await ws.receive_json()
        return aioclient_mock.mock_calls[-1][2]["messages"][0]["content"]

    assert (await system()).startswith("Charter (the owner's values")  # empty = the built-in draft

    from custom_components.aac.setup_api import ModuleSettingTool

    tool = ModuleSettingTool(mock_entry, data.domain())
    result = await tool._async_run(hass, {"module": "charter", "on": False})
    assert result.error and (await system()).startswith("Charter")


async def test_single_model_server_counts_as_online(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """llama.cpp names its one model by the file path; the entry still counts as online, no duplicate."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.get(f"{BASE_URL}/models", json={"data": [{"id": "/models/test-model-Q4.gguf"}]})
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/models"})
    result = (await ws.receive_json())["result"]
    assert result["manual"][0]["online"] is True
    assert all(m["model"] != "/models/test-model-Q4.gguf" for s in result["auto"] for m in s["models"])


async def test_machines_group_models_and_take_names(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Models are grouped by the host they run on; the owner can name the machine."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.get(f"{BASE_URL}/models", json={"data": [{"id": "/models/test-model-Q4.gguf"}]})
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/models"})
    machines = (await ws.receive_json())["result"]["machines"]
    assert len(machines) == 1 and machines[0]["online"] and not machines[0]["named"]
    host = machines[0]["host"]
    assert [m["manual"] for m in machines[0]["models"]] == [True]
    await ws.send_json_auto_id({"type": "aac/machine/set", "host": host, "name": "Main PC"})
    assert (await ws.receive_json())["success"]
    await ws.send_json_auto_id({"type": "aac/models"})
    assert (await ws.receive_json())["result"]["machines"][0]["name"] == "Main PC"
