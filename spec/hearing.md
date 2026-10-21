# Hearing (contract 2)

What AAC's microphones hear is a shared interface, like the calendar: the core only hears,
modules decide what to do with it. Owner decision 2026-10-19. Since 0.46 it runs on the
[stream bus](streams.md): the stages and the hearing modules are nodes (contract 2; contract 1 had
`add_stage` / `add_hearing`).

## Core
- Sources (satellites, the HA app, later the browser), RAM ring buffers, voice activity, speech to text
  (the voice pool), the transcript (RAM, last lines), speaking (`async_say`, at the owner's volume) and
  answering through the master (`async_answer`).
- The core's node `hearing` writes `audio.pcm` (to media readers), `audio.segment` (through the annotating
  nodes) and every finished line as `text.line` to each **hearing module that is on in the voice domain**.

## Audio stages (nodes that fill the buffer)
```python
seg = {"speech": "audio.segment"}
ctx.add_node(Node(id="stt", title="Speech to text", inputs=seg, outputs=seg, adds=["text", "language"],
                  order=10, annotate=run, partial=partial))
ctx.add_node(Node(id="sounds", title="Sounds", inputs={"audio": "audio.pcm"}, outputs={"events": "sound.event"},
                  listen=between, window=2.0))
```
- **annotating** nodes on `audio.segment` run on every speech segment as a chain, lowest `order` first:
  `annotate(source, pcm, line)` returns fields for the line (`text`, `language`, `speaker`, …), `{"drop": True}`
  to throw it away, `{"split": [...]}` (one line per sentence; the later nodes work on each part), or None.
  `partial(source, pcm)` gives the text so far while someone still talks (live view).
- **media readers** on `audio.pcm` get all audio (speech or not) every `window` seconds: `listen(source, pcm)`
  returns records of their own, kind `sound`, e.g. `[{"text": "[beeping]", "labels": [["Beep", 0.8]]}]`: each
  goes out on the node's output (`sound.event`) and into the buffer as a line.
- Only nodes whose module is on in the voice domain run. Without any, a segment is still a speech line
  without text (length and loudness are measured by the core).

AAC's own spoken answers are buffer lines too (kind `aac`), so modules see both sides of a conversation.

## A heard line
```json
{"time": "2026-10-19T18:06:01+00:00", "source": "ha_app", "name": "Home Assistant", "kind": "speech",
 "text": "…", "seconds": 3.4, "loudness_db": -36.5, "addressed": false,
 "language": "en", "confidence": 0.94, "engine": "m_…", "stt_ms": 125, "words": [], "speaker": …}
```
Sure: `time`, `source`, `kind` (speech / sound), `seconds`, `loudness_db`; `text` from speech to text
(empty without it) or the sound stage. Listeners may mark a line: `addressed: true` (it was meant for
AAC), `gate: "…"` / `note: "…"` (shown in the transcript). Planned: optional annotations from stage
modules (speaker, loudness, media vs. live, room/position) — all optional, like calendar fields.

## A hearing module
```python
ctx.add_node(Node(id="wakeword", title="Wake word", inputs={"lines": "text.line"}, run=on_line))

async def on_line(domain_id, port, line):  # the same object as in the buffer: marks show in the transcript
    source, earlier = ctx.audio.source_of(line), ctx.audio.earlier(line)  # earlier: oldest first
    ...
```
Hearing modules get each line at the same time (one answering does not hold up the others).
Helpers on `ctx.audio`: `source_of(line)`, `earlier(line)`, `mentions(text)` (one of AAC's names, as whole words or written a little differently: similarity ≥ 0.8, names of 5+ letters, not a longer word starting with it), `async_stt(pcm)` (the voice pool's speech
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
- **Companion** (`companion`, off by default; v1 2026-10-19): for every spoken line a quick JSON decision on the
  fast tier reads the last `minutes` of the buffer (voices, AAC's own spoken lines = kind `aac`, sounds) and
  decides for the newest line: respond or not, with a short reason (`judged` on the line). Mode `spoken_to`
  (default) or `join`. One decision at a time per source, lines arriving meanwhile are judged together. Steps
  back while the Wake word module is on. Next: interjection gate (rate limit, timing, research before speaking).
