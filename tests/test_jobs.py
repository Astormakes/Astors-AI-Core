"""The job queue: automatic work waits for a model; time-critical jobs still reach the owner."""

from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.aac.jobs import Job

from .conftest import MODEL_CFG, setup_aac
from .test_planning import _queue, _text


async def test_heartbeat_waits_and_is_told_it_was_late(hass: HomeAssistant, aioclient_mock, mock_entry, configured, freezer):
    """No model → the heartbeat waits, the owner is told once; when the model is back it runs with a note."""
    freezer.move_to("2026-10-20 12:00:00+00:00")
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    await data.store.async_update_heartbeat({"quiet_start": "00:00", "quiet_end": "00:00"})
    data.store.data["queue"]["postpone_notice"] = 0
    data.broker.mark_down(MODEL_CFG["id"], "off")
    await data.triggers.async_heartbeat()
    await data.triggers.async_heartbeat()  # a second one does not pile up
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
