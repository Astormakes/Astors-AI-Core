# The graph (v1, owner's design of 2026-10-23)

Replaces the "message with fields + tool cards" design of 1.0.0-dev.21 (that one came from the idea that
tools need a direction of their own; owner, 2026-10-23: "the constant loop idea is better").

## In one paragraph

Everything is a node. A message is a **document**: an ordered list of entries, each written by a node and
tagged by it. Nodes are wired output → input. A model card does not compose anything: it reads the whole
document, and either answers or picks which of its wired nodes the document goes to next, which to the model
looks exactly like calling a tool. Every tool is a loop: model → node → back into the model. The chat is a
node too, with an output (what the user writes) and an input (what comes back): a chat is an editable
document, not a log.

## Port types

Only these: `text` (the message document), `audio`, `video`, each in and out, plus `pool` (the dotted line:
a model card reserves a model from a pool node; the only two-way wire).
Until the audio pipeline is moved over, its old types (`audio.pcm`, `audio.segment`, `text.line`,
`sound.event`) stay as they are.

## Outputs: round and square

- **Round**: always fires. With several wires each one gets a copy (a setting per output says whether more
  than one wire is allowed). The charter, the chat, memory: they cannot decide where a message goes.
- **Square**: the node decides which one wire the message takes. A model card (its wired nodes are its tools)
  and Decide (named outputs).

Inputs take any number of wires; where a message came from does not matter to a node.

## The document

```
entry: {id, by: <node id>, kind, text, tags: [...], time, data: {...}}
kind:  system  (stable text for the system prompt: charter, agent prompt, instructions)
       note    (goes along with the next user turn: a memory recall, context, an automatic run's situation)
       trailer (stable text after the newest user turn, read last: the Trailer card; the Header card writes system)
       user    (what a person said)
       answer  (what a model said)
       call    (a model chose a node: data {call_id, node, tool, args})
       result  (what came back on that loop: data {call_id})
```

