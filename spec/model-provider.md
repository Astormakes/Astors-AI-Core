# Model provider and broker — partial

## Provider (built)

How AAC talks to a model. Today one implementation: any OpenAI-compatible server
(llama.cpp, Ollama, vLLM, LM Studio, cloud APIs).

| Call | OpenAI endpoint | Used for |
|---|---|---|
| `list_models()` | `GET /v1/models` | finding models, online check |
| `chat(model, messages, tools?, **params)` | `POST /v1/chat/completions` | agents |
| `embed(model, texts)` | `POST /v1/embeddings` | memory |
| `transcribe(model, wav, language?)` | `POST /v1/audio/transcriptions` (multipart, `verbose_json` with word timestamps, falls back to `json`) | audio pipeline |

`transcribe` returns `{"text", "language", "confidence", "words": [{word, start, end}]}`;
`confidence` = exp(mean `avg_logprob`) when the server gives segments, else null. Without
`language` a multilingual model detects it per sentence. Tested servers: whisper.cpp
(`whisper-server --inference-path /v1/audio/transcriptions`); faster-whisper servers
(speaches) speak the same API.

**Roles** are pool lists (see Pools), so every role is interchangeable and can be set in the
panel or by AAC in a chat (`use_model`). Model kind is guessed from
the name for the pickers (`whisper|parakeet|…` = speech, `embed|bge|…` = embedding).

Errors: `ProviderConnectionError` (unreachable/timeout), `ProviderAuthError` (401/403),
`ProviderNotFoundError` (404, e.g. a speech server without `/v1/models`: it then counts
as online and its model name is typed by hand), `ProviderError` (anything else). Extra params passed through: `temperature`,
`max_tokens`, `response_format`, `chat_template_kwargs`.

## Model entry (built)

```json
{"id": "m_ae260c90", "name": "bge-m3 (embeddings)", "base_url": "http://server:8081/v1", "api_key": "…", "model": "bge-m3"}
```

- **manual**: made by the owner (ids `m_…`).
- **auto**: every model a known server offers. Known servers = HA's llama.cpp and
  Ollama config entries (only read) + the servers of manual models.
  Ids `auto|<source>|<model>` — internal to the core, opaque to everyone else.

The API key is never sent to the panel (`has_key` instead).

## Machines (built, v0.25.0)

Models are grouped by the machine they run on: the host of the server URL (`127.0.0.1` = `localhost`).
The owner may name a machine (`{host: {"name": …}}` in the store, `aac/machine/set`); unnamed it is
called after its HA integration or its host. A server with a single model serves it whatever the entry
calls it (llama.cpp names models by file path), so that entry counts as online.
In the panel each pool sits in its own menu: thinking in Compute, voice in Audio pipeline, vision in Video.
Planned: a machine as a "socket" — the models it can load, the loaded one, measured load/unload times.

## Pools (partial)

The home's compute, shared by all domains (pools stand above domains). Three pools
(owner, 2026-10-19), each an ordered list of resources = a model or engine on a machine:

| Pool | Capabilities |
|---|---|
| **thinking** | chat tiers (master, fast, big), embeddings |
| **voice** | speech to text, text to speech, later speaker ID and wake word |
| **vision** | detectors, vision LLM |

Per resource: capability, machine, privacy tier, availability (`always` for a server,
`sometimes` for a desktop PC), priority. The broker picks the best reachable resource
that fits the request, falls back down the list, and makes a fallback visible.

