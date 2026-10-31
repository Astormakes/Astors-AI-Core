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

if TYPE_CHECKING:
    from ..core import Domain
    from ..flow import Step
    from ..context import Context

CARD_TYPES: dict[str, type[Card]] = {}
LOADED: dict[str, tuple[int, ...]] = {}  # card module → the VERSION it was loaded with
UI_URL = "/aac_cards"  # a card file's UI (<stem>.js next to it) is served here
FUNCTIONS = {"type": "functions", "title": "For models", "default": {}, "fold": "For models", "permission": True,
             "help": "A Model wired to this card and back has these functions as tools: each ticked one, under the name "
                     "and with the description written here (Reset: the card's own). The card's id never reaches the model."}
CATEGORIES = ["In and out", "Text", "Logic", "Models", "Data", "Home Assistant", "Audio", "Other"]


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
    square: ClassVar[set[str]] = set()  # outputs where the card picks one wire (the others copy to every wire)
    settings_schema: ClassVar[dict[str, dict[str, Any]]] = {}
    tags: ClassVar[dict[str, str]] = {}  # tag (without the card) → what it marks
    functions: ClassVar[dict[str, dict[str, Any]]] = {}  # name → {"help", "args": {name: {"type", "help", "required"}}}
    ports_help: ClassVar[dict[str, str]] = {}  # port → one sentence: what goes in or comes out there
    values: ClassVar[dict[str, str]] = {}  # name → what it is: read in templates as {{ <card>.<name> }}
    offers: ClassVar[bool] = True  # its functions are ticked for models until the owner says otherwise
    dynamic_functions: ClassVar[bool] = False  # its functions come from its settings (a Relay's @new blocks)
    uses_tools: ClassVar[bool] = False  # it calls the functions of the cards in its loop (a Model): the panel lists them
    ui: ClassVar[bool] = False  # a page of its own: the element aac-card-<type> from the .js next to its file
    ui_url: ClassVar[str | None] = None  # where the panel loads it (set when the file is loaded)
    view: ClassVar[dict[str, Any] | None] = None  # a view inside the card in the grid ({"height": px}): view.js in its folder
    view_url: ClassVar[str | None] = None
    keeps_data: ClassVar[bool] = False
    version: ClassVar[str] = "0"  # its file's VERSION

    def __init__(self, domain: Domain, card_id: str, title: str, settings: dict[str, Any] | None) -> None:
        """Initialize."""
        self.domain, self.core, self.hass = domain, domain.core, domain.core.hass
        self.id = card_id
        self.name = title or type(self).title
        self.settings = {k: f.get("default") for k, f in self.settings_schema.items()} | dict(settings or {})
        self.data: dict[str, Any] = {}

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

    # ------------------------------------------------------------------ ports and work
    def ports(self) -> tuple[dict[str, str], dict[str, str], set[str]]:
        """Inputs, outputs, square outputs (a card may make them from its settings)."""
        return dict(self.inputs), dict(self.outputs), set(self.square)

    def tag(self, name: str) -> str:
        """One of its tags with the card in front."""
        return f"{self.id}.{name}"

    async def handle(self, context: Context, port: str, step: Step) -> list[Any] | None:
        """A context arrived. Return None: on to every round output; []: it ends here; [port] or [(port, card)]:
        the outputs (or the one wire) it takes."""
        return None

    async def progress(self, context: Context, port: str) -> list[Any] | None:
        """A snapshot of a turn still being worked on (a model wrote a sentence, called a tool): the same turn comes
        again, and finished, to handle(). By default it passes on unchanged to the round outputs; a card that shows or
        speaks things as they come (Chat, Text to speech) answers it. Returns like handle()."""
        return None

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

    async def stop(self, stop: dict[str, Any] | None = None) -> bool:
        """A stop arrived (`stop`: its entry; None: the Send stop button). A busy card closes its open block as cut
        off, appends the stop and sends its own context on; it says True. Not busy: False (the stop goes on)."""
        return False

    @classmethod
    def upgrade(cls, settings: dict[str, Any], made_with: str) -> dict[str, Any]:
        """A card was saved by another version of its file (`made_with`): its settings as this version wants them."""
        return settings

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
                "inputs": cls.inputs, "outputs": cls.outputs, "square": sorted(cls.square),
                "settings": cls.settings_schema, "tags": cls.tags, "functions": cls.functions, "values": cls.values, "ports_help": cls.ports_help, "ui": cls.ui_url,
                "version": cls.version, "offers": cls.offers}

    def nav(self) -> dict[str, Any] | None:
        """Its entries in the sidebar (a card with a page): {"entries": [{id, title, children?}], "add": "New …",
        "fold": "arrow" (its group stays open or closed by the arrow; default: opens while in use)}; None: one item there."""
        return None

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        """A request from its own page (its .js): what the page needs that is not a function for models."""
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
        ins, outs, square = self.ports()
        return {"id": self.id, "type": self.type, "title": self.name, "settings": self.settings, "functions": self.functions,
                "inputs": ins, "outputs": outs, "square": sorted(square), "ui": self.ui_url, "view": self._view(),
                **({"tools": [c.id for c in self.domain.flow.tools(self.id)]} if self.uses_tools else {}),
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