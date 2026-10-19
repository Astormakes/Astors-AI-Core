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

**Roles** are just model ids in settings, so every role is interchangeable and can be
set in the panel or by AAC in a chat (`use_model`): master (chat), memory (embeddings),
speech (speech to text; HA STT engines are the other choice). Model kind is guessed from
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

Built so far: one choice per role plus an automatic fallback for speech to text (the chosen
model; when its machine is unreachable, HA's local engine; retried every minute; shown in the
panel and to the setup tools). Planned: ordered lists per capability instead of single role ids.

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
