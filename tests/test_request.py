"""The message graph (spec/v1.md, flow.py): the automatic wiring by role, what reaches the model, Reply."""

from homeassistant.core import HomeAssistant

from custom_components.aac.broker import async_run_request

from .conftest import BASE_URL, memory_of, setup_aac


def _answer(aioclient_mock, text="ok"):
    calls = []

    async def side_effect(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        calls.append(data)
        return AiohttpClientMockResponse(method, url, json={"choices": [{"message": {"role": "assistant", "content": text}}]})

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    return calls


async def test_automatic_wiring_by_role(hass: HomeAssistant, mock_entry, configured):
    """Sources through the transforms (by order) into the model, the model into Reply and the keeper, every card
    onto the model's tool input; one type for request and answer."""
    await setup_aac(hass, mock_entry)
    wires = {(f"{a}.{ap}", f"{b}.{bp}") for a, ap, b, bp in mock_entry.runtime_data.bus.wires("assistant")}
    assert ("chat.out", "charter.in") in wires and ("charter.out", "agent.in") in wires and ("agent.out", "model.in") in wires
    assert ("voice.out", "charter.in") in wires and ("triggers.out", "charter.in") in wires
    assert not any(a.startswith("heartbeat.") for a, _ in wires)  # its module is off by default
    assert ("model.out", "reply.in") in wires and ("model.out", "memory_keeper.in") in wires
    assert {("home.tools", "model.tools"), ("memory.tools", "model.tools"), ("lists_tools.tools", "model.tools")} <= wires
    assert not any(b.startswith(("home.", "memory.")) for _, b in wires)  # cards hang on the model, not in the line


async def test_what_reaches_the_model(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """Charter first, then the agent's prompt; the attached memory card hands along what it recalls and gives its
    tools; the home and the modules' cards give theirs; setup only with the owner; the way is recorded."""
    from custom_components.aac.modules import async_set_access

    await setup_aac(hass, mock_entry)
    await memory_of(hass).async_remember("The drill is on basement shelf 2.", "fact")
    calls = _answer(aioclient_mock, "On shelf 2.")
    run, answer = await async_run_request(hass, mock_entry, "Where is the drill?", kind="chat", trigger="chat", title="t")
    assert answer.text == "On shelf 2." and run.model_node == "model"
    system, user = calls[0]["messages"][0]["content"], calls[0]["messages"][-1]["content"]
    assert system.startswith("Charter") and "You are a test." in system and "basement shelf 2" in user
    names = {t["function"]["name"].split("__")[0] for t in calls[0]["tools"]}
    assert {"aac-home", "aac-memory", "aac-lists"} <= names and "aac-setup" not in names
    nodes = [s["node"] for s in run.trace]
    assert nodes[:2] == ["charter", "agent"] and "memory" in nodes and nodes[-1] in ("model", "reply")

    await async_set_access(mock_entry, "lists", "assistant", {"enabled": False})
    await async_run_request(hass, mock_entry, "hi", kind="chat", trigger="chat", title="t")
    assert "aac-lists" not in {t["function"]["name"].split("__")[0] for t in calls[-1]["tools"]}
