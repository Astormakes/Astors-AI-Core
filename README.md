# AAC – Astor's AI Core

An open, modular AI core for Home Assistant. Home Assistant stays the connection
to the world (devices, sensors, calendars …); AAC is meant to grow into the brain
on top: memory, initiative, agents and modules.

> **Status: early (v0.1.0).** Right now AAC is a conversation agent that talks to
> any OpenAI-compatible model server and can control Home Assistant through the
> Assist API. Everything else is still being designed.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/Astormakes/Astors-AI-Core`, type **Integration**
2. Install **AAC – Astor's AI Core**, restart Home Assistant
3. Settings → Devices & services → **Add integration** → *AAC*
4. Enter the base URL of your model server (including `/v1`) and the API key if it has one, then pick a model
5. Settings → Voice assistants → choose the AAC agent as the conversation agent

Manual install: copy `custom_components/aac` into your HA `config/custom_components/` and restart.

## Model servers

Anything that speaks the OpenAI chat completions API, for example:

| Server | Base URL example |
|---|---|
| llama.cpp (`llama-server --jinja`) | `http://192.168.1.20:8080/v1` |
| Ollama | `http://192.168.1.20:11434/v1` |
| vLLM / LM Studio | `http://host:8000/v1` |

Tool use (controlling HA) needs a model and server with function calling
(for llama.cpp start it with `--jinja`).

## Options

- **Instructions**: the system prompt (templates allowed)
- **Control Home Assistant**: which LLM APIs the agent may use (e.g. Assist)
- **Temperature**, **Max tokens**
- **Let the model think**: sends `chat_template_kwargs.enable_thinking` (llama.cpp / vLLM with Qwen3-style templates)

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest                # mocked tests
.venv/bin/pytest -m live -s     # against a real server (AAC_LIVE_URL, AAC_LIVE_KEY)
```
