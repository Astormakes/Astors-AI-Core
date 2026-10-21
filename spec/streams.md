# Streams — planned (draft for the owner's review)

The core is a **collection of streams**, like the calendar is a collection of the modules' entries
(owner, 2026-10-21: "everything can be wired into anything", node-based almost). Modules are **nodes**:
they read some streams and write others. The core only knows the stream types, keeps the buffers,
does the wiring and enforces who may read what. Everything else (heartbeat, triggers, speech to text,
the companion, video) is a module.

Last shippable version before this rework: tag `v0.43.1-shippable`.

## Words

| Word | Meaning |
|---|---|
| **stream type** | a fixed format, e.g. `text.line`; every writer must follow it, so every reader can rely on it |
| **stream** | one live flow of one type from one writer, e.g. the transcript lines of the bedroom mic |
| **node** | a module's worker with typed **inputs** and **outputs** (ports) |
| **wire** | output port → input port; one output may feed many inputs, one input may take many outputs |
| **slot** | an interface with exactly **one** provider per domain, asked on demand (memory); not a stream |

Streams **push** (something happened, here it is). Capabilities and slots are **pulled** (the calendar,
memory: "give me what you have for this question"). Both stay; they answer different needs.

## Stream types

Two classes:
- **continuous**: raw media, RAM only, in a ring buffer per stream, never stored, never sent to a model raw.
- **records**: small JSON objects (times ISO 8601 UTC), kept in RAM (last N per stream); stored only when a
  node whose job is storing (memory, the run log) reads them.

| Type | Class | Format | Written by (today's code) |
|---|---|---|---|
| `audio.pcm` | continuous | 16 kHz mono s16le frames + source | audio sources (satellite, HA app) |
| `audio.segment` | record + pcm ref | one stretch of speech: source, start, end, loudness, pcm (in the buffer) | the core's voice activity detection |
| `text.line` | record | a heard or spoken line (the record in [hearing.md](hearing.md)) | STT, AAC's own answers |
| `sound.event` | record | `{"labels": [["Beep", 0.8]], "text": "[beeping]"}` | Sounds |
| `ha.event` | record | HA's event: `type`, `data` ([event.md](event.md)) | the core (HA bus) |
| `time.tick` | record | `{"at": "…", "name": "every 30 min"}` | a timer node |
| `master.request` | record | `{"text", "context", "tier", "tools", "title", "trigger", "priority"}` | heartbeat, triggers, companion, wake word |
| `master.answer` | record | the finished [run](run.md) (result, chat_id) | the master node |
| `say` | record | `{"text", "language", "room"}` → spoken | answers, announcements |
| `notify` | record | `{"title", "text", "to"}` → phone/panel | reminders, findings |
| `video.frame` | continuous | frames + source | planned (cameras) |
| `vision.detection` | record | `{"what", "who", "box", "confidence"}` | planned |

New types are added to this table (contract change); a module may also declare its own type under its
id (`lists.item_changed`), readable by any node that names it.

**Annotating.** Several nodes may add fields to the same record instead of writing a new one, in order
(today's segment stages): STT gives `text`, Voices gives `speaker`, the attention check gives `addressed`.
An annotating node has the same type in and out (`text.line → text.line`) and declares which fields it adds.

## A node

```python
ctx.add_node(Node(
    id="stt",
    title="Speech to text",
    inputs={"speech": "audio.segment"},
    outputs={"lines": "text.line"},
    run=run,              # async run(port, item, emit) → emit("lines", {...}) zero or more times
    adds=["text", "language"],   # only for annotating nodes
    reads_pcm=True,       # needs the raw audio behind the segment (counts for the read rules)
    privacy="local-only", # where its work may happen at most (see below)
))
```

Kinds by ports: **source** (only outputs: a mic, a timer, HA's events), **transform** (in and out),
**sink** (only inputs: speak, notify, memory, the master). The master is a node like the others:
`master.request` in, `master.answer` out; it stays core because every domain has one.

## Wiring

- Stored per domain: `[{"from": "stt.lines", "to": "companion.heard", "filter": {"source": "bedroom"}}]`.
- **Default wiring by type**: a module that is switched on gets its inputs wired to every matching output
  (`auto`), until the owner changes a wire by hand; then that node's wires are kept as set.
- `filter` narrows a wire by fields (source, room, kind, language) without a node in between.
- Cycles are refused (a node can't feed itself through others).
- Later: a node view in the panel (boxes and wires, drag to connect); the same actions as tools for AAC
  (list, connect, disconnect), owner present only.

## Who may read what (privacy)

- Every stream carries the privacy tier of its source (`local-only` · `home-network` · `cloud-ok`); a node
  never receives a stream whose tier is stricter than what it declares it can keep.
- The owner can block a stream for a node outright ("the bedroom mic never reaches Sounds"); blocked
  beats wired.
- Raw media (`audio.pcm`, `video.frame`, a segment's pcm) only to nodes that declare `reads_pcm`/`reads_frames`.
- A paused source writes nothing (privacy switch, as today).

## Slots

One provider per domain, chosen in SETTINGS; the core and other modules ask the slot, never the module.

| Slot | Asks | Default provider |
|---|---|---|
| `memory` | `recall(query)`, `remember(item)`, `context(request)` → text for a run | today's memory (memory.md) as a module |

Without a provider the slot answers empty, and AAC works without memory. Candidates for later slots:
`tts` (the voice), `presence` (who is where).

## Today's parts as nodes

| Part | Inputs | Outputs |
|---|---|---|
| Heartbeat | `time.tick` | `master.request` (only when something matters) |
| Triggers | `ha.event`, `time.tick` | `master.request` |
| Speech to text | `audio.segment` | `text.line` |
| Voices | `text.line` (+pcm) | `text.line` (+speaker) |
| Sounds | `audio.pcm` | `sound.event` |
| Wake word | `text.line` | `master.request`, `say` |
| Companion | `text.line`, `sound.event` | `master.request` |
| Reminders (planning) | `time.tick` | `notify`, `master.request` |

## Order of the rework

1. This contract, reviewed with the owner.
2. The bus in the core (types, buffers, wires, read rules) next to the old code; nothing moves yet.
3. **Heartbeat** as the first node module (smallest; proves the pattern), then **Triggers**.
4. Audio onto the bus (stages and hearing listeners become nodes; `hearing.md` folds into this).
5. Memory as the first slot.
6. The node view in the panel.

## Open questions for the owner

1. Default wiring: auto by type (proposed), or nothing until the owner wires it?
2. Should AAC itself be allowed to rewire (owner present, like the setup tools), or only the owner in the panel?
3. Records kept in RAM per stream: how many / how long by default (proposed: last 200 or 10 minutes)?
4. Which other slots matter to you besides memory?
