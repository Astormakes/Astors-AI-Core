"""The Relay's script: Python, checked before it runs. Functions marked `@on("tag")` run on the entries with that tag
(when they are closed, or on every piece with `live=True`); `@new("name")` makes an entry of the card's own. Only the
parts of Python listed in ALLOWED run (no imports, no while, nothing starting with `_`); the code runs in a worker
thread, a wired card's function is called back on HA's loop."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass, field
import fnmatch
import re
import traceback
from types import SimpleNamespace
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import EVENTS, entry

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
WHICH = ("last", "first", "all")
MAX_RANGE = 10000
INNER_IMPORTS = {"time", "_strptime", "datetime", "calendar", "locale", "re", "_locale"}
MISSING = {"id": "", "by": "", "kind": "", "text": "", "tags": [], "data": {}, "state": "closed"}

# what the ? panel and models read: (group, signature, what it does, example)
REFERENCE = [
    ("Basics", "def name(e):", "A block: the lines under it that are moved in (4 spaces) belong to it. It does nothing by itself: the line "
     "with @ right above it says when it runs. The name is only for you; e is what it works on.",
     '@on("chat.said")\ndef shout(e):\n    e.text = e.text.upper()'),
    ("Basics", "if … :", "Only when it is true, the moved-in lines under it run (elif: else if, else: otherwise).",
     'if "stop" in e.text.lower():\n    stop()\nelse:\n    print("no stop")'),
    ("Basics", '"text"', "Text goes in quotes. Long text over several lines: three quotes \"\"\"…\"\"\". f\"It is {now():%H:%M}\" "
     "puts values into a text.", ""),
    ("Basics", "x = 5   x == 5", "= gives something a name (x is 5 from now on); == asks (is x 5?).", ""),
    ("Basics", "for x in list:", "The moved-in lines run once for each item.", 'for word in ["computer", "assistent"]:\n    if word in e.text.lower():\n        output("yes")'),
    ("Basics", "# a note", "Everything after # is a note for you; it does not run.", ""),
    ("Blocks", '@on("chat.said")', "Runs the block when an entry with this tag passes through the Relay (once it is finished). "
     "e is that entry.", '@on("chat.said")\ndef clean(e):\n    e.text = e.text.replace("computer", "")'),
    ("Blocks", '@on("*.said")', "* means any card: everyone's said. Several tags: @on(\"chat.said\", \"ears.heard\").", ""),
    ("Blocks", '@on("chat.said", which="all")', "Which matching entry: \"last\" (the newest, if you leave it out), \"first\" or "
     "\"all\" (the block runs once for each).", ""),
    ("Blocks", '@on("*", kind="said")', "Only entries of one kind: said (what someone said), answer (a model's), result (a "
     "tool's), system (the system prompt), note / trailer (attached before / after a message).", ""),
    ("Blocks", '@on("*.heard", live=True)', "Runs on every piece while the entry is still being written (a stop word in the "
     "middle of a sentence). Without it the Relay waits until the entry is finished.", ""),
    ("Blocks", '@on("context")', "Runs once with the whole context instead of one entry.", ""),
    ("Blocks", '@new("charter")', "Writes a text of this Relay's own: set e.text. It becomes part of the system prompt (what "
     "a model reads first), replaced in place each turn so the model server stays fast. The name in quotes is its tag "
     "and the name of its function for models (For models).", '@new("charter")\ndef charter(e):\n    e.text = """You are AAC."""'),
    ("Blocks", '@new("time", at="end")', "Attached after the newest thing said: what matters for this turn. A new one each "
     "turn; replace=True keeps only one, on the newest.", '@new("time", at="end")\ndef time(e):\n    e.text = f"It is {now():%H:%M}."'),
    ("Blocks", '@new("room", before="chat.said")', "Attached right before (or after=) the newest entry with that tag.", ""),
    ("Entry", "e.text", "Its text. Change it with e.text = \"…\"", ""),
    ("Entry", "e.tags", "Its tags, e.g. [\"chat.said\"]: if \"user.urgent\" in e.tags:", ""),
    ("Entry", "e.kind", "What it is: said, answer, result, system, note, trailer.", ""),
    ("Entry", "e.by", "The card that wrote it (its id).", ""),
    ("Entry", "e.calls", "An answer's tool calls: [{name, args}] (empty for others).", ""),
    ("Entry", "e.data", "What the card that wrote it added (e.g. the satellite: e.data[\"mic\"]).", ""),
    ("Entry", "e.add_tag(\"urgent\")", "Adds a tag (without a dot it becomes user.urgent).", ""),
    ("Entry", "e.remove()", "Takes the entry out of the context.", ""),
    ("Other entries", "chat.said.text", "The text of the newest entry with another tag (empty when there is none).", ""),
    ("Other entries", "context.text", "The whole context as one text.", ""),
    ("Other entries", "context.entries", "Every entry, oldest first.", ""),
    ("Other entries", 'context.find("*.said")', "The newest entry with a tag (which=\"first\", kind=\"said\" work too).", ""),
    ("Other entries", 'context.find_all("*.said")', "Every entry with a tag, oldest first.", ""),
    ("Text", 'text.replace("a", "b")', "Every a becomes b (capitals count).", '"Hi computer".replace("computer", "AAC") → "Hi AAC"'),
    ("Text", "text.strip()", "Removes spaces and empty lines at the start and the end.", '"  hi  ".strip() → "hi"'),
    ("Text", "text.lower()", "Small letters (upper(): capitals, title(): Each Word Capital).", '"Stop".lower() → "stop"'),
    ("Text", '"stop" in text', "True when the text contains it (use text.lower() to ignore capitals).", ""),
    ("Text", 'text.startswith("hey")', "True when it starts so (endswith: ends so).", ""),
    ("Text", 'text.split(",")', "A list of the parts between the commas.", '"a,b".split(",") → ["a", "b"]'),
    ("Text", '", ".join(items)', "One text from a list, with \", \" between.", '", ".join(["a", "b"]) → "a, b"'),
    ("Text", 'between(text, "from", "to")', "The part after \"from\" and before \"to\" (empty \"\": from the start / to the end).",
     'between("say: hi!", ": ", "!") → "hi"'),
    ("Text", "len(text)", "How many characters (or how many items in a list).", ""),
    ("Text", 're.search(r"\\bstop\\b", text, re.I)', "A pattern (regular expression); re.sub(pattern, new, text) replaces.", ""),
    ("Actions", 'output("night")', "Picks the way out. The card gets an output called night (it appears on the card as soon "
     "as the script uses it). When this line runs, the context leaves only there, to the cards wired from night, not at "
     "out. When it does not run, the context leaves at out as usual. Nothing wired there: the context ends.",
     '@on("*", kind="said")\ndef wake_word(e):\n    if "computer" in e.text.lower():\n        output("yes")   # to the cards wired from yes\n    # otherwise: to out'),
    ("Actions", "stop()", "Sends stop: every card lets go of what it holds.", ""),
    ("Actions", 'signal("done")', "Adds a signal (user.done) other cards can react to; never sent to a model.", ""),
    ("Actions", 'lists.add_items(list="Shopping", items=[e.text])', "Calls a function of a card in this Relay's loop (wired "
     "from it and back into it, straight or round; the card's id, a dot, the function). The call travels along the wires; the script waits for the "
     "result.", ""),
    ("Actions", 'print("…")', "Shows under Last run (for checking a script).", ""),
    ("Values", "now()", "The time now: now().hour, f\"{now():%A %H:%M}\".", ""),
    ("Values", 'states("sensor.x")', "A Home Assistant state (state_attr(id, \"name\"), is_state(id, \"on\")).", ""),
    ("Values", "lists.total", "A card's value (each card lists its values).", ""),
    ("Values", "audio.last.seconds", "When AAC last spoke (audio.satellites[id] per satellite).", ""),
]


def reference_text() -> str:
    """The reference as plain text (for models)."""
    return "\n".join(f"{sig}: {what}" for _, sig, what, _ in REFERENCE)


class ScriptError(Exception):
    """A mistake in a script (shown with its line)."""


@dataclass
class Handler:
    """One function of the script."""

    name: str
    line: int
    new: bool
    patterns: tuple[str, ...] = ()
    which: str = "last"
    kind: str | None = None
    live: bool = False
    tag: str = ""
    at: str = "start"
    before: str | None = None
    after: str | None = None
    replace: bool = True


@dataclass
class Script:
    """A checked script: its functions, outputs and problems."""

    source: str
    handlers: list[Handler] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    names: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    code: Any = None

    @property
    def new_tags(self) -> list[str]:
        """The tags its @new blocks write (without the card)."""
        return [h.tag for h in self.handlers if h.new]


def _const(node: ast.AST, what: str) -> Any:
    if not isinstance(node, ast.Constant):
        raise ScriptError(f"line {node.lineno}: {what} must be written out (in quotes, or True / False)")
    return node.value


def _decorator(fn: ast.FunctionDef) -> Handler:
    if len(fn.decorator_list) != 1 or not isinstance(dec := fn.decorator_list[0], ast.Call) \
            or not isinstance(dec.func, ast.Name) or dec.func.id not in ("on", "new"):
        raise ScriptError(f"line {fn.lineno}: put @on(\"tag\") or @new(\"name\") above def {fn.name}")
    if len(fn.args.args) > 1 or fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs:
        raise ScriptError(f"line {fn.lineno}: def {fn.name}(e) takes one name: the entry")
    kw = {k.arg: _const(k.value, k.arg or "it") for k in dec.keywords}
    args = [_const(a, "the tag") for a in dec.args]
    if dec.func.id == "new":
        unknown = set(kw) - {"at", "before", "after", "replace"}
        if unknown or len(args) != 1 or not isinstance(args[0], str) or not re.fullmatch(r"[a-z0-9_]+", args[0]):
            raise ScriptError(f"line {fn.lineno}: @new(\"name\") takes one name (small letters, digits, _) and at=, "
                              "before=, after=, replace=")
        at = kw.get("at", "start")
        if at not in ("start", "end"):
            raise ScriptError(f"line {fn.lineno}: at= is \"start\" or \"end\"")
        return Handler(fn.name, fn.lineno, True, tag=args[0], at=at, before=kw.get("before"), after=kw.get("after"),
                       replace=bool(kw.get("replace", at == "start" or bool(kw.get("before") or kw.get("after")))))
    unknown = set(kw) - {"which", "kind", "live"}
    if unknown or not args or not all(isinstance(a, str) and a for a in args):
        raise ScriptError(f"line {fn.lineno}: @on(\"tag\", …) takes tags in quotes and which=, kind=, live=")
    if kw.get("which", "last") not in WHICH:
        raise ScriptError(f"line {fn.lineno}: which= is \"last\", \"first\" or \"all\"")
    if "context" in args and len(args) > 1:
        raise ScriptError(f"line {fn.lineno}: @on(\"context\") stands alone")
    return Handler(fn.name, fn.lineno, False, tuple(args), kw.get("which", "last"), kw.get("kind"), bool(kw.get("live")))


def check(source: str) -> Script:
    """Read a script: its blocks, outputs and the names it uses; problems instead of code when it is not allowed."""
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
        elif kind is ast.Call and isinstance(node.func, ast.Name) and node.func.id == "output":
            if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str) \
                    or not re.fullmatch(r"[a-z0-9_]+", node.args[0].value):
                script.problems.append(f"line {node.lineno}: output(\"name\") takes its name in quotes (small letters, digits, _)")
            elif node.args[0].value not in script.outputs:
                script.outputs.append(node.args[0].value)
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            try:
                handler = _decorator(node)
            except ScriptError as err:
                script.problems.append(str(err))
                continue
            if node.name in names:
                script.problems.append(f"line {node.lineno}: there is already a def {node.name}")
            names.add(node.name)
            script.handlers.append(handler)
            node.decorator_list = []
        elif not isinstance(node, (ast.Assign, ast.AugAssign)) and not (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
            script.problems.append(f"line {node.lineno}: at the top only def blocks and x = … (put it inside a def)")
    if len({h.tag for h in script.handlers if h.new}) < len(script.new_tags):
        script.problems.append("two @new blocks have the same name")
    if not script.problems:
        script.code = compile(tree, "<script>", "exec")
    return script


# ------------------------------------------------------------------ what the script sees
def _matches(e: dict[str, Any], patterns: tuple[str, ...], kind: str | None) -> bool:
    if e["kind"] in EVENTS or (kind and e["kind"] != kind):
        return False
    return any(p == "*" or any(fnmatch.fnmatchcase(t, p) for t in e["tags"]) for p in patterns)


def _pick(found: list[dict[str, Any]], which: str) -> list[dict[str, Any]]:
    return found if which == "all" else found[:1] if which == "first" else found[-1:]


class Values:
    """A card's values or the core's: read as values.x or values["x"]."""

    __slots__ = ("_v",)

    def __init__(self, v: dict[str, Any]) -> None:
        self._v = v

    def __getattr__(self, name: str) -> Any:
        if name not in self._v:
            raise AttributeError(f"no value {name} (there are: {', '.join(map(str, self._v)) or 'none'})")
        return wrap(self._v[name])

    def __getitem__(self, name: Any) -> Any:
        return wrap(self._v[name])

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
    """An entry as the script sees it."""

    __slots__ = ("_e", "_run", "_name")

    def __init__(self, e: dict[str, Any], run: Run, name: str = "") -> None:
        self._e, self._run, self._name = e, run, name

    @property
    def text(self) -> str:
        """Its text."""
        return self._e["text"]

    @text.setter
    def text(self, value: Any) -> None:
        if self._e is MISSING:
            raise ScriptError(f"there is no {self._name} in this context to change")
        self._e["text"] = str(value)

    @property
    def tags(self) -> list[str]:
        """Its tags."""
        return list(self._e["tags"])

    @property
    def kind(self) -> str:
        """Its kind."""
        return self._e["kind"]

    @property
    def by(self) -> str:
        """The card that wrote it."""
        return self._e["by"]

    @property
    def data(self) -> Values:
        """What its card added."""
        return Values(dict(self._e.get("data") or {}))

    @property
    def state(self) -> str:
        """open (still growing), closed or cut."""
        return self._e.get("state", "closed")

    @property
    def calls(self) -> list[Values]:
        """An answer's tool calls: name, args."""
        return [Values({"name": c.get("name"), "args": c.get("args") or {}}) for c in (self._e.get("data") or {}).get("calls") or []]

    def add_tag(self, tag: str) -> None:
        """Adds a tag (without a dot: user.<tag>)."""
        tag = str(tag) if "." in str(tag) else f"user.{tag}"
        if self._e is not MISSING and tag not in self._e["tags"]:
            self._e["tags"] = [*self._e["tags"], tag]

    def remove(self) -> None:
        """Takes it out of the context."""
        if self._e is not MISSING:
            self._run.context.remove(self._e)

    def __bool__(self) -> bool:
        return self._e is not MISSING

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return repr(self.text)

    def __format__(self, spec: str) -> str:
        return format(self.text, spec)

    def __eq__(self, other: object) -> bool:
        return self.text == (other.text if isinstance(other, Entry) else other)

    def __hash__(self) -> int:
        return hash(self._e["id"])

    def __contains__(self, part: str) -> bool:
        return part in self.text


