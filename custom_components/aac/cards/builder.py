"""Card editor: lets a model see and change the setup (cards and wires) of its domain, with your permission settings:
straight in what runs, in a slot, or in the draft (slot 4) it then switches to and must keep; every change can be
undone (Cards → Undo)."""

from __future__ import annotations

import json

from typing import Any

from custom_components.aac.api import CARD_TYPES, RULES, Card, Context, register

VERSION = "3.0.4"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)


# (no function named "call": with it among many tools, Gemma 4 stops before its first call, live 2026-11-03)
BUILDS = {"add_card", "change_card", "remove_card", "wire", "unwire", "copy", "switch"}  # after the guide
SHORT = 80  # longer settings are shortened in the list of cards (characters)
WHERE = {"type": "string", "help": "Which setup: now (default: the one that runs), 1, 2, 3 (the owner's slots), 4 (your draft)."}

GUIDE = """How to build in AAC with the Card editor. Read it once per conversation before you change anything.

1. What you work with
- A domain is one setup: cards and the wires between them. Everything AAC does is a card: the chat you talk in, you
  (a Model card), the tools you call, the system prompt (a Relay card writes it), the voice chain.
- A card has inputs and outputs (ports). A wire goes from an output to an input of the same kind: text into text,
  audio into audio, a clip into a clip. A read output (e.g. 'provides') goes only into a read input: a dotted wire,
  read when something else arrives, never starting anything.
- What moves on text wires is a context: a list of entries (tag, text, data). Call rules for the few rules every
  card follows. Each card type's help (call help with its type) says what it does, its ports, settings and functions.
- Where you build (the argument where): now = the setup that runs; 1, 2, 3 = the owner's slots (saved setups);
  4 = your draft. Nothing in a slot or the draft runs until something switches to it.

2. Small change or draft: decide first
- Small and safe (a setting, a name, a new tool card wired from a model's 'calls' and back into its 'in', a new
  Relay in a side path): change now, straight. It runs at once; every change can be undone.
- Big, or anything on your own way (the chat, the cards between the chat and you, you, this Card editor, their
  wires), or several changes that only work together: build in the draft. A half-done change to what runs can cut
  you off, and then nobody can fix it.
  - copy(from="now", to="4") starts the draft from what runs: do this first. Without it the draft holds only what
    you add (no chat, no model, not you), and wires to those fit nothing there.
  - Change it with where="4": add_card, change_card, remove_card, wire, unwire. Look with cards(where="4").
  - check(where="4") lists what is wrong (no such type, a wire that fits nothing, a setting a type does not have,
    this Card editor not wired in and out there). Fix all of it.
  - switch(where="4") is the last thing you call in that answer: say in one sentence what you switch to and end
    your answer. Once it is written the new setup runs (every card is made again).
  - Then a message from the Card editor comes into this conversation. Only a working way back reaches you, so
    read it as the proof: look whether it does what it should (cards, try_card to try a card), then call keep. No keep
    within the time it names: the setup before comes back by itself, and the draft stays as it is to fix.
  - Once switched, the draft's cards are what runs: fix and change them with where="now" from then on (a change
    in slot 4 changes nothing that runs).
- Never change slots 1, 2, 3 unless the owner asks: they are the owner's saved setups.

3. The order that works
- rules (once), cards (what runs: ids, ports, settings, wires), types (what can be added), help for every type you
  add or set up: its exact port names (a Chat's output is 'said', its input 'answers'; a Model's input is 'in', its
  outputs 'out' and 'calls') and its settings (only those exist: a Relay has one, 'script'). Ids are names: a card called "Web search" has the id web_search; its tag starts with it.
- Plan the whole path before the first change: where does a context start, which cards does it pass, where does
  the answer go. Then add the cards, then the wires, then try it.
- Settings: only those the type's help lists, with values of the kind it says. Permission settings (May use, For
  models, and those marked so) are the owner's: you cannot set them.
- A new card that reaches outside AAC (Web: web, File: files, Home Assistant: entities, Notify: notify …) may do
  nothing there until the owner allows it (its May use). Once it runs, ask with try_card(card, "request_access",
  {things, level, reason}) and tell the owner what you asked for and why; until then it answers with an error.
- After every change look at the answer: it shows what is there now (a wire that does not fit is left out).

4. The usual shapes
- A chat with a model: Chat.said -> (Relays that add to the context: the system prompt, the time) -> Model.in;
  Model.out -> Chat.answers and Model.calls -> Chat.answers (the chat shows the calls too).
- A tool: a card with functions, wired Model.calls -> tool.in and tool.out -> Model.in. The model then has its
  functions as tools (the ones ticked under For models). Through other cards works too, as long as it comes back.
- The system prompt: a Relay before the model with
      context = input("in")
      context.set("context.system", \"\"\"You are …\"\"\")
      output("out", context)
  Several Relays in a row each add their part (a charter, the time) with context.set or context.add.
- Something read into every turn (a file, a chat's history): a dotted wire from the card's 'provides' into a
  Relay's read input, e.g. a File card's provides -> Memory reader.memory, and in the Relay's script:
      context = input("in")
      notes = input("memory", read=True)
      if notes and notes.get("*.text"):
          context.set("context.system", context.system.text + "\\n\\nWhat you keep in your notes:\\n" + notes.get("*.text").text)
      output("out", context)
  (A File card's provides holds one entry tagged <file>.text with the whole file.) Wire the File also as a tool
  (Model.calls -> file.in, file.out -> Model.in) and the model can write it; set its Appends setting to empty then,
  or it appends every answer that passes.
- Waking a chain without a message: Schedule (times), Heartbeat (every N minutes), HA event (an entity changes):
  their out goes where a chat's said would go, and the answer comes back into their 'answers'.
- Voice: Microphone -> Audio clipper.in, clipper.out -> Speech to text.in, Speech to text.out -> clipper.lines;
  clipper.said is what was heard (into a wake word Relay, then the model's way); Model.out -> Text to speech.in,
  Text to speech.out -> Speaker.in. Leave a voice chain that works alone unless the owner asks.

5. Relays (scripts)
- Call help with relay before writing one: it lists everything a script can do. A script is checked Python: no
  imports, no while, nothing that starts with _. It runs top to bottom for every context that arrives, and only
  output(...) sends; a script that forgets output stops the chain there.
- A model's answer arrives again after every finished sentence (context.streaming is True until the last): pass it
  on each time; call another card only under if not context.streaming:.
- A def in a script is a function models can use only once the owner ticks it under For models, and it gets its
  arguments, not the context (there is no context inside a def; it calls other cards or returns a value). For a
  tool, prefer a card that already has the functions (File: read, append, write; Lists; Memory; Web) wired as a
  tool: it works at once.

6. Trying it
- try_card(card, function, args) runs another card's function as a model would (only what it offers to models; its
  permissions apply): read a file you wired, search with a Web card, read a list.
- Try every card you added before you say it works. Say exactly what works, what does not yet and why (a
  permission the owner must give, a function the owner must tick). For a whole path the owner's next message is the
  test; say what to try.
- Clean up after yourself: a card you added twice or no longer use, remove it (or ask the owner to).

7. What not to do
- Do not remove or rewire the chat, the cards on your way or this Card editor in what runs: use the draft.
- No loops without an end: a context that goes round and round turns the wire red (blocked). A red wire stays red
  until it is fixed and unblocked; every answer here lists red wires.
- Do not remove cards you did not add unless the owner asks (removing needs the owner's May remove).
- Limits are not puzzles: what is not allowed is the owner's decision. Tell the owner, or use request_access.
"""


