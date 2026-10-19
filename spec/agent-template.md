# Agent template — partial

A reusable kind of agent. The "roles" (research, plan the day, watch the printer,
curate memory) are templates; an agent = template + task + budget, started when
needed and ended afterwards (framework, section 3).

```json
{
  "id": "heartbeat",
  "name": "Heartbeat check-in",
  "contract": 1,
  "prompt": "Look through the states. If anything needs the user's attention now …",
  "recipe": "heartbeat",
  "model": {"use": "master"},
  "tools": [],
  "output": "decision",
  "limits": {"tool_rounds": 4, "max_tokens": 1024, "temperature_max": 0.3},
  "privacy": "local-only",
  "speaks_to": "aac"
}
```

Every agent's system prompt starts with the owner's **charter** (their values in plain
words, editable only by the owner, see the framework design), then the template's prompt.

| Field | Meaning |
|---|---|
| `prompt` | the template's instructions (the owner can override them) |
| `recipe` | which [context recipe](context-recipe.md) builds what it sees |
| `model` | `{"use": "master"}` · `{"model_id": "m_…"}` · later `{"tier": "big"}` (broker decides, see [model-provider.md](model-provider.md)) |
| `tools` | LLM API ids it may use (`assist`, `aac_memory`, …). Empty = look only |
| `output` | `text` (an answer) or `decision` (see below) |
| `limits.tool_rounds` | max model ↔ tool round trips; after that one last call **without** tools forces an answer |
| `privacy` | the highest tier of data it may send out (the broker enforces it) |
| `speaks_to` | where a spoken result goes: `aac` ("From AAC" chat), later `voice`, `phone` |

## Output `decision`

For agents that mostly should stay quiet. The model must answer, enforced by the
server's grammar (OpenAI `response_format` / JSON schema):

```json
{"speak": false, "message": ""}
```

`speak: false` → run status `silent`, nothing is posted. Automatic runs that
were allowed tools end with one extra call (no tools, same JSON) after their tool
rounds, so every automatic run decides the same way. Tested: Qwen3-8B 12/12,
Qwen3.6-35B 23/24 (alarm vs. quiet home), much better than asking for a magic
word. Servers without JSON schema support fall back to the text rule
("answer exactly NOTHING") plus a paraphrase check.

## Built-in templates (in code today)

| id | Started by | Tools | Output |
|---|---|---|---|
| `chat` | the owner (panel chat, HA voice) | master's tools + memory | text |
| `heartbeat` | heartbeat trigger | none by default (`tools` switch) | decision |
| `event-reaction` | event trigger | master's tools + memory | text (+ NOTHING rule) |

Planned: templates as data in [modules](module.md); `spawn` (a template starting
another as a sub-agent, max depth 2) with a shared blackboard instead of free
agent-to-agent talk.

Code: `custom_components/aac/triggers.py`, `conversation.py` (limits, decision output).
