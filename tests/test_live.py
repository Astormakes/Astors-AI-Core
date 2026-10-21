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

from .conftest import heartbeat_now, set_heartbeat, setup_aac, store_data, memory_of

LIVE_URL = os.environ.get("AAC_LIVE_URL", "http://127.0.0.1:8080/v1")

# Optional embedding server for the memory test (e.g. llama-server --embeddings with bge-m3)
EMBED_URL = os.environ.get("AAC_LIVE_EMBED_URL")
EMBED_HOST = EMBED_URL.split("/")[2].split(":")[0] if EMBED_URL else "127.0.0.1"

LIVE_HOST = LIVE_URL.split("/")[2].split(":")[0]

pytestmark = [
    pytest.mark.live,
    pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost", EMBED_HOST, LIVE_HOST]),
]


@pytest.fixture(autouse=True)
def _allow_live_hosts(socket_enabled):
    """The HA test plugin pins sockets to 127.0.0.1; allow the model and embedding servers."""
    import pytest_socket

    pytest_socket.socket_allow_hosts(["127.0.0.1", "::1", EMBED_HOST, LIVE_HOST], allow_unix_socket=True)


async def _ask(hass, hass_storage, entry, question: str, **master) -> str:
    data = store_data(prompt="Answer with one short sentence.", **master)
    data["data"]["models"][0].update(
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
    data["data"]["models"][0].update(
        base_url=LIVE_URL, api_key=os.environ.get("AAC_LIVE_KEY"), model="any"
    )
    hass_storage[STORAGE_KEY] = data
    hass.states.async_set("light.desk", "off", {"friendly_name": "Desk lamp"})
    await setup_aac(hass, mock_entry)
    await set_heartbeat(mock_entry, tools=tools)
    run = await heartbeat_now(hass)
    print(f"\nHeartbeat (tools={tools}): {run.status} after {len(run.steps)} steps: {run.result}")
    assert run.status == "silent", run.result


async def test_live_heartbeat_speaks_up(hass: HomeAssistant, hass_storage, mock_entry, socket_enabled):
    """Something really wrong (smoke) must not be swallowed."""
    data = store_data(llm_hass_api=["assist"])
    data["data"]["models"][0].update(
        base_url=LIVE_URL, api_key=os.environ.get("AAC_LIVE_KEY"), model="any"
    )
    hass_storage[STORAGE_KEY] = data
    hass.states.async_set(
        "binary_sensor.kitchen_smoke", "on", {"friendly_name": "Kitchen smoke detector", "device_class": "smoke"}
    )
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    async_expose_entity(hass, "conversation", "binary_sensor.kitchen_smoke", True)
    await setup_aac(hass, mock_entry)
    run = await heartbeat_now(hass)
    print(f"\nHeartbeat with smoke: {run.status}: {run.result}")
    assert "smoke" in run.context.lower(), "smoke detector not in the snapshot"
    assert run.status == "done" and "smoke" in run.result.lower()


@pytest.mark.extractor
async def test_live_memory_round_trip(hass: HomeAssistant, hass_storage, mock_entry, socket_enabled):
    """Tell it something in one chat, ask in a new one (needs remember + context/recall)."""
    data = store_data(prompt="You are a helpful home assistant. Answer briefly.")
    data["data"]["models"][0].update(base_url=LIVE_URL, api_key=os.environ.get("AAC_LIVE_KEY"), model="any")
    if EMBED_URL:
        data["data"]["models"].append(
            {"id": "m_embed", "name": "embed", "base_url": EMBED_URL,
             "api_key": os.environ.get("AAC_LIVE_EMBED_KEY"), "model": "bge-m3"}
        )
        data["data"]["memory"] = {"enabled": True, "embed_model": "m_embed"}
    hass_storage[STORAGE_KEY] = data
    await setup_aac(hass, mock_entry)

    await conversation.async_converse(
        hass, "Please remember: the oat milk from Alpro was great, the soy milk from Lidl was awful.",
        None, Context(), agent_id=mock_entry.entry_id,
    )
    stats = await memory_of(hass).async_count()
    print(f"\nStored memories: {stats}")
    assert stats["total"] >= 1, "the model did not call remember"
    if EMBED_URL:
        assert stats["embedded"] == stats["total"]

    result = await conversation.async_converse(
        hass, "Which plant-based milk should I buy again?", None, Context(), agent_id=mock_entry.entry_id
    )
    speech = result.response.speech["plain"]["speech"]
    print(f"Recall answer: {speech}")
    assert "alpro" in speech.lower() or "oat" in speech.lower()


@pytest.mark.extractor
async def test_live_goal_emerges(hass: HomeAssistant, hass_storage, mock_entry, socket_enabled):
    """No goal code: memory rules + self-scheduling should be enough for 'I want to lose weight'."""
    data = store_data(prompt="You are a caring home companion. Answer briefly.")
    data["data"]["models"][0].update(base_url=LIVE_URL, api_key=os.environ.get("AAC_LIVE_KEY"), model="any")
    if EMBED_URL:
        data["data"]["models"].append({"id": "m_embed", "name": "embed", "base_url": EMBED_URL,
                                       "api_key": os.environ.get("AAC_LIVE_EMBED_KEY"), "model": "bge-m3"})
        data["data"]["memory"] = {"enabled": True, "embed_model": "m_embed"}
    hass_storage[STORAGE_KEY] = data
    await setup_aac(hass, mock_entry)
    rt = mock_entry.runtime_data

    async def say(text, conversation_id=None):
        result = await conversation.async_converse(hass, text, conversation_id, Context(), agent_id=mock_entry.entry_id)
        print(f"\nUSER: {text}\nAAC:  {result.response.speech['plain']['speech']}")
        await hass.async_block_till_done(wait_background_tasks=True)  # memory extractor
        return result

    first = await say(
        "I want to lose about 4 kg by spring so I feel fitter. Today I weigh 82.4 kg. "
        "Could you check in with me on weekday mornings at 7:30?"
    )
    memories = await memory_of(hass).async_list()
    print("MEMORIES:", [(m.kind, m.text) for m in memories])
    print("TRIGGERS:", [(t["name"], t["when"], t["do"]["instruction"]) for t in rt.store.triggers])
    assert any(m.kind == "plan" for m in memories), "goal not remembered as a plan"
    assert any(t["created_by"] == "agent" and t["when"].get("at") == "07:30" for t in rt.store.triggers)

    # A week later, in a new conversation
    await say("Weighed myself this morning: 81.6 kg.")
    print("MEMORIES:", [(m.kind, m.text) for m in await memory_of(hass).async_list()])
    facts = await memory_of(hass).async_facts()
    print("FACTS:", [(f["subject"], f["attribute"], f["value"], f["unit"]) for f in facts])
    assert any(f["attribute"] == "weight" and f["value"] == 81.6 for f in facts), "81.6 kg not stored as a fact"
    final = await say("How am I doing with my weight goal?")
    speech = final.response.speech["plain"]["speech"].lower()
    assert "81.6" in speech or "0.8" in speech, "did not use the remembered measurements"
