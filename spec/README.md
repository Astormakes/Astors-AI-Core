# AAC contracts (v2)

The interfaces everything in AAC plugs into. v1's contracts are in git under the tag `v1-final`.

| Contract | What it describes |
|---|---|
| [Cards](cards.md) | a card file: ports, settings, functions, values, the context it works on, and the core's services (channels, models, audio) |
| [Audio source](audio-source.md) | a microphone (an AAC satellite) streaming to AAC and playing what AAC says |

## A setup file

The panel's *Save file* / *Load file* (`aac/setup/export`, `aac/setup/import`): a whole setup as JSON, without the
cards' own data (chats, lists, memories stay where they are).

```json
{"aac": 2, "domain": "Assistant",
 "cards": {"chat": {"type": "chat", "title": "Chat", "settings": {}, "version": "1.1.0"}},
 "wires": [{"from": "chat.said", "to": "model.in"}],
 "positions": {"chat": {"x": 0, "y": 0}}}
```

## Rules for every contract

- **Plain JSON.** Everything here serializes to JSON; times are ISO 8601 UTC.
- **The core never asks for a card's type** (docs/v2 rule 10): cards are used only through what they offer, so any
  card can be swapped for another, AAC's own included.
