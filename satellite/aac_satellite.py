#!/usr/bin/env python3
"""AAC satellite: streams a microphone to AAC and plays AAC's spoken answers.

Runs anywhere with PulseAudio/PipeWire (parec/paplay) or ALSA (arecord/aplay):
a PC, a Raspberry Pi, or (later) as a Home Assistant app next to a USB or
Bluetooth headset. Only dependency: aiohttp.

    python aac_satellite.py --url http://homeassistant.local:8123 --token-file ~/.aac_token --name PC --area Office
    python aac_satellite.py --list      # microphones and speakers

The token is a Home Assistant long-lived access token (profile → Security).
Protocol: see custom_components/aac/audio_view.py.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile

import aiohttp

RATE = 16000
CHUNK = 640  # 20 ms of 16-bit mono
LOG = logging.getLogger("aac_satellite")


def capture_cmd(device: str | None) -> list[str]:
    """Command that writes raw 16 kHz mono s16le to stdout."""
    if shutil.which("parec"):
        cmd = ["parec", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=20"]
        return [*cmd, f"--device={device}"] if device else cmd
    if shutil.which("arecord"):
        cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"]
        return [*cmd, "-D", device] if device else cmd
    raise SystemExit("Neither parec (PulseAudio/PipeWire) nor arecord (ALSA) found")


def play_cmd(path: str, device: str | None) -> list[str]:
    """Command that plays a WAV file."""
    if shutil.which("paplay"):
        return ["paplay", *([f"--device={device}"] if device else []), path]
    if shutil.which("aplay"):
        return ["aplay", "-q", *(["-D", device] if device else []), path]
    raise SystemExit("Neither paplay nor aplay found")


def list_devices() -> None:
    """Print microphones and speakers."""
    if shutil.which("pactl"):
        for kind in ("sources", "sinks"):
            print(f"--- {'microphones (--mic)' if kind == 'sources' else 'speakers (--speaker)'}")
            out = subprocess.run(["pactl", "list", kind, "short"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                name = line.split("\t")[1]
                if not name.endswith(".monitor"):
                    print(" ", name)
    else:
        subprocess.run(["arecord", "-l"])
        subprocess.run(["aplay", "-l"])


class Satellite:
    """One connection to AAC (reconnects forever)."""

    def __init__(self, args: argparse.Namespace, token: str) -> None:
        """Initialize."""
        self.args = args
        self.token = token
        self.playing = False

    @property
    def ws_url(self) -> str:
        url = self.args.url.rstrip("/")
        return ("wss://" + url[8:] if url.startswith("https://") else "ws://" + url.split("://", 1)[-1]) + "/api/aac/audio"

    async def run_forever(self) -> None:
        """Connect, stream, reconnect with backoff."""
        delay = 1
        while True:
            try:
                await self._session()
                delay = 1
            except (aiohttp.ClientError, OSError, TimeoutError) as err:
                LOG.warning("Connection problem: %s (retry in %s s)", err, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def _session(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with aiohttp.ClientSession(headers=headers) as http, http.ws_connect(
            self.ws_url, heartbeat=30, max_msg_size=16 * 1024 * 1024
        ) as ws:
            await ws.send_json({"type": "hello", "id": self.args.id, "name": self.args.name,
                                "area": self.args.area, "rate": RATE})
            welcome = await ws.receive_json(timeout=10)
            LOG.info("Connected to AAC as %s", welcome.get("source_id"))
            mic = await asyncio.create_subprocess_exec(
                *self._mic_cmd(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            sender = asyncio.create_task(self._send_mic(ws, mic))
            try:
                await self._receive(ws)
            finally:
                sender.cancel()
                with contextlib.suppress(ProcessLookupError):
                    mic.kill()
                await mic.wait()
            LOG.info("Disconnected")

    def _mic_cmd(self) -> list[str]:
        if not self.args.input_file:
            return capture_cmd(self.args.mic)
        # Testing: a recording as the microphone, in real time, then silence
        return ["sh", "-c", f"ffmpeg -loglevel error -re -i {shlex.quote(str(self.args.input_file))} "
                f"-f s16le -ac 1 -ar {RATE} -; ffmpeg -loglevel error -re -f lavfi -i anullsrc=r={RATE}:cl=mono "
                f"-f s16le -"]

    async def _send_mic(self, ws: aiohttp.ClientWebSocketResponse, mic: asyncio.subprocess.Process) -> None:
        assert mic.stdout
        while chunk := await mic.stdout.read(CHUNK):
            if not self.playing and not ws.closed:  # don't send AAC's own voice back
                await ws.send_bytes(chunk)
        LOG.error("Microphone stopped (wrong --mic?)")
        await ws.close()

    async def _receive(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        pending: dict | None = None
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = msg.json()
                if data.get("type") == "play":
                    pending = data
                    self.playing = True
                    LOG.info("AAC: %s", data.get("text", ""))
            elif msg.type == aiohttp.WSMsgType.BINARY and pending is not None:
                pending = None
                await self._play(msg.data)
                self.playing = False
                await ws.send_json({"type": "played"})

    async def _play(self, wav: bytes) -> None:
        if self.args.output_dir:  # testing: keep the answers as files
            self.args.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.args.output_dir / f"answer_{len(list(self.args.output_dir.glob('*.wav'))) + 1}.wav"
            path.write_bytes(wav)
            LOG.info("Saved %s", path)
            return
        with tempfile.NamedTemporaryFile(suffix=".wav") as file:
            file.write(wav)
            file.flush()
            proc = await asyncio.create_subprocess_exec(*play_cmd(file.name, self.args.speaker))
            await proc.wait()


def main() -> None:
    """CLI."""
    parser = argparse.ArgumentParser(description="Stream a microphone to AAC and play its answers.")
    parser.add_argument("--url", default=os.environ.get("AAC_URL", "http://homeassistant.local:8123"))
    parser.add_argument("--token-file", type=Path, default=os.environ.get("AAC_TOKEN_FILE"))
    parser.add_argument("--name", default=socket.gethostname(), help="Shown in AAC, e.g. 'PC'")
    parser.add_argument("--id", default=None, help="Stable id (default: from the name)")
    parser.add_argument("--area", default=None, help="Room, e.g. 'Office'")
    parser.add_argument("--mic", default=None, help="Microphone (see --list); default: system default")
    parser.add_argument("--speaker", default=None, help="Speaker (see --list); default: system default")
    parser.add_argument("--list", action="store_true", help="List microphones and speakers")
    parser.add_argument("--input-file", type=Path, help="Testing: stream this recording instead of a mic (needs ffmpeg)")
    parser.add_argument("--output-dir", type=Path, help="Testing: save answers as WAV files instead of playing them")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")
    if args.list:
        list_devices()
        return
    token = os.environ.get("AAC_TOKEN") or (args.token_file.expanduser().read_text().strip() if args.token_file else "")
    if not token:
        raise SystemExit("Give a Home Assistant access token: --token-file or AAC_TOKEN")
    args.id = args.id or args.name
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(Satellite(args, token).run_forever())


if __name__ == "__main__":
    main()
