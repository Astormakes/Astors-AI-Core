"""Plantronics/Poly BT600 dongle (USB 047f:0417, reports as "BT300C") with a Calisto speaker.

The dongle only opens the Bluetooth link when the host signals "off-hook" (a call) over USB
HID; Linux never does, so there is no sound and the mic delivers only zeros. Fix: write
report 5 = 0x01 ("LED Off-Hook") to the telephony HID interface (1.3); 0x00 hangs up.
Details: the homelab handoff (docs/handoff-calisto-bt600.md in the AAC project).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import time

from . import DevicePlugin

LOG = logging.getLogger("aac_satellite.bt600")
USB_ID = "0003:047F:0417"
TELEPHONY_IF = ":1.3/"
OFF_HOOK, ON_HOOK = b"\x05\x01", b"\x05\x00"
SILENCE_RESEND = 5.0  # seconds of silence before off-hook is sent again
SILENT_RMS = 3.0  # below this the link is down: a dropped link gives zeros, or ±1 noise (about -90 dBFS, 2026-10-23)
MIN_GAP = 10.0  # do not resend more often than this


def find_hidraw() -> str | None:
    """The telephony hidraw node (its number changes with every replug)."""
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        try:
            path = os.path.realpath(node / "device").upper()
        except OSError:
            continue
        if TELEPHONY_IF in path and USB_ID in path:
            return f"/dev/{node.name}"
    return None


class BT600(DevicePlugin):
    """Keeps the dongle off-hook while the satellite runs."""

    name = "Plantronics BT600 + Calisto (off-hook)"
    mic_hint = speaker_hint = "BT300C"

    def __init__(self) -> None:
        """Initialize."""
        self._node: str | None = None
        self._silent_since: float | None = None
        self._last_sent = 0.0

    @classmethod
    def detect(cls) -> bool:
        """Present when its HID interface is there."""
        return find_hidraw() is not None

    def _write(self, data: bytes, why: str) -> bool:
        node = find_hidraw()
        if node is None:
            LOG.warning("BT600 not found (unplugged?)")
            return False
        try:
            with open(node, "wb", buffering=0) as dev:
                dev.write(data)
        except PermissionError:
            LOG.error("No permission to write %s: the satellite needs access to the HID device", node)
            return False
        except OSError as err:
            LOG.error("Writing %s failed: %s", node, err)
            return False
        self._node, self._last_sent = node, time.monotonic()
        LOG.info("BT600 %s (%s, %s)", "off-hook" if data == OFF_HOOK else "on-hook", node, why)
        return True

    async def start(self) -> None:
        """Open the Bluetooth audio link."""
        self._write(OFF_HOOK, "start")

    async def stop(self) -> None:
        """Hang up."""
        self._write(ON_HOOK, "stop")

    def on_audio(self, rms: float) -> None:
        """Watchdog: (almost) digital silence means the link dropped (speaker off, out of range, re-pair)."""
        now = time.monotonic()
        if rms >= SILENT_RMS:
            self._silent_since = None
            return
        self._silent_since = self._silent_since or now
        if now - self._silent_since >= SILENCE_RESEND and now - self._last_sent >= MIN_GAP:
            self._write(OFF_HOOK, "mic silent")

    async def tick(self) -> None:
        """A replug gives the dongle a new hidraw node: send off-hook again."""
        node = find_hidraw()
        if node and node != self._node and time.monotonic() - self._last_sent >= 2:
            self._write(OFF_HOOK, "replugged")


PLUGIN = BT600
