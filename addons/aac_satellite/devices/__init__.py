"""Device plugins: hardware quirks stay here, out of AAC and out of the satellite's core.

A plugin says whether its device is attached (detect), which microphone/speaker belong
to it (names to look for), and what the device needs to work: start/stop hooks and a
look at the audio (e.g. a watchdog when the mic only delivers silence). Any file in
this folder with a `PLUGIN` class is loaded.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
import pkgutil

LOG = logging.getLogger("aac_satellite.devices")


class DevicePlugin:
    """Base class: everything optional."""

    name = "device"
    mic_hint: str | None = None  # part of the microphone's (PulseAudio) name
    speaker_hint: str | None = None  # part of the speaker's name

    @classmethod
    def detect(cls) -> bool:
        """Is the device attached to this machine?"""
        return False

    async def start(self) -> None:
        """Before audio starts."""

    async def stop(self) -> None:
        """When the satellite stops."""

    def on_audio(self, rms: float) -> None:
        """Every chunk of microphone audio (loudness), e.g. for a watchdog."""

    async def tick(self) -> None:
        """Every few seconds (e.g. notice a replug)."""


def attached() -> list[DevicePlugin]:
    """Instances of every plugin whose device is attached."""
    found = []
    for info in pkgutil.iter_modules([str(Path(__file__).parent)]):
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
            plugin = getattr(module, "PLUGIN", None)
            if plugin and plugin.detect():
                LOG.info("Device found: %s", plugin.name)
                found.append(plugin())
        except Exception:  # noqa: BLE001 - one broken plugin must not stop the satellite
            LOG.exception("Device plugin %s failed", info.name)
    return found
