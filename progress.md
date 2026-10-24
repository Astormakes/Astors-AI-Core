# AAC – progress

Read this first. v1 (1.0.0-dev.75) is history: git tag `v1-final`, its progress file `docs/progress-v1.md` (private).
The plan for v2 lives in `docs/v2/` (private): page 1 "What AAC is" (settled), page 2 "The cards" (in progress).

## Current state (2026-10-23) – 2.0.0-alpha.18, live on the owner's HA

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
- **Request view (alpha.5):** a chat's Request button shows what the model got for the newest turn: each message with
  the cards its text came from, and the tools it had (`aac/chat/request`; kept in memory, 30 chats per Model card).
- **Grid like v1 (alpha.6):** sidebar, card list and settings resize by dragging their edge (double-click resets;
  kept per browser); cards snap into columns (a column per step); Arrange puts them in v1's slots (tools stacked
  under their card, separate chains below each other; undoable, only positions change); wires use v1's routes
  (curves forward, side lanes for tool loops, lanes below the cards for replies and far wires). alpha.12: a card
  wired only to cards above it in its column (a tool under its model) is mirrored (in right, out left; v1).
- **Rules editor (alpha.7):** Conditional rules are edited as forms (IF which entries + test, THEN actions with the
  fields each needs: card and function pickers for calls, output picker; reorder, remove); JSON stays under "As JSON".
- **Monitor windows (alpha.9):** a wire's Monitor button pins a window on the wire (live text and count, flashes);
  a click shows the newest message (entries, way) and what passed in the last 10 minutes (`flow.records`).
- **Card values in templates (alpha.10):** cards declare values (Lists: lists/open/total, Calendar: today/next,
  Buffer: text/lines/count, Memory: count); a Relay's template reads `{{ lists.total }}` or `{{ cards['id'].x }}`;
  the settings panel lists each card's values.
