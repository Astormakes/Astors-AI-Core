# AAC – progress

Read this first. v1 (1.0.0-dev.75) is history: git tag `v1-final`, its progress file `docs/progress-v1.md` (private).
The plan for v2 lives in `docs/v2/` (private): page 1 "What AAC is" (settled), page 2 "The cards" (in progress).

## Current state (2026-10-23) – 2.0.0-alpha.2, live on the owner's HA

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
- **First start** (`defaults.py`): the default domain (Chat → Charter → Time → Model → Chat; Pool "Models"; Lists and
  Home as the model's tools), taking over v1's models, master order, charter, lists and entity access.
- **Panel** (`www/aac.js`): sidebar (domain switch + clone, a section per Chat card with its chats, per Lists card its
  lists, Nodes, Models), chat (live answer, document view, stop), lists page, models page (find a server's models),
  node view (types left with info, click in the grid to place, drag to wire, Delete/button to unwire, settings right
  with functions and tags, Undo, slots 1–3 (Shift-click loads), Send stop, monitor on a wire).
- Tests: 10 (`../.venv/bin/python -m pytest -q -p no:logging`).

## Owner's data

- Lists exported before v2: `docs/aac-lists-2026-10-23.md` and `docs/aac-lists-2026-10-23-v1.json`; they live in the
  default domain's Lists card now (Open Issues 4, Project Portfolio 10).
- v1 storage on the HA is untouched (`/config/.storage/aac.*`), backup `/config/aac/bak-before-v2-20261023/`.
- HA's own Assist pipeline uses `conversation.qwen3_8b` (not AAC): v2 has no conversation agent yet, nothing broke.

## Deploy and test

- `AAC_HA_SSH=root@192.168.1.107 AAC_SSH_KEY=~/.ssh/claude_proxmox scripts/deploy_ha.sh` (bump the manifest version for
  every panel change). Local test HA: `~/cluade/AAC/dev-ha` (port 8124), runs from the repo.
- Screenshots: `scripts/ha_screenshot.py` (FIND_PANEL is the `aac-panel` element; `p.go('nodes')`, `p.go('lists','lists')`).

## Next (from docs/v2 page 2)

- Audio cards (left out on purpose for now): Microphone, Noise gate, Pause cut, Speech to text, Voice chat, Voice out
  with its status output; the satellite add-on still calls v1's `/api/aac/audio` (404 until then).
- In and out: HA Assist (AAC as a conversation agent), Schedule, HA event, Heartbeat. New: Web, Notify, Timer/Delay,
  Builder (a model changes cards and wires), File, Memory, Calendar.
- Live entries through the flow (today the model streams only to the panel), stop as a message along the wires.
- Panel: a proper rules editor for Conditional (JSON for now), the owner's own tags (`aac/tags/add`), monitors on wires
  in the grid, Save/Load as files, presets.
