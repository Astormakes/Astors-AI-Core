"""The card worker (docs/v2 page 6, step 5): a process of its own, walled in (seccomp), talking to the core over one
channel; something that holds it too long is stopped, a worker stuck in C is started again."""

import asyncio
from pathlib import Path
import platform

import pytest

from custom_components.aac.worker import Worker, WorkerGone

pytestmark = pytest.mark.fresh
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
