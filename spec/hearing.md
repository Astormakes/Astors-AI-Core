# Hearing (contract 1)

What AAC's microphones hear is a shared interface, like the calendar: the core only hears,
modules decide what to do with it. Owner decision 2026-10-19.

## Core
- Sources (satellites, the HA app, later the browser), RAM ring buffers, voice activity, speech to text
  (the voice pool), the transcript (RAM, last lines), speaking (`async_say`, at the owner's volume) and
  answering through the master (`async_answer`).
- Every new transcript line is handed to each **hearing module that is on in the voice domain**.

## Audio stages (modules that fill the buffer)
```python
ctx.add_stage(Stage(id="stt", title="Speech to text", kind="segment", run=run, partial=partial, order=10))
```
- **segment** stages run on every speech segment, in `order`: `run(source, pcm, line)` returns fields for the
  line (`text`, `language`, `speaker`, `mood`, …), `{"drop": True}` to throw it away, or None.
  `partial(source, pcm)` gives the text so far while someone still talks (live view).
- **window** stages run on all audio (speech or not) every `window` seconds: `run(source, pcm)` returns lines
  of their own, kind `sound`, e.g. `[{"text": "[beeping]", "labels": [["Beep", 0.8]]}]`.
- Only stages whose module is on in the voice domain run. Without any, a segment is still a speech line
  without text (length and loudness are measured by the core).

AAC's own spoken answers are buffer lines too (kind `aac`), so modules see both sides of a conversation.

## A heard line
```json
{"time": "2026-10-19T18:06:01+00:00", "source": "ha_app", "name": "Home Assistant", "kind": "speech",
 "text": "…", "seconds": 3.4, "loudness_db": -36.5, "addressed": false,
 "language": "en", "confidence": 0.94, "engine": "m_…", "stt_ms": 125, "words": [], "speaker": …, "mood": …}
```
Sure: `time`, `source`, `kind` (speech / sound), `seconds`, `loudness_db`; `text` from speech to text
(empty without it) or the sound stage. Listeners may mark a line: `addressed: true` (it was meant for
AAC), `gate: "…"` / `note: "…"` (shown in the transcript). Planned: optional annotations from stage
modules (speaker, loudness, mood, media vs. live, room/position) — all optional, like calendar fields.

## A hearing module
```python
ctx.add_hearing(Hearing(id="wakeword", title="Wake word", heard=heard))

async def heard(source, line, earlier):  # earlier: the source's previous lines, oldest first
    ...
```
Helpers on `ctx.audio`: `mentions(text)` (one of AAC's names), `async_stt(pcm)` (the voice pool's speech
engines), `async_answer(source, text, before)`,
`async_say(source, text)`, `changed()` (the panel shows a marked line).

A manifest may say `"enabled_by_default": false`: the owner switches it on per domain.

## Built-in modules
- **Speech to text** (`stt`, on by default): segment stage, the voice pool's engine; live words (model server
  only), drops whisper's made-up lines. Settings: live, filter.
- **Wake word** (`wakeword`, off by default): a name or a follow-up → attention check → the master
  answers by voice, with the lines of the last `look_back` seconds. Settings: follow_up, attention, look_back.
- **Sounds** (`sounds`): window stage → sound lines ("[laughter, music]") between spoken lines, segment stage →
  `sounds` on spoken lines (top AudioSet labels). Pool role `sounds` (services/aac_senses).
- **Voices** (`voices`): segment stage → `speaker` {id, name, kind unknown/person/media, note, score, new}; voice
  prints in HA storage, unnamed ones forgotten after keep_days; tools list_voices, name_voice, forget_voice.
  Pool role `voices`. Wake word ignores voices of kind media, and its attention check sees voice and sounds.
- **Mood** (`mood`, off by default): segment stage → `mood` {label neutral/happy/angry/sad, score, sure}
  from a speech emotion model (the sound of the voice, not the words). `sure` = not neutral and ≥ threshold;
  only sure moods reach the Companion and the master ("by voice #8, sounds happy", `audio_hub.mood_word`).
  Pool role `mood` (services/aac_senses `/v1/audio/mood`, superb/wav2vec2-base-superb-er).
- **Companion** (`companion`, off by default; v1 2026-10-19): for every spoken line a quick JSON decision on the
  fast tier reads the last `minutes` of the buffer (voices, AAC's own spoken lines = kind `aac`, sounds) and
  decides for the newest line: respond or not, with a short reason (`judged` on the line). Mode `spoken_to`
  (default) or `join`. One decision at a time per source, lines arriving meanwhile are judged together. Steps
  back while the Wake word module is on. Next: interjection gate (rate limit, timing, research before speaking).
