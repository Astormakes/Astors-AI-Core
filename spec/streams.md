# Streams — partial

Built (0.47): the memory slot (`slots.py`, provider: the memory module). Built (0.44): the bus (`streams.py`: types, default wiring by type, recent records), the core's broker node
(`broker.py`) and the first node module, the heartbeat. 0.45: the triggers module (a node writing `broker.request`).
0.46: audio on the bus (the core node `hearing`; speech to text, sounds and voices are annotating nodes on
`audio.segment`, sound captions read `audio.pcm`, wake word and companion read `text.line`; [hearing.md](hearing.md)). Not yet: own wiring, read rules, slots, audio on the bus.

The core is a **collection of streams**, like the calendar is a collection of the modules' entries
(owner, 2026-10-21: "everything can be wired into anything", node-based almost). Modules are **nodes**:
they read some streams and write others. The core only knows the stream types, keeps the buffers,
does the wiring and enforces who may read what. Everything else (heartbeat, triggers, speech to text,
the companion, video) is a module.

Last shippable version before this rework: tag `v0.43.1-shippable`.

## Words

Owner, 2026-10-21: the **broker** takes every request to a model (a chat, a spoken line, a trigger, the
heartbeat), builds its context, asks the **pool** for a model of the request's tier and records the run.
The **pool** only picks (the ordered lists per role). **Master** means only the main model (the master role).

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
| `broker.request` | record | see below | heartbeat, triggers (built); companion, wake word (planned) |
| `broker.answer` | record | the finished [run](run.md) (result, chat_id) | the broker node |
| `model.request` | record | the request record (`request.py`: text, history, system, instructions, context, tools, …), filled by its chain | chats, voice, Assist, the broker's queue (v1) |
| `model.answer` | record | `{"request", "run", "text", "provider", "model", "stored_now"}` | the model node (v1) |
| `say` | record | `{"text", "language", "room"}` → spoken | answers, announcements |
| `notify` | record | `{"title", "text", "to"}` → phone/panel | reminders, findings |
| `video.frame` | continuous | frames + source | planned (cameras) |
| `vision.detection` | record | `{"what", "who", "box", "confidence"}` | planned |

New types are added to this table (contract change); a module may also declare its own type under its
id (`lists.item_changed`), readable by any node that names it.

**`broker.request`** (built): `{"trigger", "title", "text", "intro", "context": ["time", "home", "open_loops"],
"instructions", "tier", "tools", "priority", "due_minutes", "tags", "coalesce", "chat": "aac" | "own", "chat_prefix",
"critical", "fallback", "keep", "now"}`. The broker node queues it as a job (or runs it at once with `now`, and the writer gets the run back),
builds the context when the run starts, skips it while one with the same trigger waits or runs (`coalesce`), and
writes `broker.answer` `{"run_id", "trigger", "status", "result", "chat_id"}` when done.

**Context on the way (owner, 2026-10-21).** Every request to a model (chats too, once they run through
the bus) passes nodes that add context to it before the broker sends it: the charter adds the owner's values,
memory adds what it recalls, the home adds its snapshot, the triggers module adds AAC's own plans. Each is a
small module of the annotating kind (`broker.request → broker.request`, adds to `context`). Built so far: the
broker builds the named parts itself, modules may add lines to "open loops" (`capabilities.add_open_loops`), and
prompt parts to the start of every system prompt (`add_prompt`; the charter module, 0.48).
Rule for the rework: the core must not make this impossible later, and must not force it on anyone; a module
can still do its job the short way.

