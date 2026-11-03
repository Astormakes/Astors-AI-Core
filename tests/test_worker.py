"""The card worker (docs/v2 page 6, step 5): a process of its own, walled in (seccomp), talking to the core over one
channel; something that holds it too long is stopped, a worker stuck in C is started again."""

import asyncio
from pathlib import Path
import platform

import pytest

from custom_components.aac.worker import Worker, WorkerGone

pytestmark = pytest.mark.fresh  # (the backup copy is about the stored data, not this)
HERE = str(Path(__file__).parent)


async def _worker(**kw) -> tuple[Worker, list[dict]]:
    told: list[dict] = []

    async def answer(do, args):
        if do == "double":
            return args["x"] * 2
        raise ValueError(do)

    worker = Worker("worker_handlers", [HERE], answer=answer, told=told.append, **kw)
    await worker.start()
    return worker, told


async def test_it_talks_both_ways_and_is_walled_in():
    """The core asks, the worker answers; the worker asks the core (the interface); once walled in it opens no file and
    imports nothing new from disk."""
    worker, _ = await _worker()
    try:
        assert await worker.ask("echo", {"a": 1}) == {"a": 1}
        assert await worker.ask("ask_core", {"x": 21}) == 42
        if platform.machine() in ("x86_64", "aarch64"):
            assert worker.ready["walled"] is True, worker.ready
            with pytest.raises(PermissionError):  # an error arrives as its kind
                await worker.ask("open", {"path": __file__})
            with pytest.raises(PermissionError):
                await worker.ask("import", {"name": "xml.dom.minidom"})
        with pytest.raises(ValueError, match="no nothing"):
            await worker.ask("nothing")
        assert await worker.ask("echo", {"wav": b"RIFF\x00\xff"}) == {"wav": b"RIFF\x00\xff"}  # bytes travel
    finally:
        await worker.stop()


async def test_a_hold_in_python_is_stopped_and_the_worker_goes_on():
    """Something that holds the worker longer than the limit (a loop that never gives it back) is stopped there; the
    worker answers again at once (no restart)."""
    worker, told = await _worker(hang=0.5)
    try:
        with pytest.raises(RuntimeError, match="Spinner held the worker over 0.5 s"):
            await worker.ask("spin", timeout=10)
        assert {"hung": "Spinner", "seconds": pytest.approx(0.5, abs=0.4)} in told
        assert await worker.ask("echo", {"b": 2}) == {"b": 2} and worker.started == 1
    finally:
        await worker.stop()


async def test_a_worker_stuck_in_c_is_started_again():
    """Held where Python cannot stop it, the beats stop: the core kills the worker and starts it again; what waited
    gets WorkerGone, the new worker answers."""
    worker, told = await _worker(hang=0.5)
    try:
        with pytest.raises(WorkerGone):
            await worker.ask("hold_in_c", {"seconds": 30}, timeout=15)
        for _ in range(100):
            if worker.started == 2 and worker._proc is not None:
                break
            await asyncio.sleep(0.1)
        assert await worker.ask("echo", {"c": 3}) == {"c": 3} and worker.started == 2
        assert any(m.get("restart") == "stuck (no beat)" for m in told)
    finally:
        await worker.stop()


async def test_another_machine_is_walled_by_libseccomp():
    """A machine without numbers of its own (not x86_64 or aarch64) is walled by libseccomp, by the same names."""
    import ctypes.util
    import subprocess
    import sys

    if not ctypes.util.find_library("seccomp") or platform.system() != "Linux":
        pytest.skip("no libseccomp here")
    folder = Path(__file__).parent.parent / "custom_components" / "aac" / "worker"
    probe = (f"import sys, socket; sys.path.insert(0, {str(folder)!r}); import wall; wall._lock_libseccomp()\n"
             "for f in (lambda: open(__file__ if False else '/etc/hostname'), socket.socket):\n"
             "    try: f(); print('open')\n"
             "    except PermissionError: print('walled')\n")
    out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=20, check=True).stdout
    assert out.split() == ["walled", "walled"]


async def test_aac_runs_its_cards_in_the_worker(hass, mock_entry, aioclient_mock, hass_ws_client, monkeypatch):
    """The whole card layer in the walled worker: AAC starts it, the panel's state and commands come from it, a chat
    turn goes to the model (the core asks the server) and back; Home Assistant itself holds no Domain."""
    from .conftest import model_answers, reply, setup_aac

    monkeypatch.setattr("custom_components.aac.core.CARDS_IN_WORKER", True)
    asked = model_answers(aioclient_mock, reply("Hello from the worker."))
    core = await setup_aac(hass, mock_entry)
    try:
        assert core.worker is not None and core.domain is None
        if platform.machine() in ("x86_64", "aarch64"):
            assert core.worker.ready["walled"] is True
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "aac/state"})
        state = (await client.receive_json())["result"]
        assert {"chat", "model", "charter"} <= {c["id"] for c in state["domain"]["cards"]} and state["types"]
        await client.send_json_auto_id({"type": "aac/card/ui", "card": "chat", "action": "send", "args": {"text": "hi"}})
        got = (await client.receive_json())["result"]["result"]
        assert got["answer"] == "Hello from the worker." and asked[0]["messages"][-1]["content"].startswith("hi")
        await client.send_json_auto_id({"type": "aac/card/call", "card": "home", "function": "get_state",
                                        "args": {"entity_ids": ["sun.sun"]}})
        assert (await client.receive_json())["success"]
    finally:
        await hass.config_entries.async_unload(mock_entry.entry_id)


async def test_the_workers_clock():
    """The worker's own clock: later runs a job once (a coroutine too) unless cancelled; every_minute runs at the start
    of each minute."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from custom_components.aac.layer import Clock

    clock, ran = Clock(), []

    async def job():
        ran.append("coro")
    clock.later(0.05, job)
    cancel = clock.later(0.05, lambda: ran.append("cancelled"))
    cancel()
    await asyncio.sleep(0.15)
    assert ran == ["coro"]
    with patch("homeassistant.util.dt.utcnow", return_value=datetime(2026, 11, 3, 5, 24, 59, 900000, tzinfo=UTC)):
        stop = clock.every_minute(ran.append)  # 0.1 s before the minute
        await asyncio.sleep(0.3)
    stop()
    assert ran[1:] == [datetime(2026, 11, 3, 5, 25, tzinfo=UTC)]  # once, for its minute
    assert await clock.blocking(lambda a: a + 1, 1) == 2