def setup_help(kind: Any) -> str:
    """A card type's setup help, from what every card declares (its help, ports, settings, functions): the same as
    the ? panel shows."""
    t = kind.describe()
    ports = t.get("ports_help") or {}
    lines = [f"{t['title']} (type {t['type']}, version {t['version']}): {t['help']}"]
    for title, found in (("Inputs", t["inputs"]), ("Outputs", t["outputs"])):
        if found:
            lines.append(f"{title}:")
            lines += [f"- {p} ({k}){f': {ports[p]}' if ports.get(p) else ''}" for p, k in found.items()]
    if ports.get("*"):
        lines.append(f"Other ports: {ports['*']}")
    if t["settings"]:
        lines.append("Settings:")
        for key, f in t["settings"].items():
            lines.append(f"- {key} ({f.get('title') or key}, default {json.dumps(f.get('default'), ensure_ascii=False)[:200]})"
                         + (" (the owner's: a permission)" if f.get("permission") else "") + (f": {f['help']}" if f.get("help") else ""))
    if t["functions"]:
        lines.append("Functions:")
        lines += [f"- {n}({', '.join(a + ('' if x.get('required') else '?') for a, x in (f.get('args') or {}).items())}): {f.get('help', '')}"
                  for n, f in t["functions"].items()]
    return "\n".join(lines)


