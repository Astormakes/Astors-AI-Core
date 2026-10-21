# Module manifest — partial

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

## Setup steps (planned; the core's own steps already follow this shape)

A module that needs something from the owner declares it, instead of hiding it in code.
The steps show up in SETUP **per domain**: a domain that uses a module that is not set up
yet makes SETUP appear again there, until the steps are done or (optional ones) skipped.

```json
"settings": {
  "list": {"type": "entity", "domain": "todo", "title": "Shopping list"},
  "stores": {"type": "text", "title": "Stores you shop at", "default": ""}
},
"setup": [
  {"id": "list", "title": "Choose the shopping list", "optional": false,
   "hint": "Which HA to-do list AAC keeps your shopping in.",
   "done_when": {"setting": "list"},
   "how": ["panel", "chat"]},
  {"id": "stores", "title": "Tell AAC where you shop", "optional": true,
   "hint": "Lets it sort the list by store.",
   "done_when": {"setting": "stores"},
   "how": ["chat"]}
]
```

| Field | Meaning |
|---|---|
| `settings` | the module's own settings, **per domain**, with a type (`entity`, `text`, `number`, `bool`, `model_role`, `time`); the panel builds the form from this |
| `setup[].done_when` | when the step is done, checked by the core: `{"setting": key}` (set and not empty), `{"entity_exists": id}`, `{"role": "stt"}` (a pool role has an entry), `{"rule": true}` (an availability rule exists) |
| `setup[].how` | `panel` = a form from `settings`; `chat` = AAC asks and fills it in with the generic setup tool `module_setting(module, key, value)` (owner present only, same rules as the other setup tools) |
| `optional` | optional steps can be skipped per domain |

The core is a module like any other: its steps (connect a model, master, memory, ears and voice,
rules for the machines, heartbeat) are declared the same way, so SETUP has one code path.

## Built (v0.18.0): built-in modules

`custom_components/aac/modules/<id>/` with `manifest.json` and `async_setup_module(ctx)`. The context
gives: `storage(domain, name)` (per AAC domain), `add_calendar(provider)`, `add_llm_api(factory)` (one
LLM API per domain, offered to that domain's agent), `add_ws_commands(...)`, `add_node(node)` / `emit(domain, node, port, item)` / `enabled(domain)` (the [stream bus](streams.md),
0.44), `add_panel_page(id, title,
element, script, visible_setting)` (a web component without shadow DOM, so the panel's styles and skins
apply; the panel keeps the element across re-renders), `setting(domain, key)`, `languages` (the home's languages, ISO codes, first = main). Settings from the manifest
are stored per domain (`store.module_settings`) and edited in SETTINGS → Modules (form built from the
schema: bool, text, longtext, number, time, select, entity, entities; `placeholder`; `options_from` = choices a module or
the core computes at runtime, e.g. `tiers`). AAC sets any module up in the owner's chat with the setup tool
`module_setting(module, on, settings)` (built 0.44; replaces `set_heartbeat`).
A module that fails to load is skipped. Deleting a domain removes its module data too.

**Permissions and properties (v0.19.0)**, per domain, for every module (`store.module_access`, `aac/module/access`;
panel: "⋯" next to the module in the sidebar → its page): `enabled` (off = no tools, no page, no calendar entries
there), `name` (own display name), `instructions` (own instructions for the AI; empty = the manifest's
`instructions`, "Reset" button), `access` (full · read = only read-only tools · none), `auto` (automatic runs may
use the tools), `destructive_needs_owner` (deleting tools only while the owner is in a chat; a tool counts as
deleting unless it is read-only or says destructive=false, HA's default is destructive). Enforced by
`GuardedAPI`, which wraps every module's tools each time an agent asks for them. Setup steps from the
manifest are built (see above; ids `<domain>/mod/<module>/<step>`), e.g. the calendar module's "Connect a calendar".

Modules so far: `heartbeat` (0.44: a source node writing `broker.request`; off by default; page with its last
runs and "Run once now"), `lists` (lists of anything: tree entries with free fields; tools; calendar provider;
page; setting "show dated entries in the calendar") and `calendar` (HA calendars adapter; where new
entries go; agenda page; setting "show the calendar page"). Still planned: external modules, AAC-made modules, sandbox for code modules.

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
