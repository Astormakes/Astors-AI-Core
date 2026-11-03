"""The Relay's script (docs/v2 page 4): plain Python, checked before it runs, that runs top to bottom every time a
context arrives. `input("in")` is what arrived, `output("out", what)` sends; nothing leaves otherwise. A `def` is a
function the Relay offers: a call runs only that def. Only the parts of Python listed in ALLOWED run (no imports, no
while, nothing starting with `_`); the code runs in a worker thread, a card's function is called back on HA's loop. A
script stops itself when its own time is up (WAIT; waiting for the cards it calls does not count): a thread cannot be
stopped from outside, so it checks on every line."""

from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import re
import sys
import time
import traceback
from types import SimpleNamespace
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import SYSTEM, TOOLS, Context

ALLOWED = {
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign, ast.AugAssign, ast.Expr, ast.If,
    ast.For, ast.Break, ast.Continue, ast.Pass, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Call,
    ast.keyword, ast.IfExp, ast.Attribute, ast.Subscript, ast.Slice, ast.Name, ast.Load, ast.Store, ast.Constant,
    ast.JoinedStr, ast.FormattedValue, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.ListComp, ast.SetComp,
    ast.DictComp, ast.GeneratorExp, ast.comprehension, ast.Starred,
    ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
}
NOT_ALLOWED = {ast.While: "while (use for)", ast.Import: "import", ast.ImportFrom: "import", ast.Lambda: "lambda",
               ast.Pow: "** (power)", ast.Try: "try", ast.With: "with", ast.ClassDef: "class", ast.Global: "global",
               ast.Nonlocal: "nonlocal", ast.Delete: "del", ast.AsyncFunctionDef: "async", ast.Await: "await",
               ast.Yield: "yield", ast.YieldFrom: "yield", ast.Raise: "raise", ast.NamedExpr: ":="}
HIDDEN_ATTRS = {"format", "format_map", "mro"}  # str.format can reach inside objects
HIDDEN_PREFIX = ("_", "gi_", "f_", "cr_", "ag_", "tb_", "co_")
MAX_RANGE = 10000
WAIT = 3  # seconds a script's own lines (or one of its defs') may run; waiting for the cards it calls does not count
CALL_WAIT = 60  # seconds one card call of a script may take
TOTAL = 120  # seconds a run may take in all, waiting included (then the turn goes on without it)
INNER_IMPORTS = {"time", "_strptime", "datetime", "calendar", "locale", "re", "_locale"}
PORT = re.compile(r"[a-z0-9_]+")
BUILTINS = ("len", "str", "int", "float", "bool", "round", "min", "max", "abs", "sorted", "reversed", "any", "all",
            "list", "dict", "set", "tuple", "enumerate", "zip", "isinstance", "range")
OWN = {"input", "output", "stop", "print", "print_log", "now", "states", "state_attr", "is_state", "re",
       "audio", "cards", *BUILTINS}  # the script's own names: they win over a card with that id (it is cards["id"])
EDITS = ("add", "set")  # the context's methods that write an entry with a tag

DEFAULT = 'context = input("in")\noutput("out", context)\n'

