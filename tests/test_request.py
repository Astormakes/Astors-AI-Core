"""The request record and its chain (spec/v1.md): system parts in order, recalls in the context, tool sets."""

from homeassistant.core import HomeAssistant

from custom_components.aac.request import ModelRequest, async_prepare

from .conftest import memory_of, setup_aac


async def test_the_chain_fills_the_request(hass: HomeAssistant, mock_entry, configured):
    """Charter first, then the agent's prompt; memory adds what it recalls; the home and the modules add their
    tools; setup only with the owner in the chat; nothing from modules that are off."""
    from custom_components.aac.modules import async_set_access

    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    await memory_of(hass).async_remember("The drill is on basement shelf 2.", "fact")
    request = await async_prepare(hass, mock_entry, ModelRequest(domain=data.domain(), text="Where is the drill?"))
    assert request.system[0].startswith("Charter") and request.system[1] == "You are a test."
    assert any("basement shelf 2" in c for c in request.context)
    assert request.tools[:2] == ["aac_home", "aac_calendar"] and "aac_memory" in request.tools
    assert "aac_setup" not in request.tools  # nobody is logged in here

    await async_set_access(mock_entry, "lists", "assistant", {"enabled": False})
    request = await async_prepare(hass, mock_entry, ModelRequest(domain=data.domain(), text="hi"))
    assert "aac_lists" not in request.tools

    nodes = {n["id"]: n for n in data.bus.summary("assistant")["nodes"]}
    assert nodes["charter"]["kind"] == "annotating" and nodes["memory"]["order"] == 20
    wires = data.bus.summary("assistant")["wires"]
    assert {"from": "chat.requests", "to": "charter.requests"} in wires  # the chain starts at the sources
