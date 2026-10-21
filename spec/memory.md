# Memory — built (v1: text + facts)

Storing and finding things for the long term. **One interface, two stores** in
one local SQLite file: text memories (searched by keywords and meaning) for
concepts, and **structured facts** (fields) for values that must be exact.

Since 0.47 memory is a module (`modules/memory`), the default provider of the core's **memory slot**
([streams.md](streams.md) → Slots). The core only asks the slot: `async_context(domain, text)` (what goes
along with a message), `async_after_message(domain, text, stored_now, provider, model)` (the write path),
`async_model_changed()` (new embedding model → re-embed), `async_remove_domain(domain)`. The module brings
the tools (`aac-memory__…`; `forget` only with the owner in the chat, like every module's deleting tools),
adds open plans to the open loops, and has the page (MODULES → Memory). On/off = the module's switch per
domain (0.46's `memory.enabled` moves there); setting `extract` = the write path after each message. Files
unchanged: `aac/memory[.<domain>].db`.

## Item

```json
{
  "id": "4a135a6cb396",
  "text": "The drill is on basement shelf 2.",
  "kind": "fact",
  "source": "user said",
  "confirmed": true,
  "created": "2026-10-19T00:01:12Z",
  "updated": "2026-10-19T00:01:12Z",
  "embedded": true
}
```

| Field | Values |
|---|---|
| `text` | one self-contained sentence; the original text is always kept |
| `kind` | `fact` · `preference` · `event` · `note` · `plan` (something the owner wants to achieve or do; shown to automatic runs as an open loop) |
| `source` | `user said` · `agent believes` · `sensor` |
| `confirmed` | `agent believes` starts `false`; it counts as a fact only once the owner confirms it. Everything else starts `true` |
| `embedded` | whether a vector from the current embedding model exists |

Rule: **an agent's guess never becomes a fact by itself.** This stops
hallucinations from spreading through the memory.

## Structured fact

```json
{"subject": "user", "attribute": "weight", "value": 81.6, "unit": "kg",
 "time": "2026-10-19T05:30:00Z", "source": "user said", "memory_id": "4a135a6cb396"}
```

- `value` is a number when it is one (stored numerically) or a short text ("basement shelf 2").
- `subject` / `attribute` compare case-insensitively, `_` and `-` count as spaces.
- Every fact belongs to a text memory (`memory_id`); forgetting the memory removes the fact.
- A series = all facts with the same subject + attribute, oldest first. Its summary is
  computed by code, not by the model: latest value, and for numbers first, latest and change.

## Operations

| Operation | Input → output |
|---|---|
| `remember` | text, kind, source, optional subject/attribute/value/unit (→ also a fact) → item |
| `lookup_facts` | subject?, attribute? → series with summaries and values |
| `recall` | query, limit, min_score → items, best first |
| `update` | id + any of text/kind/source/confirmed → item (new text = new vector) |
| `forget` | id → deleted? |
| `list` | limit, only unconfirmed? → items, newest first |
| `reembed` | → number of items that got a vector from the current model |

As tools for agents (LLM API `aac_memory`): `remember`, `recall`, `lookup_facts`, `forget`.
Panel: WebSocket `aac/memory/list|save|delete|settings|reembed`, `aac/facts`.

## Write path (built)

After every message from the owner (not for automatic runs), a short background
call extracts what is worth keeping: facts, preferences, events/measurements with
numbers, plans, with structured fields where there is a value. Duplicates are
skipped: same text, or a very close vector **with the same numbers** (embeddings
barely see numbers: "weighs 81.6 kg" and "weighs 82.4 kg" are >0.9 similar).
Needed because models often answer nicely and never call `remember`.

## Context (built)

Before every answer the best memories (vector similarity ≥ 0.5) are attached to the
user message, plus the summaries of fact series behind them
("user · weight: 82.4 → 81.6 kg (change -0.8, 2 values since …)").

## Search

Hybrid, merged by reciprocal rank fusion:
- **Keywords** (SQLite FTS5, filler words in English/German dropped) — exact names and numbers.
- **Meaning** (embeddings, cosine similarity, only vectors from the same model) — "cellar" finds "basement", German questions find English memories.

Without an embedding model only keywords are used. Each vector stores the model
that made it; changing the model re-embeds everything from the text.

`min_score` for vector hits: 0.35 for explicit searches, 0.5 when memories are
offered automatically (bge-m3 rates unrelated text around 0.4).

## Planned

Timeline (what happened when, summaries), entities/people (stable IDs, links,
provisional "unknown #17"), curator (nightly: merge duplicates, flag contradictions),
privacy tier per item, per-person memory.

Code: `custom_components/aac/modules/memory/` (`store.py` the database, `api.py` the tools, `extractor.py` the write path, `__init__.py` slot, commands `aac/memory/*` and `aac/facts`, `page.js`); the slot: `slots.py`.
