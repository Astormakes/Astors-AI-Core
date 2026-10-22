"""The core's own nodes in the request chain (spec/v1.md), until they become node types of their own.

Order in the chain (lowest first; modules fill the gaps): 0 charter · 10 the agent's prompt and HA's own
tools · 20 memory recalls · 30 the home · 40 the modules' tool sets · 90 setup (only with the owner in the
chat). The request sources are nodes of their own: chat (the panel), voice, assist (HA), auto (heartbeat and
triggers through the broker's queue); automatic wiring sends them all into the same chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT

from .home_api import API_ID as HOME_API_ID
from .request import ANSWER, REQUEST, SOURCES, ModelRequest
from .setup_api import API_ID as SETUP_API_ID, owner_present
from .streams import Node

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import AACConfigEntry

PORT = {"requests": REQUEST}


def core_nodes(hass: HomeAssistant, entry: AACConfigEntry) -> list[Node]:
    """The agent, the home and setup as annotating nodes; the request sources as one node."""

    async def agent(request: ModelRequest) -> None:
        master = request.domain.settings.master
        if prompt := (master.get(CONF_PROMPT) or "").strip():
            request.system.append(prompt)
        for api_id in master.get(CONF_LLM_HASS_API) or []:  # HA's own tools (Assist), if the owner chose them
            request.add_tools(api_id)

    async def home(request: ModelRequest) -> None:
        request.add_tools(request.domain.api_id(HOME_API_ID))  # within the owner's access list

    async def setup(request: ModelRequest) -> None:
        if await owner_present(hass, request.user_id):  # with the owner present it helps set itself up
            request.add_tools(request.domain.api_id(SETUP_API_ID))

    def again(fn):
        """make() for a node that can be added again as it is (e.g. the home's tools in a second chain)."""
        def make(inst_id: str, title: str, settings: dict) -> Node:
            return Node(id=inst_id, title=title, inputs=PORT, outputs=PORT, adds=["tools"], order=30, annotate=fn,
                        settings=settings)
        return make

    def agent_make(inst_id: str, title: str, settings: dict) -> Node:
        async def annotate(request: ModelRequest) -> None:  # its own prompt instead of the domain's
            if prompt := (settings.get("prompt") or "").strip():
                request.system.append(prompt)
        return Node(id=inst_id, title=title, inputs=PORT, outputs=PORT, adds=["system"], order=10, annotate=annotate,
                    settings=settings)

    def text_make(field: str):
        def make(inst_id: str, title: str, settings: dict) -> Node:
            async def annotate(request: ModelRequest) -> None:
                if text := (settings.get("text") or "").strip():
                    getattr(request, field).append(text)
            return Node(id=inst_id, title=title, inputs=PORT, outputs=PORT, adds=[field], order=15, annotate=annotate,
                        settings=settings)
        return make

    text = {"text": {"type": "longtext", "title": "Text", "default": "", "rows": 5}}
    return [
        # Building blocks that exist only when the owner adds them (spec/v1.md): own instructions in the system
        # prompt, own context with every message
        Node(id="instructions", title="Instructions", inputs=PORT, outputs=PORT, adds=["system"], template=True,
             make=text_make("system"), settings_schema={"text": {**text["text"], "help": "Added to the system prompt (stable: good for the prompt cache)."}}),
        Node(id="context", title="Context", inputs=PORT, outputs=PORT, adds=["context"], template=True,
             make=text_make("context"), settings_schema={"text": {**text["text"], "help": "Goes along with every message (e.g. who uses this chain)."}}),
        # The request sources: each can be wired into its own chain (automatic: all into the same one)
        *(Node(id=sid, title=title, outputs=PORT, inputs={"answers": ANSWER}) for sid, title in SOURCES.items()),
        Node(id="agent", title="Agent", inputs=PORT, outputs=PORT, adds=["system", "tools"], order=10, annotate=agent,
             make=agent_make, settings_schema={"prompt": {"type": "longtext", "title": "Prompt", "default": "", "rows": 5,
                                                          "help": "This agent's own prompt (the domain's agent prompt is on SETTINGS → Master agent)."}}),
        Node(id="home", title="Home", inputs=PORT, outputs=PORT, adds=["tools"], order=30, annotate=home, make=again(home)),
        Node(id="setup", title="Setup (owner present)", inputs=PORT, outputs=PORT, adds=["tools"], order=90, annotate=setup,
             make=again(setup)),
    ]
