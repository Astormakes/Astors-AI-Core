"""What a test worker does (tests/test_worker.py): each `do` one thing a card might do."""

import importlib
import time


async def handle(do, args, host):
    if do == "echo":
        return args
    if do == "ask_core":  # an interface call: the core answers
        return await host.ask("double", {"x": args["x"]})
    if do == "spin":  # holds the worker in Python
        with host.running("Spinner"):
            n = 0
            while True:
                n += 1
    if do == "hold_in_c":  # holds the worker where Python cannot stop it
        time.sleep(args["seconds"])
        return "slept"
    if do == "open":
        with open(args["path"], encoding="utf-8") as f:
            return f.read()
    if do == "import":
        return str(importlib.import_module(args["name"]))
    raise ValueError(f"no {do}")
