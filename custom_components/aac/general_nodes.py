"""Universal nodes (spec/graph.md; owner, 2026-10-23: "make them universal and not special, so they can be used
for different things").

- Buffer: keeps the last N texts that pass it and writes them into the message as one block; a model wired to
  it can read, add, remove a slot, remove what contains a word, or clear it.
- Heard lines: the bridge from the audio pipeline: every finished spoken line becomes a message of its own
  (an answer to it goes to the voice endpoint, which speaks it at that microphone).
- Markdown file: a note file (`<config>/aac/notes/<name>.md`): in a line it appends what passes it, wired to a model
  it can be read, appended to and rewritten; the panel lists it like a chat (owner: "an MD file writer as a chat in
  the list for now, later a file access tool").
Both exist only as added cards (Add node), wired by hand.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.json import json_dumps
from homeassistant.util import dt as dt_util

from .flow import pending_call
from .request import TEXT, Message
from .streams import Node

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import AACConfigEntry

IN, OUT = {"in": TEXT}, {"out": TEXT}
BUFFER_VARS = {"lines": "The kept texts as a numbered block.", "count": "How many it keeps now.", "newest": "The newest text."}
HEARD_VARS = {"text": "What was said.", "speaker": "Whose voice it was (if known).", "mic": "The microphone's id.",
              "mic_name": "The microphone's name.", "area": "The microphone's area."}

BUFFER_SETTINGS = {
    "size": {"type": "number", "title": "Slots", "default": 10, "help": "How many texts it keeps (the oldest goes first)."},
    "write": {"type": "select", "title": "Writes them as", "default": "note",
              "options": [["note", "a note in the conversation"], ["system", "part of the system prompt"], ["none", "nothing (only for its tools)"]]},
    "take": {"type": "select", "title": "Keeps", "default": "said",
             "options": [["said", "the newest line (what was said or answered)"], ["user", "only what people said"],
                         ["answer", "only the answers"]]},
}


def _fn(name: str, description: str, props: dict[str, Any] | None = None, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": props or {}, **({"required": required} if required else {})}}}


BUFFER_TOOLS = [
    _fn("read", "What the buffer holds now (numbered slots, newest last)."),
    _fn("add", "Put a text into the buffer.", {"text": {"type": "string"}}, ["text"]),
    _fn("remove_slot", "Take one slot out by its number.", {"slot": {"type": "integer"}}, ["slot"]),
    _fn("remove_text", "Take out every slot that contains this text.", {"text": {"type": "string"}}, ["text"]),
    _fn("clear", "Empty the buffer."),
]


FILE_SETTINGS = {
    "file": {"type": "text", "title": "File name", "default": "notes", "help": "Kept as <config>/aac/notes/<name>.md."},
    "append": {"type": "select", "title": "In a line it writes", "default": "answer",
               "options": [["answer", "the newest answer"], ["said", "the newest line (said or answered)"], ["none", "nothing (only for its tools)"]]},
}
FILE_TOOLS = [
    _fn("read", "The whole file."),
    _fn("append", "Add text at the end (Markdown).", {"text": {"type": "string"}}, ["text"]),
    _fn("write", "Replace the whole file with this text (Markdown).", {"text": {"type": "string"}}, ["text"]),
]


def note_path(hass: HomeAssistant, settings: dict[str, Any]) -> Path:
    """Where a Markdown card's file lives (only a plain name: no folders)."""
    name = re.sub(r"[^\w\- ]", "", str(settings.get("file") or "notes")).strip() or "notes"
    return Path(hass.config.path("aac", "notes", f"{name}.md"))


