# AAC contracts

> **v2 rebuild (2026-10-23).** AAC is being rebuilt from scratch (2.0.0-alpha). These contracts describe v1 and are
> history until v2's are written; v1 is git tag `v1-final`.


The interfaces everything else in AAC plugs into. If these stay stable, any
part behind them (models, memory store, triggers, modules) can be swapped.

**Contract version: 1** (draft). Every manifest and exported package names the
contract version it was written for.

Status per contract: **built** = the current code follows it, **partial** =
some of it exists, **planned** = spec only, nothing in the code yet.

| Contract | What it describes | Status |
|---|---|---|
| [Run](run.md) | one piece of agent work and its record (audit log) | built |
| [Trigger](trigger.md) | what wakes an agent (heartbeat, HA state change, …) | partial |
| [Context recipe](context-recipe.md) | what an agent gets to see before it starts | partial |
| [Agent template](agent-template.md) | a reusable kind of agent: prompt, tools, model, output | partial |
| [Memory](memory.md) | text memories + structured facts, search, write path | built (v1) |
| [Model provider](model-provider.md) | talking to models; the pool; broker requests | partial |
| [Audio source](audio-source.md) | a microphone streaming to AAC and AAC's spoken answers | partial |
| [Event](event.md) | everything that happens, on one bus | partial |
| [Module manifest](module.md) | a bundle of the above, installable and shareable | partial |
| [Requests](requests.md) | AAC asks, the owner decides outside the chat; entity access as the first kind | built |
| [Graph](graph.md) | everything is a card; a message is a document; live wires, templates and values, audio cards | built (v1) |
| [Streams](streams.md) | the earlier typed-streams design (superseded by Graph; slots still apply) | superseded |
| [Hearing](hearing.md) | the earlier audio pipeline (superseded by Graph "Audio cards") | superseded |
| [v1 plan](v1.md) | the v1 rewrite: core, node types and instances, phases | draft |
| [Calendar](calendar.md) | one calendar over all modules: an interface, no table | built |
| [Domain package](domain.md) | a whole AAC setup, exportable and importable | partial |

## Rules that hold for every contract

- **Agents organize tasks, not rules.** Nothing in these formats lets an agent
  change policy (what is allowed, privacy, who the owner is). Policy is set by
  the owner only.
- **Plain JSON.** Every object here serializes to JSON; times are ISO 8601 UTC.
- **IDs are opaque strings.** Never parse them (the one exception documented in
  [model-provider.md](model-provider.md) is internal to the core).
- **Unknown fields are kept, not rejected,** so a newer module can be loaded
  by an older core without losing data (it just ignores what it does not know).
- **Privacy tier** (where it appears): `local-only` · `home-network` · `cloud-ok`.
