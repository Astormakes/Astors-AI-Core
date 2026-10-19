# Progress

Running log for AAC. Read this first in a new session. Newest state on top.

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

## Current state (2026-10-19) – v0.9.0
- HACS-installable custom integration `aac` (`custom_components/aac`, `hacs.json`, min HA 2026.10.0), single instance
- **Everything is configured in the AAC panel** (spec: `docs/design.md`). Config flow is one click; settings in `.storage/aac.config` (`store.py`), chats in `.storage/aac.chats` (`chats.py`), runs in `.storage/aac.runs` (`runs.py`, last 100). Entry v1 (0.1.0) migrates automatically
- **HA's own AI is never changed** (user decision): no editing of Assist pipelines; HA AI only as optional tools. Other integrations are only read
- Panel `www/aac-panel.js` (plain web component, no build, offline, responsive phone → 4K TV, keyboard/remote focus, safe areas). Segments: SETUP · WORKERS (chats, From AAC, folded Auto (n) runs) · SETTINGS (master agent, triggers, models, audio pipeline + sources, MCP/memory planned). Live updates via `aac/subscribe`
- **Master runs** (`master.py`): every master call (chat, heartbeat, event) is a recorded run with context, steps (model text, tool calls, results) and result. `current_run` contextvar lets the conversation entity record steps
- **Triggers** (`triggers.py`, contract format `when`/`do` since 0.6.0, old flat ones migrate): heartbeat (interval, quiet hours, off by default, optional setup step), `state` (entity, optional target state, cooldown), `time` (at, weekdays; reminders: the agent is told to write the message). Per-trigger quiet hours. Speaking up lands in the "From AAC" chat; silence is the normal case
- **Context recipe** (`context.py`): compact snapshot of exposed entities, binary sensors translated (DETECTED/OPEN/WET…)
- **Charter**: the owner's values, first in every agent's system prompt (stable → cached), editable only in SETTINGS → Charter (`aac/charter`); "" = built-in draft (`DEFAULT_CHARTER` in `store.py`)
- **Silence**: every automatic run ends in a grammar-enforced JSON decision `{"speak", "message"}` (temperature ≤ 0.3): directly when it has no tools, or as one extra call after its tool rounds. `is_silent()` paraphrase check only as fallback. Forced last round without tools. Limits: 10 tool rounds (chat), 4 (auto). Lessons written into the framework doc
- Models (`models.py`, WS `aac/models`, `aac/model/save|delete`; ids `m_xxxx` manual, `auto|<source>|<model>` auto, source `w:<id>` = a manual model's server): manual ones in the store (`models` key; 0.4.0 stored them as `workers` and the master's as `worker`, migrated on load), auto = models of servers in HA's llama.cpp/Ollama entries + manual servers. Naming rule: **workers = chats/agents, models = brains**
- **Memory** (`memory.py`, `memory_api.py`): SQLite file `<config>/aac/memory.db` (in HA backups). Text + kind + source (`user said` / `agent believes` = unconfirmed / `sensor`), FTS5 keywords (filler words dropped) + embeddings (blob, numpy or pure Python cosine), reciprocal rank fusion. Embedding model = any model from Models (`/v1/embeddings`), chosen on the Memory page; changing it re-embeds in the background. Without one: keywords only
  - Master always gets the LLM API `aac_memory` (tools `remember`/`recall`/`forget`; with several APIs HA prefixes them, e.g. `aac-memory__recall`, `assist__homeassistant__GetLiveContext`)
  - **Context engine, first piece**: before every answer the best memories (vector sim ≥ 0.5) are attached to the *user* message (not the system prompt → prompt cache stays). Needed because the 8B never calls recall on its own
  - **Structured facts** (table `facts` in the same DB): subject/attribute/value/unit/time, linked to a text memory; series summaries computed by code; tool `lookup_facts`; summaries attached to the context
  - **Write path** (`extractor.py`): after every owner message a background call stores lasting things (with fact fields), skipping duplicates (same numbers required for a vector match). Mocked out in tests unless `@pytest.mark.extractor`
  - Panel Memory page: settings, stats, facts card (series, click for history), remember form, search, unconfirmed filter, confirm/edit/delete
