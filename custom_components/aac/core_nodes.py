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
from homeassistant.helpers import llm
import voluptuous as vol

from .const import (
    CONF_MAX_TOKENS,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
)

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


def replace_edit(node: Node, edit) -> Node:
    """The standard model with its settings editable (temperature, answer length, thinking)."""
    node.edit = edit
    return node


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

    def master(domain_id: str) -> dict[str, Any]:
        return entry.runtime_data.domain(domain_id).settings.master

    def editing(schema: dict[str, Any], keys: dict[str, tuple[str, Any, Any]]):
        """A core card's settings in the node view, kept in the domain's agent settings (was SETTINGS → Master
        agent). keys: setting → (stored key, default, validator); a wrong value raises vol.Invalid."""
        def get(domain_id: str) -> dict[str, Any]:
            return {k: master(domain_id).get(conf, default) for k, (conf, default, _) in keys.items()}

        async def put(domain_id: str, values: dict[str, Any]) -> None:
            await entry.runtime_data.domain(domain_id).settings.async_update_master(
                {keys[k][0]: keys[k][2](v) for k, v in values.items() if k in keys})
        return schema, get, put

    agent_edit = editing({"prompt": {"type": "longtext", "title": "Prompt", "rows": 8,
                                     "help": "The assistant's role and manner, e.g. \"You are AAC, a calm home companion.\""}},
                         {"prompt": (CONF_PROMPT, "", vol.Coerce(str))})
    model_edit = editing({
        "temperature": {"type": "number", "title": "Temperature", "help": "0 = always the same answer, higher = more varied (0–2)."},
        "max_tokens": {"type": "number", "title": "Longest answer (tokens)", "help": "16–131072."},
        "thinking": {"type": "bool", "title": "Let the model think first (slower; llama.cpp / vLLM)"},
    }, {"temperature": (CONF_TEMPERATURE, DEFAULT_TEMPERATURE, vol.All(vol.Coerce(float), vol.Range(0, 2))),
        "max_tokens": (CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS, vol.All(vol.Coerce(int), vol.Range(16, 131072))),
        "thinking": (CONF_THINKING, DEFAULT_THINKING, vol.Boolean())})
    ha_apis = [[api.id, api.name] for api in llm.async_get_apis(hass) if not api.id.startswith("aac_")]
    ha_edit = editing({"apis": {"type": "multiselect", "title": "Home Assistant's tool sets", "options": ha_apis,
                                "help": "Assist gives live states, timers and HA's to-do lists. None ticked: the card adds nothing."}},
                      {"apis": (CONF_LLM_HASS_API, [], vol.All([vol.In([a for a, _ in ha_apis])]))})

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

    def card(card_id: str, title: str, provide, help_text: str, edit=None) -> Node:
        def make(inst_id: str, inst_title: str, settings: dict) -> Node:  # the same card on another model
            return Node(id=inst_id, title=inst_title, outputs=CARD, role="tool", provide=provide, settings=settings)
        return Node(id=card_id, title=title, outputs=CARD, role="tool", provide=provide, make=make, description=help_text,
                    edit=edit)

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
             description="Adds the assistant's role and manner to the system prompt: the domain's agent prompt (in this card's settings, "
                         "e.g. \"You are AAC, a calm home companion …\"). An added Agent card has a "
                         "prompt of its own, e.g. for a second model with another job.", edit=agent_edit,
             make=agent_make, settings_schema={"prompt": {**text, "title": "Prompt",
                                                          "help": "This agent's own prompt (the domain's is on the standard Agent card)."}}),
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
             "(live states, timers, HA's to-do lists): tick which ones in this card's settings.", ha_edit),
        # The model, Decide, Reply
        replace_edit(model_node(), model_edit),
        Node(id="decide", title="Decide", inputs=IN, outputs={"yes": MESSAGE, "no": MESSAGE}, role="decide", template=True,
             description=DECIDE_HELP,
             make=decide_node, settings_schema=DECIDE_SETTINGS),
        Node(id=REPLY, title="Reply", inputs=IN, role="reply",
             description="Brings the message home: to the chat, microphone or Home Assistant it came from, however many "
                         "cards and models it passed. An automatic finding (heartbeat, trigger) goes to its chat; silence "
                         "(NOTHING) stays silent."),
    ]
