# Progress

Running log for AAC. Read this first in a new session. Newest state on top.

## Working agreements
- **The user pushes** (GitHub Desktop only). Claude commits locally whenever something is in a runnable state, never pushes
- `docs/` holds the private design and homelab notes and is **gitignored** on purpose (IPs, hardware, personal plans). Never commit it, never copy private details into tracked files
  - `docs/assistant-framework.md` = the architecture/vision (source of truth for design)
  - `docs/dev-environment.md` = how to reach the real HA, the model servers, where credentials live
  - `docs/projects.md` = personal, not part of this project, ignore it
- Keep docs tidy: design decisions go into `docs/assistant-framework.md`, status goes here, no duplicate notes
- Version lives in `custom_components/aac/manifest.json` (`version`). Bump it for every release and tag it (`v0.1.0` …) so HACS shows the version

## Current state (2026-10-18) – v0.1.0
- HACS-installable custom integration `aac` (`custom_components/aac`, `hacs.json`, min HA 2026.10.0)
- Config flow: model server URL (+ optional API key) → pick a model from `/v1/models`
- Options: prompt, LLM APIs (Assist control), temperature, max tokens, thinking on/off
- Conversation agent: OpenAI-compatible chat via `provider.py` (aiohttp, no `openai` dependency), HA tool calling loop through HA's `ChatLog`
- Tests: 8 mocked (`pytest`), 2 live (`pytest -m live -s`, simple answer + real tool call). All pass against Qwen3.6-35B-A3B on the main PC
- **Runs in the real HA** (deployed by SSH copy, config entry pointing at the server's llama-server). Answers + Assist tool calls work. Details: `docs/dev-environment.md`

## Testing in the real HA
- No GitHub/HACS round trip needed: `scripts/deploy_ha.sh` copies the working tree over SSH and restarts HA Core (`AAC_HA_SSH`, `AAC_SSH_KEY`; real values in `docs/dev-environment.md`)
- Release path for others: bump `version` in the manifest, commit, the user pushes and creates a GitHub release/tag `vX.Y.Z` → HACS offers the update

## Dev environment (this machine)
- venv lives **outside** the repo at `../.venv` (parent folder `~/cluade/AAC/`), Python 3.14, HA 2026.10.2 test harness
  - `../.venv/bin/python -m pytest` / `../.venv/bin/python -m pytest -m live -s`
- Local test model: `llama-server -m ~/models/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf --host 127.0.0.1 --port 8080 -c 32768 -fa on --fit on --jinja` (not a service, start it when needed)
- HA tool names are namespaced since 2026.10 (`homeassistant__GetLiveContext`, `llm__GetDateTime`, `intent__HassTurnOn`)

## Known limitations / notes
- `chat_template_kwargs` is always sent. Fine for llama.cpp/vLLM/Ollama; a strict cloud API might reject it → make it optional if that happens
- No streaming yet
- License not chosen yet (open decision in the framework doc), so no LICENSE file

## Next steps
- First GitHub release `v0.1.0` (user), then try installing via HACS custom repository once
- Contracts first (framework doc, "Rough order" step 0): event format, module manifest, memory interface, agent template, broker request, domain format
- Then core + memory + context engine