# what the ? panel and models read: (group, signature, what it does, example)
REFERENCE = [
    ("Start here", "context = input(\"in\")", "The script runs from the top to the bottom every time something arrives at "
     "the card. input(\"in\") is what arrived at the input called in: the context, a list of entries (texts with tags: "
     "who wrote them and what they are). The name context is your choice.", ""),
    ("Start here", 'output("out", context)', "Sends the context out at the output called out: the cards wired there get "
     "it. Nothing leaves the card unless a line like this runs. A new Relay starts with these two lines: it passes "
     "everything on.", 'context = input("in")\noutput("out", context)'),
    ("Start here", "a whole example", "Adds the time at the end of every turn, and sends the turn to yes when someone "
     "said computer, otherwise to no.",
     'context = input("in")\ncontext.add("time", f"It is {now():%H:%M}.")\nsaid = context.get("*.said")\n'
     'if said and "computer" in said.text.lower():\n    output("yes", context)\nelse:\n    output("no", context)'),
    ("Python basics", "x = 5   x == 5", "= gives something a name (x is 5 from now on); == asks (is x 5?).", ""),
    ("Python basics", "if … :", "Only when it is true, the moved-in lines under it run (4 spaces). elif: else if, else: "
     "otherwise.", 'if "stop" in text:\n    stop()\nelse:\n    print("no stop")'),
    ("Python basics", "for x in things:", "The moved-in lines run once for each item.",
     'for word in ["computer", "assistant"]:\n    if word in text:\n        print("named:", word)'),
    ("Python basics", '"text"', "Text goes in quotes. Over several lines: three quotes \"\"\"…\"\"\". f\"It is {now():%H:%M}\" "
     "puts values into a text.", ""),
    ("Python basics", "# a note", "Everything after # is a note for you; it does not run.", ""),
    ("Python basics", "None", "Nothing. if x: is False for None, an empty text and an empty list.", ""),
    ("The context", "what a context is", "A list of entries in order. First always context.system (the system prompt), "
     "second context.tools (what a model may call), then the conversation. Every entry has a tag: the card that wrote "
     "it, a dot, a name (chat.said: what was typed in the chat card; audio_clipper.said: a heard line; model.said: a "
     "model's answer). Everything anyone says is .said. The ? panel lists this domain's tags.", ""),
    ("The context", 'context.get("chat.said")', "The newest entry with that tag, or None when there is none.",
     'said = context.get("chat.said")\nif said:\n    print(said.text)'),
    ("The context", '"*.said"', "* stands for anything: \"*.said\" is anyone's said, \"*\" every entry.", ""),
    ("The context", '"time" (no dot)', "A tag without a dot is this Relay's own: on a Relay called Time, \"time\" is "
     "time.time.", ""),
    ("The context", 'context["chat.said"]', "The same as get, but a mistake (KeyError) when there is none.", ""),
    ("The context", '"chat.said" in context', "True when an entry has that tag. To look for a word, look in a text: "
     "\"stop\" in said.text.", ""),
    ("The context", 'context.all("*.said")', "Every entry with that tag, oldest first (a list). .text is their texts "
     "as one, one per line.", 'heard = context.all("*.said")\nprint(heard.text)'),
    ("The context", "context.text", "The conversation's texts as one text, one per line.",
     'if "lights" in context.text.lower():\n    print("lights came up")'),
    ("The context", "for e in context:", "Every entry, oldest first (system and tools too). context[0] is the first, "
     "context[-1] the newest, len(context) how many.", ""),
    ("The context", "what a model reads", "context.system is its system prompt, context.tools its tools; its own .said "
     "entries are its earlier answers, every other entry with text is a user message (a time you add, a heard line). "
     "Speed: the model server keeps what it has read; a change at the beginning or in the middle (the system prompt, "
     "older entries) makes it read everything from there again. The end is cheap.", ""),
    ("The context", 'context.add("note", text)', "A new entry at the end of the conversation (at=\"first\": at its "
     "beginning). Each call adds a new one.", 'context.add("time", f"Local time: {now():%A %H:%M}")'),
    ("The context", 'context.set("charter", text)', "Writes the newest entry with that tag where it is; when there is "
     "none, a new one, as add makes it (at= the same). The system prompt: context.set(\"context.system\", text).",
     'context.set("context.system", """You are AAC, a helpful assistant.""")'),
    ("The context", 'context.remove("audio_clipper.said")', "Takes out every entry with that tag. at=\"first\" / "
     "at=\"last\": only the oldest / newest. context.system and context.tools always stay: context.remove(\"*\") empties "
     "the conversation.", ""),
    ("The context", 'context.set("chat.id.2026-11-02", "")', "Names a chat: a Chat keeps the context in the chat with "
     "that id (it is made when there is none yet). One chat per day: context.set(\"chat.id.\" + now().strftime(\"%Y-%m-%d\"), \"\").", ""),
    ("The context", "a + b", "One context: a's entries, then b's (a chat's conversation, then the new lines); the "
     "system prompt and tools are b's (a's when b has none).", 'context = input("chat", read=True) + input("in")'),
    ("The context", "context.system", "The system prompt's entry (context.system.text). context.tools: the tools' "
     "entry (context.tools.data.tools).", ""),
    ("An entry", "e.text", "Its text. e.text = \"…\" changes it.", 'said.text = said.text.replace("computer", "").strip()'),
    ("An entry", "e.tag", "Its tag, e.g. \"chat.said\": the card that wrote it is in front.", ""),
    ("An entry", "e.id", "Its own id (a result names the call it answers by it).", ""),
    ("An entry", "e.data", "What its card added (the satellite that heard it: e.data.get(\"mic\")). e.data[\"mic\"] = "
     "\"kitchen\" changes it.", ""),
    ("An entry", "e.state", "streaming (still being written), ended, or cut (a stop ended it).", ""),
    ("An entry", "e.time", "When it was written (UTC, ISO text).", ""),
    ("Text", 'text.replace("a", "b")', "Every a becomes b (capitals count).", '"Hi computer".replace("computer", "AAC") → "Hi AAC"'),
    ("Text", "text.lower()", "Small letters (upper(): capitals). Use it to ignore capitals: \"stop\" in text.lower().", ""),
    ("Text", "text.strip()", "Spaces and empty lines off both ends.", ""),
    ("Text", 'text.startswith("hey")', "True when it starts so (endswith: ends so).", ""),
    ("Text", 'text.split(",")', "A list of the parts between the commas.", '"a,b".split(",") → ["a", "b"]'),
    ("Text", '", ".join(items)', "One text from a list of texts, with \", \" between.", ""),
    ("Text", "len(text)", "How many characters (or how many items in a list).", ""),
    ("Text", 're.search(r"\\d+", text)', "A pattern (regular expression): .group(0) is what it found; re.sub(pattern, "
     "new, text) replaces.", 'found = re.search(r"(\\d+) seconds", text)\nif found:\n    print(found.group(1))'),
    ("Sending and stopping", 'output("yes", what)', "Sends at the output yes (the card gets that output as soon as the "
     "script names it): every card wired there gets it. what may be a context (it goes as it is), a text (the context "
     "that arrived goes, with that text as this Relay's said), an entry or a list (their texts as one). Several output "
     "lines send several times.", 'output("out", f"It is {now():%H:%M}.")'),
    ("Sending and stopping", "stop()", "Stops every card of the domain at once (as the Stop button does): a model still "
     "writing cuts its answer, Text to speech goes quiet.",
     'heard = context.get("*.said")\nif heard and "stop" in heard.text.lower():\n    stop()'),
    ("Sending and stopping", 'input("chat", read=True)', "A dotted input: wire a card's read output to it (a Chat's "
     "provides: its conversation). It never starts the script; it is read when something arrives elsewhere.", ""),
    ("Sending and stopping", "input()", "What just arrived, at whichever input. input(\"x\") is None when it came in at "
     "another one; context.input is the name of the input it came in at. A script that names no input has one, in, and "
     "one that names no output has one, out.", ""),
    ("Sending and stopping", 'print("…")', "Shows on the card's Printed panel (for checking a script).", ""),
    ("Sending and stopping", 'print_log("…")', "Writes into AAC's log (Settings → Logs).", ""),
    ("Other cards", 'lists.add_items(list="Shopping", items=["milk"])', "Calls a function of another card: its id, a "
     "dot, the function (the ? panel lists them). The card must be in this Relay's loop: wired from it and back into it. "
     "The call travels along the wires, the script waits for the answer. It asks that card for one thing; it does not "
     "send it the context (only output does).", ""),
    ("Other cards", 'web.read("https://…")', "Arguments go in the order the card names them, or by name "
     "(web.read(url=\"https://…\")).", ""),
    ("Other cards", "lists.total", "A card's value (no brackets): each card lists its values.", ""),
    ("Other cards", 'cards["output"]', "A card whose id is one of the script's own names (a card called Output) is "
     "reached like this.", ""),
    ("Your own functions", "def name(): … return …", "A function this Relay offers: to models that reach it from their calls output (tick it under "
     "For models) and to other Relays' scripts. The text in quotes under def is what a model reads about it; what it "
     "returns is the answer.",
     'def seconds_left():\n    """Seconds left on the climate clock."""\n    page = web.read("https://example.org/clock")\n'
     '    found = re.search(r"([\\d,]+) seconds", page)\n    return found.group(1) if found else "unknown"'),
    ("Your own functions", "how a call comes and goes", "A model's call travels along the wires to this Relay like any "
     "context. Then only that def runs, not the script: it gets its arguments, not the context. What it returns travels "
     "back along the wires to whoever asked. A Relay with only defs is a toolbox: it only answers calls.", ""),
    ("Values", "now()", "The time now: now().hour, f\"{now():%A %H:%M}\".", ""),
    ("Values", 'states("sensor.x")', "A Home Assistant state (state_attr(id, \"name\"), is_state(id, \"on\")), as far as the "
                                       "Relay's May use lets it read (else \"unknown\").", ""),
    ("Values", "audio.last.seconds", "How long ago AAC last spoke (audio.satellites[id].seconds per satellite).", ""),
    ("While a model writes", "context.streaming", "A model's answer arrives after every finished sentence while it is "
     "written, and the script runs every time: output sends each one on (that is how Text to speech starts early). "
     "context.streaming is False on the last, finished one. A card call only works then: put it under if not "
     "context.streaming:.", 'answer = context.get("model.said")\nif answer:\n    answer.text = answer.text.replace("Computer", "AAC")\n'
     'output("out", context)'),
]


