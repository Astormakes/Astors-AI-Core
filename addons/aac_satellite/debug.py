"""Option `debug`: a record of what the hardware does, for hunting a fault (the Calisto blaring noise by itself,
2026-11-01). Logs and keeps, apart from the satellite's work:

- every loud stretch on the raw microphone (before echo cancellation, which would take out what the speaker plays) or
  on the speaker's monitor (PulseAudio's copy of what goes to the speaker: silent there while the microphone is loud
  means the sound did not come from this machine's software): when, how loud, how noise-like (zero crossings: speech
  about 0.1, white noise about 0.5), and whether AAC was playing; saved as a stereo WAV (left the microphone, right
  the monitor) in /share/aac_satellite_debug/;
- PulseAudio's events for cards, sinks, sources and modules (`pactl subscribe`; our own players and recorders are left
  out);
- every few minutes the levels of both;
- what a device plugin reports (the BT600's HID reports).
"""

from __future__ import annotations

from array import array
import asyncio
import contextlib
from collections import deque
import logging
import math
from pathlib import Path
import time
import wave

LOG = logging.getLogger("aac_satellite.debug")
RATE = 16000
FOLDER = Path("/share/aac_satellite_debug")
KEEP = 30  # recordings kept
LOUD = 330  # RMS (about -40 dBFS; a quiet room is about -60) that counts as loud
LOUD_FOR = 0.3  # seconds loud before it counts
QUIET_FOR = 2.0  # seconds quiet that end it
LONGEST = 30.0  # seconds a recording lasts at most
BEFORE = 5.0  # seconds kept from before it started
SUMMARY = 300  # seconds between level summaries


def db(rms: float) -> str:
    return f"{20 * math.log10(rms / 32768):.0f} dBFS" if rms > 0 else "silent"