class ContextView:
    """The whole context."""

    __slots__ = ("_run",)

    def __init__(self, run: Run) -> None:
        self._run = run

    @property
    def entries(self) -> list[Entry]:
        """Every entry, oldest first (events left out)."""
        return [Entry(e, self._run) for e in self._run.context.entries if e["kind"] not in EVENTS]

    @property
    def text(self) -> str:
        """Every entry's text, one after the other."""
        return "\n".join(e["text"] for e in self._run.context.entries if e["kind"] not in EVENTS and e["text"].strip())

    def find(self, tag: str = "*", which: str = "last", kind: str | None = None) -> Entry:
        """The newest (or first) entry with a tag; empty when none."""
        found = _pick([e for e in self._run.context.entries if _matches(e, (tag,), kind)], "first" if which == "first" else "last")
        return Entry(found[0] if found else MISSING, self._run, tag)

    def find_all(self, tag: str = "*", kind: str | None = None) -> list[Entry]:
        """Every entry with a tag, oldest first."""
        return [Entry(e, self._run) for e in self._run.context.entries if _matches(e, (tag,), kind)]

    def __str__(self) -> str:
        return self.text

    def __contains__(self, part: str) -> bool:
        return part in self.text


class CardRef:
    """Another card: its functions (when it is wired to the Relay and back), its values, the entries with its tags."""

    __slots__ = ("_card", "_run")

    def __init__(self, card: Any, run: Run) -> None:
        self._card, self._run = card, run

    def __getattr__(self, name: str) -> Any:
        card = self._card
        if name in card.functions:
            spec = card.functions[name].get("args") or {}

            def call(*args: Any, **kwargs: Any) -> Any:
                kwargs.update(zip(spec, args))
                return self._run.call(card, name, kwargs)
            return call
        values = card.current() if card.values else {}
        if name in values:
            return wrap(values[name])
        tag = f"{card.id}.{name}"
        found = [e for e in self._run.context.entries if tag in e["tags"]]
        return Entry(found[-1] if found else MISSING, self._run, tag)