def reference_text() -> str:
    """The reference as plain text (for models: the script setting's help, the Card editor's help("relay"))."""
    out = []
    for _, sig, what, example in REFERENCE:
        out.append(f"{sig}: {what}" + (f"\n    {example.replace(chr(10), chr(10) + '    ')}" if example else ""))
    return "\n".join(out)


class ScriptError(Exception):
    """A mistake in a script (shown with its line)."""


@dataclass
class Script:
    """A checked script: its ports, functions, the tags it writes, problems."""

    source: str
    inputs: dict[str, str] = field(default_factory=dict)  # name → text or read
    outputs: list[str] = field(default_factory=list)
    functions: dict[str, dict[str, Any]] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)  # its own tags (without the card) → what they mark
    names: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    code: Any = None
    defs: Any = None  # only the defs: what a call runs


def check(source: str) -> Script:
    """Read a script: its inputs, outputs, functions and tags; problems instead of code when it is not allowed."""
    script = Script(source)
    if not source.strip():
        return script
    try:
        tree = ast.parse(source, "<script>")
    except SyntaxError as err:
        script.problems.append(f"line {err.lineno}: {err.msg}")
        return script
    for node in ast.walk(tree):
        kind = type(node)
        if kind not in ALLOWED:
            what = NOT_ALLOWED.get(kind, kind.__name__)
            script.problems.append(f"line {getattr(node, 'lineno', '?')}: {what} is not allowed here")
        elif kind is ast.Attribute and (node.attr.startswith(HIDDEN_PREFIX) or node.attr in HIDDEN_ATTRS):
            script.problems.append(f"line {node.lineno}: .{node.attr} is not allowed (use f\"…{{x}}…\" for format)")
        elif kind is ast.Name:
            if node.id.startswith("_"):
                script.problems.append(f"line {node.lineno}: names may not start with _")
            script.names.add(node.id)
        elif kind is ast.FunctionDef and node not in tree.body:
            script.problems.append(f"line {node.lineno}: def only at the top (not inside another block)")
        elif kind is ast.Call:
            _call(node, script)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            _function(node, script)
    if not script.problems:
        try:
            script.code = compile(tree, "<script>", "exec")
            script.defs = compile(ast.Module([n for n in tree.body if isinstance(n, ast.FunctionDef)], []), "<script>", "exec")
        except SyntaxError as err:  # e.g. return outside a def
            script.problems.append(f"line {err.lineno}: {err.msg}")
    return script


