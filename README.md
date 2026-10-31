# AAC – Astor's AI Core

An AI core for Home Assistant, built from cards you wire together. Home Assistant stays the connection to the world
(devices, sensors, calendars …); AAC is the brain on top: chats, voice, memory, lists, and whatever you wire in.

> **Status: v2 alpha.** AAC was rebuilt from scratch; v1 is in git under the tag `v1-final`.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/Astormakes/Astors-AI-Core`, type **Integration**
2. Install **AAC – Astor's AI Core**, restart Home Assistant
3. Settings → Devices & services → **Add integration** → *AAC* (one click, no questions)
4. Open **AAC** in the sidebar, add a model server on the **Models** page, and start chatting

Everything is configured in the AAC panel. AAC never changes Home Assistant's own voice assistants or AI settings.
It can be picked as the conversation agent in an Assist pipeline (`conversation.aac`); those conversations show up
as chats.

Manual install: copy `custom_components/aac` into your HA `config/custom_components/` and restart.

## Cards and wires

The **Cards** page is a grid of cards: a Chat, a Model, a Relay (a small Python script), Lists, Memory, a Calendar, Home
Assistant, the audio cards and more. A solid wire pushes a context (a document of entries) from one card to the next;
a dotted wire with triangles lets a card read what another one provides. Every card is a Python file in
`<config>/aac/cards/`, AAC's own ones included, so any card can be replaced or added: see
[`spec/cards.md`](spec/cards.md).

## Model servers

Anything that speaks the OpenAI chat completions API: llama.cpp (`llama-server --jinja`, e.g.
`http://192.168.1.20:8080/v1`), Ollama (`…:11434/v1`), vLLM, LM Studio, cloud APIs. Each model gets tags for what it
can do (text, tools, embeddings, speech to text, …), and each card picks its list of models, with queues.

## Microphones and speakers

AAC listens through its own satellite program, and each satellite is also a Home Assistant media player:
- **On a PC:** `addons/aac_satellite/aac_satellite.py` (see its README).
- **On the Home Assistant machine** (USB, or Bluetooth through a dongle): add this repository as an app repository
  in Home Assistant and install **AAC satellite**. Device quirks (e.g. Poly BT600 + Calisto) are handled by plugins.

## Contracts

The interfaces AAC is built around are in [`spec/`](spec/README.md).

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest                # mocked tests
```