- **Goals are not a module** (user decision 2026-10-19: behavior should come out of general rules, no purpose-built functions). They emerge from: memory kind `plan` (shown to automatic runs as open loops), structured facts, and **AAC planning for itself** (`schedule_api.py`: schedule/list/cancel, autonomy off/ask/allowed, agents can't touch the owner's triggers, every plan posted to From AAC, one-off triggers with `date`)
- **AAC helps set itself up** (`setup_api.py`, LLM API `aac_setup`, v0.9.0; user decision: once a model is wired in, AAC aids in setting up everything else together with the user): setup_status, list_models, add_model, use_model (master/memory), set_heartbeat, skip_setup_step. Offered only when an admin is in a chat run (`owner_present`), checked again inside each tool; never automatic runs or anonymous voice; charter/autonomy not offered. Panel SETUP shows "Set up with AAC" once the master is chosen. 8B on the real HA: status → explains → asks → sets the heartbeat
- Audio pipeline (`audio.py`): AAC's own, planned; panel shows stages + candidate sources
- WebSocket API (`websocket.py`): state, master, workers (models), chat(s), runs, heartbeat, triggers, audio, subscribe
- Tests: 56 mocked, 7 live (incl. the weight-goal story without goal code); both models pass the live suite (`-m live`: answer, tool call, heartbeat ×2, smoke alarm must speak, memory round trip; `AAC_LIVE_EMBED_URL/KEY` for embeddings). Both models (8B server, 35B PC) pass the live suite. Real HA: chat with tool calls works, heartbeat on the 8B silent in ~11 s

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
- When deploying, run the tests with `set -o pipefail` (a `| tail` hides failures)
- HA tool names are namespaced since 2026.10 (`homeassistant__GetLiveContext`, `llm__GetDateTime`, `intent__HassTurnOn`)

## Known limitations / notes
- `chat_template_kwargs` is always sent. Fine for llama.cpp/vLLM/Ollama; a strict cloud API might reject it → make it optional if that happens
- No streaming yet
- The panel re-renders fully on every change (simple, fine for now); form values are preserved across re-renders of the same view
- The existing entity keeps its old id `conversation.aac_qwen3_8b` in the real HA (from 0.1.0); fresh installs get `conversation.aac`
- License not chosen yet (open decision in the framework doc), so no LICENSE file

## Next steps
- **PC big model = tests only** (user decision 2026-10-19). Start it by hand when testing, stop it afterwards; AAC's master stays on the 8B. Command and key file in `docs/dev-environment.md`
- **Charter**: the draft stays for now (user decision 2026-10-19); the user changes it when something feels off
- User: check the phone again (safe-area fix); try the heartbeat for a day; GitHub release
- **Own voices** (plan in `docs/plans.md`): GLaDOS EN/DE + Portal announcer via game lines → imitation model → owner hand-picks → retrain → dataset → Piper. Waiting for Portal 2 to be installed in Steam on the PC. Never in the public repo
- **First audio source**: a Plantronics USB/BT headset, being made to work on Linux in another session. Add audio setup tools to `aac_setup` with it
- Audio hub: first real source (probably the browser mic or a Wyoming/ESPHome satellite streaming continuously), ring buffer + VAD
- Contracts first (framework "Rough order" step 0) before memory/context engine
- Contracts written (`spec/`, 2026-10-19); triggers follow the contract now. Next in that direction: templates as data, then declarative modules
- Memory next: timeline/entities stores, curator (nightly merge/dedupe, e.g. duplicate plans from tool + extractor). **ai_memory stays separate** (user decision 2026-10-19: no import, don't touch it)
- Known model limits: the 8B reads "weekday mornings" as Mon/Wed/Fri sometimes; it rarely calls remember itself (the extractor covers it)
