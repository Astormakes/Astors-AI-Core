# Cards (v2)

Every behaviour of AAC is a card. A card type is a Python file in `<config>/aac/cards/`, and every card is loaded
from there when AAC starts (and when the integration is reloaded). AAC ships its own card files and copies them there,
unless a newer version of the same file is already there (a download, or one made for you). Each file has a
`VERSION`; a saved card remembers the version it was made with, and a newer one can update its settings
(`upgrade(settings, made_with)`). There is no difference between AAC's cards and any other: none may use anything but
the public interface below, and the core never asks for a card's type (docs/v2 rule 10).

## A card file

```python
from custom_components.aac.api import Card, Context, register

VERSION = "1.0.0"  # raise it when the file changes; a newer file is never replaced by an older one


@register
class Shout(Card):
    """Upper case."""

    type = "shout"                      # unique; the same as an AAC card's replaces it
    title = "Shout"
    category = "Text"                   # palette group: In and out, Text, Logic, Models, Data, Home Assistant, Audio, Other
    help = "Makes the newest line upper case."
    inputs = {"in": "text"}             # port → kind: text (a context), read (dotted: read, never starts), audio (a live stream), clip (a finished bit of audio); "audio|clip": either
    outputs = {"out": "text"}           # same kinds; a read output (a triangle) is what held(port) provides, wired only into read inputs
    square = set()                      # outputs where the card picks one wire (else every wire gets a copy)
    ports_help = {"in": "a context arrives", "out": "the same context, louder"}
    settings_schema = {"loud": {"type": "bool", "title": "Really loud", "default": True}}
    tags = {"shouted": "lines it changed"}          # written as shout.shouted
    functions = {"shout": {"help": "Upper-case a text.", "args": {"text": {"type": "string", "required": True}}}}
    values = {"count": "how many lines it shouted"}  # {{ shout.count }} in templates

    async def handle(self, context: Context, port: str, step) -> list | None:
        """A context arrived. None: on to every round output; []: it ends here; ["out"] or [("out", card_id)]: there."""
        said = context.last("said")
        if said:
            said["text"] = said["text"].upper()
        return None

    async def call(self, function: str, args: dict, context: Context | None = None):
        """One of its functions (a model's tool call, a Conditional's action)."""
        if function == "shout":
            return args["text"].upper()
        return await super().call(function, args, context)
```

While a card works on a turn it may send snapshots of it: `await step.progress(picks)` (taken when called). A snapshot
(`context.progress`) passes each card's `async progress(context, port)`, which by default sends it straight on (like
`handle` returning None); cards that show or speak things as they come answer it (Chat holds the turn, Text to speech
speaks each newly finished sentence). The finished turn always follows through `handle()`. A Model sends one after
every finished sentence and every tool result.

More a card may define: `default_data()` + `keeps_data = True` (its own stored data in `self.data`, `await
self.save()`), `start()` / `unload()` (timers, listeners, channel subscriptions), `stop(stop_entry) -> bool` (a stop
arrived: True when it was busy and sent its own context on), `held(port)` (what a dotted wire from that read output
reads), `current()` (its values), `ui = True` (a page of its own, see below). A setting of type `modellist` with a
`tag` gives it a list of models with queues.

## A card's page

A card with `ui = True` brings its page as a `.js` file next to its `.py` (`lists.py` → `lists.js`, copied and
versioned with it, served at `/aac_cards/lists.js`). The file defines a custom element `aac-card-<type>` (`_` → `-`)
without shadow DOM, so the panel's look and skins apply. The panel hands it:

- `.panel`: `ws(type, data)`, `call(function, args)` (the card's functions, like a model), `ui(action, args)` (the
  card's `ui_call()`), `data()` (its stored data), `save(settings)`, `go(entry)` (open another of its entries),
  `header(title, buttonsHtml)`, `tip(html)`, `esc(text)`, `md(text)`, `cardTitle(id)`;
- `.card` (the card: id, title, settings …) and `.entry` (the sidebar entry picked, or null), then calls `show()`.

It may define `event(ev)` (what the card tells open panels: `core.notify(what, card=self.id, …)`) and `add()` (the
`+` in its sidebar group). The element is kept while the page is open elsewhere, so its state survives.

In Python: `nav()` gives its sidebar entries (`{"entries": [{id, title, children?}], "add": "New list"}`; None: one
item), and `ui_call(action, args)` answers its page for what is not a function for models (a Chat's send).

## The core's services

Through `self.core`:

- `subscribe(channel, card, handler)`: take what comes in on a channel. `audio` (handler `(chunk, meta)`: satellite
  audio), `ha_conversation` (handler `async (text, conversation_id, where) -> answer`: Home Assistant's Assist),
  `ha_calendar` (handler `events(start, end)`: Home Assistant's calendar entity). Subscriptions end when the card
  unloads.
- `pool`: the models. `pool.entries(list, tag)` (a card's list of models doing a job), `async with pool.slot(entries)
  as model_id` (a free model, else the queues), `pool.tags(model_id)`; `run(pool, entries, job)` and `Client(hass,
  model).chat(...)` from the api.
- `audio`: `say(satellite, text, language, engine=None, voices=None)` (a card's engine and its voice per language
  `{code: {language: locale, voice}}`; else the Audio page's default engine), `voices(engine)` (an engine's voices per
  home language), `play(satellite, wav)`, `stop(satellite)`, `spoken()` (when AAC last spoke), `stt(pcm, language,
  models)` (no language: the home's).
- `languages()`: the home's languages, main first (ISO codes; the Audio page, else as Home Assistant describes the
  home). Templates read `{{ languages }}`.
- Audio helpers from the api: `pcm_to_wav(pcm, rate)`, `wav_to_pcm(wav)`, `resample(pcm, rate, to)`. A clip is
  `{"pcm": 16-bit mono, "rate", "seconds", "source" (a satellite), "text"?, "language"?}`, or `{"stop": True}`. A card
  takes clips with `async clip(port, clip)` and a live stream with `stream(port, chunk, meta)`; it sends them with
  `self.domain.flow.clip(...)` / `flow.stream(...)`.
- `notify(what, **data)`: tell open panels.

Through `self.domain`: `flow.send(context, self.id, port)` (start a context at an output), `cards` (other cards, used
only through what they offer: `call`, `current()`, `held()`, `dated()`), `template_values()`.

A context (`Context`) is a document of entries `{id, by, kind, text, tags, time, data, state}`; `context.add(...)`,
`context.put(...)` (a card's standing text), `context.last(kind)`, `context.tagged(tag)`, `context.answer`,
`context.stopped`. Kinds: system, note, trailer, said, answer, call, result, signal (a stop), error.
