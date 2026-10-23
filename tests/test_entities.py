"""Two layers: what a domain can see (tick list) and read/write per entity; requests the owner decides."""

import json

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.aac.entities import level

from .conftest import setup_aac
from .test_planning import _chat, _queue, _text, _tool_reply


def _call(name, args):
    return _tool_reply(name, json.dumps(args))


async def test_layers(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """Start from HA's Assist exposure; the tick list limits what a domain sees; read/write is separate."""
    await setup_aac(hass, mock_entry)
    hass.states.async_set("light.desk", "on", {"friendly_name": "Desk"})
    hass.states.async_set("sensor.temp", "21", {"friendly_name": "Temperature", "unit_of_measurement": "°C"})
    hass.states.async_set("lock.door", "locked", {"friendly_name": "Door"})
    async_expose_entity(hass, "conversation", "light.desk", True)
    settings = mock_entry.runtime_data.store.default_domain
    assert level(hass, settings, "light.desk") == "control" and level(hass, settings, "sensor.temp") == "invisible"

    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/domain/visible/set", "visible": ["light.desk", "sensor.temp"]})
    visible = {e["entity_id"]: e["visible"] for e in (await ws.receive_json())["result"]["entities"]}
    assert visible["sensor.temp"] and not visible["lock.door"]
    assert level(hass, settings, "sensor.temp") == "hidden"  # seen, but no read yet

    await ws.send_json_auto_id({"type": "aac/entities/set", "access": {"sensor.temp": {"read": True}, "light.desk": {"write": False},
                                                                         "lock.door": {"write": True}}})
    access = {e["entity_id"]: (e["read"], e["write"]) for e in (await ws.receive_json())["result"]["entities"]}
    assert access["sensor.temp"] == (True, False) and access["light.desk"] == (True, False) and access["lock.door"] == (True, True)
    assert level(hass, settings, "sensor.temp") == "read" and level(hass, settings, "light.desk") == "read"
    assert level(hass, settings, "lock.door") == "invisible"  # allowed to write, but this domain cannot see it

    from custom_components.aac.context import home_snapshot

    snap = home_snapshot(hass, settings)
    assert "Temperature | 21 °C" in snap and "Desk" in snap and "Door" not in snap

    created = await mock_entry.runtime_data.store.async_create_domain("Work")
    assert created.visible == [] and level(hass, created, "light.desk") == "invisible"  # new domains see nothing


async def test_request_access_and_control(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """AAC asks with a reason for what it can see; the owner allows part; control only with write."""
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {
        "fan": {"name": "Fan"}, "heater": {"name": "Heater"}, "safe": {"name": "Safe"}}})
    await setup_aac(hass, mock_entry)
    store = mock_entry.runtime_data.store
    await store.default_domain.async_set_visible(["input_boolean.fan", "input_boolean.heater"])
    await store.async_set_entity_access({})
    _queue(
        aioclient_mock,
        _call("home__control", {"entity_id": "input_boolean.fan", "action": "turn_on"}),
        _call("home__request_access", {"entity_ids": ["input_boolean.fan", "input_boolean.heater", "input_boolean.safe"],
                                           "level": "control", "reason": "To switch the fan when it is hot."}),
        _text("I asked you for access."),
    )
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "Turn on the fan.")
    results = [s for s in message["steps"] if s["type"] == "tool_result"]
    assert results[0]["error"] and hass.states.get("input_boolean.fan").state == "off"
    await ws.send_json_auto_id({"type": "aac/requests"})
    request = (await ws.receive_json())["result"]["requests"][0]
    assert [i["id"] for i in request["items"]] == ["input_boolean.fan", "input_boolean.heater"]  # not the invisible safe
    await ws.send_json_auto_id({"type": "aac/request/decide", "request_id": request["id"], "approve": ["input_boolean.fan"]})
    assert (await ws.receive_json())["result"]["status"] == "partly"
    assert store.entity_access == {"input_boolean.fan": {"read": True, "write": True}}
    assert "approved part of" in mock_entry.runtime_data.chats.get("aac")["messages"][-1]["text"]

    aioclient_mock.clear_requests()  # the first mock would still answer
    _queue(aioclient_mock, _call("home__control", {"entity_id": "input_boolean.fan", "action": "turn_on"}), _text("On."))
    await _chat(ws, "Now turn on the fan.")
    assert hass.states.get("input_boolean.fan").state == "on"


