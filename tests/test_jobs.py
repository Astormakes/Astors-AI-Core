"""The job queue: automatic work waits for a model; time-critical jobs still reach the owner."""

from datetime import timedelta
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.aac.jobs import Job

from .conftest import MODEL_CFG, set_heartbeat, setup_aac
from .test_planning import _queue, _text


async def test_heartbeat_waits_and_is_told_it_was_late(hass: HomeAssistant, aioclient_mock, mock_entry, configured, freezer):
    """No model → the heartbeat waits, the owner is told once; when the model is back it runs with a note."""
    freezer.move_to("2026-10-20 12:00:00+00:00")
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    await set_heartbeat(mock_entry, enabled=True, quiet_start="00:00", quiet_end="00:00")
    data.store.data["queue"]["postpone_notice"] = 0
    data.broker.mark_down(MODEL_CFG["id"], "off")
    from custom_components.aac.modules.heartbeat import async_queue

    await async_queue(hass)
    await async_queue(hass)  # a second one does not pile up
    assert [j.title for j in data.jobs.waiting] == ["Heartbeat"]
    told = data.chats.get("aac")["messages"]
    assert len(told) == 1 and "no model available" in told[0]["text"]

    payloads = _queue(aioclient_mock, _text("NOTHING"), _text('{"speak": false, "message": ""}'))
    data.broker.mark_up(MODEL_CFG["id"])
    await data.jobs._tick()  # noqa: SLF001
    await hass.async_block_till_done(wait_background_tasks=True)
    assert data.jobs.waiting == [] and data.jobs.history[0].status == "done"
    assert "postponed" in payloads[0]["messages"][0]["content"]


async def test_time_critical_job_reaches_the_owner_without_a_model(hass: HomeAssistant, mock_entry, configured):
    """A reminder that found no model before its due time becomes a plain notification; others expire."""
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    data.broker.mark_down(MODEL_CFG["id"], "off")
    dom = data.domain()
    start = AsyncMock()
    past = dt_util.utcnow() - timedelta(minutes=1)
    await data.jobs.async_submit(Job(dom, "Pills", "master", "high", start, due=past, critical=True,
                                     fallback="Pills: remind me to take my pills"))
    await data.jobs.async_submit(Job(dom, "Heartbeat", "fast", "low", start, due=past, tags=["heartbeat"]))
    assert [j.priority for j in data.jobs.waiting] == ["high", "low"]
    await data.jobs._tick()  # noqa: SLF001
    start.assert_not_called()
    assert {j.title: j.status for j in data.jobs.history} == {"Pills": "notified", "Heartbeat": "expired"}
    assert any(m["text"] == "Pills: remind me to take my pills" for m in dom.chats.get("aac")["messages"])


async def test_queue_ws(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """Settings and the waiting list are visible."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/jobs/settings", "notify_no_model": False, "postpone_notice": 30})
    result = (await ws.receive_json())["result"]
    assert result["settings"] == {"notify_no_model": False, "postpone_notice": 30} and result["waiting"] == []


async def test_waiting_reminder_survives_a_restart(hass: HomeAssistant, hass_storage, mock_entry, configured):
    """A reminder that waited when HA restarted comes back; past its due time it reaches the owner."""
    trig = {"id": "t_pills", "name": "Pills", "enabled": True, "created_by": "agent",
            "when": {"type": "time", "at": "08:00", "date": "2026-10-20", "weekdays": []},
            "do": {"template": "event-reaction", "instruction": "Remind me to take my pills."}}
    hass_storage["aac.jobs"] = {"version": 1, "key": "aac.jobs", "data": [
        {"kind": "trigger", "trigger": trig, "title": "Pills", "text": "It is 08:00.", "id": "j1", "domain": "assistant",
         "created": (dt_util.utcnow() - timedelta(hours=2)).isoformat(),
         "due": (dt_util.utcnow() - timedelta(hours=1)).isoformat()}]}
    await setup_aac(hass, mock_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    data = mock_entry.runtime_data
    assert data.jobs.waiting == []
    assert data.jobs.history[0].title == "Pills" and data.jobs.history[0].status == "notified"
    assert data.chats.get("aac")["messages"][-1]["text"] == "Pills: Remind me to take my pills."


async def test_waiting_jobs_are_saved(hass: HomeAssistant, hass_storage, mock_entry, configured, freezer):
    """Trigger jobs that wait are written to storage (heartbeats are not)."""
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    data.broker.mark_down(MODEL_CFG["id"], "off")
    trig = await data.store.async_save_trigger(
        {"name": "Door", "enabled": True, "cooldown": 0, "quiet_hours": None,
         "when": {"type": "state", "entity_id": "binary_sensor.door", "to": "on"},
         "do": {"template": "event-reaction", "instruction": "Tell me."}})
    await data.jobs.async_submit(data.triggers.trigger_job(trig, "Door", "The door opened."))
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    saved = hass_storage["aac.jobs"]["data"]
    assert [j["title"] for j in saved] == ["Door"] and saved[0]["trigger"]["id"] == trig["id"]
