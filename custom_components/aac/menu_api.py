"""Tools as a menu (owner, 2026-10-20: "a tree like menu, the model first gets an overview").

Instead of every tool up front (~9,000 tokens of tool descriptions on the 35B, read again whenever
the server's cache was used for something else in between), the model gets one line per area and
two tools: open_area (an area's instructions and its tools) and use_tool (call one of them). The
tool list stays the same for the whole chat, so the server's prompt cache keeps working; what an
opened area brings is appended like any tool result (~0.3 s). Chosen per model (traits tool_mode:
menu / all); bench 2026-10-20: 35B menu 12/12, all 11/12; the 8B is better with all.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
import re
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

MENU_API_ID = "aac_menu"

# One line per area (by the tool namespace HA gives an API: its name, slugified)
AREA_INFO = {
    "aac-home": "The home: find devices and sensors, read their state, switch them, ask for access",
    "aac-memory": "Long-term memory: remember and recall facts, plans, measurements about the user",
    "aac-planning": "Plans and calendar: reminders, check-ins, watching something, calendar entries",
    "aac-setup": "Setting AAC itself up: models, memory, heartbeat, audio, availability rules",
    "assist": "Home Assistant's own tools: live state, switching, timers, HA's to-do lists",
}
MENU_PROMPT = (
    "Your tools are sorted into areas. Open the area you need with open_area: it tells you how to use it "
    "and which tools it has; then call them with use_tool. Never say you cannot do something before you "
    "opened the area that fits. Areas opened earlier in this chat stay open (their tools are above).\n"
    "Areas:\n"
)
_HEADER = re.compile(r'Follow these instructions for tools from "([^"]+)":\n')


@dataclass
class Menu:
    """The areas of one request, for the two tools to work with."""

    api: llm.APIInstance  # every tool (the merged instance of the chat)
    prefix: str  # the menu's own namespace (its tools are not an area)
    instructions: dict[str, str]  # area → its instructions
    tools: dict[str, dict[str, Any]]  # full tool name → {name, description, parameters}


# The menu of the request in progress (set around the model loop; tools run in the same task)
current_menu: ContextVar[Menu | None] = ContextVar("aac_current_menu", default=None)


def split_prompt(api_prompt: str) -> dict[str, str]:
    """The merged API prompt, back into each namespace's instructions."""
    parts = _HEADER.split(api_prompt)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def build_menu(api: llm.APIInstance, prefix: str, formatted: list[dict[str, Any]],
               describe: Callable[[str, list[str]], str | None]) -> tuple[Menu, str]:
    """The menu and the text that replaces the tool instructions in the system prompt."""
    instructions = {k: v for k, v in split_prompt(api.api_prompt).items() if k != prefix}
    tools = {f["function"]["name"]: f["function"] for f in formatted}
    for name in tools:  # an area may bring tools but no instructions
        area = name.split("__", 1)[0]
        if area != prefix and "__" in name:
            instructions.setdefault(area, "")
    lines = []
    for area in instructions:
        names = [n.split("__", 1)[1] for n in tools if n.startswith(f"{area}__")]
        lines.append(f"- {area}: {describe(area, names) or 'tools: ' + ', '.join(names)}")
    return Menu(api, prefix, instructions, tools), MENU_PROMPT + "\n".join(lines)


def _area(menu: Menu, wanted: str) -> str | None:
    """The area meant (small models drop the prefix or change the case)."""
    wanted = wanted.strip().lower()
    for area in menu.instructions:
        if area.lower() == wanted or area.lower().removeprefix("aac-") == wanted.removeprefix("aac-"):
            return area
    return None


class OpenAreaTool(llm.Tool):
    """An area's instructions and tools."""

    name = "open_area"
    description = "Open an area of tools: you get how to use it and its tools (call them with use_tool)."
    parameters = vol.Schema({vol.Required("area"): str})

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> Any:
        """Open it."""
        if (menu := current_menu.get()) is None:
            return llm.ToolResult(data={"error": "No tools in this request."})
        if (area := _area(menu, str(tool_input.tool_args.get("area", "")))) is None:
            return llm.ToolResult(data={"error": "No such area", "areas": list(menu.instructions)})
        return llm.ToolResult(data={
            "area": area,
            "instructions": menu.instructions[area],
            "tools": [{"name": n, "description": f.get("description", ""), "parameters": f.get("parameters", {})}
                      for n, f in menu.tools.items() if n.startswith(f"{area}__")],
        })


class UseToolTool(llm.Tool):
    """Call a tool of an opened area."""

    name = "use_tool"
    description = "Call a tool of an area you opened, by its full name (e.g. aac-lists__lists), with its arguments."
    parameters = vol.Schema({vol.Required("tool"): str, vol.Optional("arguments", default={}): dict})

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> Any:
        """Call it on the chat's full tool set."""
        if (menu := current_menu.get()) is None:
            return llm.ToolResult(data={"error": "No tools in this request."})
        name, args = str(tool_input.tool_args.get("tool", "")), tool_input.tool_args.get("arguments") or {}
        tool = next((t for t in menu.api.tools if t.name == name), None)
        if tool is None or name.startswith(f"{menu.prefix}__"):
            return llm.ToolResult(data={"error": f'No tool "{name}". Open the area first and use the full name it lists.'})
        try:
            args = tool.parameters(args)
            result = await menu.api.async_call_tool(llm.ToolInput(tool_name=name, tool_args=args, id=tool_input.id))
        except vol.Invalid as err:
            return llm.ToolResult(data={"error": f"Wrong arguments for {name}: {err}"})
        except HomeAssistantError as err:
            return llm.ToolResult(data={"error": str(err)})
        return result if isinstance(result, llm.ToolResult) else llm.ToolResult(data=result)


@dataclass(kw_only=True)
class AACMenuAPI(llm.API):
    """The two menu tools (only offered to models whose tool mode is menu)."""

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """The tools."""
        return llm.APIInstance(api=self, api_prompt="", llm_context=llm_context, tools=[OpenAreaTool(), UseToolTool()])
