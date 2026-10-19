# Domain package — planned

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

Today there is exactly one implicit domain (the AAC config entry).
