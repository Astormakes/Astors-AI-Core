# Memory — built (v1)

Storing and finding things for the long term. One interface; the store behind
it can change (today: one local SQLite file).

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
| `kind` | `fact` · `preference` · `event` · `note` (more via [modules](module.md): memory schemas) |
| `source` | `user said` · `agent believes` · `sensor` |
| `confirmed` | `agent believes` starts `false`; it counts as a fact only once the owner confirms it. Everything else starts `true` |
| `embedded` | whether a vector from the current embedding model exists |

Rule: **an agent's guess never becomes a fact by itself.** This stops
hallucinations from spreading through the memory.

## Operations

| Operation | Input → output |
|---|---|
| `remember` | text, kind, source → item |
| `recall` | query, limit, min_score → items, best first |
| `update` | id + any of text/kind/source/confirmed → item (new text = new vector) |
| `forget` | id → deleted? |
| `list` | limit, only unconfirmed? → items, newest first |
| `reembed` | → number of items that got a vector from the current model |

As tools for agents (LLM API `aac_memory`): `remember`, `recall`, `forget`.
Panel: WebSocket `aac/memory/list|save|delete|settings|reembed`.

## Search

Hybrid, merged by reciprocal rank fusion:
- **Keywords** (SQLite FTS5, filler words in English/German dropped) — exact names and numbers.
- **Meaning** (embeddings, cosine similarity, only vectors from the same model) — "cellar" finds "basement", German questions find English memories.

Without an embedding model only keywords are used. Each vector stores the model
that made it; changing the model re-embeds everything from the text.

`min_score` for vector hits: 0.35 for explicit searches, 0.5 when memories are
offered automatically (bge-m3 rates unrelated text around 0.4).

## Planned

Timeline (what happened when), entities/people (stable IDs, links, provisional
"unknown #17"), extractor (sorts what was said into the right kind), curator
(nightly: merge duplicates, flag contradictions), privacy tier per item,
per-person memory.

Code: `custom_components/aac/memory.py`, `memory_api.py`.
