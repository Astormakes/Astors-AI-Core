"""Cards (docs/v2 page 2): every behaviour of AAC is a card the owner can see, wire, change and remove.

A card type declares its ports, settings, the tags it writes and its functions (exactly documented: models call
them as tools, a Conditional card calls them as actions). A card instance lives in one domain and owns its data.

Every card type is a file in `<config>/aac/cards/` (spec/cards.md). AAC's own card files (next to this one) are
copied there unless a newer version of the file is there (each file has a VERSION). Cards only use the core's
public interface (`custom_components.aac.api`), never each other's types.
"""

from __future__ import annotations

import importlib.util
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
UI_URL = "/aac_cards"  # a card file's UI (<stem>.js next to it) is served here
CATEGORIES = ["In and out", "Text", "Logic", "Models", "Data", "Home Assistant", "Audio", "Other"]


def register(cls: type[Card]) -> type[Card]:
    """Make a card type available (decorator); a later one with the same type replaces it (your folder after AAC's)."""
    if (old := CARD_TYPES.get(cls.type)) is not None and old.__module__ != cls.__module__:
        LOGGER.info("AAC: card type %s from %s replaces the one from %s", cls.type, cls.__module__, old.__module__)
    module = sys.modules.get(cls.__module__)
    cls.version = str(getattr(module, "VERSION", cls.version))
    file = Path(getattr(module, "__file__", "") or "")
    cls.ui_url = f"{UI_URL}/{file.stem}.js?v={cls.version}" if cls.ui and file.with_suffix(".js").exists() else None
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
    ui: ClassVar[bool] = False  # a page of its own: the element aac-card-<type> from the .js next to its file
    ui_url: ClassVar[str | None] = None  # where the panel loads it (set when the file is loaded)
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

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        """One of its functions (a model's tool call, a Conditional's action)."""
        raise ValueError(f"{self.name} has no function {function}")

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

    async def unload(self) -> None:
        """The card goes away (removed, changed, another domain runs): end its timers and listeners."""

    # ------------------------------------------------------------------ for the panel
    @classmethod
    def describe(cls) -> dict[str, Any]:
        """The type: for the node view's palette and the ? helper."""
        return {"type": cls.type, "title": cls.title, "category": cls.category, "help": cls.help,
                "inputs": cls.inputs, "outputs": cls.outputs, "square": sorted(cls.square),
                "settings": cls.settings_schema, "tags": cls.tags, "functions": cls.functions, "values": cls.values, "ports_help": cls.ports_help, "ui": cls.ui_url,
                "version": cls.version}

    def nav(self) -> dict[str, Any] | None:
        """Its entries in the sidebar (a card with a page): {"entries": [{id, title, children?}], "add": "New …"};
        None: the card is one item there."""
        return None

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        """A request from its own page (its .js): what the page needs that is not a function for models."""
        raise ValueError(f"{self.type} has no page action {action}")

    def summary(self) -> dict[str, Any]:
        """This card: for the node view."""
        ins, outs, square = self.ports()
        return {"id": self.id, "type": self.type, "title": self.name, "settings": self.settings,
                "inputs": ins, "outputs": outs, "square": sorted(square), "ui": self.ui_url,
                "tags": {self.tag(t): what for t, what in self.tags.items()}}


def tool_spec(card: Card, name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """A card function as an OpenAI tool (`<card>__<function>`)."""
    props, required = {}, []
    for arg, a in (spec.get("args") or {}).items():
        kind = a.get("type", "string")
        props[arg] = {"type": kind, "description": a.get("help", "")}
        if kind == "array":
            props[arg]["items"] = {"type": a.get("items", "string")}
        if a.get("required"):
            required.append(arg)
    return {"type": "function", "function": {
        "name": f"{card.id}__{name}", "description": f"{card.name}: {spec.get('help', '')}",
        "parameters": {"type": "object", "properties": props, **({"required": required} if required else {})}}}


def load_all(folder: str) -> None:
    """Load every card type from the cards folder (`<config>/aac/cards/`). AAC's own card files are copied there first
    unless a newer version of the file is there (a download); runs in the executor (it reads and writes files). A file
    that fails is logged; the others load."""
    cards = Path(folder)
    cards.mkdir(parents=True, exist_ok=True)
    for shipped in sorted(Path(__file__).parent.glob("*.py")):
        if shipped.name == "__init__.py":
            continue
        there = cards / shipped.name
        if not there.exists() or (_version(there) <= _version(shipped) and there.read_bytes() != shipped.read_bytes()):
            LOGGER.info("AAC: card file %s %s", shipped.name, "updated" if there.exists() else "added")
            shutil.copyfile(shipped, there)
        ui, ui_there = shipped.with_suffix(".js"), there.with_suffix(".js")  # its page goes with it
        if ui.exists() and _version(there) == _version(shipped) and (not ui_there.exists() or ui_there.read_bytes() != ui.read_bytes()):
            shutil.copyfile(ui, ui_there)
    package = sys.modules.setdefault("aac_cards", types.ModuleType("aac_cards"))  # card files can import each other
    package.__path__ = [str(cards)]
    for path in sorted(cards.glob("*.py")):
        def load(path=path):
            spec = importlib.util.spec_from_file_location(f"aac_cards.{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        _load(load, str(path))


def _version(path: Path) -> tuple[int, ...]:
    """A card file's VERSION ("1.2.0" → (1, 2, 0); none: (0,))."""
    found = re.search(r"""^VERSION\s*=\s*["']([\d.]+)["']""", path.read_text(encoding="utf-8"), re.M)
    return tuple(int(x) for x in found.group(1).split(".") if x) if found else (0,)


def _load(load, name: str) -> None:
    try:
        load()
    except Exception:  # noqa: BLE001 - one broken card file must not stop the others
        LOGGER.exception("AAC: the card file %s could not be loaded", name)