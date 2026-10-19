# Domain package — partial

A domain is a self-contained, portable package of a whole AAC setup: purpose,
charter, master configuration, active modules, triggers, memory, budget and
privacy settings. Examples: "my companion", "a friend's wiki builder".

```
my-companion.aacdomain   (a zip)
├── domain.json
├── charter.md
├── modules/            installed modules (manifests + their files)
└── memory.jsonl        optional: memory items (export only with the owner's OK)
```

```json
{
  "id": "companion",
  "name": "My companion",
  "contract": 1,
  "version": 7,
  "master": {"model": {"tier": "master"}, "prompt": "…", "tools": ["assist", "aac_memory"]},
  "modules": [{"id": "shopping", "version": "0.1.0"}],
  "triggers": ["…trigger objects…"],
  "budget": {"priority": 1, "max_parallel_agents": 3},
  "privacy": {"max": "home-network"},
  "owns_voice": true,
  "shares_memory_with": []
}
```

Rules:
- **Imported domains are untrusted**: they see none of the owner's memory, their
  code modules run in the sandbox, and the owner sets their permissions on import.
- Only **one domain owns the voice** at a time; others report through it or the panel.
- Memory is per domain; sharing is explicit (`shares_memory_with`).
- Domains are versioned (`version` + history): snapshot, compare, restore.
- Never in a package: API keys, tokens, HA URLs. Those are filled in on import.
- **Pools stand above domains** (see [model-provider.md](model-provider.md)): machines and models
  belong to the home, not to a domain. A domain names the capabilities it needs (`{"tier": "master"}`,
  "speech to text, multilingual"), never a machine; on import the broker maps them to the local pools.

## Built (v0.15.0): domains inside one AAC

- Config: `domains` = list of `{id, name, master, heartbeat, triggers, autonomy, charter, memory}`;
  `voice_domain` = the one the microphones talk to. Shared (above domains): models, pools,
  traits, audio, skins, setup. Settings from before 0.15 become the first domain "Assistant".
- Per domain at runtime: chats (`aac.chats[.<id>]`), runs (`aac.runs[.<id>]`), memory file
  (`memory[_<id>].db`), trigger engine, a conversation agent (`conversation.aac_<name>`; the first
  domain keeps its entity), and its own LLM APIs (`aac_memory[_<id>]`, `aac_schedule[_<id>]`,
  `aac_setup[_<id>]`), so an agent only sees its own memory and plans.
- Every `aac/*` WebSocket command takes an optional `domain` (default: the first).
  `aac/domain/create` (name, optional `copy_from`: settings only, never memory or chats; agent plans
  are not copied), `aac/domain/rename`, `aac/domain/delete` (not the first; removes its memory,
  chats and runs), `aac/domain/voice`. Create/rename/delete reload AAC.
- Panel: the domain switcher under "AAC" at the top of the sidebar.

Planned: export/import as a package (above), memory sharing between domains, per-module setup steps
per domain (a module a domain uses but that is not set up brings SETUP back for that domain).
