"""AAC memory: store, hybrid search, tools, WebSocket API."""

import math

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.aac.memory import MemoryStore, fuse
from homeassistant.core import HomeAssistant

from .conftest import BASE_URL, setup_aac

# A tiny fake "embedding": counts of a few topic words, so meaning-like search is testable
TOPICS = ["milk", "oat", "soy", "drill", "tool", "basement", "battery", "printer"]
SYNONYMS = {"dairy": "milk", "hammer": "tool", "cellar": "basement", "screwdriver": "tool"}


def fake_vector(text: str) -> list[float]:
    words = [SYNONYMS.get(w, w) for w in text.lower().replace(".", " ").replace("?", " ").split()]
    vec = [float(sum(w.startswith(t) for w in words)) for t in TOPICS]
    return vec if any(vec) else [0.01] * len(TOPICS)


async def fake_embed(texts):
    return [fake_vector(t) for t in texts], "fake-embed"


async def test_store_hybrid_search(hass: HomeAssistant, tmp_path):
    """Keywords find exact words, vectors find meaning; both merge."""
    memory = MemoryStore(hass, tmp_path / "m.db", fake_embed)
    await memory.async_open()
    await memory.async_remember("Oat milk from brand X was good.", "preference")
    await memory.async_remember("Soy milk was crap.", "preference")
    await memory.async_remember("The drill is on basement shelf 2.", "fact")
    belief = await memory.async_remember("The user probably likes oat milk.", source="agent believes")
    assert belief.confirmed is False

    # keyword hit (exact word)
    assert (await memory.async_recall("drill"))[0].text.startswith("The drill")
    # meaning hit without a shared word: "cellar" ~ basement, "screwdriver" ~ tool
    found = await memory.async_recall("what is in the cellar?")
    assert found and "basement" in found[0].text
    # update text re-embeds; delete forgets
    await memory.async_update(belief.id, confirmed=True)
    assert (await memory.async_list(unconfirmed=True)) == []
    assert await memory.async_delete(belief.id)
    assert (await memory.async_count())["total"] == 3
    await memory.async_close()


async def test_store_without_embeddings_and_reembed(hass: HomeAssistant, tmp_path):
    """No embedding model: keywords only. Later a model fills in the vectors."""
    embedder = {"on": False}

    async def maybe(texts):
        return await fake_embed(texts) if embedder["on"] else None

    memory = MemoryStore(hass, tmp_path / "m.db", maybe)
    await memory.async_open()
    await memory.async_remember("Battery is LiFePO4, 8 cells.", "fact")
    assert (await memory.async_count())["embedded"] == 0
    assert (await memory.async_recall("battery"))[0].kind == "fact"
    embedder["on"] = True
    assert await memory.async_reembed() == 1
    assert (await memory.async_count())["embedded"] == 1
    await memory.async_close()


def test_fuse_drops_weak_vector_hits():
    """Reciprocal rank fusion; vector hits below the threshold do not count."""
    assert fuse(["a", "b"], [("b", 0.9), ("c", 0.1)]) == ["b", "a"]
    assert math.isclose(len(fuse([], [("x", 0.2)])), 0)


async def test_master_uses_memory_tools(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured
):
    """The master remembers via its tool and recalls later; the panel lists it."""
    await setup_aac(hass, mock_entry)
    replies = [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "r1", "type": "function", "function": {"name": "remember",
             "arguments": '{"text": "Soy milk was crap.", "kind": "preference"}'}}]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "Noted."}}]},
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "r2", "type": "function", "function": {"name": "recall", "arguments": '{"query": "soy milk"}'}}]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "You said soy milk was crap."}}]},
    ]
    payloads = []

    async def side_effect(method, url, data):
        payloads.append(data)
        return AiohttpClientMockResponse(method, url, json=replies.pop(0))

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/chat", "text": "Remember: soy milk was crap."})
    await ws.receive_json()
    await ws.send_json_auto_id({"type": "aac/chat", "text": "Which milk was bad?"})
    answer = (await ws.receive_json())["result"]["message"]
    assert answer["text"] == "You said soy milk was crap."
    recall_result = next(s for s in answer["steps"] if s["type"] == "tool_result")
    assert "Soy milk was crap." in recall_result["data"]
    assert "long-term memory" in payloads[0]["messages"][0]["content"]

    await ws.send_json_auto_id({"type": "aac/memory/list"})
    listed = (await ws.receive_json())["result"]
    assert listed["memories"][0]["text"] == "Soy milk was crap."
    assert listed["stats"]["total"] == 1


