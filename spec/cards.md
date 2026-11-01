# Cards (3.0)

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

The rules every card follows are in `custom_components/aac/rules.py` (`RULES`; the Card editor's `rules`, the panel's
How AAC works page; docs/v2 page 5). In short: a context is a list of entries; a card acts when the context holds
something for it and sends only when it did something.

```python
from custom_components.aac.api import Card, Context, register

VERSION = "1.0.0"  # raise it when the file changes; a newer file is never replaced by an older one


@register
class Shout(Card):
    """Upper case."""

    type = "shout"                      # unique; the same as an AAC card's replaces it
    title = "Shout"
    category = "Text"                   # palette group: In and out, Text, Logic, Models, Data, Home Assistant, Audio, Other
    help = "Makes the newest said line upper case."
    inputs = {"in": "text"}             # port → kind: text (a context), read (dotted: read, never starts), audio (a live stream), clip (a finished bit of audio); "audio|clip": either; "any": every kind
    outputs = {"out": "text"}           # same kinds; a read output (a triangle) is what held(port) provides, wired only into read inputs; every wire of an output gets a copy
    ports_help = {"in": "a context arrives", "out": "the same context, louder"}
    settings_schema = {"loud": {"type": "bool", "title": "Really loud", "default": True}}
    tags = {"shouted": "lines it changed"}          # written as shout.shouted
    functions = {"shout": {"help": "Upper-case a text.", "args": {"text": {"type": "string", "required": True}}}}
    values = {"count": "how many lines it shouted"}  # shout.count in a Relay's script

    async def handle(self, context: Context, port: str) -> None:
        """A context arrived: act when it holds something for this card, send only when it did something."""
        said = context.get("*.said")
        if said is None or context.streaming:
            return  # nothing for it: the context ends here
        said["text"] = said["text"].upper()
        await self.send(context, "out")

    async def call(self, function: str, args: dict, context: Context | None = None):
        """One of its functions (a model's tool call, a Relay's script)."""
        if function == "shout":
            return args["text"].upper()
        return await super().call(function, args, context)
```

