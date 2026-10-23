"""A node whose tools are an HA LLM API (spec/graph.md): the home, setup, HA's Assist tools, the modules' tools.

Wired to a model's output and back into its input it is a loop: the model sees its tools; a call comes in as
the document's open call, the node runs it and writes the result into the document, and passes it on. Reached
without a call for it (in a line) it does what `inline` says (memory: hand along matching memories) or nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.json import json_dumps

from .const import DOMAIN
from .flow import pending_call
from .model_node import format_tool
from .request import TEXT, Message
from .streams import Node

LOOP = {"in": TEXT}, {"out": TEXT}


async def api_instance(hass: HomeAssistant, message: Message, api_ids: list[str]) -> llm.APIInstance | None:
    """The merged tools of some LLM APIs, for the person and place of a message."""
    if not api_ids:
        return None
    llm_context = llm.LLMContext(platform=DOMAIN, context=Context(user_id=message.user_id), language=message.language,
                                 assistant="conversation", device_id=message.device_id)
    try:
        return await llm.async_get_api(hass, api_ids, llm_context)
    except HomeAssistantError:
        return None  # an API that is gone (HA's own, switched off)


def api_node(hass: HomeAssistant, node_id: str, title: str, description: str,
             api_ids: Callable[[Message], Awaitable[list[str]]],
             inline: Callable[[Message], Awaitable[None]] | None = None, **extra: Any) -> Node:
    """A node offering the tools of the LLM APIs `api_ids(message)` gives (none: it offers nothing)."""

    async def tools(message: Message) -> tuple[list[dict[str, Any]], str | None]:
        if (api := await api_instance(hass, message, await api_ids(message))) is None:
            return [], None
        return [format_tool(t, api.custom_serializer) for t in api.tools], api.api_prompt

    async def handle(message: Message, step) -> None:
        if (call := pending_call(message, node_id)) is None:
            if inline is not None:
                await inline(message)
            return None
        data = call["data"]
        api = await api_instance(hass, message, await api_ids(message))
        try:
            if api is None:
                raise HomeAssistantError(f"{title} has no tools right now")
            result = await api.async_call_tool(llm.ToolInput(tool_name=data.get("tool") or "", tool_args=data.get("args") or {}))
            if isinstance(result, llm.ToolResult):
                result, error = result.data, result.error
            else:
                error = False
        except Exception as err:  # noqa: BLE001 - wrong arguments, a broken tool: the model reads what went wrong
            result, error = {"error": type(err).__name__, **({"error_text": str(err)} if str(err) else {})}, True
        message.add(node_id, "result", json_dumps(result), error=error)
        return None

    return Node(id=node_id, title=title, inputs=LOOP[0], outputs=LOOP[1], role="tool", description=description,
                tools=tools, handle=handle, **extra)