- Entries are tagged by the node that writes them (`charter`, `memory`, `ha`, the chat's id …). Tags are the
  pointers: the chat shows its `user`/`answer` entries, an inserter finds "the last memory entry", the expert
  view shows every entry with its node.
- A node that keeps a standing text (charter, agent prompt) **replaces its own entry** instead of adding one
  every time: the start of the document stays the same, so the model server's prompt cache keeps working.
  Editing the middle of a document (an edited chat message, an inserter far back) makes the server
  recompute from there on; appending is cheap.
- The document carries pointers besides entries: `origin` (the node it started at), `reply_to` (where an
  answer goes by default; a heartbeat says `chat`), `chat_id`, the user, the language, the device, and for
  automatic ones how long they may wait.
- While a model waits for a loop, everything nodes write is tagged with that call (`data.call_id`), and becomes
  the tool result the model reads.

## Templates and values

The text cards (Agent prompt, Header, Trailer, Context) render their text with Home Assistant's own template
engine (Jinja, the syntax of HA automations): `{{ now().strftime('%H:%M') }}`, `{{ states('sensor.x') }}`,
`{% if is_state('person.x', 'home') %}…{% endif %}`. A template error shows in the text, it does not stop the
message.

Cards hand on values: `message.vars` is `{card id: {name: value}}` and travels with the message (copied on
every branch); a template reads `{{ <card id>.<name> }}`, plus `{{ message.language }}` (also `user_id`,
`origin`, `trigger`, `title`, `kind`, `device_id`). The card id comes from the name the card got when it was
added ("Kitchen buffer" → `kitchen_buffer`). So far: Heard lines (`text`, `speaker`, `mic`, `mic_name`, `area`),
Buffer (`lines`, `count`, `newest`).

The **Clock** card (core, in the line by default, last before the model) writes the local time as a trailer in every round, or, set to "as a tool", only answers a model that calls it (`time`); its text is a template. The model card adds nothing unseen any more.

Text that changes on every message (the time) belongs in a Trailer: at the start it makes the model server
recompute the prompt.

## The model card

Inputs `in` (text) and later `pool`; one square output `out`.

1. It builds the conversation from the document in the usual chat format (system / user / assistant / tool):
   `system` entries → the system prompt, `note` entries → with the user turn, its own answers → assistant,
   other models' answers → "Model B said …", calls and results → tool calls and tool results. To the model it
   is an ordinary chat with tools; small models know this format.
2. **Its tools are the nodes wired to its output**, except the one its answer goes to. Each node says how it
   is called: a node with tool sets (Home Assistant, lists, calendar, memory, setup …) offers its tools, any
   other node one tool "send it there". As a menu (per model: tool mode "menu"), each node is one function
   with its tools listed.
3. A call puts a `call` entry in the document and sends the document into that node; the node writes its
   result and passes it on; when it comes back into the model, the model goes on. A node that never hands it
   back gives the model an error result.
4. A plain answer is an `answer` entry and goes to: the model that called this one (two models talking), else
   `reply_to`, else the origin, else every wired node that does not lead back into the model.

Automatic messages (heartbeat, triggers) wait in the queue at the model card (priority, due, critical,
coalesce, keep) as before.

## Endpoints

`chat`, `voice`, `assist` have an output and an input. What arrives at the input with their pointer is
delivered (the panel chat stores the document and shows the answer; voice speaks it; Assist returns it to HA).
The heartbeat and the triggers are outputs only; their answers go to the chat (`reply_to: chat`), a finding into
its own chat or "From AAC", silence stays silent.

## The chat is a text editor

A chat keeps its document. Its messages can be edited (also AAC's answers) and cut back; the next message
sends the edited document. The expert view shows every entry, with its node and tags:
`Chat > Model > Memory > Model > Chat`.

## Settings

The panel's settings shrink to Appearance, Languages and Nodes. Everything else becomes node settings:
the model servers and pools (pool nodes), the entity lists (the Home Assistant node), audio, modules.

## Live wires (proposal, owner 2026-10-23; not built yet)

Things can flow live instead of as finished pieces: a node opens an entry, adds to it piece by piece and closes
it with an end marker ("ends the paragraph"). This is meant for audio (where and when a stream is cut matters)
but is the same for every node, so people can try their own experiments: cut speech with a pause rule, with a
small model, with anything else.

- **What a node does with pieces is the node's own business** (no wire setting). Every node sees the pieces;
  only the stop signal (`close`) matters to some of them:
  - nodes that do not care let the pieces through: the charter, the agent prompt, instructions, context keep
    one standing entry at the start and replace it in place, so a growing "I", "I want", "I want to …" still
    has the charter once;
  - nodes that need a finished thought wait for the stop: the model card (today's models cannot react to half
    a sentence), Buffer, Decide, files, tools;
  - nodes that work live act on every piece: speech to text, Voice, a cut. A module may bring a node that
    reacts live in its own way (owner: "maybe someone will build a module card that is able to react live with
    some crazy magic and model constellation"); the graph does not need to know how.
- **One writer per live entry.** Only the node that opened an entry adds to it, replaces its text (a live
  transcript rewrites its last words) or closes it. Nobody else closes it: a node that wants to decide where
  something ends reads it live and writes **its own** entries.
- **So there are three kinds of nodes**, by their input and output:
  - live → live: a stream picker, speech to text, Voice (speaks the answer while it is still written),
    audio out;
  - live → whole: a cut. It reads a live stream and sends on a whole message whenever it thinks a thought is
    finished. A `Cut` node with a pause rule is built in; a model card can do the same job (owner: "in this
    particular installation I wanted to try using a model for it"). Whether that is fast enough is the
    installation's question, not the graph's;
  - whole → whole: everything that waits for the stop (Buffer, Decide, tools, files, a model's tool calls).
  A model card can write its answer live (the panel chat and Voice read it as it comes).
- **Pieces:** `open` (a new entry, its pointers: source/satellite id, speaker …), `add` (append), `replace`
  (new text for the open entry), `close`, `cancel` (stop an open entry, e.g. someone talks while AAC is still
  speaking).
- **Raw audio stays a stream.** Below the first node that makes entries (10 ms frames from a microphone),
  audio is a plain `audio` stream, not entries.
- **The id travels.** The satellite a stream came from is a pointer on the message, so an answer finds its way
  back to the speaker at that satellite without any hidden routing.

Example (the owner's chain): microphones → stream picker (passes one stream and its id) → speech to text (live
text) → a small model as the cut (whole sentences) → Buffer → Decide → main model → Chat and Voice (live) →
audio out (plays at the id). The Companion becomes a saved group of such nodes instead of one hidden card.

## Nodes from elsewhere

A module brings node types (like an HA integration from HACS). Nodes run inside AAC for now; an external node
(another process, an MCP server) can come later through the same ports.

## Built in phases

1. The document, round/square outputs, the model card with loops, endpoints, chat documents, all core and
   module nodes moved over (no tool cards, no Reply, no memory keeper).
2. The node view: round and square ports, the pool wire, the copy setting per output; the chat's expert view
   and editing.
3. Pool nodes; the settings pages folded into nodes.
4. The audio types merged into `audio` / `text`; Hearing and the Companion split into nodes on live wires
   (see Live wires).
