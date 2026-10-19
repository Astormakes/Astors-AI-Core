# Model provider and broker — partial

## Provider (built)

How AAC talks to a model. Today one implementation: any OpenAI-compatible server
(llama.cpp, Ollama, vLLM, LM Studio, cloud APIs).

| Call | OpenAI endpoint | Used for |
|---|---|---|
| `list_models()` | `GET /v1/models` | finding models, online check |
| `chat(model, messages, tools?, **params)` | `POST /v1/chat/completions` | agents |
| `embed(model, texts)` | `POST /v1/embeddings` | memory |

Errors: `ProviderConnectionError` (unreachable/timeout), `ProviderAuthError` (401/403),
`ProviderError` (anything else). Extra params passed through: `temperature`,
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
