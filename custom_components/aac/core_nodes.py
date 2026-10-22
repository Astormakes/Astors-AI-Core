"""The core's own cards in the message graph (spec/v1.md, flow.py).

- sources: the panel chat, voice, HA Assist (the heartbeat and the triggers are sources in their modules)
- transforms (in the line, they add to the message): the agent's prompt; Instructions and Context (added ones)
- cards attached to a model (the dotted line, its menu): the home, setup (only with the owner in the chat),
  Home Assistant's own Assist tools
- the model (the standard one; more can be added, each with its own tier and instructions), Decide (named outputs),
  Reply (brings the answer home)
Automatic wiring: every source through the transforms (by order) into the model, the model into Reply and the
sinks, every attached card onto the model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT

from .flow import DECIDE_SETTINGS, MODEL, MODEL_SETTINGS, REPLY
from .home_api import API_ID as HOME_API_ID
from .request import MESSAGE, SOURCES, TOOL, ModelRequest

SOURCE_HELP = {
    "chat": "Where a message from the AAC panel's chat comes in. Wire it through the cards that should prepare it into a model.",
    "voice": "Where a spoken line comes in that the wake word or the companion decided to answer; the answer is spoken back at that microphone.",
    "assist": "Where a message comes in from Home Assistant itself: an Assist pipeline, a voice satellite or an automation that uses AAC as its conversation agent.",
}
from .setup_api import API_ID as SETUP_API_ID, owner_present
from .streams import Node

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import AACConfigEntry

IN, OUT = {"in": MESSAGE}, {"out": MESSAGE}
CARD = {"tools": TOOL}


def model_node(inst_id: str = MODEL, title: str = "Model", settings: dict[str, Any] | None = None) -> Node:
    """A model card: a message in, the answered message out; cards attach to its tool input."""
    return Node(id=inst_id, title=title, inputs={"in": MESSAGE, "tools": TOOL}, outputs=OUT, role="model",
                description="Answers the message with a model from the pool. The cards attached to it (dotted lines) are "
                            "what it may use: their tools form its menu. Its answer leaves as the message again, so it can "
                            "go on into more cards, a Decide or another model. Automatic messages wait here until a model "
                            "is free, the most important first.",
                settings=settings or {}, make=None if settings is not None else (lambda i, t, s: model_node(i, t, s)),
                settings_schema=MODEL_SETTINGS)


def decide_node(inst_id: str, title: str, settings: dict[str, Any]) -> Node:
    """A Decide card: its outputs are the ones the owner named."""
    names = [o["name"] for o in settings.get("outputs") or [] if isinstance(o, dict) and o.get("name")] or ["yes", "no"]
    return Node(id=inst_id, title=title, inputs=IN, outputs={n: MESSAGE for n in names}, role="decide", settings=settings,
                description=DECIDE_HELP)


DECIDE_HELP = ("Sends the message on through one of its named outputs. A model reads the message and your description of "
               "each output and picks one; the server only allows your output names. If the decision fails, the message "
               "takes the default output. It does not change the message.")


def core_nodes(hass: HomeAssistant, entry: AACConfigEntry) -> list[Node]:
    """The core's cards."""

    async def agent(message: ModelRequest) -> None:
        if prompt := (message.domain.settings.master.get(CONF_PROMPT) or "").strip():
            message.system.append(prompt)

    async def home(message: ModelRequest) -> None:
        message.add_tools(message.domain.api_id(HOME_API_ID))  # within the owner's access list

    async def setup(message: ModelRequest) -> None:
        if await owner_present(hass, message.user_id):  # with the owner present it helps set itself up
            message.add_tools(message.domain.api_id(SETUP_API_ID))

    async def ha_tools(message: ModelRequest) -> None:
        for api_id in message.domain.settings.master.get(CONF_LLM_HASS_API) or []:  # if the owner chose them
            message.add_tools(api_id)

    def card(card_id: str, title: str, provide, help_text: str) -> Node:
        def make(inst_id: str, inst_title: str, settings: dict) -> Node:  # the same card on another model
            return Node(id=inst_id, title=inst_title, outputs=CARD, role="tool", provide=provide, settings=settings)
        return Node(id=card_id, title=title, outputs=CARD, role="tool", provide=provide, make=make, description=help_text)

    def agent_make(inst_id: str, title: str, settings: dict) -> Node:
        async def annotate(message: ModelRequest) -> None:  # its own prompt instead of the domain's
            if prompt := (settings.get("prompt") or "").strip():
                message.system.append(prompt)
        return Node(id=inst_id, title=title, inputs=IN, outputs=OUT, adds=["system"], order=10, annotate=annotate,
                    role="transform", settings=settings)

    def text_make(field: str):
        def make(inst_id: str, title: str, settings: dict) -> Node:
            async def annotate(message: ModelRequest) -> None:
                if text := (settings.get("text") or "").strip():
                    getattr(message, field).append(text)
            return Node(id=inst_id, title=title, inputs=IN, outputs=OUT, adds=[field], order=15, annotate=annotate,
                        role="transform", settings=settings)
        return make

    text = {"type": "longtext", "title": "Text", "default": "", "rows": 5}
    return [
        # Sources (the heartbeat and the triggers are sources in their modules)
        *(Node(id=sid, title=title, outputs=OUT, role="source", description=SOURCE_HELP[sid]) for sid, title in SOURCES.items()),
        # In the line: they add to the message
        Node(id="agent", title="Agent", inputs=IN, outputs=OUT, adds=["system"], order=10, annotate=agent, role="transform",
             description="Adds the assistant's role and manner to the system prompt: the domain's agent prompt (SETTINGS → "
                         "Master agent → Prompt, e.g. \"You are AAC, a calm home companion …\"). An added Agent card has a "
                         "prompt of its own, e.g. for a second model with another job.",
             make=agent_make, settings_schema={"prompt": {**text, "title": "Prompt",
                                                          "help": "This agent's own prompt (the domain's is on SETTINGS → Master agent)."}}),
        Node(id="instructions", title="Instructions", inputs=IN, outputs=OUT, adds=["system"], template=True, role="transform",
             description="Adds your own text to the system prompt, e.g. \"Answer in one sentence\" or what a second model "
                         "should check. Stable text, good for the server's prompt cache.",
             make=text_make("system"), settings_schema={"text": {**text, "help": "Added to the system prompt (stable: good for the prompt cache)."}}),
        Node(id="context", title="Context", inputs=IN, outputs=OUT, adds=["context"], template=True, role="transform",
             description="Hands your own text along with every message (not in the system prompt), e.g. \"The user is in "
                         "the bedroom\" for a way that only that room's microphone uses.",
             make=text_make("context"), settings_schema={"text": {**text, "help": "Goes along with every message (e.g. who uses this way)."}}),
        # Attached to a model (its menu)
        card("home", "Home", home, "Attach to a model to let it see and switch the home: find devices and sensors, "
             "read their state, switch them, ask you for access. Only within the entities you allowed (SETTINGS → Entities)."),
        card("setup", "Setup (owner present)", setup, "Attach to a model to let it help set AAC itself up (models, memory, "
             "audio, modules, availability rules). Only offered while you are in the chat, never by voice or in automatic runs."),
        card("ha_tools", "Home Assistant tools", ha_tools, "Attach to a model to give it Home Assistant's own Assist tools "
             "(live states, timers, HA's to-do lists), if you chose them under SETTINGS → Master agent."),
        # The model, Decide, Reply
        model_node(),
        Node(id="decide", title="Decide", inputs=IN, outputs={"yes": MESSAGE, "no": MESSAGE}, role="decide", template=True,
             description=DECIDE_HELP,
             make=decide_node, settings_schema=DECIDE_SETTINGS),
        Node(id=REPLY, title="Reply", inputs=IN, role="reply",
             description="Brings the message home: to the chat, microphone or Home Assistant it came from, however many "
                         "cards and models it passed. An automatic finding (heartbeat, trigger) goes to its chat; silence "
                         "(NOTHING) stays silent."),
    ]