# ------------------------------------------------------------------ running
class Run:
    """One run of a script on a context (in a worker thread)."""

    def __init__(self, relay: Any, context: Any, snapshot: bool, done: dict[Any, Any], tools: list[Any],
                 loop: asyncio.AbstractEventLoop, audio: Any = None) -> None:
        self.relay, self.context, self.snapshot, self.done, self.tools, self.loop = relay, context, snapshot, done, tools, loop
        self.audio = audio
        self.outputs: list[str] = []
        self.hold = False  # a snapshot that goes no further (a context block waits for everything to close)
        self.stop_all = False  # stop() during a snapshot: the card stops the domain afterwards
        self.ran: list[str] = []
        self.printed: list[str] = []
        self.problems: list[str] = []
        self._key: tuple[Any, ...] = ()

    def once(self, what: str, *args: Any) -> bool:
        """A side effect happens once per turn, block and entry (a snapshot and the finished turn run it again)."""
        key = (*self._key, what, repr(args))
        if key in self.done:
            return False
        self.done[key] = True
        return True

    def call(self, card: Any, name: str, args: dict[str, Any]) -> Any:
        if card not in self.tools:
            raise ScriptError(f"{card.id} is not in this Relay's loop: wire it so (from the Relay, back into it) to use {name}()")
        key = (*self._key, "call", card.id, name, repr(args))
        if key in self.done:
            return self.done[key]
        result = asyncio.run_coroutine_threadsafe(self.relay.travel(card, name, args, self.context), self.loop).result(60)
        self.done[key] = result
        return result

    def names(self, script: Script) -> dict[str, Any]:
        """Everything a script can use by name."""
        hass, relay = self.relay.hass, self.relay

        def now():
            return dt_util.now()

        def states(entity_id: str) -> str:
            state = hass.states.get(str(entity_id))
            return state.state if state else "unknown"

        def state_attr(entity_id: str, name: str) -> Any:
            state = hass.states.get(str(entity_id))
            return wrap(state.attributes.get(name)) if state else None

        def stop() -> None:
            if self.snapshot:
                self.stop_all = True
            elif self.once("stop"):
                self.context.add(relay.id, "signal", "stop", [relay.tag("stop")])

        def signal(name: str) -> None:
            if not self.snapshot and self.once("signal", name):
                self.context.add(relay.id, "signal", str(name), [name if "." in str(name) else f"user.{name}"])

        def output(name: str) -> None:
            if name not in self.outputs:
                self.outputs.append(name)

        def between(text: Any, start: str = "", end: str = "") -> str:
            text, low = str(text), str(text).lower()
            at = low.find(start.lower()) + len(start) if start and start.lower() in low else 0
            to = low.find(end.lower(), at) if end and end.lower() in low[at:] else len(text)
            return text[at:to].strip()

        def capped_range(*args: int) -> range:
            found = range(*args)
            if len(found) > MAX_RANGE:
                raise ScriptError(f"range up to {MAX_RANGE}")
            return found

        def show(*args: Any) -> None:
            if not self.snapshot:
                self.printed.append(" ".join(map(str, args))[:300])

        builtins = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in (
            "len", "str", "int", "float", "bool", "round", "min", "max", "abs", "sorted", "reversed", "any", "all",
            "list", "dict", "set", "tuple", "enumerate", "zip", "isinstance", "ValueError")}
        found = {
            **builtins, "range": capped_range, "print": show, "now": now, "states": states, "state_attr": state_attr,
            "is_state": lambda entity_id, value: states(entity_id) == str(value), "stop": stop, "signal": signal,
            "output": output, "between": between, "context": ContextView(self),
            "re": SimpleNamespace(search=re.search, match=re.match, fullmatch=re.fullmatch, sub=re.sub, findall=re.findall,
                                  split=re.split, escape=re.escape, I=re.I, IGNORECASE=re.I),
        }
        if "audio" in script.names:
            found["audio"] = wrap(self.audio or {})
        cards = {c.id: CardRef(c, self) for c in relay.domain.cards.values()}
        found["cards"] = cards
        for name in script.names - set(found):
            if name in cards:
                found[name] = cards[name]
        return {"__builtins__": {"__import__": _inner_import}, **found}

    def go(self, script: Script) -> None:
        """Run every block of the script on the context, top to bottom."""
        names = self.names(script)
        try:
            exec(script.code, names)  # noqa: S102 - checked by check(): no imports, nothing starting with _
        except Exception as err:  # noqa: BLE001
            self.problems.append(_where(err, "the top"))
            return
        for n, h in enumerate(script.handlers):
            try:
                self._handler(names[h.name], h, n)
            except Exception as err:  # noqa: BLE001 - one block's mistake must not stop the others
                self.problems.append(_where(err, h.name))

    def _handler(self, fn: Any, h: Handler, n: int) -> None:
        entries = self.context.entries
        if h.new:
            self._key = (n, "new")
            item = {**MISSING, "id": "", "tags": [], "data": {}}
            box = Entry(item, self, h.tag)
            item["text"] = ""
            fn(box) if _takes(fn) else fn()
            self.ran.append(h.name)
            self._place(h, item["text"].strip())
            return
        if h.patterns == ("context",):
            if self.snapshot and not h.live and any(e.get("state") == "open" for e in entries):
                self.hold = True
                return
            self._key = (n, "context")
            fn(ContextView(self)) if _takes(fn) else fn()
            self.ran.append(h.name)
            return
        for e in _pick([e for e in entries if _matches(e, h.patterns, h.kind)], h.which):
            if self.snapshot and not h.live and e.get("state") == "open":
                e["text"] = ""  # held back until it is closed
                continue
            self._key = (n, e["id"])
            fn(Entry(e, self)) if _takes(fn) else fn()
            self.ran.append(h.name)

    def _place(self, h: Handler, text: str) -> None:
        """Where a @new entry goes: at the start a system part; at the end attached after the newest thing said;
        before= / after= attached to the message of the newest entry with that tag (a system part beside a system part)."""
        relay, context = self.relay, self.context
        if not (h.before or h.after):
            context.put(relay.id, "system" if h.at == "start" else "trailer", text, h.tag, replace_it=h.replace)
            return
        tag = relay.tag(h.tag)
        if h.replace:
            for e in [e for e in context.entries if e["by"] == relay.id and tag in e["tags"]]:
                context.remove(e)
        anchor = next((e for e in reversed(context.entries) if _matches(e, (h.before or h.after,), None)), None)
        if anchor is None or not text:
            return
        if anchor["kind"] == "system":
            item = entry(relay.id, "system", text, [h.tag])
            at = next(i for i, e in enumerate(context.system) if e is anchor)
            context.system.insert(at if h.before else at + 1, item)
            return
        message = next((m for m in context.messages if m is anchor or any(a is anchor for a in m.get("attachments", []))), None)
        item = entry(relay.id, "note" if h.before else "trailer", text, [h.tag])
        attached = message.setdefault("attachments", [])
        at = next((i for i, a in enumerate(attached) if a is anchor), len(attached) if h.after else 0)
        attached.insert(at if h.before else at + 1, item)


def _inner_import(name: str, *args: Any, **kwargs: Any) -> Any:
    """Python's own imports while a script runs (datetime's strftime imports time); a script cannot import."""
    if name not in INNER_IMPORTS:
        raise ImportError(f"{name} cannot be imported here")
    return __import__(name, *args, **kwargs)


def _takes(fn: Any) -> bool:
    return fn.__code__.co_argcount == 1


def _where(err: Exception, block: str) -> str:
    """A mistake with its line in the script."""
    lines = [f.lineno for f in traceback.extract_tb(err.__traceback__) if f.filename == "<script>"]
    what = str(err) if isinstance(err, ScriptError) else f"{type(err).__name__}: {err}"
    return f"line {lines[-1] if lines else '?'} ({block}): {what}"
