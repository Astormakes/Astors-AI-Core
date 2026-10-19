# AAC – Astor's AI Core

An open, modular AI core for Home Assistant. Home Assistant stays the connection
to the world (devices, sensors, calendars …); AAC is meant to grow into the brain
on top: memory, initiative, agents and modules.

> **Status: early (v0.15.0).** AAC has its own panel, a master agent that talks to
> any OpenAI-compatible model server and can control Home Assistant through the
> Assist API, a memory (text memories + structured facts), triggers (heartbeat,
> state, time), planning for itself, and it helps you set itself up in a chat.
> The audio pipeline, MCP and modules are still being designed.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/Astormakes/Astors-AI-Core`, type **Integration**
2. Install **AAC – Astor's AI Core**, restart Home Assistant
3. Settings → Devices & services → **Add integration** → *AAC* (one click, no questions)
4. Open **AAC** in the sidebar and follow the **SETUP** segment: connect a model, choose the master model.
   From then on **Set up with AAC** lets the master walk you through the rest in a chat

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
found automatically and show up under **SETTINGS → Models**. Others can be added
there by hand, or by telling AAC in a chat.

Tool use (controlling HA) needs a model and server with function calling
(for llama.cpp start it with `--jinja`).

## The panel

- **Domain switcher** under "AAC": several whole setups (e.g. Assistant, Work), each with its own charter, memory, chats and triggers; models and pools are shared
- **MODULES**: Lists (lists of anything, with sub-entries and fields) and Calendar (one agenda over lists, AAC's plans and your HA calendars)
- **SETUP**: the workflow; hides itself when nothing is open (the eye at the top shows all segments)
- **WORKERS**: your chats with the master, "From AAC" (when it speaks up on its own) and the automatic runs (folded, with every step visible)
- **SETTINGS**: charter (your values, first in every prompt), appearance (skins: Neon, Home Assistant, own CSS files), master agent, triggers, pools (which model or engine serves which role, with fallback) and models, AAC's own audio pipeline (separate from HA's Assist), MCP (planned), memory

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