async def test_memory_ws_crud(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """Add, confirm, edit, search and delete from the panel."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {"type": "aac/memory/save", "text": "Drill: basement shelf 2", "kind": "fact", "source": "agent believes"}
    )
    mem = (await ws.receive_json())["result"]
    assert mem["confirmed"] is False
    await ws.send_json_auto_id({"type": "aac/memory/save", "memory_id": mem["id"], "confirmed": True, "text": "Drill: basement shelf 3"})
    mem = (await ws.receive_json())["result"]
    assert mem["confirmed"] is True and mem["text"].endswith("3")
    await ws.send_json_auto_id({"type": "aac/memory/list", "query": "drill"})
    assert (await ws.receive_json())["result"]["memories"][0]["id"] == mem["id"]
    await ws.send_json_auto_id({"type": "aac/memory/delete", "memory_id": mem["id"]})
    await ws.receive_json()
    await ws.send_json_auto_id({"type": "aac/memory/list"})
    assert (await ws.receive_json())["result"]["memories"] == []


async def test_embedding_model_setting(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured
):
    """Choosing an embedding model calls /embeddings and completes the setup step."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.post(
        f"{BASE_URL}/embeddings", json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}
    )
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/memory/settings", "embed_model": "m_test0001"})
    state = (await ws.receive_json())["result"]
    assert {s["id"]: s for s in state["setup"]}["memory"]["done"]
    await ws.send_json_auto_id({"type": "aac/memory/save", "text": "hello"})
    await ws.receive_json()
    await ws.send_json_auto_id({"type": "aac/memory/list"})
    stats = (await ws.receive_json())["result"]["stats"]
    assert stats["embedded"] == 1


async def test_memories_handed_to_model(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured
):
    """Matching memories are attached to the user message (prompt cache stays intact)."""
    await setup_aac(hass, mock_entry)
    await mock_entry.runtime_data.memory.async_remember("Oat milk from Alpro was really good.", "preference")
    payloads = []

    async def side_effect(method, url, data):
        payloads.append(data)
        return AiohttpClientMockResponse(method, url, json={"choices": [{"message": {"role": "assistant", "content": "Alpro oat milk."}}]})

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/chat", "text": "Which milk should I buy again?"})
    message = (await ws.receive_json())["result"]["message"]
    user_msg = payloads[0]["messages"][-1]
    assert user_msg["role"] == "user" and "Oat milk from Alpro" in user_msg["content"]
    assert "Oat milk" not in payloads[0]["messages"][0]["content"]  # system prompt untouched
    run = mock_entry.runtime_data.runs.get(message["run_id"])
    assert "Oat milk from Alpro" in run.context
    # The stored chat keeps what the user really typed
    chat = mock_entry.runtime_data.chats.get((await _last_chat(ws))["id"])
    assert chat["messages"][0]["text"] == "Which milk should I buy again?"


async def _last_chat(ws):
    await ws.send_json_auto_id({"type": "aac/chats"})
    return (await ws.receive_json())["result"]["chats"][0]


def test_keyword_terms_skip_filler():
    """Filler words do not count as matches."""
    from custom_components.aac.memory import _fts_query

    assert _fts_query("Which plant-based milk should I buy again?") == '"plant"* OR "based"* OR "milk"* OR "buy"*'
    assert _fts_query("Was ist das?") == ""