@register
class Builder(Card):
    """A model builds."""

    type = "builder"
    title = "Card editor"
    category = "Other"
    help = ("Wired from a model and back: the model can look at and change the cards and wires of this domain (e.g. "
            "\"set up a reminder card\"): small changes straight in what runs, big ones in the draft (slot 4), which it "
            "switches to and must keep in time, else the setup before comes back by itself. Its guide tells the model "
            "how to build and when to use which. Every change can be undone on the Cards page. While a wire is red (the "
            "storm stop blocked it: too many contexts went through it at once), every answer of this card says so, and "
            "the model can unblock it once the wiring is fixed.")
    needs = {"setup"}  # read: look at the setup; ask: build in the draft (and every other change waits for the owner's
    # yes); full: change what runs and the owner's slots (never a permission: the owner's)
    settings_schema = {
        "may_remove": {"type": "bool", "title": "May remove cards", "default": False, "permission": True},
    }
    functions = {
        "guide": {"help": "How to build in AAC: read it once per conversation before you change anything (when to change "
                          "straight, when to build a draft and switch to it, the usual shapes, what not to do)."},
        "rules": {"help": "How AAC works: the few rules every card follows (entries, contexts, when a card acts and "
                          "sends, calls, stop). Read them before building or wiring."},
        "cards": {"help": "An overview of a setup: each card's id, type, name, inputs and outputs and settings (long "
                          "ones shortened: see card), and the wires.", "args": {"where": WHERE}},
        "card": {"help": "One card with all its settings.", "args": {"card": {"type": "string", "help": "Its id.", "required": True},
                                                                     "where": WHERE}},
        "types": {"help": "The card types that can be added, one line each (help gives one type in full)."},
        "help": {"help": "Everything about one card type, as its ? help shows it: what it does, its inputs and outputs, "
                         "its settings, its functions (a Relay's: how to write its script). Read it before adding or "
                         "setting up a card of that type.",
                 "args": {"type": {"type": "string", "help": "A type from types (e.g. relay).", "required": True}}},
        "add_card": {"help": "Add a card.", "args": {"type": {"type": "string", "help": "A type from types.", "required": True},
                                                     "name": {"type": "string", "help": "Its name (its id follows it)."},
                                                     "settings": {"type": "object", "help": "Its settings."}, "where": WHERE}},
        "change_card": {"help": "Change a card's name or settings.", "args": {"card": {"type": "string", "help": "Its id.", "required": True},
                                                                            "name": {"type": "string", "help": "New name."},
                                                                            "settings": {"type": "object", "help": "Settings to change."},
                                                                            "where": WHERE}},
        "remove_card": {"help": "Remove a card (with its wires).", "args": {"card": {"type": "string", "help": "Its id.", "required": True},
                                                                            "where": WHERE}},
        "wire": {"help": "Wire an output to an input ('card.port').", "args": {"from": {"type": "string", "help": "e.g. chat.said", "required": True},
                                                                              "to": {"type": "string", "help": "e.g. model.in", "required": True},
                                                                              "where": WHERE}},
        "unwire": {"help": "Remove a wire.", "args": {"from": {"type": "string", "help": "card.port", "required": True},
                                                      "to": {"type": "string", "help": "card.port", "required": True}, "where": WHERE}},
        "copy": {"help": "Copy a setup over a slot (what the slot held is gone), e.g. from now to 4 to start a draft.",
                 "args": {"from": {"type": "string", "help": "now, 1, 2, 3 or 4.", "required": True},
                          "to": {"type": "string", "help": "1, 2, 3 (the owner's: only when asked) or 4 (your draft).", "required": True}}},
        "check": {"help": "What is wrong with a setup before it runs (no such type, wires that fit nothing, unknown "
                          "settings; in a slot also: this Card editor must be wired in and out there). Empty: nothing found.",
                  "args": {"where": WHERE}},
        "switch": {"help": "Run a slot instead of what runs now (usually your draft, 4), once your answer is written: make "
                           "it the last call, say what you switch to, end your answer. A message then comes into this "
                           "conversation: call keep after it, else the setup before comes back by itself.",
                   "args": {"where": {"type": "string", "help": "1, 2, 3 or 4 (default: 4).", "required": False}}},
        "keep": {"help": "Keep the setup you switched to (after the Card editor's message that it runs)."},
        "try_card": {"help": "Try another card: run one of its functions as a model would (only what it offers to models; "
                         "its permissions apply).",
                 "args": {"card": {"type": "string", "help": "Its id.", "required": True},
                          "function": {"type": "string", "help": "One of its functions (card shows them).", "required": True},
                          "args": {"type": "object", "help": "Its arguments."}}},
        "unblock": {"help": "Let a red wire carry again (the storm stop blocked it: more contexts went through it at once "
                              "than a turn sends, the wiring loops). Fix the loop first.",
                    "args": {"from": {"type": "string", "help": "card.port", "required": True},
                             "to": {"type": "string", "help": "card.port", "required": True}}},
    }

    async def start(self) -> None:
        """Made again after its own switch: the conversation that asked for it goes on, here, with the news."""
        if (found := self.switched()) and found["context"] is not None:
            self.later(1, lambda: self._switched(found))

    async def _switched(self, found: dict[str, Any]) -> None:
        context: Context = found["context"]
        problems = self.check_setup("now")
        context.add(self.tag("said"), f"(Card editor) The switch to slot {found['slot']} is done: the new setup runs. "
                    "You read this, so the way back to you works. Look whether it does what it should (cards; try_card to try "
                    f"a card), then call keep within {found['seconds']} seconds: without keep the setup before comes back "
                    "by itself." + (f" Found in what runs now: {'; '.join(problems)}." if problems else ""))
        await self.send(context, "out")

    def _made(self, context: Context | None, function: str, args: dict[str, Any] | None = None) -> int:
        """How often this conversation called one of its functions (with these arguments)."""
        return sum(1 for e in (context.entries if context else []) for c in e["data"].get("calls") or []
                   if c.get("card") == self.id and c.get("function") == function and (args is None or c.get("args") == args))

    def _kind(self, function: str, args: dict[str, Any]) -> str | None:
        """The card type a call sets up (adding one, changing settings): its help comes first."""
        if function == "add_card":
            return str(args.get("type") or "") or None
        if function == "change_card" and args.get("settings"):
            spec = self.read_setup(str(args.get("where") or "now"))["cards"].get(str(args.get("card") or ""))
            return spec["type"] if spec else None
        return None

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        """Every answer says which wires are red right now (when it may look at the setup); what it may not do is told
        as an error (the interface refuses it, not this card). A model builds only after it read the guide in this
        conversation; the same failing call again is told so."""
        if context is not None and function in BUILDS and not self._made(context, "guide"):
            return {"error": "Read the guide first: call guide (once per conversation). It says how to build here: when "
                             "to change what runs and when to build a draft, how to look at the cards and their ports, "
                             f"the usual shapes. Then call {function} again, the same way."}
        if context is not None and (kind := self._kind(function, args)) and not self._made(context, "help", {"type": kind}):
            return {"error": f"Read help with type {kind} first (its ports, settings and functions, exactly). Then call "
                             f"{function} again."}
        try:
            result = await self._call(function, args, context)
        except (PermissionError, ValueError, KeyError) as err:
            result = {"error": str(err)}
        if (n := self._made(context, function, args)) > 1:  # a model that goes round in circles is told so
            result = result if isinstance(result, dict) else {"result": result}
            result["note"] = (f"You made this same call {n} times in this conversation, and the answer is the same: "
                              + ("read the error, look (cards, card, help), change something, or tell the owner."
                                 if result.get("error") else "you have it already above. Go on with the next step."))
        if self.may("setup", "read") and (red := self.read_setup()["red"]):
            return {**(result if isinstance(result, dict) else {"result": result}), "red_wires": red,
                    "note": "These wires are blocked (red): the storm stop found a loop there. Tell the owner; fix the "
                            "wiring, then unblock them."}
        return result

    async def _call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        where = str(args.get("where") or "now")
        if function == "guide":
            return GUIDE
        if function == "rules":
            return RULES
        if function == "types":
            return [f"{t.type} ({t.title}): {t.help}" for t in CARD_TYPES.values()]
        if function == "help":
            kind = CARD_TYPES.get(str(args.get("type") or ""))
            return setup_help(kind) if kind else {"error": f"no type {args.get('type')} (see types)"}
        if function == "cards":  # an overview that fits: long settings shortened (`card` gives one card in full)
            setup = self.read_setup(where)
            def short(v: Any) -> Any:
                if isinstance(v, (dict, list)) and len(json.dumps(v)) > SHORT:
                    return f"({len(v)} entries: see card)"
                return v[:SHORT] + "…" if isinstance(v, str) and len(v) > SHORT else v
            red = set(setup["red"])
            return {"setup": where, "count": len(setup["cards"]),
                    "cards": [{"id": i, "type": c["type"], "name": c["name"], "in": list(c["inputs"]), "out": list(c["outputs"]),
                               "settings": {k: short(v) for k, v in c["settings"].items()}} for i, c in setup["cards"].items()],
                    "wires": [f"{w['from']} -> {w['to']}" + (" (red: blocked)" if f"{w['from']} -> {w['to']}" in red else "")
                              for w in setup["wires"]],
                    **({"switched": {"slot": s["slot"], "keep within seconds": s["seconds"]}} if (s := self.switched()) else {})}
        if function == "card":
            found = self.read_setup(where)["cards"].get(str(args.get("card") or ""))
            if found and where == "now" and (card := next((c for c in self.all_cards() if c.id == args.get("card")), None)):
                found = {**found, "functions": list(card.offered())}  # what call can try
            return {"id": args.get("card"), "setup": where, **found} if found else {"error": "no such card (see cards)"}
        if function == "remove_card":
            if not self.settings.get("may_remove"):
                return {"error": "Removing cards is not allowed (the Card editor card's settings)."}
            await self.remove_card(str(args.get("card")), where)
            return {"removed": args.get("card"), "setup": where}
        if function == "add_card":
            added = await self.add_card(str(args.get("type")), str(args.get("name") or ""), args.get("settings") or {}, where=where)
            if needs := CARD_TYPES[str(args.get("type"))].needs:
                added = {**added, "note": f"It reaches outside AAC ({', '.join(sorted(needs))}): it may do nothing there "
                                          "until the owner allows it. Once it runs, try_card(card, \"request_access\", …) and "
                                          "tell the owner."}
            return added
        if function == "change_card":
            return await self.change_card(str(args.get("card")), args.get("name"), args.get("settings"), where)
        if function == "unblock":
            return {"unblocked": True} if await self.unblock(str(args.get("from")), str(args.get("to"))) else {"error": "that wire is not red"}
        if function in ("wire", "unwire"):
            wire = {"from": str(args.get("from")), "to": str(args.get("to"))}
            wires = [w for w in self.read_setup(where)["wires"] if w != wire] + ([wire] if function == "wire" else [])
            if function == "wire" and (why := self.wire_problem(wire, where)):
                return {"wired": False, "error": f"Not wired: {why}."}
            now = await self.set_wires(wires, where)
            return {"wired": True} if function == "wire" else {"unwired": wire not in now}
        if function == "copy":
            await self.copy_setup(str(args.get("from") or "now"), str(args.get("to") or ""))
            return {"copied": f"{args.get('from') or 'now'} -> {args.get('to')}"}
        if function == "check":
            return {"setup": where, "problems": self.check_setup(where)}
        if function == "switch":
            slot = str(args.get("where") or "4")
            call = context.next_call() if context is not None else None
            await self.switch_setup(slot, call[1]["id"] if call else None)
            return {"switching": slot, "note": "It switches once your answer is written: end your answer now. Then a "
                                               "message comes into this conversation: call keep after it."}
        if function == "keep":
            return {"kept": True} if self.keep_setup() else {"error": "no switch waits for keep"}
        if function == "try_card":
            return await self.call_card(str(args.get("card")), str(args.get("function")), args.get("args") or {}, context)
        return await super().call(function, args, context)