def _call(node: ast.Call, script: Script) -> None:
    """input(…), output(…) and the context's writing methods: the ports and tags the script names."""
    fn = node.func
    if isinstance(fn, ast.Name) and fn.id == "input":
        read = next((k.value for k in node.keywords if k.arg == "read"), None)
        if any(k.arg != "read" for k in node.keywords) or (read is not None and not (isinstance(read, ast.Constant) and isinstance(read.value, bool))):
            script.problems.append(f"line {node.lineno}: input(\"name\") takes only read=True or read=False")
        elif not node.args:
            pass  # input(): whatever arrived
        elif len(node.args) > 1 or not _name(node.args[0]):
            script.problems.append(f"line {node.lineno}: input(\"name\") takes its name in quotes (small letters, digits, _)")
        else:
            script.inputs[node.args[0].value] = "read" if read is not None and read.value else "text"
    elif isinstance(fn, ast.Name) and fn.id == "output":
        if len(node.args) != 2 or node.keywords or not _name(node.args[0]):
            script.problems.append(f"line {node.lineno}: output(\"name\", what) takes the output's name in quotes (small "
                                   "letters, digits, _) and what it sends (a context, a text …)")
        else:
            if node.args[0].value not in script.outputs:
                script.outputs.append(node.args[0].value)
            if not isinstance(node.args[1], ast.Name):
                script.tags["said"] = "a text it sends with output()"
    elif isinstance(fn, ast.Attribute) and fn.attr in EDITS and node.args and isinstance(tag := node.args[0], ast.Constant) \
            and isinstance(tag.value, str) and "." not in tag.value and "*" not in tag.value:
        script.tags[tag.value] = "its own text"


