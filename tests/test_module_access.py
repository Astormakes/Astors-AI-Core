"""Module permissions and properties per domain, enforced where the tools are handed out."""

from homeassistant.core import Context, HomeAssistant
from homeassistant.components import conversation

from .conftest import setup_aac
from .test_planning import _queue, _text


def _list_tools(payload: dict) -> set[str]:
    return {t["function"]["name"].split("__")[1] for t in payload.get("tools") or [] if t["function"]["name"].startswith("lists_tools__")}


async def _set(ws, **values):
    await ws.send_json_auto_id({"type": "aac/module/access", "module": "lists", "values": values})
    reply = await ws.receive_json()
    assert reply["success"], reply
    return reply["result"]


async def test_permissions(hass: HomeAssistant, hass_ws_client, hass_admin_user, aioclient_mock, mock_entry, configured):
    """Owner chat sees everything; read-only hides writing; off hides the module completely."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    payloads = _queue(aioclient_mock, *[_text("ok")] * 4)
    owner = Context(user_id=hass_admin_user.id)

    async def chat():
        await conversation.async_converse(hass, "hi", None, owner, agent_id=mock_entry.entry_id)
        return payloads[-1]

    assert _list_tools(await chat()) == {"lists", "read_list", "create_list", "add_items", "update_item", "remove_items", "delete_list"}
    await _set(ws, access="read")
    assert _list_tools(await chat()) == {"lists", "read_list"}
    view = await _set(ws, access="full", instructions="Always answer in rhymes about lists.", name="Notes")
    assert view["name"] == "Notes" and view["access"]["instructions"].startswith("Always")
    system = (await chat())["messages"][0]["content"]
    assert "Always answer in rhymes about lists." in system
    view = await _set(ws, instructions=None, enabled=False)
    assert view["access"]["instructions"] is None and view["pages"] == []
    assert _list_tools(await chat()) == set()
    await ws.send_json_auto_id({"type": "aac/state"})
    mods = {m["id"]: m for m in (await ws.receive_json())["result"]["modules"]}
    assert mods["lists"]["enabled"] is False and mods["lists"]["pages"] == []


async def test_disabled_module_leaves_the_calendar(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """A switched-off module provides no calendar entries in that domain."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    day = (dt_util.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    await ws.send_json_auto_id({"type": "aac/lists/op", "op": "create", "name": "Trips"})
    await ws.receive_json()
    await ws.send_json_auto_id({"type": "aac/lists/op", "op": "add", "list": "Trips", "text": "Berlin"})
    await ws.receive_json()
    await ws.send_json_auto_id({"type": "aac/lists/op", "op": "update", "list": "Trips", "item": "Berlin", "fields": {"when": day}})
    await ws.receive_json()
    caps = mock_entry.runtime_data.capabilities
    start = dt_util.start_of_local_day()
    assert [e["name"] for e in await caps.async_calendar("assistant", start, start + timedelta(days=7))] == ["Berlin"]
    await _set(ws, enabled=False)
    assert await caps.async_calendar("assistant", start, start + timedelta(days=7)) == []


async def test_module_setup_steps(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """The calendar module's step shows up per domain, can be skipped, and is done once a calendar exists."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)

    async def step():
        await ws.send_json_auto_id({"type": "aac/state"})
        steps = {s["id"]: s for s in (await ws.receive_json())["result"]["setup"]}
        return steps.get("assistant/mod/calendar/connect")

    first = await step()
    assert first and first["optional"] and not first["done"] and first["target"] == "settings/module/calendar"
    await ws.send_json_auto_id({"type": "aac/setup/skip", "step": first["id"], "skipped": True})
    await ws.receive_json()
    assert (await step())["skipped"]
    hass.states.async_set("calendar.home", "off", {"friendly_name": "Home"})
    assert (await step())["done"]
    await ws.send_json_auto_id({"type": "aac/module/access", "module": "calendar", "values": {"enabled": False}})
    await ws.receive_json()
    assert await step() is None  # a module that is off asks for nothing


async def test_hide_from_the_sidebar(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """A hidden module stays on; the panel's state marks it hidden."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    result = await _set(ws, hidden=True)
    assert result["access"]["hidden"] and result["access"]["enabled"]
    lists = next(m for m in mock_entry.runtime_data.modules if m.id == "lists")
    domain = next(iter(mock_entry.runtime_data.domains.values()))
    summary = lists.summary(mock_entry, domain.id)
    assert summary["hidden"] and summary["pages"]
