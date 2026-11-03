"""The core's side of the card worker (docs/v2 page 6): it starts the worker (`main.py`, a process of its own, walled
in by `wall.py`), talks to it over its stdin and stdout (one JSON message per line), answers what it asks (the
interface), and watches it: no beat for longer than the hold limit and a grace, or the process gone, and it is
started again (what waited for it gets an error).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import itertools
import json
from pathlib import Path
import sys
import time
from typing import Any

MAIN = Path(__file__).parent / "main.py"
GRACE = 2.0  # seconds past the hold limit without a beat: the worker is stuck where Python cannot stop it (in C)
RESTART_WAIT = 1.0  # seconds before it is started again (doubles while it keeps dying at once, up to 30)


class WorkerGone(RuntimeError):
    """The worker ended (or was stopped) before it answered."""


class Worker:
    """One worker process for cards."""

    def __init__(self, handlers: str, paths: list[str] | None = None, *, answer: Callable[[str, dict], Awaitable[Any]] | None = None,
                 told: Callable[[dict], None] | None = None, hang: float = 3.0, wall: bool = True) -> None:
        """handlers: the module the worker runs (on paths); answer(do, args): what it asks the core; told(message): what
        it tells (hung, ready, anything else it sends)."""
        self.handlers, self.paths, self.hang, self.wall = handlers, list(paths or []), hang, wall
        self._answer, self._told = answer, told or (lambda m: None)
        self._proc: asyncio.subprocess.Process | None = None
        self._asked: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._tasks: set[asyncio.Task] = set()
        self._stopping = False
        self.ready: dict[str, Any] = {}  # walled, why, pid (from the worker)
        self.started = 0  # how often it was started
        self.last_beat = 0.0
        self._up = asyncio.Event()

    # ------------------------------------------------------------------ running
    async def start(self) -> None:
        """Start it and wait until it says it is ready."""
        self._stopping = False
        await self._spawn()
        self._keep(self._watch())

    async def _spawn(self) -> None:
        self._up.clear()
        args = [sys.executable, "-I", str(MAIN), "--handlers", self.handlers, "--hang", str(self.hang)]
        args += [x for p in self.paths for x in ("--path", p)] + ([] if self.wall else ["--no-wall"])
        self._proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                          stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024)
        self.started += 1
        self.last_beat = time.monotonic()
        self._keep(self._read(self._proc))
        self._keep(self._errors(self._proc))
        await asyncio.wait_for(self._up.wait(), 60)

    async def stop(self) -> None:
        """Stop it for good (what waits gets WorkerGone)."""
        self._stopping = True
        for task in list(self._tasks):
            task.cancel()
        await self._end("stopped")

    async def _end(self, why: str) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        for future in self._asked.values():
            if not future.done():
                future.set_exception(WorkerGone(why))
        self._asked.clear()

    async def _watch(self) -> None:
        """No beat for the hold limit and a grace (stuck in C), or the process gone: start it again."""
        wait = RESTART_WAIT
        while not self._stopping:
            await asyncio.sleep(0.2)
            proc = self._proc
            gone = proc is None or proc.returncode is not None
            stuck = not gone and time.monotonic() - self.last_beat > self.hang + GRACE
            if not (gone or stuck):
                continue
            why = "stuck (no beat)" if stuck else f"ended ({proc.returncode if proc else '?'})"
            self._told({"restart": why})
            await self._end(why)
            await asyncio.sleep(wait)
            began = time.monotonic()
            await self._spawn()
            wait = RESTART_WAIT if time.monotonic() - began > 10 else min(wait * 2, 30)

    # ------------------------------------------------------------------ talking
    async def ask(self, do: str, args: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        """Ask the worker; its answer (RuntimeError with its error; WorkerGone when it ended first)."""
        if self._proc is None:
            raise WorkerGone("not running")
        ask_id = next(self._ids)
        self._asked[ask_id] = future = asyncio.get_running_loop().create_future()
        self._send({"q": ask_id, "do": do, "args": args or {}})
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._asked.pop(ask_id, None)

    def _send(self, message: dict[str, Any]) -> None:
        if self._proc is not None and self._proc.stdin is not None:
            self._proc.stdin.write((json.dumps(message, ensure_ascii=False, default=str) + "\n").encode())

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while line := await proc.stdout.readline():
            message = json.loads(line)
            if "beat" in message:
                self.last_beat = time.monotonic()
            elif "a" in message:
                if (future := self._asked.get(message["a"])) is not None and not future.done():
                    if "error" in message:
                        future.set_exception(RuntimeError(message["error"]))
                    else:
                        future.set_result(message.get("result"))
            elif "q" in message:
                self._keep(self._reply(message))
            else:
                if "ready" in message:
                    self.ready = message["ready"]
                    self._up.set()
                self._told(message)

    async def _reply(self, message: dict[str, Any]) -> None:
        """What the worker asks the core (the interface)."""
        try:
            if self._answer is None:
                raise RuntimeError("the core answers nothing")
            self._send({"a": message["q"], "result": await self._answer(message["do"], message.get("args") or {})})
        except Exception as err:  # noqa: BLE001 - told to the worker
            self._send({"a": message["q"], "error": f"{type(err).__name__}: {err}"})

    async def _errors(self, proc: asyncio.subprocess.Process) -> None:
        """What it writes to stderr (a crash's trace): told as {"stderr": text}."""
        assert proc.stderr is not None
        while line := await proc.stderr.readline():
            self._told({"stderr": line.decode(errors="replace").rstrip()})

    def _keep(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
