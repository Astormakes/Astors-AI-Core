"""AAC's own entity access per domain, its home tools and requests the owner decides."""

import json

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import setup_aac
from .test_planning import _chat, _queue, _text, _tool_reply


def _call(name, args):
    return _tool_reply(name, json.dumps(args))


async def test_access_follows_ha_until_chosen(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """Until the owner chooses, AAC follows HA's Assist exposure; the first change makes its own list."""
    await setup_aac(hass, mock_entry)
    hass.states.async_set("light.desk", "on", {"friendly_name": "Desk"})
    hass.states.async_set("sensor.temp", "21", {"friendly_name": "Temperature", "unit_of_measurement": "°C"})
    async_expose_entity(hass, "conversation", "light.desk", True)
    async_expose_entity(hass, "conversation", "sensor.temp", False)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/entities"})
    result = (await ws.receive_json())["result"]
    levels = {e["entity_id"]: e["level"] for e in result["entities"]}
    assert result["own_list"] is False and levels["light.desk"] == "control" and levels["sensor.temp"] == "hidden"
    await ws.send_json_auto_id({"type": "aac/entities/set", "levels": {"sensor.temp": "read", "light.desk": "read"}})
    result = (await ws.receive_json())["result"]
    assert result["own_list"] is True
    assert mock_entry.runtime_data.store.default_domain.entities == {"light.desk": "read", "sensor.temp": "read"}
    from custom_components.aac.context import home_snapshot

    snap = home_snapshot(hass, mock_entry.runtime_data.store.default_domain)
    assert "Temperature | 21 °C" in snap and "Desk" in snap


async def test_request_access_and_control(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """AAC asks with a reason; the owner allows part of it; control works only at 'control'."""
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"fan": {"name": "Fan"}, "heater": {"name": "Heater"}}})
    await setup_aac(hass, mock_entry)
    settings = mock_entry.runtime_data.store.default_domain
    await settings.async_set_entities({})
    _queue(
        aioclient_mock,
        _call("aac-home__control", {"entity_id": "input_boolean.fan", "action": "turn_on"}),
        _call("aac-home__request_access", {"entity_ids": ["input_boolean.fan", "input_boolean.heater"], "level": "control",
                                           "reason": "To switch the fan when it is hot."}),
        _text("I asked you for access."),
    )
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "Turn on the fan.")
    results = [s for s in message["steps"] if s["type"] == "tool_result"]
    assert results[0]["error"] and hass.states.get("input_boolean.fan").state == "off"
    await ws.send_json_auto_id({"type": "aac/state"})
    assert (await ws.receive_json())["result"]["requests_open"] == 1

    await ws.send_json_auto_id({"type": "aac/requests"})
    request = (await ws.receive_json())["result"]["requests"][0]
    assert request["reason"] == "To switch the fan when it is hot." and len(request["items"]) == 2
    await ws.send_json_auto_id({"type": "aac/request/decide", "request_id": request["id"], "approve": ["input_boolean.fan"]})
    decided = (await ws.receive_json())["result"]
    assert decided["status"] == "partly"
    assert settings.entities == {"input_boolean.fan": "control"}
    told = mock_entry.runtime_data.chats.get("aac")["messages"][-1]["text"]
    assert "approved part of" in told and "Fan" in told

    aioclient_mock.clear_requests()  # the first mock would still answer
    _queue(aioclient_mock, _call("aac-home__control", {"entity_id": "input_boolean.fan", "action": "turn_on"}), _text("On."))
    await _chat(ws, "Now turn on the fan.")
    assert hass.states.get("input_boolean.fan").state == "on"
