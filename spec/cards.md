# Cards (v2)

Every behaviour of AAC is a card. A card type is a Python file in `<config>/aac/cards/`, and every card is loaded
from there when AAC starts (and when the integration is reloaded). While AAC runs it looks at the folder every few
seconds: a file whose `VERSION` went up (or a new one) is loaded again, and its running cards are made again (settings
upgraded, data kept) as soon as they are not `busy`; a deleted file's card types leave the palette. A card's page or
view changes after the AAC page is reloaded. AAC ships its own card files and copies them there,
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
    inputs = {"in": "text"}             # port → kind: text (a context), read (dotted: read, never starts), audio (a live stream), clip (a finished bit of audio); "audio|clip": either; "any": every kind
    outputs = {"out": "text"}           # same kinds; a read output (a triangle) is what held(port) provides, wired only into read inputs
    square = set()                      # outputs where the card picks one wire (else every wire gets a copy)
    ports_help = {"in": "a context arrives", "out": "the same context, louder"}
    settings_schema = {"loud": {"type": "bool", "title": "Really loud", "default": True}}
    tags = {"shouted": "lines it changed"}          # written as shout.shouted
    functions = {"shout": {"help": "Upper-case a text.", "args": {"text": {"type": "string", "required": True}}}}
    values = {"count": "how many lines it shouted"}  # shout.count in a Relay's script

    async def handle(self, context: Context, port: str, step) -> list | None:
        """A context arrived. None: on to every round output; []: it ends here; ["out"] or [("out", card_id)]: there."""
        said = context.last("said")
        if said:
            said["text"] = said["text"].upper()
        return None

    async def call(self, function: str, args: dict, context: Context | None = None):
        """One of its functions (a model's tool call, a Relay's script)."""
        if function == "shout":
            return args["text"].upper()
        return await super().call(function, args, context)
