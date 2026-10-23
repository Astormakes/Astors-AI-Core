# AAC – progress

Read this first. v1 (1.0.0-dev.75) is history: git tag `v1-final`, its progress file `docs/progress-v1.md` (private).
The plan for v2 lives in `docs/v2/` (private): page 1 "What AAC is" (settled), page 2 "The cards" (in progress).

## Current state (2026-10-23) – 2.0.0-alpha.4, live on the owner's HA

Rebuilt from scratch on the owner's design (docs/v2): everything is a card instance in a domain, cards own their data,
the owner wires them (no automatic wiring), the message is a document of entries with `<card>.<tag>` tags.

- **Core** (`core.py`): the model pool (shared by all domains, `models.py`: OpenAI-compatible client, streaming, tool
  calls, a model that does not answer is skipped a minute); domains (one runs at a time; clone, switch); each domain
  keeps its cards, wires, positions, Undo (20 steps) and 3 save slots on the server; each card's data in its own store
  (`aac2.card.<domain>.<card>`).
- **Flow** (`flow.py`): round outputs copy, square ones choose; a card's tools are the cards wired out of it and back
  in (functions called directly, call + result written into the message); the newest message per output is kept for
  the monitor; `flow` events pulse the wires.
- **Cards** (`cards/`): Chat, Model, Pool, Relay (nothing / start / note / after, replace tick, add text to tagged
  entries), Conditional (if … then: edit text, tag, call a wired card's function, stop, pick an output), Buffer, Lists
  (with edit and undo of a removal), Home Assistant (own access list: hidden / read / full).
- **More cards (alpha.3):** Schedule, HA event, Heartbeat (start a chain, keep a log page; Schedule entries can be
  added by a model), HA Assist (AAC is HA's conversation agent again, entity `conversation.aac_qwen3_8b`), Decide,
  Memory (by meaning with an embedding model, else by words), File, Notify, Timer, Web (DuckDuckGo or SearXNG),
  Builder (a model changes cards and wires, within its settings).
- **Audio (alpha.3, `audio.py` + `cards/audio.py`):** satellites on `/api/aac/audio` (same protocol: the add-on works
  unchanged, its HA user taken over from v1), STT in the order on the Audio page (Whisper on the PC, then HA's),
  TTS (Piper, voices per language), volume; cards Microphone, Noise gate, Pause cut, Speech to text, Voice chat
  (sessions per satellite, follow-up window after Voice out's "finished"), Voice out. Audio wires carry a stream
  (thickness = loudness), Pause cut makes pieces (`piece` wires).
- **Calendar (alpha.4, `cards/calendar.py` + `calendar.py`):** its own entries plus the open entries of Lists cards
  with a field when/date/due; HA shows them as `calendar.aac_calendar` (v1's unique id kept; create/change/delete from
  HA work); functions calendar/add/change/remove; one-time: a Calendar card as the default model's tool.
- **One-time steps on the owner's HA:** v1's 05:25 trigger → Schedule card; v1's voice settings; the voice chain
  (Wake word = a Conditional: "computer" or a follow-up; Voice rules = a Relay after the turn); an HA Assist card.
- **First start** (`defaults.py`): the default domain (Chat → Charter → Time → Model → Chat; Pool "Models"; Lists and
  Home as the model's tools), taking over v1's models, master order, charter, lists and entity access.
- **Panel** (`www/aac.js`): sidebar (domain switch + clone, a section per Chat card with its chats, per Lists card its
  lists, Nodes, Models), chat (live answer, document view, stop), lists page, models page (find a server's models),
  node view (types left with info, click in the grid to place, drag to wire, Delete/button to unwire, settings right
  with functions and tags, Undo, slots 1–3 (Shift-click loads), Send stop, monitor on a wire).
- Tests: 20 (`../.venv/bin/python -m pytest -q -p no:logging`).

## Owner's data

- Lists exported before v2: `docs/aac-lists-2026-10-23.md` and `docs/aac-lists-2026-10-23-v1.json`; they live in the
  default domain's Lists card now (Open Issues 4, Project Portfolio 10).
- v1 storage on the HA is untouched (`/config/.storage/aac.*`), backup `/config/aac/bak-before-v2-20261023/`.
- HA's own Assist pipeline uses `conversation.qwen3_8b` (not AAC). Backups of v2 storage: `/config/aac/bak-v2-alpha2/`,
  `/config/aac/bak-v2-alpha3/`.

## Deploy and test

- `AAC_HA_SSH=root@192.168.1.107 AAC_SSH_KEY=~/.ssh/claude_proxmox scripts/deploy_ha.sh` (bump the manifest version for
  every panel change). Local test HA: `~/cluade/AAC/dev-ha` (port 8124), runs from the repo.
- Screenshots: `scripts/ha_screenshot.py` (FIND_PANEL is the `aac-panel` element; `p.go('nodes')`, `p.go('lists','lists')`).

## Next

- "What the model got" view (the request with
  each part's source); card values in templates (`{{ <card>.<value> }}`); monitor windows on wires in the grid;
  Save/Load as files; a proper rules editor for Conditional (JSON now); the owner's own tags in the panel.
- Live entries through the flow (today the model streams only to the panel), stop as a message along the wires.
- Presets; docs/v2 page 2 details for the remaining cards.
