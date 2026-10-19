# AAC – Astor's AI Core

An open, modular AI core for Home Assistant. Home Assistant stays the connection
to the world (devices, sensors, calendars …); AAC is meant to grow into the brain
on top: memory, initiative, agents and modules.

> **Status: early (v0.3.0).** AAC has its own panel (setup workflow, chat,
> workers, settings) and a master agent that talks to any OpenAI-compatible model
> server and can control Home Assistant through the Assist API. Memory, MCP and
> the rest are still being designed.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/Astormakes/Astors-AI-Core`, type **Integration**
2. Install **AAC – Astor's AI Core**, restart Home Assistant
3. Settings → Devices & services → **Add integration** → *AAC* (one click, no questions)
4. Open **AAC** in the sidebar and follow the **SETUP** segment: connect a model, choose the master model

Everything is configured in the AAC panel, nowhere else. AAC never changes Home
Assistant's own voice assistants or AI settings; it only uses HA's AI pieces as
optional tools.

Manual install: copy `custom_components/aac` into your HA `config/custom_components/` and restart.

## Model servers

Anything that speaks the OpenAI chat completions API, for example:

| Server | Base URL example |
|---|---|
| llama.cpp (`llama-server --jinja`) | `http://192.168.1.20:8080/v1` |
| Ollama | `http://192.168.1.20:11434/v1` |
| vLLM / LM Studio | `http://host:8000/v1` |

Servers already set up in Home Assistant (llama.cpp and Ollama integrations) are
found automatically and show up under **WORKERS → AUTO**. Others can be added
under **WORKERS → MANUAL**.

Tool use (controlling HA) needs a model and server with function calling
(for llama.cpp start it with `--jinja`).

## The panel

- **SETUP**: the workflow; hides itself when nothing is open (the eye at the top shows all segments)
- **CHAT**: talk to the master agent
- **WORKERS**: all models AAC can use, found (auto) or made by you (manual)
- **SETTINGS**: master agent (model, instructions, tools from HA, temperature, max tokens, thinking), AAC's own audio pipeline with its sources (planned, separate from HA's Assist), MCP and memory (planned)

Works on phones, tablets, desktops and TVs (scales with the screen, touch and keyboard friendly).

## Contracts

The interfaces AAC is built around (runs, triggers, context recipes, agent
templates, memory, model providers, events, modules, domains) are written down
in [`spec/`](spec/README.md), with the status of each.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest                # mocked tests
.venv/bin/pytest -m live -s     # against a real server (AAC_LIVE_URL, AAC_LIVE_KEY)
```
