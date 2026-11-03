"""Cards (docs/v2 page 2): every behaviour of AAC is a card the owner can see, wire, change and remove.

A card type declares its ports, settings, the tags it writes and its functions (exactly documented: models call
them as tools, a Relay's script calls them). A card instance lives in one domain and owns its data.

Every card type is a file or a folder in `<config>/aac/cards/` (spec/cards.md). AAC's own cards (next to this file)
are copied there unless a newer version is there (each has a VERSION). Cards only use the core's
public interface (`custom_components.aac.api`), never each other's types.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import sys
import time
import types
from typing import TYPE_CHECKING, Any, ClassVar

from ..const import DRAFT, LOGGER
from ..context import Context
from ..permissions import Home, kinds_of, lower, rank

if TYPE_CHECKING:
    from ..core import Domain

CARD_TYPES: dict[str, type[Card]] = {}
LOADED: dict[str, tuple[int, ...]] = {}  # card module → the VERSION it was loaded with
UI_URL = "/aac_cards"  # a card file's UI (<stem>.js next to it) is served here
FUNCTIONS = {"type": "functions", "title": "For models", "default": {}, "fold": "For models", "permission": True,
             "help": "A Model wired to this card and back has these functions as tools: each ticked one, under the name "
                     "and with the description written here (Reset: the card's own). The card's id never reaches the model."}
LOG = {"log": {"type": "select", "title": "What it writes in the log", "default": "text", "fold": "Log",
               "options": [["text", "what changed (the newest text)"], ["context", "the whole context too (kept 10 minutes)"],
                           ["nothing", "nothing"]],
               "help": "What passes the card's wires goes into AAC's log (Settings → Logs; a wire's monitor shows it). A whole "
                       "context takes much room: it stays with its entry 10 minutes and is never written to disk. What goes "
                       "wrong is always written."},
       "log_at": {"type": "select", "title": "When", "default": "out", "fold": "Log",
                  "options": [["out", "what goes out"], ["in", "what comes in"], ["both", "both"]]}}
REQUEST = {"help": "Ask the owner for more than this card may do now (the owner decides later; nothing changes until "
                   "then). Only for things that exist for it.",
           "args": {"things": {"type": "array", "help": "Entity ids or web, notify, files, servers, log, setup, audio.", "required": True},
                    "level": {"type": "string", "help": "read, ask (each action waits for the owner's yes) or full.", "required": True},
                    "reason": {"type": "string", "help": "Why, in one sentence (the owner reads it).", "required": True}}}
ACCESS = {"type": "access", "title": "May use", "default": {}, "permission": True,
          "help": "What this card may reach outside AAC, each thing hidden, list (it may know it exists and ask for more), "
                  "read, ask (each action waits for your yes) or full; never more than the domain allows (its Permissions "
                  "page). Not set: list."}
CATEGORIES = ["In and out", "Text", "Logic", "Models", "Data", "Home Assistant", "Audio", "Other"]


def renamed_refs(value: Any, old: str, new: str) -> Any:
    """A card's id changed (its name did): in texts that refer to it, as a script does: `old.said` (a tag, a value, a
    call) and `cards['old']`. A word alone is left (`e.data.get("mic")` is not the card mic)."""
    if isinstance(value, str):
        return re.sub(rf"(?<![\w.]){re.escape(old)}(?=\.\w)|(?<=cards\[[\"']){re.escape(old)}(?=[\"']\])", new, value)
    if isinstance(value, dict):
        return {k: renamed_refs(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [renamed_refs(v, old, new) for v in value]
    return value


def renamed_entries(value: Any, old: str, new: str) -> Any:
    """A card's id changed: in stored entries (their tag, the card a call went to); nothing else (a data field may be
    called like a card: the Lists card keeps "lists")."""
    if isinstance(value, list):
        return [renamed_entries(v, old, new) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k == "card" and v == old:
            v = new
        elif k == "tag" and isinstance(v, str) and v.startswith(f"{old}."):
            v = f"{new}.{v[len(old) + 1:]}"
        else:
            v = renamed_entries(v, old, new)
        out[k] = v
    return out


def register(cls: type[Card]) -> type[Card]:
    """Make a card type available (decorator); a later one with the same type replaces it (your folder after AAC's)."""
    if (old := CARD_TYPES.get(cls.type)) is not None and old.__module__ != cls.__module__:
        LOGGER.info("AAC: card type %s from %s replaces the one from %s", cls.type, cls.__module__, old.__module__)
    module = sys.modules.get(cls.__module__)
    cls.version = str(getattr(module, "VERSION", cls.version))
    file = Path(getattr(module, "__file__", "") or "")
    page = file.parent / "page.js" if file.name == "card.py" else file.with_suffix(".js")  # a folder's page, or <stem>.js
    where = f"{file.parent.name}/page.js" if file.name == "card.py" else f"{file.stem}.js"
    cls.ui_url = f"{UI_URL}/{where}?v={cls.version}" if cls.ui and page.exists() else None
    has_view = cls.view and file.name == "card.py" and (file.parent / "view.js").exists()
    cls.view_url = f"{UI_URL}/{file.parent.name}/view.js?v={cls.version}" if has_view else None
    if any(f.get("type") == "element" for f in cls.settings_schema.values()):  # a setting with the card's own editor
        folder = file.parent.name if file.name == "card.py" else None
        cls.settings_schema = {k: {**f, "url": f"{UI_URL}/{folder}/{f['file']}?v={cls.version}"} if f.get("type") == "element" and folder else f
                               for k, f in cls.settings_schema.items()}
    if cls.needs and "request_access" not in cls.functions:  # every card that reaches outside can ask for more
        cls.functions = {**cls.functions, "request_access": REQUEST}
    if (cls.functions or cls.dynamic_functions) and "functions" not in cls.settings_schema:  # every card with functions
        cls.settings_schema = {**cls.settings_schema, "functions": FUNCTIONS}
    cls.settings_schema = {**cls.settings_schema, **({"access": ACCESS} if cls.needs else {}), **LOG}  # every card
    CARD_TYPES[cls.type] = cls
    return cls


class Card:
    """One card in a domain."""

    type: ClassVar[str] = ""
    title: ClassVar[str] = ""
    category: ClassVar[str] = "Other"
    help: ClassVar[str] = ""  # one or two sentences for the ? helper
    inputs: ClassVar[dict[str, str]] = {"in": "text"}  # port → wire kind: text, audio, pool
    outputs: ClassVar[dict[str, str]] = {"out": "text"}
    settings_schema: ClassVar[dict[str, dict[str, Any]]] = {}
    tags: ClassVar[dict[str, str]] = {}  # tag (without the card) → what it marks
    functions: ClassVar[dict[str, dict[str, Any]]] = {}  # name → {"help", "args": {name: {"type", "help", "required"}}}
    ports_help: ClassVar[dict[str, str]] = {}  # port → one sentence: what goes in or comes out there
    values: ClassVar[dict[str, str]] = {}  # name → what it is: read in templates as {{ <card>.<name> }}
    offers: ClassVar[bool] = True  # its functions are ticked for models until the owner says otherwise
    dynamic_functions: ClassVar[bool] = False  # its functions come from its settings (a Relay's defs)
    uses_tools: ClassVar[bool] = False  # it calls the functions of other cards (a Model, a Relay): the panel lists them
    calls_from: ClassVar[tuple[str, ...]] = ()  # the outputs its calls leave by (empty: all); its tools are the cards
    # with functions it reaches from there (Flow.tools)
    ui: ClassVar[bool] = False  # a page of its own: the element aac-card-<type> from the .js next to its file
    ui_url: ClassVar[str | None] = None  # where the panel loads it (set when the file is loaded)
    view: ClassVar[dict[str, Any] | None] = None  # a view inside the card in the grid ({"height": px}): view.js in its folder
    view_url: ClassVar[str | None] = None
    keeps_data: ClassVar[bool] = False
    needs: ClassVar[set[str]] = set()  # the kinds of things outside AAC it uses ("entities", or names of THINGS): its
    # setting `access` says how far, within what the domain allows (docs/v2 page 6)
    version: ClassVar[str] = "0"  # its file's VERSION

    def __init__(self, domain: Domain, card_id: str, title: str, settings: dict[str, Any] | None) -> None:
        """Initialize."""
        self.domain, self.core, self.hass = domain, domain.core, domain.core.hass
        self.id = card_id
        self.name = title or type(self).title
        self.settings = {k: f.get("default") for k, f in self.settings_schema.items()} | dict(settings or {})
        self.data: dict[str, Any] = {}

    # ------------------------------------------------------------------ time and tasks (the core's: in the worker its own loop)
    @property
    def loop(self) -> Any:
        """The event loop it runs in."""
        return self.core.loop

    def task(self, coro: Any, background: bool = False) -> Any:
        """Run a coroutine on its own (background: nobody waits for it)."""
        return self.core.task(coro, f"aac {self.id}", background)

    def later(self, seconds: float, job: Any) -> Any:
        """Run job (a function, or one returning a coroutine) once after some seconds; returns: cancel()."""
        return self.core.later(seconds, job)

    def every_minute(self, job: Any) -> Any:
        """Run job(now) at the start of every minute; returns: cancel()."""
        return self.core.every_minute(job)

    async def blocking(self, job: Any, *args: Any) -> Any:
        """Run a function that blocks (disk, CPU) in a thread of its own."""
        return await self.core.blocking(job, *args)

    @property
    def folder(self) -> Path:
        """Where it keeps files (<config>/aac/data/<domain>/<card>): they move with a rename and go with the domain."""
        return self.core.folder(self.domain.id, self.id)

    def log(self, text: str, level: str = "info", what: str = "", context: Context | None = None, **extra: Any) -> dict[str, Any]:
        """A line in AAC's log from this card (level: error, warning, info); a context goes with it only when its Log
        setting says the whole context."""
        whole = context.summary() if context is not None and self.settings.get("log") == "context" else None
        return self.domain.log.write(level, text, card=self.id, what=what, context=whole, **extra)

    def read_log(self, **filters: Any) -> list[dict[str, Any]]:
        """AAC's log of its domain (DomainLog.read's filters): needs log at read; what it wrote itself (card=its id) it
        may always read."""
        if filters.get("card") != self.id:
            self.need("log", "read")
        return self.domain.log.read(**filters)

    # ------------------------------------------------------------------ outside AAC (docs/v2 page 6)
    def level(self, thing: str) -> str:
        """How far it may reach a thing (an entity id or one of THINGS): the lower of what the domain allows and its
        own `access` (not set there: list, so it knows what the domain lets exist and can ask for it; hidden is said out
        loud); hidden for a kind it does not need."""
        if kinds_of(thing) not in self.needs:
            return "hidden"
        return lower(self.domain.allows(thing), (self.settings.get("access") or {}).get(thing) or "list")

    def may(self, thing: str, level: str) -> bool:
        """It may reach the thing at least this far."""
        return rank(self.level(thing)) >= rank(level)

    def need(self, thing: str, level: str) -> None:
        """Raises PermissionError (with what to do) unless it may reach the thing this far."""
        if not self.may(thing, level):
            mine, domain = (self.settings.get("access") or {}).get(thing) or "hidden", self.domain.allows(thing)
            raise PermissionError(f"{self.name} may not use {thing} at {level} (the domain allows {domain}, the card "
                                  f"{mine}): the owner allows it on the Permissions page")

    async def act(self, thing: str, what: str) -> None:
        """Before every action on a thing (switch, send, write, change …): at full it goes on; at ask it waits for the
        owner's yes (a popup in AAC, a phone notification; no answer in time is a no); else PermissionError."""
        if self.level(thing) == "ask":
            if not await self.core.asks.ask(self, thing, what):
                raise PermissionError(f"The owner did not allow it: {what}")
            return
        self.need(thing, "full")

    @property
    def ha(self) -> Home:
        """Home Assistant's entities, as far as it may reach them."""
        return Home(self)

    def web(self) -> Any:
        """The web session (needs web at read)."""
        self.need("web", "read")
        from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: PLC0415
        return async_get_clientsession(self.hass)

    async def notification(self, service: str, message: str, title: str) -> None:
        """A notification through one of Home Assistant's notify services (an action on notify)."""
        if not self.hass.services.has_service("notify", service):
            raise ValueError(f"Home Assistant has no notify service {service}")
        await self.act("notify", f"send a notification: {message[:120]}")
        await self.hass.services.async_call("notify", service, {"message": message, "title": title}, blocking=True)

    def _file(self, name: str) -> Path:
        plain = re.sub(r"[^\w\- .]", "", name).strip(" .") or "notes"  # a plain name in that folder, no folders
        return Path(self.hass.config.path("aac", "files")) / plain

    async def read_file(self, name: str) -> str:
        """A file in the shared folder <config>/aac/files ("" when there is none; needs files at read)."""
        self.need("files", "read")
        path = self._file(name)
        return await self.hass.async_add_executor_job(lambda: path.read_text(encoding="utf-8") if path.exists() else "")

    async def write_file(self, name: str, text: str, append: bool = False) -> str:
        """Write (or append to) a file in the shared folder (an action on files); returns the whole file afterwards."""
        await self.act("files", f"{'append to' if append else 'write'} {self._file(name).name}: {text.strip()[:120]}")
        path = self._file(name)

        def write() -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a" if append else "w", encoding="utf-8") as f:
                f.write(text)
            return path.read_text(encoding="utf-8")
        return await self.hass.async_add_executor_job(write)

    def listen(self, handler: Any) -> None:
        """The satellites' microphones: handler(source_id, chunk, meta) gets their audio (needs audio at read)."""
        self.need("audio", "read")
        self.core.subscribe("audio", self, handler)

    async def play(self, source_id: str, wav: bytes, text: str = "", queue: bool = False) -> bool:
        """Play on a satellite's speaker (an action on audio)."""
        await self.act("audio", f"play on {source_id}: {text[:120]}")
        return await self.core.audio.play(source_id, wav, text, queue=queue)

    async def stop_playing(self, source_id: str) -> bool:
        """Stop what a satellite plays (needs audio at ask or full; a stop never waits for a yes)."""
        self.need("audio", "ask")
        return await self.core.audio.stop(source_id)

    # ------------------------------------------------------------------ the model servers (servers at read, to act at full)
    async def servers(self) -> list[dict[str, Any]]:
        """The servers behind AAC's models and what they offer now (needs servers at read)."""
        self.need("servers", "read")
        return await self.core.pool.offers()

    async def server(self, action: str, aac_model: str, model: str) -> dict[str, Any]:
        """On the server of one of AAC's models (an action on servers): bench, use (the AAC model asks for that model
        from now on, and loads it), load, unload."""
        await self.act("servers", f"{action} {model} ({aac_model})")
        from ..models import Client  # noqa: PLC0415

        pool, core = self.core.pool, self.core
        if (found := pool.find(aac_model)) is None:
            raise ValueError(f"AAC has no model {aac_model!r}")
        if action == "bench":
            result = await pool.bench(Client(self.hass, found), model)
            core.save()
            return {"reads": f"{result['read']} tokens/s", "writes": f"{result['write']} tokens/s"}
        if action not in ("use", "load", "unload"):
            raise ValueError(f"no server action {action}")
        if action == "use":
            core.save_model({"id": found["id"], "model": model})
            core.save()
        await Client(self.hass, found).router_do("unload" if action == "unload" else "load", model)
        return {"done": f"{found['name']} uses {model}" if action == "use" else f"{action}ed {model}"}

    # ------------------------------------------------------------------ the setup (setup at read, to change it at full)
    # `where`: "now" (the running setup) or a slot: 1, 2, 3 (the owner's), 4 (the draft: a model builds there and
    # switches to it). A change to what runs or to the owner's slots is an action (at ask: the owner's yes); the draft
    # changes nothing that runs, so building there needs ask or full and asks nothing. Never a permission setting.
    def read_setup(self, where: str = "now") -> dict[str, Any]:
        """A setup: cards {id: {type, name, settings, inputs, outputs}} (also those not made yet, while a setup is being
        made), wires, positions, red wires (only what runs has them)."""
        self.need("setup", "read")
        domain, cards = self.domain, {}
        setup = domain.place(where)
        ports = domain._ports(where, setup["cards"])
        for card_id, spec in setup["cards"].items():
            made = domain.cards.get(card_id) if where == "now" else None
            kind = CARD_TYPES.get(spec.get("type", ""))
            ins, outs = ports(card_id) or ((dict(kind.inputs), dict(kind.outputs)) if kind else ({}, {}))
            cards[card_id] = {"type": spec.get("type"), "name": made.name if made else spec.get("title"),
                              "settings": dict(made.settings if made else spec.get("settings") or {}), "inputs": ins, "outputs": outs}
        return {"cards": cards, "wires": [dict(w) for w in setup["wires"]], "positions": dict(setup.get("positions") or {}),
                "red": [f"{r['from']} -> {r['to']}" for r in domain.red] if where == "now" else []}

    def wire_problem(self, wire: dict[str, str], where: str = "now") -> str:
        """Why a wire does not fit in a setup ("" when it does)."""
        self.need("setup", "read")
        return self.domain.wire_problem(where, wire)

    async def _building(self, where: str, what: str) -> None:
        if where == DRAFT:
            self.need("setup", "ask")
        else:
            await self.act("setup", what + ("" if where == "now" else f" (in slot {where})"))

    async def add_card(self, card_type: str, name: str = "", settings: dict[str, Any] | None = None,
                       at: dict[str, float] | None = None, where: str = "now") -> dict[str, Any]:
        """Add a card; returns it (in a slot: its id)."""
        if card_type not in CARD_TYPES:
            raise ValueError(f"no card type {card_type}")
        _owners(CARD_TYPES[card_type], settings or {})
        await self._building(where, f"add a {CARD_TYPES[card_type].title} card {name!r}")
        if where != "now":
            return {"id": self.domain.slot_add(where, card_type, name, settings or {}), "slot": where}
        return (await self.domain.add_card(card_type, name, settings or {}, at=at)).summary()

    async def change_card(self, card_id: str, name: str | None = None, settings: dict[str, Any] | None = None,
                          where: str = "now") -> dict[str, Any]:
        """Change a card's name or settings (its own, while it runs, it may always change)."""
        spec = self.domain.place(where)["cards"].get(card_id)
        if spec is None or (where == "now" and card_id not in self.domain.cards):
            raise ValueError("no such card")
        kind = CARD_TYPES.get(spec.get("type", ""))
        if kind is not None:
            _owners(kind, settings or {}, self.domain.cards[card_id].settings if where == "now" else spec.get("settings") or {})
        if card_id != self.id or where != "now":
            await self._building(where, f"change {spec.get('title') or card_id}: {', '.join([*(['name'] if name else []), *(settings or {})])}")
        if where != "now":
            self.domain.slot_update(where, card_id, name, settings)
            return {"id": card_id, "slot": where, **self.read_setup(where)["cards"][card_id]}
        return (await self.domain.update_card(card_id, name, settings)).summary()

    async def remove_card(self, card_id: str, where: str = "now") -> None:
        """Remove a card (with its wires)."""
        spec = self.domain.place(where)["cards"].get(card_id)
        if spec is None:
            raise ValueError("no such card")
        await self._building(where, f"remove {spec.get('title') or card_id}")
        if where != "now":
            self.domain.slot_remove(where, card_id)
            return
        await self.domain.remove_card(card_id)

    async def set_wires(self, wires: list[dict[str, str]], where: str = "now") -> list[dict[str, str]]:
        """A setup's wires, all of them; returns them as they are now (only fitting ones)."""
        now = self.domain.place(where)["wires"]
        added = [f"{w['from']} -> {w['to']}" for w in wires if w not in now]
        gone = [f"{w['from']} -> {w['to']}" for w in now if w not in wires]
        await self._building(where, "; ".join([*(["wire " + ", ".join(added)] if added else []), *(["unwire " + ", ".join(gone)] if gone else [])]) or "wires")
        if where != "now":
            return self.domain.slot_wires(where, wires)
        self.domain.set_wires(wires)
        return [dict(w) for w in self.domain.wires]

    async def copy_setup(self, source: str, slot: str) -> None:
        """A setup (the running one or a slot) into a slot: what that slot held is gone."""
        self.need("setup", "read")
        if slot == "now":
            raise ValueError("into a slot (switch runs one)")
        self.domain.place(slot)
        await self._building(slot, f"copy {'the running setup' if source == 'now' else f'slot {source}'} over slot {slot}")
        self.domain.save_slot(slot, source)

    def check_setup(self, where: str = "now") -> list[str]:
        """What is wrong with a setup (empty: nothing found); a slot is checked as this card would switch to it."""
        self.need("setup", "read")
        return self.domain.problems(where, keeper=self.id if where != "now" else None)

    async def switch_setup(self, slot: str, call_id: str | None = None) -> None:
        """Run a slot instead of the running setup (an action), once the domain is idle (the answer that asked for it
        written); then keep_setup must come within the core's keep time, else the setup before comes back. The
        conversation that holds the call goes along: `switched` gives it to this card in the new setup."""
        self.need("setup", "read")
        if slot == "now":
            raise ValueError("switch runs a slot")
        if self.domain.trial:
            raise ValueError("a switch waits for keep already")
        if found := self.domain.problems(slot, keeper=self.id):
            raise ValueError("not switched: " + "; ".join(found))
        await self.act("setup", f"switch to slot {slot} (the setup now comes back unless keep follows)")
        self.task(self.domain.switch_after(slot, self.id, call_id), background=True)

    def keep_setup(self) -> bool:
        """The switch stays (False: none waits)."""
        self.need("setup", "read")
        return self.domain.keep(by=self.name)

    def switched(self) -> dict[str, Any] | None:
        """The switch this card made, waiting for keep: {slot, seconds (left), context (once: the conversation that
        asked for it, a Context)}; None: none."""
        trial = self.domain.trial
        if not trial or trial["by"] != self.id or not self.may("setup", "read"):
            return None
        context = trial.pop("context", None)
        return {"slot": trial["slot"], "seconds": max(0, round(trial["until"] - time.time())),
                "context": Context.from_dict(context) if context else None}

    async def call_card(self, card_id: str, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        """Run another running card's function as a model would (only what it offers to models; that card's own
        permissions apply): to try it."""
        self.need("setup", "read")
        card = self.domain.cards.get(card_id)
        if card is None or card is self:
            raise ValueError("no such card (another running one)")
        if function not in card.offered():
            raise ValueError(f"{card.name} offers no function {function} (it offers: {', '.join(card.offered()) or 'none'})")
        self.log(f"Tries {card.name}: {function}", what="call")
        return await card.run(function, args or {}, context)

    async def unblock(self, a: str, b: str) -> bool:
        """A red wire carries again (an action on the setup)."""
        await self.act("setup", f"unblock the red wire {a} -> {b}")
        return self.domain.unblock(a, b, by=f"{self.name}, for a model")

    def tell(self, what: str = "card", **data: Any) -> None:
        """Open panels hear that something of this card changed (its page, the sidebar)."""
        self.core.notify(what, domain=self.domain.id, card=self.id, **data)

    def current(self) -> dict[str, Any]:
        """Its values now (see `values`)."""
        return {}

    def held(self, port: str) -> Context | None:
        """What a dotted wire from one of its outputs reads: by default the newest context that left there."""
        return self.domain.flow.held.get(f"{self.id}.{port}")

    # ------------------------------------------------------------------ data
    def default_data(self) -> dict[str, Any]:
        """Its data when it is new."""
        return {}

    async def load(self) -> None:
        """Read its own data."""
        if self.keeps_data:
            self.data = await self.domain.load_card_data(self.id) or self.default_data()

    async def save(self) -> None:
        """Keep its own data."""
        if self.keeps_data:
            await self.domain.save_card_data(self.id, self.data)

    @classmethod
    def renamed_settings(cls, settings: dict[str, Any], old: str, new: str) -> dict[str, Any]:
        """A card's id changed (it was renamed): this card's settings refer to it by the new one (a script's
        references; a card with ids in a setting of its own adds them)."""
        return renamed_refs(settings, old, new)

    async def renamed(self, old: str, new: str) -> None:
        """A card's id changed: its data refers to it by the new one (stored entries: authors, tags, calls)."""
        if self.keeps_data and (data := renamed_entries(self.data, old, new)) != self.data:
            self.data = data
            await self.save()

    # ------------------------------------------------------------------ ports and work
    @classmethod
    def ports_for(cls, settings: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
        """Inputs and outputs a card of this type has with these settings (a card may make them from its settings):
        what a setup that does not run yet, a slot, is checked by."""
        return dict(cls.inputs), dict(cls.outputs)

    def ports(self) -> tuple[dict[str, str], dict[str, str]]:
        """Its inputs and outputs now."""
        return type(self).ports_for(self.settings)

    def tag(self, name: str) -> str:
        """One of its tags with the card in front."""
        return f"{self.id}.{name}"

    async def handle(self, context: Context, port: str) -> None:
        """A context arrived at an input. The card acts when it holds something for it, and sends only when it did
        something (`send`); otherwise it ends here (the default)."""

    async def send(self, context: Context, port: str = "out") -> None:
        """Send a context from one of its outputs: every card wired there gets a copy."""
        await self.domain.flow.send(self, context, port)

    def read(self, port: str) -> list[Context]:
        """What the dotted wires into one of its inputs hold now."""
        return self.domain.flow.read(self.id, port)

    def stream_out(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        """Live audio out at one of its outputs (every card wired there that takes a stream gets it at once)."""
        self.domain.flow.stream(self.id, port, chunk, meta)

    def clip_out(self, port: str, clip: dict[str, Any]) -> None:
        """An audio clip out at one of its outputs ({pcm, rate, seconds, text, source, name, language}; {stop: True})."""
        self.domain.flow.clip(self.id, port, clip)

    def wired(self, port: str) -> bool:
        """Something is wired to one of its outputs."""
        return bool(self.domain.flow.wired_from(self.id, port))

    def wired_into(self, port: str) -> list[tuple[Card, str]]:
        """The cards (and their outputs) wired into one of its inputs."""
        return self.domain.flow.wired_into(self.id, port)

    def reaches(self, card_id: str, ports: list[str] | None = None) -> bool:
        """A context sent from its outputs (or these) can get to that card, straight or through others."""
        ports = ports or [p for p, kind in self.ports()[1].items() if kind == "text"]
        return card_id in self.domain.flow.reach(self.id, ports)

    def all_cards(self) -> list[Card]:
        """The cards of its domain (another card is used only by what it offers: functions, values, held())."""
        return list(self.domain.cards.values())

    def tools(self) -> list[Card]:
        """The cards whose functions it can call: reached from the outputs its calls leave by, and back."""
        return self.domain.flow.tools(self.id)

    async def stop_all(self) -> None:
        """Stop every card of the domain (the core function the Stop buttons use)."""
        await self.domain.stop()

    def live(self, context: Context, text: str, thinking: str = "") -> None:
        """Text still growing faster than snapshots go (a model writing): open panels show it, with the context's
        tags (a chat finds its turn by them)."""
        self.core.notify("delta", domain=self.domain.id, tags=sorted({e["tag"] for e in context.entries}),
                         text=text, thinking=thinking)

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        """One of its functions (a model's tool call, a Relay's script)."""
        raise ValueError(f"{self.name} has no function {function}")

    async def run(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        """How the core runs one of its functions: request_access (every card with needs) is the core's, so a card that
        may do nothing can still ask; the rest is the card's own `call`."""
        if function == "request_access" and self.needs:
            things = args.get("things") or []
            return self.domain.request(self, dict.fromkeys(things if isinstance(things, list) else [things],
                                                           str(args.get("level") or "read")), str(args.get("reason") or ""))
        return await self.call(function, args, context)

    def offered(self) -> dict[str, dict[str, Any]]:
        """Its functions as models see them: each ticked one with its name and description ("For models"; the card's
        own unless the owner wrote others) and its arguments."""
        own = self.settings.get("functions") or {}
        out = {}
        for fn, spec in self.functions.items():
            mine = own.get(fn) or {}
            if mine.get("on", self.offers):
                out[fn] = {"name": (mine.get("name") or "").strip() or fn,
                           "help": (mine.get("help") or "").strip() or spec.get("help", ""), "args": spec.get("args") or {}}
        return out

    async def stop(self) -> None:
        """Stop (a core function: every card of the domain gets it at once). A busy card marks what it was writing
        cut and, having done something, sends its context on; an idle card does nothing."""

    @classmethod
    def upgrade(cls, settings: dict[str, Any], made_with: str) -> dict[str, Any]:
        """A card was saved by another version of its file (`made_with`): its settings as this version wants them."""
        return settings

    async def upgrade_data(self, settings: dict[str, Any], made_with: str) -> None:
        """Made from a setup saved by another version of its file, after upgrade() and load(): its data as this version
        wants it (settings: as they were saved, e.g. entries that moved from its settings into its data)."""

    async def start(self) -> None:
        """The card is in a running domain: start its timers and listeners."""

    @property
    def busy(self) -> bool:
        """Working on something now (a newer version of its file waits until it is done)."""
        return False

    async def unload(self) -> None:
        """The card goes away (removed, changed, another domain runs): end its timers and listeners."""

    # ------------------------------------------------------------------ for the panel
    @classmethod
    def describe(cls) -> dict[str, Any]:
        """The type: for the Cards page's palette and the ? helper."""
        return {"type": cls.type, "title": cls.title, "category": cls.category, "help": cls.help,
                "inputs": cls.inputs, "outputs": cls.outputs,
                "settings": cls.settings_schema, "tags": cls.tags, "functions": cls.functions, "values": cls.values, "ports_help": cls.ports_help, "ui": cls.ui_url,
                "version": cls.version, "offers": cls.offers}

    def nav(self) -> dict[str, Any] | None:
        """Its entries in the sidebar (a card with a page): {"entries": [{id, title, children?}], "add": "New …",
        "fold": "arrow" (its group stays open or closed by the arrow; default: opens while in use)}; None: one item there."""
        return None

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        """A request from its own page (its .js): what the page needs that is not a function for models. Every card
        with needs: "access_items", the things its May use can be about (what the domain lets exist), each with the
        domain's level as its cap."""
        if action == "access_items" and self.needs:
            return await self.core.home.catalog(sorted(self.needs))
        raise ValueError(f"{self.type} has no page action {action}")

    @property
    def watched(self) -> bool:
        """An open Cards page shows its view now (compute frames only then)."""
        return self.core.watched(self.id)

    def show(self, data: Any) -> None:
        """A frame for its view (only sent while a Cards page shows it; keep it small, a few times a second)."""
        if self.watched:
            self.core.notify("view", domain=self.domain.id, card=self.id, data=data)

    def _view(self) -> dict[str, Any] | None:
        return {"url": self.view_url, "element": f"aac-view-{self.type.replace('_', '-')}", **self.view} if self.view_url else None

    def summary(self) -> dict[str, Any]:
        """This card: for the Cards page."""
        ins, outs = self.ports()
        return {"id": self.id, "type": self.type, "title": self.name, "settings": self.settings, "functions": self.functions,
                "inputs": ins, "outputs": outs, "ui": self.ui_url, "view": self._view(),
                "has_data": self.keeps_data and self.data != self.default_data(),
                **({"tools": [c.id for c in self.tools()]} if self.uses_tools else {}),
                "tags": {self.tag(t): what for t, what in self.tags.items()}}


def _owners(kind: type[Card], settings: dict[str, Any], now: dict[str, Any] | None = None) -> None:
    """Raises ValueError for a setting the type does not have, PermissionError when a change would set a permission
    setting (fields marked so: the owner's alone)."""
    if extra := [k for k in settings if k not in kind.settings_schema]:
        raise ValueError(f"a {kind.title} card has no setting {', '.join(extra)} (its settings: "
                         f"{', '.join(k for k, f in kind.settings_schema.items() if not f.get('permission'))}; see help {kind.type})")
    if found := [k for k, v in settings.items() if (kind.settings_schema.get(k) or {}).get("permission")
                 and (now is None or now.get(k) != v)]:
        raise PermissionError(f"{', '.join(found)}: permissions are the owner's to set (the Permissions page), not a card's")


def tool_spec(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """A card function as an OpenAI tool, under the name the model sees (spec: as Card.offered gives it)."""
    props, required = {}, []
    for arg, a in (spec.get("args") or {}).items():
        kind = a.get("type", "string")
        props[arg] = {"type": kind, "description": a.get("help", "")}
        if kind == "array":
            props[arg]["items"] = {"type": a.get("items", "string")}
        if a.get("required"):
            required.append(arg)
    return {"type": "function", "function": {
        "name": name, "description": spec.get("help", ""),
        "parameters": {"type": "object", "properties": props, **({"required": required} if required else {})}}}


def load_all(folder: str) -> None:
    """Load every card type from the cards folder (`<config>/aac/cards/`): a card is a file (`decide.py`) or, when it
    has more files, a folder (`chat/card.py` with `page.js`, `view.js` …). AAC's own cards are copied there first unless
    a newer version is there (a download); runs in the executor (it reads and writes files). One that fails is logged;
    the others load."""
    cards = Path(folder)
    cards.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).parent
    for shipped in sorted(here.iterdir()):
        if shipped.suffix == ".py" and shipped.name != "__init__.py":
            there = cards / shipped.name
            if not there.exists() or (_version(there) <= _version(shipped) and there.read_bytes() != shipped.read_bytes()):
                LOGGER.info("AAC: card file %s %s", shipped.name, "updated" if there.exists() else "added")
                shutil.copyfile(shipped, there)
        elif (shipped / "card.py").exists():
            there = cards / shipped.name
            if not (there / "card.py").exists() or (_version(there / "card.py") <= _version(shipped / "card.py")
                                                   and _files(there) != _files(shipped)):
                LOGGER.info("AAC: card folder %s %s", shipped.name, "updated" if there.exists() else "added")
                shutil.rmtree(there, ignore_errors=True)
                shutil.copytree(shipped, there, ignore=shutil.ignore_patterns("__pycache__"))
    package = sys.modules.setdefault("aac_cards", types.ModuleType("aac_cards"))  # card files can import each other
    package.__path__ = [str(cards)]
    for name, file in _card_files(cards):
        _load(name, file)


def reload_changed(folder: str) -> set[str]:
    """While AAC runs: card files whose VERSION went up, and new ones, are loaded again; a deleted one's card types go
    (running cards stay until AAC restarts). Returns the card types that changed. Runs in the executor."""
    changed: set[str] = set()
    files = _card_files(Path(folder))
    for gone in set(LOADED) - {name for name, _ in files}:
        LOGGER.info("AAC: card file of %s was deleted", gone)
        LOADED.pop(gone)
        for t in [t for t, cls in CARD_TYPES.items() if cls.__module__ == gone]:
            CARD_TYPES.pop(t)
            changed.add(t)
    for name, file in files:
        if name in LOADED and _version(file) <= LOADED[name]:
            continue
        LOGGER.info("AAC: card file %s %s", file, "changed" if name in LOADED else "is new")
        if _load(name, file):
            changed |= {t for t, cls in CARD_TYPES.items() if cls.__module__ == name}
    return changed


def _card_files(cards: Path) -> list[tuple[str, Path]]:
    """Every card in the folder: (module name, its Python file): a file, or a folder's card.py."""
    found = []
    for path in sorted(cards.iterdir()):
        if path.suffix == ".py":
            found.append((f"aac_cards.{path.stem}", path))
        elif (path / "card.py").exists():
            found.append((f"aac_cards.{path.name}", path / "card.py"))
    return found


def _files(folder: Path) -> dict[str, bytes]:
    """A card folder's files (to see whether it changed)."""
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


def _version(path: Path) -> tuple[int, ...]:
    """A card file's VERSION ("1.2.0" → (1, 2, 0); none: (0,))."""
    found = re.search(r"""^VERSION\s*=\s*["']([\d.]+)["']""", path.read_text(encoding="utf-8"), re.M)
    return tuple(int(x) for x in found.group(1).split(".") if x) if found else (0,)


def _load(name: str, file: Path) -> bool:
    """Run a card file (its @register cards replace those of the same type); remembers its VERSION."""
    LOADED[name] = _version(file)  # also when it fails: a broken file is tried again when its VERSION goes up
    try:  # compiled fresh each time (no cached bytecode: a file changed within the same second must not run old code)
        module = types.ModuleType(name)
        module.__file__ = str(file)
        if file.name == "card.py":  # a folder: its other Python files can be imported relative to it
            module.__path__ = [str(file.parent)]
        sys.modules[name] = module
        exec(compile(file.read_text(encoding="utf-8"), str(file), "exec"), module.__dict__)  # noqa: S102 - the cards folder is the owner's
    except Exception:  # noqa: BLE001 - one broken card file must not stop the others
        LOGGER.exception("AAC: the card file %s could not be loaded", file)
        return False
    return True