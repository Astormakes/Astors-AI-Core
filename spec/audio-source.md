# Audio source

How a microphone (an AAC satellite) streams to AAC and gets AAC's spoken answers
back. One WebSocket per source: `GET /api/aac/audio`, authenticated with a Home
Assistant access token (`Authorization: Bearer …`).

| Direction | Message |
|---|---|
| source → AAC | `{"type": "hello", "id": "pc", "name": "PC", "area": "Office", "rate": 16000}` (first message) |
| AAC → source | `{"type": "welcome", "source_id": "pc"}` |
| source → AAC | binary: raw PCM, 16-bit little endian, 16 kHz, mono, any chunk size (20 ms recommended), continuously |
| AAC → source | `{"type": "play", "format": "wav", "bytes": n, "text": "…"}`, then one binary message of n bytes |
| source → AAC | `{"type": "played"}` when playback finished (or was stopped) |
| AAC → source | `{"type": "stop"}`: stop playing now (a stop reached Voice out, or HA's stop on its media player) |

Rules:
- While a source plays AAC's answer, AAC ignores its microphone (no talking to itself).
  The source may stop sending meanwhile.
- `id` is stable per source (a reconnect replaces the old connection); AAC remembers sources
  that connected once.
- Each source is also a Home Assistant media player (`media_player.aac_satellite_<name>`): HA can play
  media and TTS on it, set the volume and stop it.
- Raw audio stays in RAM, never on disk.

What AAC does with it is up to the cards: the core hands every chunk to whoever takes the `audio` channel (the
Microphone card), and the audio cards wired after it (Noise gate, Audio clipper, Speech to text, …) turn it into contexts
(spec/cards.md). Voice out speaks through `core.audio.say()` / `play()` and stops with `core.audio.stop()`.

## The satellite program and its device plugins

`addons/aac_satellite/aac_satellite.py` (PC: run it directly; HA machine: the "AAC satellite" app from the
same folder). Device quirks are plugins in `devices/` (`detect()`, `mic_hint`/`speaker_hint`, `start`/`stop`,
`on_audio(rms)`, `tick()`), so hardware weirdness stays out of AAC and out of the satellite's core. The app reads
its access from `<config>/aac/satellite.json`, which AAC writes at start (a local-only HA
user "AAC satellite" with a long-lived token).

Code: `custom_components/aac/audio.py` (the hub, the WebSocket view, satellite.json), `media_player.py`; satellite: `addons/aac_satellite/`.