def _name(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(PORT.fullmatch(node.value))


def _function(fn: ast.FunctionDef, script: Script) -> None:
    """A def: a function the Relay offers (its arguments, its docstring for models)."""
    if fn.decorator_list:
        script.problems.append(f"line {fn.lineno}: @ above a def does nothing in a script (a def is a function: see ?)")
        return
    if fn.name in OWN:
        script.problems.append(f"line {fn.lineno}: def {fn.name}: the name is taken (the script's own {fn.name})")
        return
    if fn.name in script.functions:
        script.problems.append(f"line {fn.lineno}: there is already a def {fn.name}")
        return
    a = fn.args
    if a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs:
        script.problems.append(f"line {fn.lineno}: def {fn.name}(…) takes plain names (a, b, c=1)")
        return
    required = len(a.args) - len(a.defaults)
    script.functions[fn.name] = {"help": ast.get_docstring(fn) or "",
                                 "args": {p.arg: {"type": "string", "help": "", "required": i < required} for i, p in enumerate(a.args)}}


# ------------------------------------------------------------------ what the script sees
class Values:
    """A card's values, the core's or an entry's data: read as values.x or values["x"]; values["x"] = … writes (an
    entry's data)."""

    __slots__ = ("_v",)

    def __init__(self, v: dict[str, Any]) -> None:
        self._v = v

    def __getattr__(self, name: str) -> Any:
        if name not in self._v:
            raise AttributeError(f"no value {name} (there are: {', '.join(map(str, self._v)) or 'none'})")
        return wrap(self._v[name])

    def __getitem__(self, name: Any) -> Any:
        return wrap(self._v[name])

    def __setitem__(self, name: Any, value: Any) -> None:
        self._v[str(name)] = value

    def get(self, name: Any, default: Any = None) -> Any:
        """Like dict.get."""
        return wrap(self._v.get(name, default))

    def __iter__(self):
        return iter(self._v)

    def __contains__(self, name: Any) -> bool:
        return name in self._v

    def __repr__(self) -> str:
        return repr(self._v)


def wrap(v: Any) -> Any:
    """Dicts readable with dots."""
    return Values(v) if isinstance(v, dict) else v


class Entry:
    """One entry: its text (to read and change), tag, id, data, state, time."""

    __slots__ = ("_e",)

    def __init__(self, e: dict[str, Any]) -> None:
        self._e = e

    @property
    def text(self) -> str:
        """Its text."""
        return self._e["text"]

    @text.setter
    def text(self, value: Any) -> None:
        self._e["text"] = str(value)

    @property
    def tag(self) -> str:
        """Its tag (the card that wrote it in front)."""
        return self._e["tag"]

    @property
    def id(self) -> str:
        """Its own id."""
        return self._e["id"]

    @property
    def data(self) -> Values:
        """What its card added (writable)."""
        return Values(self._e["data"])

    @property
    def state(self) -> str:
        """streaming, ended or cut."""
        return self._e["state"]

    @property
    def time(self) -> str:
        """When it was written (UTC, ISO)."""
        return self._e["time"]

    def __contains__(self, part: Any) -> bool:
        raise ScriptError(f"to look for {part!r} in an entry, look in its text: {part!r} in e.text")

    def __str__(self) -> str:
        return self.text

    def __format__(self, spec: str) -> str:
        return format(self.text, spec)

    def __repr__(self) -> str:
        return f"<entry {self._e['tag']}: {self.text[:60]!r}>"


class Entries(list):
    """A list of entries (context.all): .text is their texts as one."""

    @property
    def text(self) -> str:
        """Their texts, one per line."""
        return "\n".join(e.text for e in self if e.text.strip())


def joined(a: Context, b: Context) -> Context:
    """One context: a's conversation, then b's (an entry both have once); the system prompt and the tools are b's,
    a's when b's are empty."""
    out = a.copy()
    have = {e["id"] for e in out.entries}
    out.entries += [deepcopy(e) for e in b.conversation if e["id"] not in have]
    if b.system["text"].strip():
        out.entries[0] = deepcopy(b.system)
    if b.tools["data"].get("tools"):
        out.entries[1] = deepcopy(b.tools)
    return out


def _at(at: str) -> None:
    if at not in ("first", "last"):
        raise ScriptError(f'at="first" or at="last" (not {at!r})')


class ContextView:
    """A context as the script sees it: a list of entries, oldest first, with ways to find, add and remove them."""

    __slots__ = ("_c", "_run", "_port")

    def __init__(self, context: Context, run: Run, port: str | None = None) -> None:
        self._c, self._run, self._port = context, run, port

    # ------------------------------------------------------------------ finding
    def _tag(self, tag: Any) -> str:
        tag = str(tag)
        return tag if "." in tag or tag == "*" else f"{self._run.relay.id}.{tag}"

    def _found(self, tag: Any) -> list[dict[str, Any]]:
        return self._c.find(self._tag(tag))

    def get(self, tag: Any, default: Any = None) -> Any:
        """The newest entry with that tag, or default (None)."""
        found = self._found(tag)
        return Entry(found[-1]) if found else default

    def all(self, tag: Any = "*") -> Entries:
        """Every entry with that tag, oldest first."""
        return Entries(Entry(e) for e in self._found(tag))

    def __getitem__(self, key: Any) -> Entry:
        if isinstance(key, int):
            return Entry(self._c.entries[key])
        if isinstance(key, slice):
            return Entries(Entry(e) for e in self._c.entries[key])
        if (found := self.get(key)) is None:
            raise KeyError(f"no entry with the tag {self._tag(key)} (context.get gives None instead)")
        return found

    def __contains__(self, tag: Any) -> bool:
        return bool(self._found(tag))

    def __iter__(self):
        return iter([Entry(e) for e in self._c.entries])

    def __len__(self) -> int:
        return len(self._c.entries)

    @property
    def text(self) -> str:
        """The conversation's texts, one per line."""
        return "\n".join(e["text"] for e in self._c.conversation if e["text"].strip())

    @property
    def system(self) -> Entry:
        """The system prompt's entry."""
        return Entry(self._c.system)

    @property
    def tools(self) -> Entry:
        """The tools' entry."""
        return Entry(self._c.tools)

    @property
    def streaming(self) -> bool:
        """True while an entry is still being written (the same context comes again)."""
        return self._c.streaming

    @property
    def input(self) -> str | None:
        """The input it came in at (None: not what arrived, e.g. a read input or a + b)."""
        return self._port

    # ------------------------------------------------------------------ writing
    def add(self, tag: Any, text: Any, at: str = "last") -> None:
        """A new entry at the end of the conversation (at="first": at its beginning)."""
        _at(at)
        self._c.add(self._tag(tag), str(text), at)

    def set(self, tag: Any, text: Any, at: str = "last") -> None:
        """Writes the newest entry with that tag where it is; none: a new one, as add makes it."""
        _at(at)
        if found := self._found(tag):
            found[-1]["text"] = str(text)
        else:
            self.add(tag, text, at)

    def remove(self, tag: Any, at: str = "all") -> None:
        """Takes out every entry with that tag (at="first" / "last": only the oldest / newest)."""
        if at not in ("all", "first", "last"):
            raise ScriptError('remove(…, at="first"), at="last" or nothing (every one)')
        found = [e for e in self._found(tag) if e["tag"] not in (SYSTEM, TOOLS)]  # those two always stay
        for e in found if at == "all" else found[:1] if at == "first" else found[-1:]:
            self._c.remove(e)

    def __add__(self, other: Any) -> ContextView:
        if not isinstance(other, ContextView):
            raise ScriptError("a context + another context (input(…) + input(…)); to add a text use add")
        return ContextView(joined(self._c, other._c), self._run)

    def __bool__(self) -> bool:
        return bool(self._c.conversation)

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"<context: {len(self)} entries{', still being written' if self.streaming else ''}>"


class CardRef:
    """Another card: its functions (when the Relay reaches it from one of its outputs) and its values."""

    __slots__ = ("_card", "_run")

    def __init__(self, card: Any, run: Run) -> None:
        self._card, self._run = card, run

    def __getattr__(self, name: str) -> Any:
        card = self._card
        if name in card.functions:
            spec = card.functions[name].get("args") or {}

            def call(*args: Any, **kwargs: Any) -> Any:
                if len(args) > len(spec):
                    raise ScriptError(f"{card.id}.{name}() takes {len(spec)} arguments: {', '.join(spec) or 'none'}")
                kwargs.update(zip(spec, args))
                return self._run.call(card, name, kwargs)
            return call
        values = card.current() if card.values else {}
        if name in values:
            return wrap(values[name])
        raise AttributeError(f"{card.id} has no function or value {name} (functions: {', '.join(card.functions) or 'none'}; "
                             f"values: {', '.join(values) or 'none'})")

    def __repr__(self) -> str:
        return f"<card {self._card.id}>"


# ------------------------------------------------------------------ running
class Run:
    """One run of a script (in a worker thread): on a context that arrived at one of its inputs, or a call of one def."""

    def __init__(self, relay: Any, arrived: Context | None, port: str | None, reads: dict[str, Context], loop: asyncio.AbstractEventLoop,
                 audio: Any = None, calling: Context | None = None) -> None:
        self.relay, self.arrived, self.port, self.reads, self.loop, self.audio = relay, arrived, port, reads, loop, audio
        self.calling = calling  # a call runs one def: its card calls travel on a copy of the caller's context
        self.view = ContextView(arrived, self, port) if arrived is not None else None
        self.sends: list[tuple[str, Context]] = []  # (output, what leaves there), in the order the script sent them
        self.printed: list[str] = []
        self.logged: list[str] = []
        self.problems: list[str] = []
        self.stop_now = False  # stop(): every card of the domain stops once the script is done
        self.waited = 0.0  # seconds spent waiting for the cards it called

    def call(self, card: Any, name: str, args: dict[str, Any]) -> Any:
        if not self.relay.reaches(card.id):
            raise ScriptError(f"{card.id} cannot be reached from this Relay: wire one of its outputs to it (and it back) to use {name}()")
        if self.arrived is not None and self.arrived.streaming:
            raise ScriptError(f"{card.id}.{name}(): a card call waits for the finished context (put it under if not context.streaming:)")
        context = self.calling.copy() if self.calling is not None else self.arrived if self.arrived is not None else Context()
        return self.wait(self.relay.travel(card, name, args, context))

    def wait(self, coro: Any) -> Any:
        """The script waits for something of the card layer's (a card's answer, a state); that time is not its own."""
        began = time.monotonic()
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result(CALL_WAIT)
        finally:
            self.waited += time.monotonic() - began

    def _what(self, what: Any) -> Context:
        """What output() sends: a context as it is; anything else as this Relay's said in the context that arrived."""
        if isinstance(what, ContextView):
            return what._c.copy()  # noqa: SLF001
        if what is None:
            raise ScriptError("output(…) got None: nothing to send (input(\"x\") is None when the context came in at another input)")
        if isinstance(what, Entry):
            text = what.text
        elif isinstance(what, (list, tuple)):
            text = "\n".join(x.text if isinstance(x, Entry) else str(x) for x in what)
        else:
            text = str(what)
        base = self.arrived.copy() if self.arrived is not None else Context()
        base.add(self.relay.tag("said"), text)
        return base

    def names(self) -> dict[str, Any]:
        """Everything a script can use by name."""
        relay = self.relay

        def now():
            return dt_util.now()

        def states(entity_id: str) -> str:  # as far as the Relay may read it (else as if it were not there)
            state = self.wait(relay.ha.state(str(entity_id)))
            return state["state"] if state else "unknown"

        def state_attr(entity_id: str, name: str) -> Any:
            state = self.wait(relay.ha.state(str(entity_id)))
            return wrap(state["attributes"].get(name)) if state else None

        def get_input(name: str | None = None, read: bool = False) -> ContextView | None:
            if read:
                return ContextView(self.reads.get(str(name)) or Context(), self)
            return self.view if name is None or name == self.port else None

        def output(name: str, what: Any) -> None:
            self.sends.append((str(name), self._what(what)))

        def stop() -> None:
            self.stop_now = True

        def capped_range(*args: int) -> range:
            found = range(*args)
            if len(found) > MAX_RANGE:
                raise ScriptError(f"range up to {MAX_RANGE}")
            return found

        def show(*args: Any) -> None:
            self.printed.append(" ".join(map(str, args))[:1000])

        def print_log(*args: Any) -> None:
            self.logged.append(" ".join(map(str, args))[:1000])

        builtins = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in BUILTINS if n != "range"}
        found = {
            **builtins, "range": capped_range, "print": show, "print_log": print_log, "now": now, "states": states,
            "state_attr": state_attr, "is_state": lambda entity_id, value: states(entity_id) == str(value),
            "input": get_input, "output": output, "stop": stop, "audio": wrap(self.audio or {}),
            "re": SimpleNamespace(search=re.search, match=re.match, fullmatch=re.fullmatch, sub=re.sub, findall=re.findall,
                                  split=re.split, escape=re.escape, I=re.I, IGNORECASE=re.I),
        }
        cards = {c.id: CardRef(c, self) for c in relay.all_cards()}
        found["cards"] = cards
        for card_id, ref in cards.items():
            found.setdefault(card_id, ref)  # the script's own names win (a card called Output is cards["output"])
        return {"__builtins__": {"__import__": _inner_import}, **found}

    def go(self, script: Script) -> None:
        """Run the script, top to bottom."""
        try:
            with timed(self):
                exec(script.code, self.names())  # noqa: S102 - checked by check(): no imports, nothing starting with _
        except Exception as err:  # noqa: BLE001
            self.problems.append(_where(err))

    def answer(self, script: Script, function: str, args: dict[str, Any]) -> Any:
        """A call: only that def runs (the script's other lines do not), with the call's arguments."""
        names = self.names()
        exec(script.defs, names)  # noqa: S102
        try:
            with timed(self):
                return names[function](**args)
        except Exception as err:
            raise ScriptError(_where(err)) from err


