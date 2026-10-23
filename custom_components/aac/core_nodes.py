"""The core's own nodes (spec/graph.md).

- endpoints (an output for what comes in, an input for what goes back): the panel chat, voice, HA Assist
  (the heartbeat and the triggers are outputs only, in their modules)
- cards with a standing text: Trailer and Context (added ones); the Header is the charter module
- the model card (square output: its wired nodes are its tools), Decide (named outputs)
- nodes with tools (loops): the home, setup (only with the owner in the chat), Home Assistant's Assist tools
Automatic wiring: every source through the standing-text cards (by order) into the model; the model into the
endpoints and into every node with tools, and those back into the model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from .api_node import api_node
from .const import (
    CONF_MAX_TOKENS,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
)
from .flow import DECIDE_SETTINGS, MODEL, MODEL_SETTINGS, pending_call
from .home_api import API_ID as HOME_API_ID
from .pools import TIERS
from .request import SOURCES, TEXT, Message, render as render_text
from .setup_api import API_ID as SETUP_API_ID, owner_present
from .streams import Node

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import AACConfigEntry

IN, OUT = {"in": TEXT}, {"out": TEXT}

SOURCE_HELP = {
    "chat": "The AAC panel's chat: what you write leaves at its output; what comes back into its input is shown "
            "(and kept: the chat is the whole conversation, editable).",
    "voice": "Speaks answers aloud: an answer to a spoken line (a Speech to text card) is spoken at the satellite it "
             "came from.",
    "assist": "A message from Home Assistant itself (an Assist pipeline, a voice satellite, an automation using AAC "
              "as its conversation agent) leaves here; what comes back is HA's answer.",
}
DECIDE_HELP = ("Sends the message on through one of its named outputs. A model reads the conversation and your "
               "description of each output and picks one; the server only allows your output names. If the "
               "decision fails, the message takes the default output. It does not change the message.")


# Text cards render HA's own templates (owner, 2026-10-23: "can we incorporate HA's own variables")
TEMPLATE_HINT = ("HA templates work: {{ now().strftime('%H:%M') }}, {{ states('sensor.outside_temperature') }}, "
                 "{{ <card id>.<value> }} from cards before it, {{ message.language }}. Changing text (like the time) "
                 "belongs in a Trailer: at the start it makes the server recompute the prompt.")

def model_node(inst_id: str = MODEL, title: str = "Model", settings: dict[str, Any] | None = None) -> Node:
    """A model card: reads the whole conversation; its output is square (its wired nodes are its tools)."""
    return Node(id=inst_id, title=title, inputs={"in": TEXT, "pool": "pool"}, outputs={"out": TEXT}, role="model", square={"out"},
                description="Reads the whole conversation and answers it with a model from the pool. The nodes wired to "
                            "its output are its tools: calling one sends the conversation there, and what that node "
                            "writes comes back as the result (wire it back into this card). The answer goes back to "
                            "where the message came from. The dotted pool wire says which models it may use (none: its "
                            "Model setting). Automatic messages wait here until a model is free.",
                settings=settings or {}, make=None if settings is not None else (lambda i, t, s: model_node(i, t, s)),
                settings_schema=MODEL_SETTINGS)


def decide_node(hass_flow, inst_id: str, title: str, settings: dict[str, Any]) -> Node:
    """A Decide card: its outputs are the ones the owner named."""
    names = [o["name"] for o in settings.get("outputs") or [] if isinstance(o, dict) and o.get("name")] or ["yes", "no"]

    async def handle(message: Message, step) -> list[str]:
        return [await hass_flow().decide(step.node, message)]

    return Node(id=inst_id, title=title, inputs=IN, outputs={n: TEXT for n in names}, role="decide", settings=settings,
                square=set(names), handle=handle, description=DECIDE_HELP)


def core_nodes(hass: HomeAssistant, entry: AACConfigEntry) -> list[Node]:
    """The core's nodes."""

    def master(domain_id: str) -> dict[str, Any]:
        return entry.runtime_data.domain(domain_id).settings.master

    def editing(schema: dict[str, Any], keys: dict[str, tuple[str, Any, Any]]):
        """A core card's settings in the node view, kept in the domain's agent settings. keys: setting →
        (stored key, default, validator); a wrong value raises vol.Invalid."""
        def get(domain_id: str) -> dict[str, Any]:
            return {k: master(domain_id).get(conf, default) for k, (conf, default, _) in keys.items()}

        async def put(domain_id: str, values: dict[str, Any]) -> None:
            await entry.runtime_data.domain(domain_id).settings.async_update_master(
                {keys[k][0]: keys[k][2](v) for k, v in values.items() if k in keys})
        return schema, get, put

    model_edit = editing({
        "temperature": {"type": "number", "title": "Temperature", "help": "0 = always the same answer, higher = more varied (0–2)."},
        "max_tokens": {"type": "number", "title": "Longest answer (tokens)", "help": "16–131072."},
        "thinking": {"type": "bool", "title": "Let the model think first (slower; llama.cpp / vLLM)"},
    }, {"temperature": (CONF_TEMPERATURE, DEFAULT_TEMPERATURE, vol.All(vol.Coerce(float), vol.Range(0, 2))),
        "max_tokens": (CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS, vol.All(vol.Coerce(int), vol.Range(16, 131072))),
        "thinking": (CONF_THINKING, DEFAULT_THINKING, vol.Boolean())})
    ha_apis = [[api.id, api.name] for api in llm.async_get_apis(hass) if not api.id.startswith("aac")]
    ha_edit = editing({"apis": {"type": "multiselect", "title": "Home Assistant's tool sets", "options": ha_apis,
                                "help": "Assist gives live states, timers and HA's to-do lists. None ticked: it offers nothing."}},
                      {"apis": (CONF_LLM_HASS_API, [], vol.All([vol.In([a for a, _ in ha_apis])]))})

    def endpoint(sid: str, title: str | None = None, kind: str | None = None, settings: dict | None = None) -> Node:
        kind = kind or sid  # chat · voice · assist (an added Chat card is a chat of its own, with its own title)

        async def handle(message: Message, step) -> list:
            from .broker import is_silent, short  # noqa: PLC0415 - avoid an import cycle

            chats = message.domain.chats
            if sid == "chat" and message.kind == "auto":  # a finding (heartbeat, trigger); silence stays silent
                text = message.answer
                if text and not is_silent(text) and message.trigger != "try" and message.chat == "own":
                    chat_id = f"{message.chat_prefix or message.trigger}_{dt_util.utcnow().strftime('%Y%m%d%H%M%S')}"
                    if not chats.get(chat_id):
                        chats.create(f"{message.title} · {short(text)}", chat_id, auto=message.chat_auto)
                    run = step.run
                    if run is not None:
                        run.chat_id = chat_id
                    chats.add_message(chat_id, "aac", text, proactive=True, run_id=run.id if run else None,
                                      trigger=message.title)
                    chats.set_entries(chat_id, message.entries)
                step.deliver(message)
                return []
            if message.chat_id and kind in ("chat", "voice") and chats.get(message.chat_id):
                chats.set_entries(message.chat_id, message.entries)  # the conversation as it now is
            if kind == "voice" and message.origin != "voice":  # a spoken line (Speech to text): speak at its mic
                user = message.last("user")
                audio = entry.runtime_data.audio
                text = message.answer
                source = audio.sources.get(user["data"].get("mic")) if user and audio else None
                if source is not None and text and not is_silent(text):
                    entry.async_create_background_task(hass, audio.async_say(source, text), "aac voice answer")
            if message.origin == sid or message.reply_to == sid:
                step.deliver(message)
            return []

        # Voice only speaks (owner, 2026-10-23: spoken lines start at a Speech to text card now, not here)
        return Node(id=sid, title=title or SOURCES[sid], inputs=IN, outputs={} if kind == "voice" else OUT, role="endpoint",
                    description=SOURCE_HELP[kind], handle=handle, settings=settings or {})

    def render(text: str, message: Message) -> str:
        return render_text(hass, text, message)

    def text_make(kind: str, replace: bool = True):
        def make(inst_id: str, title: str, settings: dict) -> Node:
            async def handle(message: Message, step) -> None:
                text = render((settings.get("text") or "").strip(), message)
                if pending_call(message, inst_id) is not None:  # wired from a model and back: a tool that gives its text
                    message.add(inst_id, "result", text)
                    return
                message.put(inst_id, kind, text, replace=bool(settings.get("replace", replace)))
            return Node(id=inst_id, title=title, inputs=IN, outputs=OUT, adds=[kind], order=15, handle=handle,
                        role="transform", settings=settings)
        return make

    async def home_apis(message: Message) -> list[str]:
        return [message.domain.api_id(HOME_API_ID)]  # within the owner's access list

    async def setup_apis(message: Message) -> list[str]:
        # with the owner present it helps set itself up
        return [message.domain.api_id(SETUP_API_ID)] if await owner_present(hass, message.user_id) else []

    async def ha_apis_of(message: Message) -> list[str]:
        return list(message.domain.settings.master.get(CONF_LLM_HASS_API) or [])

    text = {"type": "longtext", "title": "Text", "default": "", "rows": 5, "hint": TEMPLATE_HINT}
    flow = lambda: entry.runtime_data.flow  # noqa: E731
    return [
        *(endpoint(sid) for sid in SOURCES),
        # More chats, each with its title (owner: "a chat with just the memory"): its own section in the sidebar
        Node(id="chat_card", title="Chat", inputs=IN, outputs=OUT, role="endpoint", template=True,
             description="A chat of its own with a title (e.g. \"Memory\"): it gets its own section in the sidebar. What "
                         "you write there leaves at its output; wire it to the cards it should use and a model's output back "
                         "into its input.",
             make=lambda i, t, s: endpoint(i, t, "chat", s)),
        # The pools (dotted: a model card reserves a model from the one wired to it)
        *(Node(id=f"pool_{tier}", title=f"Models: {tier}", outputs={"models": "pool"}, role="pool",
               description=f"Hands a model card a model from the pool: {about} (the Pools page holds the models). "
                           "Wire its dotted output into a model card's pool input.")
          for tier, about in TIERS.items()),
        # Standing text: replaced in place every time (the start of the conversation stays the same: the cache)
        Node(id="trailer", title="Trailer", inputs=IN, outputs=OUT, adds=["trailer"], template=True, role="transform",
             description="Writes your own text after what was just said, e.g. the time, \"Answer in German\" or \"Reply "
                         "with yes or no\": the model reads it close to the question. Wired from a model and back it is "
                         "a tool that gives its text. With no text it adds nothing and only passes the message on: a "
                         "relay (its round output gives every wire a copy, e.g. a model's answer to Voice and a chat).",
             make=text_make("trailer", replace=False), settings_schema={
                 "text": {**text, "help": "After the turn it follows."},
                 "replace": {"type": "bool", "title": "Replace", "default": False,
                             "help": "Unticked: every message gets its own line, older ones stay where they were (the "
                                     "conversation only grows at the end: the server's cache keeps working).",
                             "warn": {"when": True, "text": "Ticked: the one line moves to the newest message, which changes "
                                                            "the conversation further back. The server recomputes from there, "
                                                            "so answers take longer."}}}),
        Node(id="context", title="Context", inputs=IN, outputs=OUT, adds=["note"], template=True, role="transform",
             description="Writes your own text into the conversation as a note (not the system prompt), e.g. \"The user "
                         "is in the bedroom\" for a way that only that room's microphone uses.",
             make=text_make("note"), settings_schema={"text": {**text, "help": "A note in the conversation (e.g. who uses this way)."}}),
        # Nodes with tools: wired out of a model and back into it, a loop each
        api_node(hass, "home", "Home", "Lets a model see and switch the home: find devices and sensors, read their "
                 "state, switch them, ask you for access. Only within the entities you allowed (SETTINGS → Entities).",
                 home_apis),
        api_node(hass, "setup", "Setup (owner present)", "Lets a model help set AAC itself up (models, memory, audio, "
                 "modules, availability rules). Only while you are in the chat, never by voice or in automatic runs.",
                 setup_apis),
        api_node(hass, "ha_tools", "Home Assistant tools", "Home Assistant's own Assist tools (live states, timers, HA's "
                 "to-do lists): tick which ones in this node's settings.", ha_apis_of, edit=ha_edit),
        # The model, Decide
        _with_edit(model_node(), model_edit),
        Node(id="decide", title="Decide", inputs=IN, outputs={"yes": TEXT, "no": TEXT}, role="decide", template=True,
             square={"yes", "no"}, description=DECIDE_HELP,
             make=lambda i, t, s: decide_node(flow, i, t, s), settings_schema=DECIDE_SETTINGS),
    ]


def _with_edit(node: Node, edit) -> Node:
    """The standard model with its settings editable (temperature, answer length, thinking)."""
    node.edit = edit
    return node
