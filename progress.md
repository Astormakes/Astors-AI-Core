# Progress

Running log for AAC. Read this first in a new session. Newest state on top. The long v0 history is in `docs/history-v0.md` (private).

## Current state (2026-10-23) – 1.0.0-dev.39

- **v1 on `main`**, live on the owner's HA (owner: "I'm not really using it, work on the real HA"; only the lists matter, exported to `docs/aac-lists-2026-10-23.md`). The last v0 is the tag `v0.50.1-last-v0`. `scripts/deploy_ha.sh` needs `AAC_ALLOW_V1=1`.
- **The graph** (`spec/graph.md`, owner's design of 2026-10-23): a message is a document of tagged entries (`request.py`); every node writes into it; a model card (`model_node.py`) reads it all as an ordinary chat with tools; **its wired nodes are its tools** (calling one sends the document there and waits until it comes back: a loop, `flow.Step.call`); round outputs copy to every wire, square ones (model, Decide) choose; endpoints (`chat`, `voice`, `assist`) take answers home; the chat keeps its document (edits change it; expert view in the chat: every entry and the way, e.g. Panel chat › Model › Home › Model).
- Nodes with tools are HA LLM APIs behind `api_node.py` (home, setup, HA's Assist tools, each module's tools); memory is one node (in the line it writes matching memories, called it remembers/recalls); no memory keeper (nothing is stored on its own). Inputs can be named for the models (`aac/nodes/names`): a card's input named `yes` is the tool `yes`.
- Standing text (charter, agent prompt, instructions) replaces its own entry, so the start of the conversation stays the same (prompt cache); the local time goes with the newest user turn, not into the system prompt.
- The panel: the Master agent page is gone (its settings are on the Agent, Model and Home Assistant tools cards); node positions are kept on the server; round/square ports.
- **Node view in slots** (owner, 2026-10-23: no forces): a column per step in the flow (a card feeding another sits one column left of it, one fed by it one column right, a loop card in its model's column stacked below); wires: short forward ones curve, loops in nested side lanes, backward or far ones in lanes below the cards of their own chain; drops snap to the column; Tidy lays everything out again (Undo goes back). The info panel sits right of the graph; Try it is gone; the wheel scrolls, Ctrl + wheel zooms, the middle button drags. While Nodes is open the sidebar lists the cards by role (click: pick and centre; drag into the grid: place) and the types to add.
- Universal nodes: **Buffer** (last N texts as one block; tools read/add/remove_slot/remove_text/clear), **Heard lines** (spoken lines from Hearing become messages; the Voice card speaks the answer at that mic). Outputs can be set to one wire (`aac/nodes/single`). **Pool cards** (`pool_master`, `pool_fast` …): a model card takes its models from the pool wired to its dotted pool input.
- **Chat cards** (type `chat_card`, added like any card): a chat of its own with a title; each is a section in the sidebar with its own + (a chat remembers its `card`; its messages leave there). **Markdown file cards** (`md_file`): `<config>/aac/notes/<name>.md`; in a line they append what passes, wired to a model they are a tool (read/append/write); listed under FILES in the sidebar, readable and editable (`aac/notes/get|save`).
- Memory hangs under the model as a loop by default (owner); wired into the line by hand it also writes matching memories in.
- **Voice through the graph:** Wake word and Companion have an output: a line they decide to answer leaves there, takes the line (charter, agent) into the model, and the answer goes to the Voice card (`reply_to: voice`). Module pages (Lists, Calendar, Memory …) open from their cards; the sidebar has no Modules section any more. Pinch zoom on phones.
- Settings list: Appearance, Languages, Nodes; the other pages open from their cards (Home → Entities, pools → the Pools page (also the gear next to POOLS in the Nodes sidebar; was "Compute"), Hearing/STT → Audio, toolbar → Modules). The chat input grows with long messages.
- Memories were cleared on the owner's HA (2026-10-23, backup `/config/aac/bak-before-clean-20261023/`). Backups before deploys: `/config/aac/bak-1.0.0-dev.28-before/` and older.
- Tests: 154 mocked (`../.venv/bin/python -m pytest -q -p no:logging`).

## Working agreements
- **The user pushes** (GitHub Desktop only). Claude commits locally whenever something is in a runnable state, never pushes
- `docs/` holds the private design and homelab notes and is **gitignored** on purpose (IPs, hardware, personal plans). Never commit it, never copy private details into tracked files
  - `docs/assistant-framework.md` = the architecture/vision (source of truth for design)
  - `docs/design.md` = the panel UI spec (layout, segments, neon design language)
  - **`spec/` (public, tracked)** = the contracts (run, trigger, context recipe, agent template, memory, model provider, event, module, domain) with built/partial/planned status. Keep them in sync with the code; no private details (IPs, hosts) in there
  - `docs/dev-environment.md` = how to reach the real HA, the model servers, where credentials live
  - `docs/projects.md` = personal, not part of this project, ignore it
- Keep docs tidy: design decisions go into `docs/assistant-framework.md`, status goes here, no duplicate notes
- Version lives in `custom_components/aac/manifest.json` (`version`). Bump it for every release and tag it (`v0.1.0` …) so HACS shows the version


## Testing in the real HA
- No GitHub/HACS round trip needed: `scripts/deploy_ha.sh` copies the working tree over SSH and restarts HA Core (`AAC_HA_SSH`, `AAC_SSH_KEY`; real values in `docs/dev-environment.md`)
- Release path for others: bump `version` in the manifest, commit, the user pushes and creates a GitHub release/tag `vX.Y.Z` → HACS offers the update

## Checking the panel in the real HA (any screen size)
- `scripts/ha_screenshot.py out.png <w> <h> [/aac] [js]` with `AAC_HA_URL`, `AAC_HA_TOKEN_FILE` (values: `docs/dev-environment.md`). Headless Firefox via WebDriver BiDi, only aiohttp needed. `FIND_PANEL` in the JS argument reaches the panel element, e.g. `"(()=>{const p=FIND_PANEL;p._go('settings/master');})()"`. Prints viewport vs. scroll size (overflow check)
- Can't simulate a phone's safe areas (notch, gesture bar), so phone issues there need the user's eyes

## Checking the panel without HA
- No node/browser automation here. Harness with a mocked `hass.callWS`: `index.html` + copy of `aac-panel.js` served by `python -m http.server`, screenshots with `firefox --headless --profile <dir> --no-remote --window-size=1400,850 --screenshot out.png <url>` (needs an existing profile dir). JS errors are printed into the page. Kept in the session scratchpad only, rebuild if needed

## Dev environment (this machine)
- venv lives **outside** the repo at `../.venv` (parent folder `~/cluade/AAC/`), Python 3.14, HA 2026.10.2 test harness
  - `../.venv/bin/python -m pytest` / `../.venv/bin/python -m pytest -m live -s`
- Local test model: `llama-server -m ~/models/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf --host 127.0.0.1 --port 8080 -c 32768 -fa on --fit on --jinja` (not a service, start it when needed). For HA to reach it: `--host 0.0.0.0 --api-key <key>` and the ufw rule above; live tests then need `AAC_LIVE_KEY`
- Prompt experiments: talk to both models directly (OpenAI API, ~1 s per call on the 35B) with a fixed quiet/alarm/leak snapshot before changing prompts; that is how the heartbeat prompt was found
- Test harness: `frontend` would pull in half of HA, so tests mark `frontend`/`panel_custom` as loaded and stub the panel registration (`tests/conftest.py`). The asset folder is `www/`, not `frontend/` (that name clashed with the `frontend` import when patching)
- Gotchas: background tasks (event triggers) need `async_block_till_done(wait_background_tasks=True)` in tests; the HA test plugin pins sockets to 127.0.0.1 (live tests reaching other hosts call `pytest_socket.socket_allow_hosts`); tools must return `llm.ToolResult` (dicts deprecated, removed 2027.11); test memory DB is patched per test (HA's test config dir is shared)
- **The real HA now holds the user's own data** (organic use from 2026-10-19): tests delete only what they created (by id), never all chats/memories/triggers. Experiments preferably in a separate test domain
- **Tests on the real HA: only questions, never action sentences** ("turn off the light" made the 8B switch a real socket; it was already off)
- When deploying, run the tests with `set -o pipefail` (a `| tail` hides failures)
- HA tool names are namespaced since 2026.10 (`homeassistant__GetLiveContext`, `llm__GetDateTime`, `intent__HassTurnOn`)

## Known limitations / notes
- `chat_template_kwargs` is always sent. Fine for llama.cpp/vLLM/Ollama; a strict cloud API might reject it → make it optional if that happens
- No streaming yet
- The panel re-renders fully on every change (simple, fine for now); form values are preserved across re-renders of the same view
- The existing entity keeps its old id `conversation.aac_qwen3_8b` in the real HA (from 0.1.0); fresh installs get `conversation.aac`
- License not chosen yet (open decision in the framework doc), so no LICENSE file


## Next steps
- Open design question (owner, 2026-10-23): Hearing has three outputs (audio.pcm, audio.segment, text.line) and hidden VAD/STT/addressing; owner leans to one audio source per satellite, a stream picker node (by volume), audio in the message document so Voices can add the voice id before STT. Decide before Phase 4.

- Bench 2026-10-23 (35B on the PC, Decide's grammar): "addressed to AAC?" 12/12 right in 0.26 s each (English, German, a video, a follow-up that only counts with the Buffer's line before it). Script: session scratchpad `bench/addressed.py`.
- Try the owner's audio chain with the real models: Heard lines → Buffer → Decide ("addressed to AAC?") → yes: an Instructions card → Model → Voice (all cards exist now).
- Sidebar drag into the grid: built, not yet tried in a real browser.
- **Setup** (owner, 2026-10-23): it doesn't fit the graph any more; it gets replaced once the owner has played with a clean domain. Decide then, not before.
- German voice: de_prod3 rejected ("wrong voice"); blind converter comparison `runs/tune_de5` on :8094, then fix the recipe.
- Phase 3: pool nodes; the settings pages fold into nodes (Compute → pools, Entities → the Home node, Audio, Modules); settings left: Appearance, Languages, Nodes.
- Phase 4: the audio types merged into `audio` / `text`.
- Open from before: bench Decide routing with real models; voice picker rounds (`~/cluade/voicelab`, pickers on :8092 German, :8093 English when started); the server's 7800 XT runs on Vulkan for now (owner works on it in another session); the bigger chat input (Open Issues list).
