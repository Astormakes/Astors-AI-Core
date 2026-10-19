"""Domains: whole AAC setups that share models and pools but keep their own settings and data."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.aac.store import STORAGE_KEY

from .conftest import setup_aac
from .test_planning import _chat, _queue, _text


async def test_old_config_becomes_the_assistant_domain(hass: HomeAssistant, hass_storage, mock_entry, configured):
    """Up to 0.14 the settings were at the top level: they become the first domain, nothing moves."""
    await setup_aac(hass, mock_entry)
    store = mock_entry.runtime_data.store
    assert list(store.domains) == ["assistant"] and store.default_domain.name == "Assistant"
    assert store.default_domain.master["prompt"] == "You are a test."
    assert store.data["voice_domain"] == "assistant"
    assert hass.states.get("conversation.aac") is not None  # same entity as before


async def test_create_chat_and_delete_a_domain(hass: HomeAssistant, hass_ws_client, hass_storage, aioclient_mock, mock_entry, configured):
    """A new domain gets its own agent, settings, chats and memory; deleting removes them."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/domain/create", "name": "Work", "copy_from": "assistant"})
    assert (await ws.receive_json())["result"] == {"domain": "work"}
    await hass.async_block_till_done(wait_background_tasks=True)

    data = mock_entry.runtime_data
    assert set(data.domains) == {"assistant", "work"}
    work = data.domains["work"]
    assert work.entity_id(hass, mock_entry.entry_id) == "conversation.aac_work"
    assert work.settings.master["prompt"] == "You are a test."  # copied settings

    ws = await hass_ws_client(hass)  # the old connection survives a reload, but start clean
    await ws.send_json_auto_id({"type": "aac/charter", "text": "Only work topics.", "domain": "work"})
    await ws.receive_json()
    payloads = _queue(aioclient_mock, _text("work answer"))
    await ws.send_json_auto_id({"type": "aac/chat", "text": "hi", "domain": "work"})
    reply = (await ws.receive_json())["result"]
    assert reply["message"]["text"] == "work answer"
    system = payloads[0]["messages"][0]["content"]
    assert system.startswith("Only work topics.")
    tools = {t["function"]["name"] for t in payloads[0]["tools"]}
    assert any(t.startswith("aac-memory-work") for t in tools), tools
    assert data.domains["assistant"].chats.list() == [] and len(work.chats.list()) == 1
    assert data.domains["assistant"].settings.data["charter"] == ""

    await ws.send_json_auto_id({"type": "aac/state", "domain": "work"})
    state = (await ws.receive_json())["result"]
    assert state["domain"]["id"] == "work" and [d["id"] for d in state["domains"]] == ["assistant", "work"]

    await ws.send_json_auto_id({"type": "aac/domain/delete", "domain_id": "assistant"})
    assert not (await ws.receive_json())["success"]  # the first one stays
    await ws.send_json_auto_id({"type": "aac/domain/delete", "domain_id": "work"})
    assert (await ws.receive_json())["success"]
    await hass.async_block_till_done(wait_background_tasks=True)
    assert set(mock_entry.runtime_data.domains) == {"assistant"}
    assert er.async_get(hass).async_get("conversation.aac_work") is None
    assert [d["id"] for d in hass_storage[STORAGE_KEY]["data"]["domains"]] == ["assistant"]