- **Setup files and own tags (alpha.11):** Save file / Load file in the node view (cards, settings, wires, positions;
  the cards' own data stays; loading is undoable); with no card selected the side panel lists the domain's tags and
  adds/removes the owner's own (`user.…`).
- **Stop as a context (alpha.15):** a stop is a context whose newest entry is a stop signal (`<card>.stop`); it runs
  forward on the wires; a busy card (a model writing) cuts its block, appends the stop (marked done: visible history)
  and sends its own context on, the stop ends there; idle cards pass it on. The chat's Stop button sends the chat's
  context with a stop; the next turn shows the model "[stopped]". 
- **Live wires (alpha.15):** a push lights the wires it took (brighter, thicker, fading over 4 s) with a -_-_ wave
  running along; audio streams keep the wave running while sound flows.
- **Contexts and wires (alpha.16, owner 2026-10-24):** solid wire = push (a context arrives, the card runs), dotted
  wire = read (looked at when the card runs, never starts anything). Chat holds a context per chat: typing adds the
  line and sends the whole context; its input replaces what it holds; what it shows is read from that context. A
  Relay with a dotted wire into `reads` starts from what it reads (a Chat's current conversation; new after the quiet
  time) and puts the arriving lines on top. Core: when AAC last spoke (`{{ audio.last… }}`, per satellite
  `{{ audio.satellites[id]… }}`); Conditional has a template test.
- **Voice chain (alpha.16):** Speech to text → three Conditionals side by side: Stop word (sends stop forward), Wake
  word ("computer"/"assistent" → Spoken turn), Follow-up (AAC spoke there < 10 s ago → Follow-up turn: a note "maybe not
  meant for you, then answer nothing"); both Relays read the Chat dotted and go to the Charter; Model → Answer (Relay) →
  Chat and Voice out. The Voice chat card and Voice out's status port are gone; voice chats moved into the Chat card.
  Ports have descriptions (folded "Ports" section); rules, functions, values, tags fold.
- **Satellites are HA media players (alpha.17):** `media_player.aac_satellite_<name>` per known satellite (play any
  media / TTS, volume = AAC's volume, stop, browse); Voice out speaks at the line's satellite or on any media player
  (HA's tts.speak) and a stop cuts it off (the add-on 0.1.2 understands `{"type": "stop"}`; protocol in
  spec/audio-source.md). Live test: TTS on the satellite, HA's stop cut it.
- **Words (alpha.17):** what runs through the wires is a **context** (`context.py`, class `Context`), in code, panel
  and docs/v2.
- **No dead weight (alpha.18, owner rule):** the v1 import and every one-time upgrade step are gone (they ran on the
  owner's HA; a fresh install builds the whole default domain: Chat, HA Assist, Charter, Time, Pool, Model, Lists, Home,
  Calendar; the voice chain once satellites are known). An empty Pool means every model. Offline satellites can be
  forgotten on the Audio page (their media player goes too). Removed on the HA: the test satellite, conversation.aac_test,
  calendar.aac_test_calendar, v1's AAC device.
- **One-time steps that ran on the owner's HA (code removed):** v1's 05:25 trigger → Schedule card; v1's voice settings; the voice chain
  (Wake word = a Conditional: "computer" or a follow-up; Voice rules = a Relay after the turn); an HA Assist card.
- **First start** (`defaults.py`): the default domain (Chat → Charter → Time → Model → Chat; Pool "Models"; Lists and
  Home as the model's tools), taking over v1's models, master order, charter, lists and entity access.
- **Panel** (`www/aac.js`): sidebar (domain switch + clone, a section per Chat card with its chats, per Lists card its
  lists, Nodes, Models), chat (live answer, document view, stop), lists page, models page (find a server's models),
  node view (types left with info, click in the grid to place, drag to wire, Delete/button to unwire, settings right
  with functions and tags, Undo, slots 1–3 (Shift-click loads), Send stop, monitor on a wire).
- Tests: 25 (`../.venv/bin/python -m pytest -q -p no:logging`).

## Owner's data

- Lists exported before v2: `docs/aac-lists-2026-10-23.md` and `docs/aac-lists-2026-10-23-v1.json`; they live in the
  default domain's Lists card now (Open Issues 4, Project Portfolio 10).
- v1 storage on the HA is untouched (`/config/.storage/aac.*`), backup `/config/aac/bak-before-v2-20261023/`.
- HA's own Assist pipeline uses `conversation.qwen3_8b` (not AAC). Backups of v2 storage: `/config/aac/bak-v2-alpha2/`,
  `/config/aac/bak-v2-alpha3/`, `/config/aac/bak-v2-alpha15/` (before the voice rebuild), `/config/aac/bak-v2-alpha16/`; add-on 0.1.1 files in
  `/config/aac/bak-addon-0.1.1`.

## Cleanup waiting for the owner (2026-10-24)

The permission check blocked deleting these on the HA; run it in the HA terminal (or allow it). Everything is v1 or
superseded; `/config/aac/bak-v2-alpha17/` (v2 storage before alpha.18) stays as the one backup, `satellite.json` stays
(the add-on reads it).

- `/config/.storage/aac.*`: v1's storage (chats, config + its 4 old backups, jobs, lists, triggers, voices, requests,
  runs, the `.test` copies); v2 no longer reads it.
- `/config/.storage/aac2.card.assistant.voice`: the removed Voice chat card's data (its chats are in the Chat card).
- `/config/aac/bak-*` except `bak-v2-alpha17`: 18 old backups (v1 0.46 … 1.0.0-dev.69, before-clean, before-v2,
  v2 alpha2/3/15/16, add-on 0.1.1).
- `/config/aac/aac.config.bak-1.0.0-dev.19`, `/config/aac/memory*.db*` (v1's memory), `/config/aac/skins` (v1's skins).

```sh
cd /config/aac && for d in bak-*; do [ "$d" = bak-v2-alpha17 ] || rm -rf "$d"; done
rm -rf /config/aac/aac.config.bak-1.0.0-dev.19 /config/aac/memory.db* /config/aac/memory_test.db* /config/aac/skins
rm -f /config/.storage/aac.* /config/.storage/aac2.card.assistant.voice
```

## Deploy and test

- `AAC_HA_SSH=root@192.168.1.107 AAC_SSH_KEY=~/.ssh/claude_proxmox scripts/deploy_ha.sh` (bump the manifest version for
  every panel change). Local test HA: `~/cluade/AAC/dev-ha` (port 8124), runs from the repo.
- Screenshots: `scripts/ha_screenshot.py` (FIND_PANEL is the `aac-panel` element; `p.go('nodes')`, `p.go('lists','lists')`).

## Next

- Live entries through the flow (today the model streams only to the panel), stop as a message along the wires.
- Presets; docs/v2 page 2 details for the remaining cards.
