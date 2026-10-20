# Audio source — partial

How a microphone (an AAC satellite) streams to AAC and gets AAC's spoken answers
back. One WebSocket per source: `GET /api/aac/audio`, authenticated with a Home
Assistant access token (`Authorization: Bearer …`).

| Direction | Message |
|---|---|
| source → AAC | `{"type": "hello", "id": "pc", "name": "PC", "area": "Office", "rate": 16000}` (first message) |
| AAC → source | `{"type": "welcome", "source_id": "pc"}` |
| source → AAC | binary: raw PCM, 16-bit little endian, 16 kHz, mono, any chunk size (20 ms recommended), continuously |
| AAC → source | `{"type": "play", "format": "wav", "bytes": n, "text": "…"}`, then one binary message of n bytes |
| source → AAC | `{"type": "played"}` when playback finished |

Rules:
- While a source plays AAC's answer, AAC ignores its microphone (no talking to itself).
  The source may stop sending meanwhile.
- `id` is stable per source (a reconnect replaces the old connection); AAC remembers sources
  that connected once.
- Raw audio lives only in AAC's RAM ring buffer (last 30 s), never on disk.

What AAC does with it (built): voice activity → speech segments → speech to text (a
speech model from Models, see [model-provider.md](model-provider.md), or an HA STT engine) →
transcript line `{time, source, name, text, addressed, language, confidence}` → when addressed, the master
answers and the answer is spoken on the same source, in the owner's voice for the answer's language
(`audio.voices`: `de`/`en` → `{language, voice}` of the TTS engine; guessed from the answer's words; the engine's
default when unset or failing). Voice runs are anonymous (no
setup tools) until there is speaker ID.

Planned: listening modes per source (off / wake word / continuous), arbitration
between mics, quality values per segment, speaker ID, HA devices and the panel
(browser) as sources.

## The satellite program and its device plugins

`addons/aac_satellite/aac_satellite.py` (PC: run it directly; HA machine: the "AAC satellite" app from the
same folder). Device quirks are plugins in `devices/` (`detect()`, `mic_hint`/`speaker_hint`, `start`/`stop`,
`on_audio(rms)`, `tick()`), so hardware weirdness stays out of AAC and out of the satellite's core. The app reads
its access from `<config>/aac/satellite.json`, which AAC writes on request (`aac/satellite/app`: a local-only HA
user "AAC satellite" with a long-lived token).

Code: `custom_components/aac/audio_view.py`, `audio_hub.py`, `satellite_app.py`; satellite: `addons/aac_satellite/`.
