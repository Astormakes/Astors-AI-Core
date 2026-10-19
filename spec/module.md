# Module manifest — planned

A module is a bundle of the other contracts with one manifest. Everything is a
module, even the defaults; the core is small. Installable from a GitHub repo,
shareable with a friend, and creatable by AAC itself at runtime.

```json
{
  "id": "shopping",
  "name": "Shopping",
  "version": "0.1.0",
  "contract": 1,
  "kind": "declarative",
  "description": "Keeps the shopping list, knows what was good.",
  "provides": {
    "templates": ["templates/plan-shopping.json"],
    "triggers": ["triggers/weekly.json"],
    "recipes": ["recipes/shopping.json"],
    "memory_kinds": [{"kind": "product_rating", "fields": ["product", "rating", "store"]}],
    "panel_cards": ["cards/list.json"]
  },
  "uses": {
    "ha": ["todo.*"],
    "tools": ["assist", "aac_memory"]
  },
  "permissions": {"internet": false, "privacy_max": "local-only"}
}
```

## Two kinds

| kind | Contains | Risk | May AAC activate it alone? |
|---|---|---|---|
| `declarative` | only data: templates, triggers, recipes, memory kinds, panel cards, wiring of existing tools | low | owner setting (e.g. "yes, tell me afterwards") |
| `code` | its own code (new tools, pipeline stages, drivers) | high | never without the owner; runs isolated, only what `permissions` declares |

## Lifecycle

```
proposed ─► sandbox (test run, no real actions) ─► active ─► disabled ─► removed
            every change = a new version, rollback possible
```

Loading, unloading and updating happen at runtime without restarting the core.

## Distribution

- Default modules live in the core repo under `modules/`, each fully self-contained
  (as if external). Split into own repos once stable.
- A registry (a file in a repo, like HACS's default list) lists known module repos.
- A module states the `contract` version it needs; the core refuses newer ones it
  does not understand.