```

While a card works on a turn it may send snapshots of it: `await step.progress(picks)` (taken when called). A snapshot
(`context.progress`) passes each card's `async progress(context, port)`, which by default sends it straight on (like
`handle` returning None); cards that show or speak things as they come answer it (Chat holds the turn, Text to speech
speaks each newly finished sentence). The finished turn always follows through `handle()`. A Model sends one after
every finished sentence and when it calls tools.

**For models.** Every card with functions has the setting `functions` (type `functions`, in the fold "For models"):
per function `{on, name, help}`, the tick, the name a model sees and the description it reads; empty keeps the card's
own (`offers` on the class is the tick's default; a Relay's is off). `card.offered()` gives the ticked ones as models
see them. A card whose functions come from its settings sets `dynamic_functions = True` and `self.functions` per
instance (a Relay: one per `@new` block). A setting may say `fold` to sit in a fold at the bottom of the card's settings.

**Tools** (docs/v2 page 3). A card that uses tools (`uses_tools = True`: a Model, a Relay's script) gets the functions
of the cards wired from one of its outputs and straight back into it (`flow.paired()`, `flow.tools()`); nothing else
is a tool. A model's call travels: the context goes along the wire to the called card, the core runs the function
there (`call()`), adds the result as a `result` message and sends the context back along that card's wire to the
caller; several calls go one after the other. Tool names are the function's own (as For models names it); the card's
name goes in front only when two cards in one model's tools offer the same name. The card id never reaches a model.
The answer goes back to the card the context started at when that is wired (even when it is a tool too), else to
every wired card but the tools.

More a card may define: `busy` (a property: True while it works on something; a newer version of its file waits),
`default_data()` + `keeps_data = True` (its own stored data in `self.data`, `await
self.save()`), `start()` / `unload()` (timers, listeners, channel subscriptions), `stop(stop_entry) -> bool` (a stop
arrived: True when it was busy and sent its own context on), `held(port)` (what a dotted wire from that read output
reads), `current()` (its values), `ui = True` (a page of its own, see below). A setting of type `modellist` with a
`tag` gives it a list of models with queues. A setting may say `min` (and `off`, the value that turns it off),
and `confirm` (a question the panel asks before it is first turned on, for a setting that changes more than itself). A setting marked
`permission` also shows on the domain's Permissions page (what the domain's cards may do, all in one place). A
setting of type `access` is a list of the card's own entries, each hidden / read-only / full (not set: hidden):
the card returns them from `ui_call("access_items")` as `[{id, name, area, group}]`; the value is `{id: level}`.
A setting of type `element` (with `element`: a custom element's name, `file`: a .js in the card's folder) is edited
by the card's own element: the panel imports the file and hands the element `.panel` (as a page's below, with
`save(settings)` saving and redrawing, `type(t)`, `domain()`, `card()`), calls `setup({card, key, value, settings})`
after every redraw and `remember()` before one; the element is kept, so typing goes on (the Relay's script editor).

## A card folder

One card type per file, always: never several card types in one file, so each can be removed or replaced alone (a
helper or base class two cards share is copied into each, or offered by the core's `api`). A card with more than one
file is a folder: `chat/card.py` (with `VERSION`), `page.js`, `view.js`. It is copied, versioned and loaded as a whole
(`aac_cards.chat`); a single-file card stays `decide.py`. Files are served at `/aac_cards/<folder>/<file>.js`.

## A card's view

A card with `view = {"height": 90}` and a `view.js` in its folder shows something inside its card in the grid (the
card grows by that height); `view = {"inline": True}` puts a small view in its first port row, between the port
names, so the card keeps the size of any card. The file defines `aac-view-<type>`; the panel sets `.card` and calls `frame(data)` for
every frame. The card sends frames with `self.show(data)`, which only goes out while a Nodes page shows the card
(`self.watched`: check it before computing anything). Keep frames small (a few dozen numbers), up to ~30 a second.

## A card's page

A card with `ui = True` brings its page as `page.js` in its folder (served at `/aac_cards/<folder>/page.js`). The file defines a custom element `aac-card-<type>` (`_` → `-`)
without shadow DOM, so the panel's look and skins apply. The panel hands it:

- `.panel`: `ws(type, data)`, `call(function, args)` (the card's functions, like a model), `ui(action, args)` (the
  card's `ui_call()`), `data()` (its stored data), `save(settings)`, `go(entry)` (open another of its entries),
  `header(title, buttonsHtml)`, `tip(html)`, `esc(text)`, `md(text)`, `cardTitle(id)`;
- `.card` (the card: id, title, settings …) and `.entry` (the sidebar entry picked, or null), then calls `show()`.
- AAC's own dialogs: `confirm(text, okLabel, danger)` → true/false, `prompt(text, value, multiline)` → text or null,
  `alert(text)`, `dialog({title, text, input, buttons: [[value, label, "primary"|"danger"]]})` → `{button, value}`.

It may define `event(ev)` (what the card tells open panels: `core.notify(what, card=self.id, …)`) and `add()` (the
`+` in its sidebar group). The element is kept while the page is open elsewhere, so its state survives.

In Python: `nav()` gives its sidebar entries (`{"entries": [{id, title, children?, actions?}], "add": "New list"}`;
None: one item). An entry's `actions` (`[{id, label, icon?, ask?, confirm?}]`) show in the sidebar: one with an icon
as a button, the others in its ⋯ menu; `ask` asks for a value first, `confirm` asks first. The panel then calls
`ui_call("nav_action", {entry, action, value})`, and `ui_call(action, args)` answers its page for what is not a function for models (a Chat's send).

## The core's services

Through `self.core`:

- `subscribe(channel, card, handler)`: take what comes in on a channel. `audio` (handler `(chunk, meta)`: satellite
  audio), `ha_conversation` (handler `async (text, conversation_id, where) -> answer`: Home Assistant's Assist),
  `ha_calendar` (handler `events(start, end)`: Home Assistant's calendar entity). Subscriptions end when the card
  unloads.
- `pool`: the models. `pool.entries(list, tag)` (a card's list of models doing a job), `async with pool.slot(entries)
  as model_id` (a free model, else the queues), `pool.tags(model_id)`, `pool.find(name)` (a model by id or name),
  `await pool.offers()` (every server behind the pool and what it offers: a llama-server router lists its models and
  their state); `run(pool, entries, job)` and `Client(hass, model)` from the api: `chat(...)`, and on a router
  `router()` (its models) and `router_do(action, name)` (`load`, `unload`, `download`, `delete`).
- `save_model({"id", ...})` then `save()`: change one of the pool's models (e.g. which server model it uses).
- `audio`: `say(satellite, text, language, engine=None, voices=None)` (a card's engine and its voice per language
  `{code: {language: locale, voice}}`; else the Audio page's default engine), `voices(engine)` (an engine's voices per
  home language), `play(satellite, wav)`, `stop(satellite)`, `spoken()` (when AAC last spoke), `stt(pcm, language,
  models)` (no language: the home's).
- `languages()`: the home's languages, main first (ISO codes; the Audio page, else as Home Assistant describes the
  home).
- Audio helpers from the api: `pcm_to_wav(pcm, rate)`, `wav_to_pcm(wav)`, `resample(pcm, rate, to)`. A clip is
  `{"pcm": 16-bit mono, "rate", "seconds", "source" (a satellite), "text"?, "language"?}`, or `{"stop": True}`. A card
  takes clips with `async clip(port, clip)` and a live stream with `stream(port, chunk, meta)`; it sends them with
  `self.domain.flow.clip(...)` / `flow.stream(...)`.
- `notify(what, **data)`: tell open panels.

Through `self.domain`: `flow.send(context, self.id, port)` (start a context at an output), `cards` (other cards, used
only through what they offer: `call`, `current()`, `held()`, `dated()`), `template_values()` (every card's values: the Values panel, a Relay's script).

A context (`Context`, docs/v2 page 3) has the shape of a model request: `system` (parts), `messages` (said, answer,
result; an answer's calls in `data["calls"]`, a result's `data["call_id"]`; a message's `attachments`: note before
its text, trailer after), `tools` (`{name, card, function, description, args}`, filled by a Model), `events` (signal,
error, call: never sent to a model). Every item is an entry `{id, by, kind, text, tags, time, data, state}`.
`context.add(by, kind, …)` puts an entry where its kind belongs (a note or trailer on the newest said message);
`context.put(...)` (a card's standing text: a system part stays in place, an attachment moves to the newest message),
`context.remove(e)`, `context.entries` (all in order), `context.last(kind)`, `context.tagged(tag)`, `context.answer`,
`context.open_calls(card)`, `context.stopped`.

## The Relay's script

A Relay card's setting `script` is Python, checked before it runs (`cards/relay/script.py`, `check()`): only the
parts listed there (no imports, no `while`, nothing starting with `_`). `@new("name", at="start"|"end",
before=tag, after=tag, replace=)` above a `def` writes an entry of its own (tag `<relay>.<name>`; the start is a
`system` part, the end a `trailer` on the newest said message, before / after a `note` / `trailer` on the message of
that tag); `@on("tag", …, which="last"|"first"|"all",
kind=, live=)` runs on entries with a tag (`*` = any card, `"context"` = the whole context once). Names: `context`,
other cards (`chat.said.text`, `lists.total`, `lists.add_items(...)` for cards wired to it and back; such a call is an event of kind `call`), `now()`, `states()`,
`stop()`, `output("name")` (the Relay gets that output), `signal()`, `between()`, `re`, `audio`. In a snapshot
(a stream) an open entry a block works on goes on empty until it is closed; `live=True` runs on every piece.
Side effects (stop, signals, calls) happen once per turn and entry. The full list with examples is `REFERENCE`
there (the editor's ? panel; models read it in the setting's help).