def measure(chunk: bytes) -> tuple[float, float]:
    """RMS and zero-crossing rate of 16-bit mono."""
    s = array("h", chunk[: len(chunk) // 2 * 2])
    if not s:
        return 0.0, 0.0
    rms = (sum(x * x for x in s) / len(s)) ** 0.5
    zcr = sum(1 for a, b in zip(s, s[1:]) if (a < 0) != (b < 0)) / max(1, len(s) - 1)
    return rms, zcr


def parec(device: str) -> list[str]:
    return ["parec", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=20", f"--device={device}"]


class Watch:
    """Records the raw microphone, the speaker's monitor and PulseAudio's events by itself, apart from the satellite."""

    def __init__(self, mic_device: str, monitor_device: str, playing) -> None:
        """mic_device / monitor_device: the real microphone and the real speaker's monitor; playing(): is AAC playing."""
        self.mic_device = mic_device
        self.monitor_device = monitor_device
        self.playing = playing
        self.monitor = bytearray()  # what the monitor recorded, waiting to be lined up with the microphone
        self.ring_mic: deque[bytes] = deque(maxlen=int(BEFORE * RATE * 2 / 640) + 1)
        self.ring_mon: deque[bytes] = deque(maxlen=self.ring_mic.maxlen)
        self.loud_since: float | None = None
        self.event: dict | None = None
        self.stats = {"mic": 0.0, "mic_max": 0.0, "mon": 0.0, "mon_max": 0.0, "n": 0, "since": time.monotonic(),
                      "due": time.monotonic() + 10}  # the first summary after 10 s: both recordings work
        self.tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        with contextlib.suppress(OSError):
            FOLDER.mkdir(parents=True, exist_ok=True)
        LOG.info("Debug on: loud stretches are logged and saved in %s; microphone %s, speaker monitor %s", FOLDER,
                 self.mic_device, self.monitor_device)
        self.tasks = [asyncio.create_task(self._read_mic()), asyncio.create_task(self._read_monitor()),
                      asyncio.create_task(self._pulse_events())]

    def stop(self) -> None:
        for task in self.tasks:
            task.cancel()

    # ------------------------------------------------------------------ inputs
    async def _read_mic(self) -> None:
        while True:
            proc = await asyncio.create_subprocess_exec(*parec(self.mic_device), stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL)
            try:
                assert proc.stdout
                while chunk := await proc.stdout.read(640):
                    self.feed(chunk[: len(chunk) // 2 * 2])
            finally:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            LOG.warning("Microphone recording stopped; again in 5 s")
            await asyncio.sleep(5)

    async def _read_monitor(self) -> None:
        while True:
            proc = await asyncio.create_subprocess_exec(*parec(self.monitor_device), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                assert proc.stdout
                while chunk := await proc.stdout.read(640):
                    self.monitor += chunk
                    if len(self.monitor) > RATE * 2:  # more than a second ahead of the microphone: drop the oldest
                        del self.monitor[: len(self.monitor) - RATE * 2]
            finally:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            LOG.warning("Speaker monitor recording stopped; again in 5 s")
            await asyncio.sleep(5)

    async def _pulse_events(self) -> None:
        while True:
            proc = await asyncio.create_subprocess_exec("pactl", "subscribe", stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL)
            try:
                assert proc.stdout
                while line := await proc.stdout.readline():
                    text = line.decode(errors="replace").strip()
                    if not any(k in text for k in ("sink-input", "source-output", "client")):  # our own players and recorders
                        LOG.info("PulseAudio: %s", text)
            finally:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            LOG.warning("pactl subscribe stopped; again in 5 s")
            await asyncio.sleep(5)

    # ------------------------------------------------------------------ every microphone chunk
    def feed(self, chunk: bytes) -> None:
        """One chunk of the raw microphone, lined up with as much of the monitor."""
        mon = bytes(self.monitor[: len(chunk)]).ljust(len(chunk), b"\0")
        del self.monitor[: len(chunk)]
        rms, zcr = measure(chunk)
        mon_rms, _ = measure(mon)
        self._summary(rms, mon_rms)
        now = time.monotonic()
        if self.event is not None:
            self._during(chunk, mon, rms, zcr, mon_rms, now)
        else:
            self.ring_mic.append(chunk)
            self.ring_mon.append(mon)
            if max(rms, mon_rms) >= LOUD:
                self.loud_since = self.loud_since or now
                if now - self.loud_since >= LOUD_FOR:
                    self._begin(rms, zcr, mon_rms, now)
            else:
                self.loud_since = None

    def _begin(self, rms: float, zcr: float, mon_rms: float, now: float) -> None:
        self.event = {"start": now, "wall": time.strftime("%Y%m%d-%H%M%S"), "mic": list(self.ring_mic), "mon": list(self.ring_mon),
                      "quiet_since": None, "peak": rms, "zcr": [zcr], "mon_peak": mon_rms, "playing": self.playing(),
                      "second": now, "sec_rms": [], "sec_zcr": [], "sec_mon": []}
        LOG.warning("LOUD start: mic %s, zero crossings %.2f, speaker monitor %s, AAC playing: %s",
                    db(rms), zcr, db(mon_rms), self.event["playing"])

    def _during(self, chunk: bytes, mon: bytes, rms: float, zcr: float, mon_rms: float, now: float) -> None:
        ev = self.event
        ev["mic"].append(chunk)
        ev["mon"].append(mon)
        ev["peak"], ev["mon_peak"] = max(ev["peak"], rms), max(ev["mon_peak"], mon_rms)
        ev["sec_rms"].append(rms)
        ev["sec_zcr"].append(zcr)
        ev["sec_mon"].append(mon_rms)
        if now - ev["second"] >= 1:  # one line per second while it lasts
            n = len(ev["sec_rms"])
            LOG.warning("  loud: mic %s, zero crossings %.2f, speaker monitor %s, AAC playing: %s",
                        db(max(ev["sec_rms"])), sum(ev["sec_zcr"]) / n, db(max(ev["sec_mon"])), self.playing())
            ev["zcr"].append(sum(ev["sec_zcr"]) / n)
            ev["second"], ev["sec_rms"], ev["sec_zcr"], ev["sec_mon"] = now, [], [], []
        ev["quiet_since"] = None if max(rms, mon_rms) >= LOUD else ev["quiet_since"] or now
        if (ev["quiet_since"] and now - ev["quiet_since"] >= QUIET_FOR) or now - ev["start"] >= LONGEST:
            self.event = None
            self.loud_since = None
            LOG.warning("LOUD end after %.1f s: mic peak %s, zero crossings %.2f, speaker monitor peak %s",
                        now - ev["start"], db(ev["peak"]), sum(ev["zcr"]) / len(ev["zcr"]), db(ev["mon_peak"]))
            asyncio.get_running_loop().run_in_executor(None, self._save, ev)

    def _summary(self, rms: float, mon_rms: float) -> None:
        st = self.stats
        st["mic"] += rms
        st["mon"] += mon_rms
        st["mic_max"], st["mon_max"] = max(st["mic_max"], rms), max(st["mon_max"], mon_rms)
        st["n"] += 1
        if time.monotonic() >= st["due"]:
            LOG.info("Levels %.0f s: mic average %s, loudest %s; speaker monitor average %s, loudest %s",
                     time.monotonic() - st["since"], db(st["mic"] / st["n"]), db(st["mic_max"]), db(st["mon"] / st["n"]), db(st["mon_max"]))
            self.stats = {"mic": 0.0, "mic_max": 0.0, "mon": 0.0, "mon_max": 0.0, "n": 0, "since": time.monotonic(),
                          "due": time.monotonic() + SUMMARY}

    @staticmethod
    def _save(ev: dict) -> None:
        mic, mon = array("h", b"".join(ev["mic"])), array("h", b"".join(ev["mon"]))
        n = min(len(mic), len(mon))
        both = array("h", bytes(n * 4))
        both[0::2], both[1::2] = mic[:n], mon[:n]
        path = FOLDER / f"loud-{ev['wall']}.wav"
        try:
            with wave.open(str(path), "wb") as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(RATE)
                out.writeframes(both.tobytes())
            for old in sorted(FOLDER.glob("loud-*.wav"))[:-KEEP]:
                old.unlink()
        except OSError as err:
            LOG.error("Saving %s failed: %s", path, err)
            return
        LOG.info("Saved %s (%.1f s, %d s before it started)", path, n / RATE, BEFORE)
