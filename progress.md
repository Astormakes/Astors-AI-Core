# Progress

Running log for AAC. Read this first in a new session. Newest state on top.

## Working agreements
- **The user pushes** (GitHub Desktop only). Claude commits locally whenever something is in a runnable state, never pushes
- `docs/` holds the private design and homelab notes and is **gitignored** on purpose (IPs, hardware, personal plans). Never commit it, never copy private details into tracked files
  - `docs/assistant-framework.md` = the architecture/vision (source of truth for design)
  - `docs/design.md` = the panel UI spec (layout, segments, neon design language)
  - `docs/dev-environment.md` = how to reach the real HA, the model servers, where credentials live
  - `docs/projects.md` = personal, not part of this project, ignore it
- Keep docs tidy: design decisions go into `docs/assistant-framework.md`, status goes here, no duplicate notes
- Version lives in `custom_components/aac/manifest.json` (`version`). Bump it for every release and tag it (`v0.1.0` …) so HACS shows the version

## Current state (2026-10-18) – v0.2.0
- HACS-installable custom integration `aac` (`custom_components/aac`, `hacs.json`, min HA 2026.10.0), single instance
- **Everything is configured in the AAC panel** (spec: `docs/design.md`). The config flow is one click with no fields; settings live in `.storage/aac.config` (`store.py`), not in the config entry
  - Entry v1 (0.1.0, server/model in the entry) is migrated to v2 automatically (`async_migrate_entry`)
- Panel: `www/aac-panel.js`, plain web component, no build step, no external resources. Served at `/aac_static/`, registered as sidebar panel `aac` (admin only)
  - Segments: SETUP (hides when done, eye shows it again) · CHAT (master) · WORKERS (auto/manual, + button) · SETTINGS (master agent, pipelines flow, MCP + memory as "planned")
- Backend for the panel: `websocket.py` (`aac/state`, `aac/master/update`, `aac/workers`, `aac/worker/save|delete`, `aac/server/models`, `aac/setup/skip`, `aac/pipelines`, `aac/pipeline/route`, `aac/chat`)
- Workers (`workers.py`): manual ones from the store; auto = every model of servers found in HA (llama.cpp, Ollama entries) + servers of manual workers. Ids: `m_xxxxxxxx`, `auto|<source>|<model>`
- Setup steps + pipeline overview/routing: `status.py`. Routing remembers the previous agent and restores it
- Conversation agent = the master: reads model/prompt/tools from the store at every message (no reload needed), OpenAI-compatible chat via `provider.py`, HA tool calling loop through HA's `ChatLog`
- Tests: 16 mocked, 2 live. In the real HA: migration ok, panel registered, chat via panel API answers with real tool calls

## Testing in the real HA
- No GitHub/HACS round trip needed: `scripts/deploy_ha.sh` copies the working tree over SSH and restarts HA Core (`AAC_HA_SSH`, `AAC_SSH_KEY`; real values in `docs/dev-environment.md`)
- Release path for others: bump `version` in the manifest, commit, the user pushes and creates a GitHub release/tag `vX.Y.Z` → HACS offers the update

## Checking the panel without HA
- No node/browser automation here. Harness with a mocked `hass.callWS`: `index.html` + copy of `aac-panel.js` served by `python -m http.server`, screenshots with `firefox --headless --profile <dir> --no-remote --window-size=1400,850 --screenshot out.png <url>` (needs an existing profile dir). JS errors are printed into the page. Kept in the session scratchpad only, rebuild if needed

## Dev environment (this machine)
- venv lives **outside** the repo at `../.venv` (parent folder `~/cluade/AAC/`), Python 3.14, HA 2026.10.2 test harness
  - `../.venv/bin/python -m pytest` / `../.venv/bin/python -m pytest -m live -s`
- Local test model: `llama-server -m ~/models/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf --host 127.0.0.1 --port 8080 -c 32768 -fa on --fit on --jinja` (not a service, start it when needed)
- Test harness: `frontend` would pull in half of HA, so tests mark `frontend`/`panel_custom` as loaded and stub the panel registration (`tests/conftest.py`). The asset folder is `www/`, not `frontend/` (that name clashed with the `frontend` import when patching)
- HA tool names are namespaced since 2026.10 (`homeassistant__GetLiveContext`, `llm__GetDateTime`, `intent__HassTurnOn`)

## Known limitations / notes
- `chat_template_kwargs` is always sent. Fine for llama.cpp/vLLM/Ollama; a strict cloud API might reject it → make it optional if that happens
- No streaming yet
- The panel re-renders fully on every change (simple, fine for now); form values are preserved across re-renders of the same view
- The existing entity keeps its old id `conversation.aac_qwen3_8b` in the real HA (from 0.1.0); fresh installs get `conversation.aac`
- License not chosen yet (open decision in the framework doc), so no LICENSE file

## Next steps
- User: look at the panel in HA, give feedback on look and flow; GitHub release `v0.2.0`, then try installing via HACS once
- Optional: route the GLaDOS pipeline through AAC from the panel (not done, the user's call)
- Contracts first (framework doc, "Rough order" step 0): event format, module manifest, memory interface, agent template, broker request, domain format
- Then core + memory + context engine