**Annotating** (built 0.46). Several nodes may add fields to the same record instead of writing a new one, in order:
STT gives `text`, Voices gives `speaker`. An annotating node has the same type in and out
(`audio.segment → audio.segment`), declares which fields it adds (`adds`) and its place (`order`, lowest first).
The wiring shows them as a chain: writers → first → … → last → the type's readers. Whoever writes such a
type runs the chain (the audio hub, with the raw audio: `annotate(source, pcm, record)`). Readers of a
record get the same object at the same time and may mark it (the wake word's `addressed`).

## A node

```python
ctx.add_node(Node(          # built: Node(id, title, inputs, outputs, run, adds); ctx.emit(domain, node, port, item)
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
**sink** (only inputs: speak, notify, memory, the broker). The broker is a node like the others:
`broker.request` in, `broker.answer` out; it stays core because every domain has one.

## Wiring

- A core card with settings of its own (Agent, Model, Home Assistant tools) carries `edit_schema` and `edit_values` in `aac/nodes`; `aac/nodes/settings {"node", "values"}` changes them (kept in the domain's agent settings). `aac/nodes/positions {"positions": {id: {x, y, pinned}}}` stores the view's card positions (`aac/nodes` returns them as `positions`, null until saved).
- Stored per domain (built v1, `aac/nodes/wires`, store `node_view.<domain>.wires`): `[{"from": "stt.lines",
  "to": "companion.heard"}]`; absent = automatic. Chains follow the wires (`Bus.chain`): an annotating node
  taken out of the chain no longer runs, one wired in elsewhere runs there. Later: `"filter": {"source": "bedroom"}`.
- **Default wiring by type**: a module that is switched on gets its inputs wired to every matching output
  (`auto`), until the owner changes a wire by hand; then that node's wires are kept as set.
- `filter` narrows a wire by fields (source, room, kind, language) without a node in between.
- Cycles are refused (a node can't feed itself through others).
- Later: a node view in the panel (boxes and wires, drag to connect); the same actions as tools for AAC
  (list, connect, disconnect), owner present only.
- The node view lays itself out (owner, 2026-10-21): a force-directed graph. Wires pull like springs, nodes
  keep their distance, the whole thing floats into a stable shape. The owner can drag a node (it stays
  pinned, or floats back when let go); a new wire, made in the graph or on a module's settings, pulls the
  node toward its new partners and the rest settles around it. One view for every pipeline (audio stages,
  triggers and heartbeat into the broker, later video).

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
| `memory` | `async_context(domain, text)` → text for a run, `async_after_message(...)` (write path), `async_model_changed()`, `async_remove_domain(domain)` | the memory module ([memory.md](memory.md); built 0.47) |

Without a provider the slot answers empty, and AAC works without memory. Candidates for later slots:
`tts` (the voice), `presence` (who is where).

## Today's parts as nodes

| Part | Inputs | Outputs |
|---|---|---|
| Heartbeat | `time.tick` | `broker.request` (only when something matters) |
| Triggers | `ha.event`, `time.tick` | `broker.request` |
| Hearing (core) | — | `audio.pcm`, `audio.segment`, `text.line` (built) |
| Speech to text | `audio.segment` | `audio.segment` (+text; built) |
| Sounds behind speech | `audio.segment` | `audio.segment` (+sounds; built) |
| Voices | `audio.segment` | `audio.segment` (+speaker; built) |
| Sounds | `audio.pcm` | `sound.event` (built) |
| Wake word | `text.line` | answers through the hub today; later `broker.request`, `say` |
| Companion | `text.line` | the same |
| Reminders (planning) | `time.tick` | `notify`, `broker.request` |

## Target model: everything is nodes and wires (owner, 2026-10-21)

The owner's picture from the start, nudged into place piece by piece (0.44–0.48), now written down:
the user can build pipelines from nodes with clear inputs and outputs; the system stays open, and typed
ports keep it restricted enough to just work. "Everything is text in the end" for the model.

**One kind of thing: nodes with typed ports, connected by wires.** The bus is only the layer that holds
the wires. Wires are stored per domain; until the owner changes one, the defaults apply (wired by type
and by the manifests' `order`), so a fresh domain works without any wiring. A wire only connects ports of
the same type (`audio.pcm` → `audio.pcm`: the microphone straight to a speaker is allowed; text into an
audio input is not).

**Annotating chains do the detail work.** A record runs through nodes in order and each adds its part
(built for audio: STT adds text, Voices adds the speaker). The same for the model:

```
chat ──▶ [charter] ──▶ [memory] ──▶ [home] ──▶ [lists] ──▶ [MODEL] ──▶ answer ──▶ chat / speaker / notify
```

**The request is a structured record** (type `model.request`), not one long text, so each node writes to
the right place and the server's prompt cache keeps working:

```json
{"text": "Is the bed light on?", "history": [...], "own_words": "…",
 "system": ["<charter>", "<the agent's own prompt>"],
 "context": ["Memories that may help: …", "Open loops: …"],
 "tools": ["aac_home", "aac_lists", "aac_memory"],
 "tier": "master", "kind": "chat", "trigger": "chat", "chat": "…", "domain": "assistant"}
```

- `system`: stable parts, first in the system prompt (charter: order 0).
- `context`: what goes along with this message (memory recalls, open loops, the home snapshot for automatic runs).
- `tools`: what the model may call; a node that brings tools adds its API (home: with the owner's entity
  list as that node's setting). The model sees exactly what is wired before it and chooses by itself
  (tool mode "menu": areas first).
- The **model node** is simple: `model.request` in, `model.answer` out (the run, its result). It picks the
  model from the pool by `tier`. Today's broker is this node.

**Groups hide the detail without making it static** (like Blender's node groups, Node-RED's subflows): a
group is a saved piece of graph with its own outer ports. Every domain starts with one group "Assistant"
(`chat ──▶ [Assistant] ──▶ chat`); opening it shows charter → memory → home → … → model, every node and
wire, changeable. Saved groups can be reused ("work assistant without home control"). A new user never
needs to open one.

**Kept restricted:** typed ports; privacy tiers per stream (a node never gets data stricter than it may
keep); owner-only nodes (the charter: no agent changes or removes it); cycles refused; a paused source
writes nothing.

### Today's parts in the target model

| Today | Target |
|---|---|
| broker (`broker.py`) | the model node (`model.request` → `model.answer`; queue, coalesce, keep, critical stay its job) |
| prompt parts (`add_prompt`, the charter module) | annotating nodes adding to `system` |
| memory slot (context before, write path after) | an annotating node adding to `context`, plus a node reading `model.answer` (the write path) |
| open loops (`add_open_loops`) | annotating nodes adding to `context` in automatic runs |
| a module's tools (`add_llm_api`, Assist API, setup with the owner present) | annotating nodes adding to `tools` |
| the conversation entity (HA Assist), the panel chat, the voice answer | sources writing `model.request`; the answer goes back to where it came from |
| heartbeat, triggers (`broker.request`) | sources writing `model.request` (`broker.request` is its first form) |
| audio: hearing, stt, voices, sounds, wake word, companion | as built (0.46); wake word and companion write `model.request`, a speaker node reads `say` |
| `slots.py` | a default chain position ("one memory node per domain"); the registry can go once the chain exists |

### Steps toward it

1. This section, checked by the owner.
2. The node view, read-only first: the whole system as it is wired today (streams, and the request chain
   once it exists), self-organizing (see Wiring).
3. The request record and its chain inside the broker: prompt parts, memory and the modules' tools become
   annotating nodes on `model.request` (same behaviour, now visible and in order).
4. Stored wires: the owner rewires streams in the node view (and AAC with tools, owner present only).
5. Groups: "Assistant" per domain, open, change, save, reuse.

Open: how a node's settings are edited inside the graph (today: the module's settings page); whether one
domain may have several model nodes (an assistant and a separate "research" pipeline); how answers find
their way back when a request came through several groups.

## Order of the rework

1. This contract, reviewed with the owner.
2. The bus in the core (types, buffers, wires, read rules) next to the old code; nothing moves yet.
3. **Heartbeat** as the first node module (smallest; proves the pattern), then **Triggers**. ✅ 0.44 / 0.45
4. Audio onto the bus (stages and hearing listeners become nodes). ✅ 0.46
5. Memory as the first slot. ✅ 0.47
6. The node view in the panel.

## Defaults taken (the owner can change them later)

1. Default wiring: automatic by type.
2. Rewiring: only the owner (AAC gets tools for it later, owner present only, like the setup tools).
3. Records kept in RAM per output: the last 200, at most 10 minutes old.
4. Slots: memory first; more when a second provider for something appears.
