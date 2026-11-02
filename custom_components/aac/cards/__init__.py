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
import types
from typing import TYPE_CHECKING, Any, ClassVar

from ..const import LOGGER
from ..permissions import Home, catalog, kinds_of, lower, rank

if TYPE_CHECKING:
    from ..core import Domain
    from ..context import Context

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
ACCESS = {"type": "access", "title": "May use", "default": {}, "permission": True,
          "help": "What this card may reach outside AAC, each thing hidden, list (it may know it exists), read or full; "
                  "never more than the domain allows (its Permissions page). Not set: hidden."}
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
        own `access`; hidden for a kind it does not need."""
        if kinds_of(thing) not in self.needs:
            return "hidden"
        return lower(self.domain.allows(thing), (self.settings.get("access") or {}).get(thing))

    def may(self, thing: str, level: str) -> bool:
        """It may reach the thing at least this far."""
        return rank(self.level(thing)) >= rank(level)

    def need(self, thing: str, level: str) -> None:
        """Raises PermissionError (with what to do) unless it may reach the thing this far."""
        if not self.may(thing, level):
            mine, domain = (self.settings.get("access") or {}).get(thing) or "hidden", self.domain.allows(thing)
            raise PermissionError(f"{self.name} may not use {thing} at {level} (the domain allows {domain}, the card "
                                  f"{mine}): the owner allows it on the Permissions page")

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
        """A notification through one of Home Assistant's notify services (needs notify at full)."""
        self.need("notify", "full")
        if not self.hass.services.has_service("notify", service):
            raise ValueError(f"Home Assistant has no notify service {service}")
        await self.hass.services.async_call("notify", service, {"message": message, "title": title}, blocking=True)

    def files(self, write: bool = False) -> Path:
        """The shared files folder <config>/aac/files (needs files at read; to write, at full)."""
        self.need("files", "full" if write else "read")
        return Path(self.hass.config.path("aac", "files"))

    def listen(self, handler: Any) -> None:
        """The satellites' microphones: handler(source_id, chunk, meta) gets their audio (needs audio at read)."""
        self.need("audio", "read")
        self.core.subscribe("audio", self, handler)

    async def play(self, source_id: str, wav: bytes, text: str = "", queue: bool = False) -> bool:
        """Play on a satellite's speaker (needs audio at full)."""
        self.need("audio", "full")
        return await self.core.audio.play(source_id, wav, text, queue=queue)

    async def stop_playing(self, source_id: str) -> bool:
        """Stop what a satellite plays (needs audio at full)."""
        self.need("audio", "full")
        return await self.core.audio.stop(source_id)

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
    def ports(self) -> tuple[dict[str, str], dict[str, str]]:
        """Inputs and outputs (a card may make them from its settings)."""
        return dict(self.inputs), dict(self.outputs)

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
            return [{**x, "cap": cap} for x in catalog(self.hass, set(self.needs))
                    if (cap := self.domain.allows(x["id"])) != "hidden"]
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