Built (v0.12.0, `pools.py`): `store.pools` = role → ordered list of model ids / HA engine ids.
Roles: `master` (thinking), `memory` (thinking, one model: vectors do not mix), `stt`, `tts`
(voice). The broker hands out the first usable entry; a resource whose machine did not answer
(connect timeout 3 s) is marked down for 60 s and the next one is used (master: within the
same message; speech: per segment, HA's local engine last). WebSocket `aac/pools`,
`aac/pool/set`; setup tool `use_model(role, id, position first|last, remove)`. Old single
choices (≤ 0.11) migrate into the first entry.

### Traits and requests (built, v0.13.0)

Per resource (`store.traits`): measured speed (tokens/s; llama.cpp `timings.predicted_per_second`,
else completion tokens / time; moving average, newest 30 %), size in billions of parameters
(llama.cpp `/v1/models` meta `n_params`, else parsed from the name: "35B-A3B" → 35), and the
owner's own tags (e.g. `efficient` for energy-saving hardware).

A run asks for a **tier** instead of a fixed model (`run.tier`, recorded with `run.model`):

| Tier | Picks, among the master role's models that answer right now |
|---|---|
| `master` | the owner's order |
| `fast` | the highest measured speed |
| `smart` | the most parameters |
| `small` | the fewest parameters (only when sizes differ) |
| any own tag | the first model with that tag |

Unknown tier or nothing fits → the master's first reachable model. The heartbeat asks for
`fast` by default (setting), event triggers for `master` (`do.tier`). Planned users: the voice
gate (a fast model decides whether the smart one is needed), research and curator (`smart`).
`aac/pool/measure` and the setup tool `measure_models` run one short generation per model;
`aac/pool/tags` / `tag_model` set own tags.

Measured on the real setup: Qwen3-8B on 2× K2200 6.1 tok/s, Qwen3.6-35B on the RTX 5070 Ti 86 tok/s.

### Availability rules (built, v0.16.0)

The owner's rule for when a model may be used, stored with its traits:
`{"applies_to": "background" | "all", "when": [conditions], "note": "the owner's words"}`.
Conditions (all must hold): `{"type": "state", "entity_id", "state": str | [str]}` (unknown or
unavailable = not met) and `{"type": "time", "after", "before", "weekdays"}` (may wrap midnight).
`background` rules limit only automatic runs (heartbeat, triggers, later research); the owner's
chats may always use the model. Set by the owner: in a chat AAC turns their words into a rule
(setup tool `set_availability`, owner present only; it must not guess a presence entity and asks
when none tracks the owner), or `aac/pool/rule`. Agents can never set rules on their own.

Real test (35B): "use my PC for heavy background stuff, but only while I'm at work" → it saw both
trackers report "unknown", asked, and from "Monday to Friday, roughly 7 to 16" set a weekday
07:00–16:00 background rule. Before the tool text was sharpened it picked a computer's tracker.

## Job queue (built, v0.17.0)

Automatic work (heartbeat, triggers, AAC's own plans, later module jobs) is a **job** with classes,
like models have traits (`jobs.py`):

| Field | Meaning |
|---|---|
| `priority` | `high` (reminders, appointments) · `normal` (event reactions) · `low` (heartbeat, background) |
| `due` | after this the job is pointless (heartbeat: the next one; reminders: +30 min; events: +15 min) |
| `critical` | if no model came before `due`, the `fallback` text reaches the owner anyway (HA notification + "From AAC"), without a model |
| `tags` | e.g. `heartbeat`, `reminder`, `event`, `own plan` |

A job runs at once when the broker has an allowed model (background rules apply), else it waits,
highest priority first, checked every 30 s. Owner settings (`store.queue`): `notify_no_model` (tell
once per outage), `postpone_notice` (minutes; a job that waited longer is told so when it runs). The
owner's chats and voice never queue. Triggers may override the classes in `do`: `priority`,
`critical`, `due_minutes`. Waiting trigger jobs are saved (`.storage/aac.jobs`) and rebuilt after a restart; past their due time, critical ones reach the owner right away. Heartbeats are not kept. WebSocket
`aac/jobs`, `aac/jobs/settings`; panel: Triggers → Queue, "+n waiting" next to Auto.

## Broker request (planned)

Agents never grab hardware; they ask the broker:

```json
{
  "tier": "big",
  "privacy": "local-only",
  "deadline": "2026-10-20T08:00:00+02:00",
  "interruptible": true,
  "estimate": {"tokens": 20000}
}
```

| Tier | For |
|---|---|
| `master` | the master agent, voice answers (whatever runs anyway) |
| `fast` | pre-filters, quick classification |
| `big` | research, curator, planning, vision |
| `speech` / `vision` | STT, TTS, detectors, vision LLM |
| `cloud` | only with data marked `cloud-ok` |

Answer: `{"run_now": "<model id>"}` · `{"queued_until": "<time>"}` · `{"refused": "<reason>"}`.
Compute nodes (e.g. a gaming PC that offers its GPU when idle) report
`free`/`busy`; interruptible jobs move back when the PC is needed.
