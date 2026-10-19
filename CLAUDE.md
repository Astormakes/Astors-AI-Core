# CLAUDE.md

- Start every session by reading `progress.md` (state, agreements, next steps) and, for design questions, `docs/assistant-framework.md`
- `docs/` is private and gitignored; `docs/projects.md` is personal, ignore it
- `spec/` is public: the contracts. Update the matching spec file when an interface changes
- Commit when something is runnable; never push (the user pushes with GitHub Desktop)
- Update `progress.md` at the end of a work step
- Tests: `../.venv/bin/python -m pytest` (mocked), `-m live -s` against a real model server
- Deploy to the real HA for testing: `scripts/deploy_ha.sh` (values in `docs/dev-environment.md`)