**The context** (`context.py`). `Context.entries` is a list; the first entry is always `context.system` (its text: the
system prompt), the second `context.tools` (its `data["tools"]`: `[{name, card, function, description, args}]`), then
the conversation (`context.conversation`). An entry is `{id, tag, text, data, time, state}`: the tag `<card>.<name>`
(optionally `.<extra>`; `card.tag(name)` puts the card in front), state `streaming` / `ended` / `cut`. Reading:
`get(pattern)` (the newest whose tag fits, `*` stands for anything), `find(pattern)`, `by_id(id)`, `newest`,
`streaming`, `waiting` (it waits for a call's result), `next_call()`. Writing: `add(tag, text, at="last"|"first",
**data)`, `remove(entry)` (system and tools stay), `copy()`. Stored as `to_dict()` / `from_dict()` (`{entries}`).
Everything anyone says is `<card>.said`.

**Snapshots.** While a card writes (a Model), it sends the same context again after every finished sentence, its entry
`streaming`; the last one has it `ended`. Every card gets it through `handle()` like any context: Text to speech speaks
the finished sentences, a Chat shows it growing, a Model finds nothing for it.

**For models.** Every card with functions has the setting `functions` (type `functions`, in the fold "For models"):
per function `{on, name, help}`, the tick, the name a model sees and the description it reads; empty keeps the card's
own (`offers` on the class is the tick's default; a Relay's is off). `card.offered()` gives the ticked ones as models
see them. A card whose functions come from its settings sets `dynamic_functions = True` and `self.functions` per
instance (a Relay: one per `def` in its script). A setting may say `fold` to sit in a fold at the bottom of the card's settings.

**Calls and tools.** A card that uses tools (`uses_tools = True`: a Model, a Relay's script) gets the functions of the
cards it reaches from the outputs its calls leave by (`calls_from`, empty: all its outputs; a Model's is `calls`),
straight or through other cards, and that reach it again for the result (`card.tools()`). A call is in the `data
["calls"]` of the entry that makes it (`[{id, card, function, name, args}]`). The context goes out like every context;
every card it passes may look at it (`context.waiting`: it waits for a result); the card the next call names
(`context.next_call()`: the first call without a result while the newest entry with calls is followed by nothing but
results) does not get `handle()`: the core runs its function (`call()`), adds a `<card>.result` entry (`data
["call_id"]`) and sends the context on from that card's outputs. Calls go one after the other. Tool names are the
function's own (as For models names it); the card's name goes in front only when two cards in one model's tools offer
the same name. The card id never reaches a model.

**The Model** acts when the newest entry is not its own (`<its id>.*`) and nothing is streaming. Its request, from the
tags: `context.system` is the system prompt; its own `.said` entries are assistant messages (with their calls); the
results of its calls are tool messages; every other entry with text is a user message (two in a row are one). Its
answer is a `<its id>.said` entry (`data`: `calls`, `thinking`, `cut`, `usage`; the newest also `request`, the Plain
text view), sent at `out`; what it says before calling also goes out at `out`, its calls at `calls`. An answer that is
nothing, or only the word in its "Means no answer" setting, is no answer: nothing is sent.

**Stop** is a core function (`card.stop_all()`, `aac/stop`, the Stop buttons, a script's `stop()`): every card's
`async stop()` is called at once. A busy card marks what it writes `cut` and sends its context on; an idle card does
nothing. A stop is no entry and travels nowhere.

**The storm limit.** Home Assistant gets its turn between two cards (`flow.enter` yields). More contexts through one
wire within `STORM_SECONDS` (1) than the storm limit (AAC's settings: `aac/settings`, `aac/settings/set {storm}`;
default `STORM`, 30) make it red (`flow.passes()`, `domain.redden()`): it carries nothing until it is unblocked, it
stays red over a restart (`domain.red`: `{from, to, since, count}`, in the domain's store and its summary), the owner
gets a Home Assistant notification, the log an error (`what` red). Unblocked by the owner (the Cards page: click the
wire, Unblock; `aac/wire/unblock {from, to}`) or a model through the Card editor (`unblock`); a wire that is removed is
no longer red. While a wire is red, every answer of the Card editor carries `red_wires` and its `cards` overview marks
them. A slow loop (through a Timer, which holds a context on its own) may run for ever.

More a card may define: `busy` (a property: True while it works on something; a newer version of its file waits),
`default_data()` + `keeps_data = True` (its own stored data in `self.data`, `await
self.save()`; `async upgrade_data(settings, made_with)`: made from a setup saved by another version of its file, after
`upgrade()` and loading its data, it gets the settings as they were saved, e.g. to move entries from a setting into its
data), `self.folder` (where it keeps files: `<config>/aac/data/<domain>/<card>`, moved with a rename, gone with
its domain; a removed card's data and files stay while Undo, Redo or a slot can bring it back, then go; the owner
removing a card whose data differs from `default_data()` is asked: keep it, or let it go now), `start()` / `unload()` (timers, listeners, channel subscriptions), `stop()` (see Stop), `held(port)` (what a dotted wire from that read output
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
every frame. The card sends frames with `self.show(data)`, which only goes out while a Cards page shows the card
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

In Python: `nav()` gives its sidebar entries (`{"entries": [{id, title, children?, actions?}], "add": "New list", "fold": "arrow"}`;
None: one item). `fold: "arrow"`: its group stays open until its arrow closes it (Chat, Lists); without it the group
opens while one of its pages is in use. The owner can change either in Appearance. An entry's `actions` (`[{id, label, icon?, ask?, confirm?}]`) show in the sidebar: one with an icon
as a button, the others in its ⋯ menu; `ask` asks for a value first, `confirm` asks first. The panel then calls
`ui_call("nav_action", {entry, action, value})`, and `ui_call(action, args)` answers its page for what is not a function for models (a Chat's send).

## The core's services

Through `self.core`:

- `subscribe(channel, card, handler)`: take what comes in on a channel. `audio` (handler `(chunk, meta)`: satellite
  audio), `ha_conversation` (handler `async (text, conversation_id, where) -> answer`: Home Assistant's Assist).
  Subscriptions end when the card unloads.
- `calendar`: AAC's calendar (v1's design: the core keeps no entries, only who offers them). A card offers its entries
  with `calendar.offer(card, query, create=None, change=None, remove=None)` (usually in `start()`; it ends when the card
  unloads): `query(start, end)` gives the entries of a period `[{key, summary, start, end?, description?}]` (start and
  end "YYYY-MM-DD" or a time; repeating entries once per occurrence), `async create(entry) -> key`, `async change(key,
  changes)`, `async remove(key)`; what it leaves out it does not offer, and asking for it gives a clear "cannot".
  `calendar.entries(start, end)` collects every card's entries at that moment, by start: `{uid ("<card>:<key>"),
  summary, start, end, description, source (the card), all_day, start_v, end_v, can}`; `await calendar.add(entry)`
  (into the first card that takes new ones), `change(uid, changes)`, `remove(uid)` (where the entry lives). Home
  Assistant's `calendar.aac_calendar`, the Calendar card and models ask here, never a card. `parse_when(text)` from
  the api reads a date or a time. Offered today: the Calendar card (its own entries, all four ways), Lists (dated
  entries: a field when / date / due; read only), Schedule (each time an entry rings; read only).
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
  `self.clip_out(port, clip)` / `self.stream_out(port, chunk, meta)`.
- `notify(what, **data)`: tell open panels.
- `log(part, level, text)`: a part of AAC itself ("audio", "models" …) writes in the running domain's log.

**The log.** Each domain has AAC's own log (`self.domain.log`, the file `<config>/aac/data/<domain>/log.jsonl`: it moves
with a rename, goes with the domain; a copy starts its own). It rotates in two lanes, so a busy card cannot push the rest out: the newest 5,000
entries of what passed the wires (level flow) stay, and the newest 5,000 of everything else. An entry is
`{id, time (epoch s), level (error, warning, info, flow), card | part, what, text (600 chars at most), port?, to?, n?
(the same entry again right after it: counted up), trace?}`; while it is younger than 10 minutes it may carry the
whole `context` (only in memory, never on disk). A card writes with `self.log(text, level="info", what="", context=None,
**extra)` (the context goes along only when its Log setting says the whole context) and reads with
`self.read_log(card=, part=, port=, what=, levels=, search=, before=, last=)` (part `"*"`: AAC itself): its own entries
always, the rest only when the owner allowed it. Asking for that: `needs = {"read_log"}` on the class gives it the
permission setting `may_read_log` (off; on the Permissions page). Every card has the settings `log` (`text`: what
changed, the default; `context`: the whole context too; `nothing`) and `log_at` (`out`, the default; `in`; `both`) in
the fold "Log": what passes its wires is written as `flow` entries (`what` in or out, `port`, `to`); snapshots are
not. The core writes what fails (with the trace), a model that does not answer, a wire turned red, and what the
owner changes (cards added, removed, renamed, settings, wires, Undo, Redo, slots, files, the domain
starting). HA's own log only hears about AAC running on Home Assistant (card files loading). The panel: Settings →
Logs (filters, Save as text, Clear); a wire's monitor shows the entries of its output (`aac/monitor`; `aac/log`,
`aac/log/clear`).

**The card interface** (`api.py`; cards may later run in a sandbox, so this is all a card uses): `handle`, `send(context,
port)`, `read(port)`, `held(port)`, `call`, `stop`, `stop_all()`, `tools()`, `wired(port)`, `wired_into(port)`,
`reaches(card_id, ports=None)`, `all_cards()` (other cards, used only through what they offer: `call`, `current()`,
`held()`), `stream_out`, `clip_out`, `live(context, text, thinking)`, `show(data)`, its data and folder, the log, and
the core's services above. Cards that build (the Card editor; an Audio clipper adding its Phrase joiner) also use the
setup through `self.domain`: `add_card`, `update_card`, `remove_card`, `set_wires`, `wires`, `export`,
`template_values()`.

**A card's id is its name** (lowercase, other characters `_`: "Speech to text" is `speech_to_text`; a second card of
that name `speech_to_text_2`), so a card is called the same everywhere. Renamed (by the owner, the Card editor, Undo,
a slot, a file; or loaded with an id that does not fit), its id moves everywhere: wires, positions, Undo steps and
slots, its stored data and folder, the sidebar's fold (and what the browser keeps: wire monitors, folds), and what the other cards keep about it: their settings through the class
method `renamed_settings(settings, old, new)` (by default a script's references `old.tag` and `cards['old']`; a card
with ids in a setting of its own adds them, as the Calendar does for `lists`) and their data through
`async renamed(old, new)` (by default stored entries: their `tag`, a call's `card`).

**A domain's id is its name** too (the same rule): renamed (`aac/domain/rename` answers with the id it has then) or
loaded with an id that does not fit, its store, its cards' stores and folders move, and a running domain starts again
under the new id.

**The Chat** keeps every context that reaches `answers`: in the chat whose entry `<chat card>.id.<chat id>` (no text) it
holds, else in a new chat named by the id of the context's first entry (so the rest of that turn, its snapshots and
calls, finds it), which writes its id entry. It shows the entries its Shown setting lists (`*.said, *.post, *.error`),
those Shown as you on your side.

## The Relay's script

A Relay card's setting `script` is Python (docs/v2 page 4), checked before it runs (`cards/relay/script.py`,
`check()`): only the parts listed there (no imports, no `while`, nothing starting with `_`). It runs top to bottom
every time a context arrives, snapshots included (`context.streaming` is True on those); nothing leaves unless
`output("name", what)` runs. Its ports are what it names: `input("x")` (text), `input("x", read=True)` (dotted, read
when something arrives elsewhere), `output("x", …)`; none named: `in`, `out`. `input()` is what arrived
(`input("x")`: None when it came in elsewhere). `output` sends a context as it is, anything else (a text, an entry, a
list) as the Relay's said (tag `<relay>.said`) in the context that arrived, to every card wired there.

To the script the context is a list of entries (`ContextView`): `get(tag)`, `[tag]`, `tag in context`, `all(tag)`
(`.text`: their texts as one), `text` (the conversation's), `system`, `tools`, `streaming`, `add(tag, text,
at="last"|"first")`, `set(tag, text, at=)` (written in place; none: as `add`; the system prompt:
`set("context.system", text)`), `remove(tag, at="all"|"first"|"last")` (system and tools stay), `a + b` (a's entries,
then b's; the system prompt and tools are b's, a's when b has none). A tag without a dot is the Relay's own
(`<relay>.<name>`), `*` stands for anything. An entry: `text` (settable), `tag`, `id`, `data` (writable), `state`,
`time`. Other names: `stop()` (every card stops, once the script is done), `print()` (the editor's Printed panel),
`print_log()` (AAC's log), `now()`, `states()`, `re`, `audio`, and every card by its id: its values (`lists.total`)
and, for a card it reaches from one of its outputs, its functions (`lists.add_items(...)`, arguments in order or by
name; not on a snapshot). Such a call travels like a model's: an entry `<relay>.call` with the call goes out at the
outputs that reach the card, the card's result comes back into the Relay, and the script goes on with the context as
it came back. The script's own names win over a card's id (`cards["output"]`). Every `def` is a function the Relay
offers (`self.functions`: its parameters, its docstring): a call runs only the defs, then that one, with its
arguments; its return is the result. The full list with examples is `REFERENCE` there (the editor's ? panel; the
`script` setting's help, which the Card editor's `help("relay")` gives a model).
