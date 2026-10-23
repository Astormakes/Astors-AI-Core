"""Cards (docs/v2 page 2): every behaviour of AAC is a card the owner can see, wire, change and remove.

A card type declares its ports, settings, the tags it writes and its functions (exactly documented: models call
them as tools, a Conditional card calls them as actions). A card instance lives in one domain and owns its data.
New card types are registered here; adding one needs no restart of anything else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from ..core import Domain
    from ..flow import Step
    from ..message import Message

CARD_TYPES: dict[str, type[Card]] = {}
CATEGORIES = ["In and out", "Text", "Logic", "Models", "Data", "Home Assistant", "Audio", "Other"]


def register(cls: type[Card]) -> type[Card]:
    """Make a card type available (decorator)."""
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
    values: ClassVar[dict[str, str]] = {}  # name → what it is: read in templates as {{ <card>.<name> }}
    page: ClassVar[str | None] = None  # a page of its own in the sidebar ("chat", "lists")
    keeps_data: ClassVar[bool] = False

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

    async def handle(self, message: Message, port: str, step: Step) -> list[Any] | None:
        """A message arrived. Return None: on to every round output; []: it ends here; [port] or [(port, card)]:
        the outputs (or the one wire) it takes."""
        return None

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
        """One of its functions (a model's tool call, a Conditional's action)."""
        raise ValueError(f"{self.name} has no function {function}")

    async def stop(self) -> None:
        """A stop arrived: drop what it is doing."""

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
                "settings": cls.settings_schema, "tags": cls.tags, "functions": cls.functions, "values": cls.values, "page": cls.page}

    def summary(self) -> dict[str, Any]:
        """This card: for the node view."""
        ins, outs, square = self.ports()
        return {"id": self.id, "type": self.type, "title": self.name, "settings": self.settings,
                "inputs": ins, "outputs": outs, "square": sorted(square), "page": self.page,
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


def load_all() -> None:
    """Import the built-in card types (each registers itself)."""
    from . import (  # noqa: F401, PLC0415
        assist, audio, auto, buffer, builder, calendar, chat, conditional, decide, homeassistant, lists, memory, model, pool, relay, tools)