class timed:  # noqa: N801 - used as `with timed(run):`
    """While the script's own lines run in this thread, each one first checks the time: past WAIT of its own (the time
    it waited for the cards it called taken off) the script stops there with a mistake on that line."""

    def __init__(self, run: Run) -> None:
        self.run = run

    def __enter__(self) -> None:
        began, run = time.monotonic(), self.run

        def line(frame: Any, event: str, arg: Any) -> Any:
            if event == "line" and time.monotonic() - began - run.waited > WAIT:
                raise ScriptError(f"it ran longer than {WAIT:g} s")
            return line

        sys.settrace(lambda frame, event, arg: line if frame.f_code.co_filename == "<script>" else None)

    def __exit__(self, *exc: object) -> None:
        sys.settrace(None)


def _inner_import(name: str, *args: Any, **kwargs: Any) -> Any:
    """Python's own imports while a script runs (datetime's strftime imports time); a script cannot import."""
    if name not in INNER_IMPORTS:
        raise ImportError(f"{name} cannot be imported here")
    return __import__(name, *args, **kwargs)


def _where(err: Exception) -> str:
    """A mistake with its line in the script."""
    lines = [f.lineno for f in traceback.extract_tb(err.__traceback__) if f.filename == "<script>"]
    what = str(err) if isinstance(err, ScriptError) else f"{type(err).__name__}: {err}"
    return f"line {lines[-1] if lines else '?'}: {what}"
