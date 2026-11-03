"""The card layer as the worker runs it (docs/v2 page 6): the handlers module `main.py` loads. Everything it will ever
need is imported here, before the wall: AAC's card layer (without the integration's `__init__`, so without Home
Assistant), every card file from the cards folder, the home's time zone. Then it builds the Layer, whose link is the
worker's channel to the core.

Where things are comes in the environment: AAC_ROOT (the folder holding custom_components), AAC_CARDS (the cards
folder), AAC_TZ (the home's time zone).
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import types
from typing import Any
from zoneinfo import ZoneInfo

import wire  # next to main.py: the same module the channel uses

LAZY = ("numpy.fft", "numpy.linalg", "numpy.random", "numpy.polynomial", "numpy.ma", "numpy.lib.stride_tricks",
        "numpy.char", "numpy.strings", "numpy.rec", "numpy.dtypes", "numpy.exceptions")
ROOT = os.environ["AAC_ROOT"]
for _name, _where in (("custom_components", os.path.join(ROOT, "custom_components")),
                      ("custom_components.aac", os.path.join(ROOT, "custom_components", "aac")),
                      ("custom_components.aac.worker", os.path.dirname(os.path.abspath(__file__)))):
    _package = types.ModuleType(_name)
    _package.__path__ = [_where]
    sys.modules[_name] = _package
sys.modules["custom_components.aac.worker.wire"] = wire  # one Hung, one set of kinds on this side

from homeassistant.util import dt as dt_util  # noqa: E402 - the one pure helper of Home Assistant's the cards use

dt_util.set_default_time_zone(ZoneInfo(os.environ.get("AAC_TZ") or "UTC"))

from custom_components.aac import defaults, panel  # noqa: E402, F401 - imported now: nothing can be read later
from custom_components.aac.cards import load_cards  # noqa: E402
from custom_components.aac.const import ModelError, ModelUnreachable  # noqa: E402
from custom_components.aac.layer import Clock, Layer  # noqa: E402

wire.known(ModelError, ModelUnreachable)
load_cards(os.environ["AAC_CARDS"])
for _part in LAZY:  # parts a package loads only when first used: now, before the wall (a card's numpy.fft …)
    if _part.split(".")[0] in sys.modules:
        with contextlib.suppress(ImportError):
            importlib.import_module(_part)

LAYER: Layer | None = None


class Link:
    """The worker's channel, as the Layer uses it."""

    def __init__(self, host: Any) -> None:
        self.host = host

    async def ask(self, do: str, args: dict[str, Any]) -> Any:
        return await self.host.ask(do, args)

    def tell(self, message: dict[str, Any]) -> None:
        self.host.tell(message)

    def doing(self, what: str) -> Any:
        """Names what runs (the watchdog says it when it holds the worker too long)."""
        return self.host.running(what)


async def start(host: Any) -> None:
    global LAYER  # noqa: PLW0603
    LAYER = Layer(Link(host), Clock())


async def handle(do: str, args: dict[str, Any], host: Any) -> Any:
    return await LAYER.handle(do, args)


def told(message: dict[str, Any], host: Any) -> None:
    try:
        LAYER.told(message)
    except Exception as err:  # noqa: BLE001 - one bad message must not stop the channel
        LAYER.log("core", "error", f"A message from the core failed: {err!r}")