async def test_migration_from_per_domain_levels(hass: HomeAssistant, hass_storage, mock_entry):
    """0.21 had levels per domain; a domain that followed HA's exposure keeps its entities."""
    from custom_components.aac.store import STORAGE_KEY

    from .conftest import MODEL_CFG

    hass.states.async_set("light.desk", "on")
    hass.states.async_set("sensor.soc", "66")
    async_expose_entity(hass, "conversation", "light.desk", True)
    hass_storage[STORAGE_KEY] = {"version": 1, "key": STORAGE_KEY, "data": {
        "models": [dict(MODEL_CFG)], "pools": {"master": [MODEL_CFG["id"]]},
        "domains": [{"id": "assistant", "name": "Assistant", "entities": None},
                    {"id": "test", "name": "Test", "entities": {"sensor.soc": "read"}}]}}
    await setup_aac(hass, mock_entry)
    store = mock_entry.runtime_data.store
    assert store.entity_access == {"light.desk": {"read": True, "write": True}, "sensor.soc": {"read": True, "write": False}}
    assert store.domains["test"].visible == ["sensor.soc"] and store.domains["assistant"].visible is None
    assert level(hass, store.domains["assistant"], "light.desk") == "control"
    assert level(hass, store.domains["test"], "sensor.soc") == "read"


async def test_overview_and_visibility_request(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """AAC knows which integrations exist even when it cannot see their entities, and can ask to see them."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from homeassistant.helpers import entity_registry as er

    steam = MockConfigEntry(domain="steam_online", title="Steam")
    steam.add_to_hass(hass)
    reg = er.async_get(hass)
    for uid in ("me", "friend"):
        e = reg.async_get_or_create("sensor", "steam_online", uid, config_entry=steam, suggested_object_id=f"steam_{uid}")
        hass.states.async_set(e.entity_id, "online")
    await setup_aac(hass, mock_entry)
    await mock_entry.runtime_data.store.default_domain.async_set_visible([])
    _queue(
        aioclient_mock,
        _call("home__home_overview", {}),
        _call("home__request_visibility", {"integrations": ["steam_online"], "reason": "To see when you play.", "with_read": True}),
        _text("There is a Steam integration with 2 entities I cannot see yet; I asked you."),
    )
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "Is there a Steam integration?")
    overview = next(s for s in message["steps"] if s["type"] == "tool_result")
    assert '"integration":"steam_online"' in overview["data"].replace(" ", "") and '"visible":0' in overview["data"].replace(" ", "")
    await ws.send_json_auto_id({"type": "aac/requests"})
    request = (await ws.receive_json())["result"]["requests"][0]
    assert request["kind"] == "visibility" and request["items"][0]["label"] == "Steam"
    await ws.send_json_auto_id({"type": "aac/request/decide", "request_id": request["id"], "approve": True})
    await ws.receive_json()
    settings = mock_entry.runtime_data.store.default_domain
    assert sorted(settings.visible) == ["sensor.steam_friend", "sensor.steam_me"]
    assert level(hass, settings, "sensor.steam_me") == "read"


async def test_search_finds_entities_by_integration(hass: HomeAssistant, mock_entry, configured):
    """Steam's entities are named after people; searching 'steam' must still find them."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from homeassistant.helpers import entity_registry as er

    from custom_components.aac.entities import domain_view

    steam = MockConfigEntry(domain="steam_online", title="Astor")
    steam.add_to_hass(hass)
    e = er.async_get(hass).async_get_or_create("sensor", "steam_online", "astor", config_entry=steam, suggested_object_id="astor")
    hass.states.async_set(e.entity_id, "online", {"friendly_name": "Astor"})
    await setup_aac(hass, mock_entry)
    settings = mock_entry.runtime_data.store.default_domain
    await settings.async_set_visible([e.entity_id])
    found = [x for x in domain_view(hass, settings) if "steam" in f"{x['name']} {x['entity_id']} {x['integration']}".lower()]
    assert [(x["entity_id"], x["integration"], x["integration_title"]) for x in found] == [("sensor.astor", "steam_online", "Astor")]


async def test_list_entities_hints_rooms_and_finds_light_switches(hass: HomeAssistant, mock_entry, configured):
    """Asked in German, the model searched 'Schlafzimmer' for a room named 'Bedroom' and a bed light that is a
    switch (live, 2026-10-20): an empty result names the rooms; type light includes switches named like a light."""
    from homeassistant.helpers import area_registry as ar, entity_registry as er, llm

    from custom_components.aac.home_api import ListEntitiesTool

    bedroom = ar.async_get(hass).async_create("Bedroom")
    e = er.async_get(hass).async_get_or_create("switch", "test", "bed", suggested_object_id="bed_light")
    er.async_get(hass).async_update_entity(e.entity_id, area_id=bedroom.id)
    hass.states.async_set(e.entity_id, "off", {"friendly_name": "Bed light"})
    await setup_aac(hass, mock_entry)
    await mock_entry.runtime_data.store.default_domain.async_set_visible([e.entity_id])
    tool = ListEntitiesTool(mock_entry, mock_entry.runtime_data.domain())

    async def ask(**args):
        return (await tool.async_call(hass, llm.ToolInput(tool_name="list_entities", tool_args=args), None)).data

    missed = await ask(area="Schlafzimmer", type="light")
    assert missed["entities"] == [] and "Rooms here: Bedroom." in missed["hint"]
    assert [x["entity_id"] for x in (await ask(area="bedroom", type="light"))["entities"]] == [e.entity_id]
    assert "hint" not in await ask(search="bedroom")  # the room is searched too
