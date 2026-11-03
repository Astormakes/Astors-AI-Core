"""The card worker's own program (docs/v2 page 6): a process of its own, apart from Home Assistant, that the core starts
(`worker/__init__.py`). It loads its code (the module named by --handlers), walls itself in (`wall.py`) and then only
talks to the core over its stdin and stdout: one JSON message per line.

Messages (`wire.py`: bytes travel too, errors keep their kind): {"q": id, "do": name, "args": {…}} asks (either
side), {"a": id, "result": …} or {"a": id, "error": "…", "kind": "…"} answers, {"tell": {…}} tells (no answer:
`handlers.told(message, host)` on this side); the worker also sends {"ready": {"walled": bool, "why": how or why not}} once, {"beat": seconds} every BEAT, and
{"hung": what, "seconds": n} when something held it too long.

A card may hold the worker HANG seconds without giving it back (awaiting is giving it back: a model's answer may take
a minute). Longer, the watchdog (a thread of its own) raises Hung into what runs: a Python loop stops there, the
worker goes on. Stuck where Python cannot stop it (inside C), the beats stop: the core kills the worker and starts it
again.

The handlers module: `async def handle(do, args, host)` answers the core's asks; optional `async def start(host)` and
`told(message, host)`. `host.ask(do, args)` asks the core, `host.tell(message)` tells it, `with host.running(what):` names what runs (the
watchdog says it).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import importlib
import itertools
import os
import sys
import threading
import time
from typing import Any

BEAT = 0.5  # seconds between beats to the core
HANG = 3.0  # seconds something may hold the worker without giving it back
PRELOAD = ("concurrent.futures.thread", "asyncio.base_events", "json.decoder", "json.encoder", "encodings.idna",
           "encodings.utf_8", "encodings.latin_1", "unicodedata", "zoneinfo", "datetime", "re", "hashlib", "base64",
           "random", "uuid", "traceback", "linecache", "fnmatch", "copy", "struct", "math", "statistics")  # what
# Python imports late on its own: once walled in nothing can be read from disk any more


wire: Any = None  # wire.py, imported in main() once the path is set


class Hung(BaseException):  # noqa: N818 - not an Exception: a card's `except Exception` does not swallow it
    """Raised into what held the worker too long."""


class Host:
    """The core, as the worker sees it."""

    def __init__(self, loop: asyncio.AbstractEventLoop, hang: float) -> None:
        self.loop, self.hang = loop, hang
        self._write = threading.Lock()
        self._asked: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self.doing = ""  # what runs now (the watchdog says it)
        self.held = ""  # what the watchdog stopped last
        self.last = time.monotonic()  # the loop's last beat (the watchdog reads it)

    def send(self, message: dict[str, Any]) -> None:
        """One message to the core (from any thread)."""
        line = wire.dumps(message)
        with self._write:
            view = memoryview(line)
            while view:
                view = view[os.write(1, view):]

    def tell(self, message: dict[str, Any]) -> None:
        """Tell the core something (no answer)."""
        self.send({"tell": message})

    async def ask(self, do: str, args: dict[str, Any] | None = None) -> Any:
        """Ask the core something (an interface call) and wait for its answer."""
        ask_id = next(self._ids)
        self._asked[ask_id] = future = self.loop.create_future()
        self.send({"q": ask_id, "do": do, "args": args or {}})
        try:
            return await future
        finally:
            self._asked.pop(ask_id, None)

    @contextlib.contextmanager
    def running(self, what: str):
        """Name what runs while the block runs."""
        before, self.doing = self.doing, what
        try:
            yield
        finally:
            self.doing = before

    def answered(self, message: dict[str, Any]) -> None:
        if (future := self._asked.get(message["a"])) is not None and not future.done():
            if "error" in message:
                future.set_exception(wire.raised(message))
            else:
                future.set_result(message.get("result"))


def _watch(host: Host, thread_id: int) -> None:
    """The watchdog (a thread): the loop beats every BEAT; held longer than HANG, Hung goes into the loop's thread,
    once per hold."""
    fired = None
    while True:
        time.sleep(0.1)
        held = time.monotonic() - host.last
        if held > host.hang and fired != host.last:
            fired, host.held = host.last, host.doing or "the worker"
            host.send({"hung": host.held, "seconds": round(held, 1)})
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), ctypes.py_object(Hung))


async def _serve(host: Host, handlers: Any) -> None:
    reader = asyncio.StreamReader(limit=64 * 1024 * 1024)
    await host.loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

    def beat() -> None:
        host.last = time.monotonic()
        host.send({"beat": round(host.last, 2)})
        host.loop.call_later(BEAT, beat)

    async def answer(message: dict[str, Any]) -> None:
        try:
            result = await handlers.handle(message["do"], message.get("args") or {}, host)
            host.send({"a": message["q"], "result": result})
        except Hung:
            host.send({"a": message["q"], "error": f"{host.held} held the worker over {host.hang:g} s: stopped"})
        except Exception as err:  # noqa: BLE001 - told to the core, the worker goes on
            host.send({"a": message["q"], **wire.error(err)})

    beat()
    if hasattr(handlers, "start"):
        await handlers.start(host)
    while line := await reader.readline():
        message = wire.loads(line)
        if "a" in message:
            host.answered(message)
        elif "q" in message:
            host.loop.create_task(answer(message))
        elif "tell" in message and hasattr(handlers, "told"):
            handlers.told(message["tell"], host)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handlers", required=True)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--hang", type=float, default=HANG)
    parser.add_argument("--no-wall", action="store_true")
    opts = parser.parse_args()
    sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)), *opts.path]
    global wire  # noqa: PLW0603 - next to this file, found once the path is set
    import wall  # noqa: PLC0415
    import wire  # noqa: PLC0415

    for name in PRELOAD:
        importlib.import_module(name)
    handlers = importlib.import_module(opts.handlers)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(loop.run_in_executor(None, lambda: None))  # its thread pool, made before the wall
    host = Host(loop, opts.hang)
    walled, why = False, "not asked"
    if not opts.no_wall:
        try:
            walled, why = True, wall.lock()
        except OSError as err:
            why = str(err)
    host.send({"ready": {"walled": walled, "why": why, "pid": os.getpid()}})
    threading.Thread(target=_watch, args=(host, threading.main_thread().ident), daemon=True, name="watchdog").start()
    loop.run_until_complete(_serve(host, handlers))  # ends when the core closes the channel (or Hung landed outside
    # a task, a hold that ended just then: the core starts the worker again)


if __name__ == "__main__":
    main()
