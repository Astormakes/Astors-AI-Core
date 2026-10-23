"""The job queue: automatic work waits for a model; time-critical jobs still reach the owner."""

from datetime import timedelta
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.aac.jobs import Job

from .conftest import MODEL_CFG, set_heartbeat, setup_aac, triggers_of, everything
from .test_planning import _queue, _text


async def test_heartbeat_waits_and_is_told_it_was_late(hass: HomeAssistant, aioclient_mock, mock_entry, configured, freezer):
    """No model → the heartbeat waits, the owner is told once; when the model is back it runs with a note."""
    freezer.move_to("2026-10-20 12:00:00+00:00")
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    await set_heartbeat(mock_entry, enabled=True, quiet_start="00:00", quiet_end="00:00")
    data.store.data["queue"]["postpone_notice"] = 0
    data.pool.mark_down(MODEL_CFG["id"], "off")
    from custom_components.aac.modules.heartbeat import async_queue

    await async_queue(hass)
    await async_queue(hass)  # a second one does not pile up
    assert [j.title for j in data.jobs.waiting] == ["Heartbeat"]
    told = data.chats.get("aac")["messages"]
    assert len(told) == 1 and "no model available" in told[0]["text"]

    payloads = _queue(aioclient_mock, _text("NOTHING"), _text('{"speak": false, "message": ""}'))
    data.pool.mark_up(MODEL_CFG["id"])
    await data.jobs._tick()  # noqa: SLF001
    await hass.async_block_till_done(wait_background_tasks=True)
    assert data.jobs.waiting == [] and data.jobs.history[0].status == "done"
    assert "postponed" in everything(payloads[0])


async def test_time_critical_job_reaches_the_owner_without_a_model(hass: HomeAssistant, mock_entry, configured):
    """A reminder that found no model before its due time becomes a plain notification; others expire."""
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    data.pool.mark_down(MODEL_CFG["id"], "off")
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


def _saved(**message):
    """A waiting message as the queue saves it (kind "message": its model node and the message as JSON)."""
    from custom_components.aac.request import entry

    base = {"entries": [entry("triggers", "note", "Remind me."), entry("triggers", "user", "It is 08:00.")],
            "kind": "auto", "trigger": "event: t_pills", "title": "Pills", "origin": "triggers", "reply_to": "chat",
            "priority": "high", "critical": True, "fallback": "Pills: Remind me to take my pills.", "due_minutes": 30,
            "keep": True, "tags": ["reminder"], "context_parts": ["time"]}
    return {"kind": "message", "node": "model", "title": "Pills", "message": {**base, **message}}


async def test_waiting_reminder_survives_a_restart(hass: HomeAssistant, hass_storage, mock_entry, configured):
    """A reminder that waited when HA restarted comes back; past its due time it reaches the owner anyway."""
    hass_storage["aac.jobs"] = {"version": 1, "key": "aac.jobs", "data": [
        {**_saved(), "id": "j1", "domain": "assistant", "created": (dt_util.utcnow() - timedelta(hours=2)).isoformat(),
         "due": (dt_util.utcnow() - timedelta(hours=1)).isoformat()}]}
    await setup_aac(hass, mock_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    data = mock_entry.runtime_data
    assert data.jobs.waiting == []
    assert data.jobs.history[0].title == "Pills" and data.jobs.history[0].status == "notified"
    assert data.chats.get("aac")["messages"][-1]["text"] == "Pills: Remind me to take my pills."


async def test_a_waiting_message_comes_back(hass: HomeAssistant, hass_storage, aioclient_mock, mock_entry, configured):
    """Not due yet: it is queued again at its model node, with its original times (and runs once a model is free)."""
    from .conftest import BASE_URL

    created = dt_util.utcnow() - timedelta(minutes=5)
    hass_storage["aac.jobs"] = {"version": 1, "key": "aac.jobs", "data": [
        {**_saved(), "id": "j2", "domain": "assistant", "created": created.isoformat(),
         "due": (dt_util.utcnow() + timedelta(minutes=20)).isoformat()}]}
    aioclient_mock.post(f"{BASE_URL}/chat/completions", json={"choices": [{"message": {"role": "assistant", "content": "Pills now."}}]})
    await setup_aac(hass, mock_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    data = mock_entry.runtime_data
    job = next(j for j in data.jobs.waiting + data.jobs.history if j.id == "j2")
    assert job.created == created and job.reason == "waited before Home Assistant restarted"
    assert data.chats.get("aac")["messages"][-1]["text"] == "Pills now."  # it ran and came home


async def test_waiting_jobs_are_saved(hass: HomeAssistant, hass_storage, mock_entry, configured, freezer):
    """A trigger's message that waits for a model is written to storage (heartbeats are not)."""
    from custom_components.aac.broker import async_send

    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    data.pool.mark_down(MODEL_CFG["id"], "off")
    triggers = triggers_of(hass)
    trig = await triggers.async_save_trigger(
        {"name": "Door", "enabled": True, "cooldown": 0, "quiet_hours": None,
         "when": {"type": "state", "entity_id": "binary_sensor.door", "to": "on"},
         "do": {"template": "event-reaction", "instruction": "Tell me."}})
    await async_send(hass, mock_entry, data.domain(), triggers.request(trig, "Door", "The door opened."), "triggers")
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    saved = hass_storage["aac.jobs"]["data"]
    assert [j["title"] for j in saved] == ["Door"] and saved[0]["kind"] == "message" and saved[0]["node"] == "model"
    assert saved[0]["message"]["trigger"] == f"event: {trig['id']}"