def general_nodes(hass: HomeAssistant, entry: AACConfigEntry) -> list[Node]:
    """The universal node types (templates: they exist as added cards)."""
    slots: dict[tuple[str, str], deque[str]] = {}  # (domain, card) → texts (RAM: a restart empties them)

    def buffer_make(inst_id: str, title: str, settings: dict[str, Any]) -> Node:
        size = max(1, min(200, int(settings.get("size") or 10)))

        def held(message: Message) -> deque[str]:
            found = slots.setdefault((message.domain.id, inst_id), deque(maxlen=size))
            if found.maxlen != size:
                found = slots[(message.domain.id, inst_id)] = deque(found, maxlen=size)
            return found

        def block(items: deque[str]) -> str:
            return f"{title} (newest last):\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1)) if items else ""

        async def tools(message: Message):
            return BUFFER_TOOLS, None

        async def handle(message: Message, step) -> None:
            items = held(message)
            if (call := pending_call(message, inst_id)) is not None:  # a model asks
                tool, args = call["data"].get("tool"), call["data"].get("args") or {}
                if tool == "add" and str(args.get("text") or "").strip():
                    items.append(str(args["text"]).strip())
                elif tool == "remove_slot":
                    n = int(args.get("slot") or 0)
                    if 1 <= n <= len(items):
                        del items[n - 1]
                elif tool == "remove_text":
                    word = str(args.get("text") or "").lower()
                    kept = [t for t in items if word not in t.lower()] if word else list(items)
                    items.clear()
                    items.extend(kept)
                elif tool == "clear":
                    items.clear()
                message.add(inst_id, "result", json_dumps({"slots": list(items)}))
            else:  # in a line: keep the newest line
                take = settings.get("take") or "said"
                newest = next((e for e in reversed(message.entries) if e["kind"] in ("user", "answer")), None)
                if newest and (take == "said" or newest["kind"] == take) and newest["text"].strip():
                    text = (newest["data"].get("own_words") or newest["text"]).strip()
                    if not items or items[-1] != text:
                        items.append(text)
            message.set_vars(inst_id, lines=block(items), count=len(items), newest=items[-1] if items else "")
            kind = settings.get("write") or "note"
            if kind != "none":
                message.put(inst_id, kind, block(items))
            return None

        return Node(id=inst_id, title=title, inputs=IN, outputs=OUT, settings=settings, tools=tools, handle=handle,
                    description=BUFFER_HELP, variables=BUFFER_VARS)

    def heard_make(inst_id: str, title: str, settings: dict[str, Any]) -> Node:
        async def run(domain_id: str, port: str, line: dict[str, Any]) -> None:
            if line.get("kind", "speech") != "speech" or not (line.get("text") or "").strip():
                return  # AAC's own words, sounds
            domain = entry.runtime_data.domain(domain_id)
            message = Message(domain=domain, kind="chat", trigger=f"heard: {line.get('name') or line.get('source')}",
                              title=f"Heard · {line.get('name') or ''}", origin=inst_id, reply_to="voice",
                              language=line.get("language") or hass.config.language, priority="normal")
            who = line.get("speaker") or {}
            message.add(inst_id, "user", line["text"].strip(), mic=line.get("source"), mic_name=line.get("name"),
                        voice=who.get("name") if isinstance(who, dict) else None)
            message.set_vars(inst_id, text=line["text"].strip(), mic=line.get("source"), mic_name=line.get("name"),
                             area=line.get("area"), speaker=who.get("name") if isinstance(who, dict) else None)
            entry.async_create_background_task(hass, entry.runtime_data.flow.async_send(message, start=inst_id),
                                               f"aac heard {inst_id}")

        return Node(id=inst_id, title=title, inputs={"lines": "text.line"}, outputs=OUT, role="source", run=run,
                    settings=settings, description=HEARD_HELP, variables=HEARD_VARS)

    def file_make(inst_id: str, title: str, settings: dict[str, Any]) -> Node:
        path = note_path(hass, settings)

        def read() -> str:
            return path.read_text(encoding="utf-8") if path.exists() else ""

        def write(text: str, add: bool) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a" if add else "w", encoding="utf-8") as f:
                f.write(text)

        async def tools(message: Message):
            return FILE_TOOLS, None

        async def handle(message: Message, step) -> None:
            if (call := pending_call(message, inst_id)) is not None:
                tool, args = call["data"].get("tool"), call["data"].get("args") or {}
                if tool in ("append", "write"):
                    text = str(args.get("text") or "")
                    await hass.async_add_executor_job(write, (text if text.endswith("\n") else text + "\n"), tool == "append")
                content = await hass.async_add_executor_job(read)
                message.add(inst_id, "result", content[-6000:] if tool == "read" else json_dumps({"done": tool, "length": len(content)}))
                return None
            take = settings.get("append") or "answer"
            newest = next((e for e in reversed(message.entries) if e["kind"] in ("user", "answer")), None)
            if take != "none" and newest and (take == "said" or newest["kind"] == "answer") and newest["text"].strip():
                stamp = dt_util.now().strftime("%Y-%m-%d %H:%M")
                await hass.async_add_executor_job(write, f"\n## {stamp}\n\n{newest['text'].strip()}\n", True)
            return None

        node = Node(id=inst_id, title=title, inputs=IN, outputs=OUT, settings=settings, tools=tools, handle=handle,
                    description=FILE_HELP)
        node.read_file, node.write_file = read, write  # the panel opens it (aac/notes)
        return node

    return [
        Node(id="md_file", title="Markdown file", inputs=IN, outputs=OUT, template=True, make=file_make,
             settings_schema=FILE_SETTINGS, description=FILE_HELP),
        Node(id="buffer", title="Buffer", inputs=IN, outputs=OUT, template=True, make=buffer_make,
             settings_schema=BUFFER_SETTINGS, description=BUFFER_HELP),
        Node(id="heard", title="Heard lines", inputs={"lines": "text.line"}, outputs=OUT, role="source", template=True,
             make=heard_make, description=HEARD_HELP),
    ]


BUFFER_HELP = ("Keeps the last texts that pass it (a set number of slots) and writes them into the message as one "
               "numbered block, e.g. the last ten lines heard in a room. Wired to a model it is a tool: read, add, "
               "remove a slot, remove what contains a word, clear. Values for templates: {{ <card id>.lines }}, "
               ".count, .newest.")
FILE_HELP = ("A Markdown note file (<config>/aac/notes/<name>.md), listed under FILES in the sidebar where you can read "
             "and edit it. In a line it appends what passes it (the newest answer, with the time); wired to a model it "
             "is a tool: read, append, write.")
HEARD_HELP = ("Turns every finished spoken line from Hearing into a message of its own (wire Hearing's lines into it). "
              "What comes back to the Voice card is spoken at the microphone the line came from. Values for templates: "
              "{{ <card id>.text }}, .speaker, .mic_name, .area.")
