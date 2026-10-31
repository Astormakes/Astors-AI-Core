"""The default setup: a domain built from ordinary cards (docs/v2: defaults come from presets); the voice chain is
added once satellites are known."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import LOGGER

if TYPE_CHECKING:
    from .core import Core

CHARTER = """Charter (the owner's values; you follow it and never change it):
- Honesty over comfort: never hide or soften facts to be liked, never fake progress, say when you made a mistake.
- Little noise: speak up only when it matters; suggest something at most once, accept "no".
- My decisions stay mine: you advise, I decide. Ask before anything that cannot be undone or leaves the house.
- What you learn about me stays private.
- Limits are not puzzles: if a rule stops you, say so instead of working around it."""
TIME = """@new("time", at="end", replace=False)
def time(e):
    e.text = f"Local time: {now():%A %Y-%m-%d %H:%M}"
"""


def new_text(name: str, text: str, at: str = "start") -> str:
    """A Relay script that writes one text."""
    where = "" if at == "start" else ', at="end", replace=False'
    return f'@new("{name}"{where})\ndef {name}(e):\n    e.text = """{text}"""\n'



async def async_first_start(core: Core) -> None:
    """Build the default domain once."""
    from .core import Domain  # noqa: PLC0415

    core.data["domains"] = [{"id": "assistant", "name": "Assistant"}]
    core.data["active"] = "assistant"
    domain = Domain(core, "assistant", "Assistant")
    await domain.load()
    await build_default(domain)
    domain.undo = []
    await domain.save_now()
    core.save()
    LOGGER.info("AAC: default domain built")


async def build_default(domain) -> None:
    """The default setup: Chat → Charter → Time → Model → Chat; Lists, Home and Calendar as the model's tools; HA
    Assist conversations land in the Chat (conversation.py). A card's id is its name (chat, charter, …)."""
    add = domain.add_card
    chat = (await add("chat", "Chat", {"ha_agent": True}, at={"x": 0, "y": 0})).id
    charter = (await add("relay", "Charter", {"script": new_text("charter", CHARTER)}, at={"x": 290, "y": 0})).id
    time = (await add("relay", "Time", {"script": TIME}, at={"x": 580, "y": 0})).id
    model = (await add("model", "Model", at={"x": 870, "y": 0})).id
    tools = [(await add(kind, title, at={"x": 870, "y": y})).id
             for kind, title, y in (("lists", "Lists", 140), ("homeassistant", "Home", 260), ("calendar", "Calendar", 380))]
    domain.set_wires([
        {"from": f"{chat}.said", "to": f"{charter}.in"},
        {"from": f"{charter}.out", "to": f"{time}.in"}, {"from": f"{time}.out", "to": f"{model}.in"},
        {"from": f"{model}.out", "to": f"{chat}.answers"},
        *[w for tool in tools for w in ({"from": f"{model}.out", "to": f"{tool}.in"}, {"from": f"{tool}.out", "to": f"{model}.in"})],
    ])


VOICE_RULES = ("You are heard and answer aloud: one or two short sentences, plain words, no lists. If you need to look "
               "something up or do something, call the tool first, then answer.")
STOP_WORD = """@on("*", kind="said")
def says_stop(e):
    if "stop" in e.text.lower():
        stop()
        output("stop")
"""
WAKE_WORD = """@on("*", kind="said")
def wake_word(e):
    if any(word in e.text.lower() for word in [WORDS]):
        output("yes")
"""
FOLLOW_UP_CHECK = """@on("*", kind="said")
def follow_up(e):
    # you started talking less than 6 s after AAC spoke at that satellite (long answers too)
    spoke = audio.satellites.get(e.data.get("mic"), {}).get("seconds", 999999)
    said_for = e.data.get("seconds") or 0
    named = any(word in e.text.lower() for word in [WORDS])
    if not e.data.get("aac_spoke") and spoke - said_for < 6 and not named:
        output("yes")
"""
FOLLOW_UP = "(Said right after you spoke aloud; it may not be meant for you. If it is not, answer only: nothing) "
FOLLOW_UP_NOTE = f"""
@on("EARS.heard")
def maybe_not_for_me(e):
    e.text = "{FOLLOW_UP}" + e.text
"""


async def async_voice_chain(core: Core) -> None:
    """Once satellites are known, the default domain gets the voice chain as ordinary cards."""
    if core.data.get("voice_chain"):
        return
    core.data["voice_chain"] = True
    core.save()
    domain = core.domain
    if not core.audio.settings.get("known") or any(c.type == "microphone" for c in domain.cards.values()):
        return
    add = domain.add_card
    mic = (await add("microphone", "Microphone", {"source": "any"}, at={"x": -720, "y": 420})).id
    cut = (await add("pause_cut", "Audio clipper", {"pause": 800}, at={"x": -480, "y": 420})).id
    ears = (await add("speech_to_text", "Speech to text", at={"x": -240, "y": 420})).id
    await add("text_to_speech", "Text to speech", at={"x": 1440, "y": 600})
    await add("speaker", "Speaker", at={"x": 1730, "y": 600})
    domain.set_wires([*domain.wires, {"from": f"{mic}.out", "to": f"{cut}.in"}, {"from": f"{cut}.out", "to": f"{ears}.in"}])
    await _voice_cards(domain, ["computer"])
    domain.undo = []
    LOGGER.info("AAC: the voice chain was added to the default domain")


async def _voice_cards(domain, names: list[str]) -> None:
    """The voice cards after Speech to text and their wires (… → Text to speech → Speaker)."""
    words = ", ".join(f'"{n}"' for n in names)
    find = lambda kind: next((c.id for c in domain.cards.values() if c.type == kind), None)  # noqa: E731
    ears, voice, speaker = find("speech_to_text"), find("text_to_speech"), find("speaker")
    add = domain.add_card
    stop = (await add("relay", "Stop word", {"script": STOP_WORD}, at={"x": 0, "y": 300})).id
    wake = (await add("relay", "Wake word", {"script": WAKE_WORD.replace("WORDS", words)}, at={"x": 0, "y": 420})).id
    follow = (await add("relay", "Follow-up", {"script": FOLLOW_UP_CHECK.replace("WORDS", f'"stop", {words}')}, at={"x": 0, "y": 540})).id
    spoken = (await add("relay", "Spoken turn", {"script": new_text("rules", VOICE_RULES, "end")}, at={"x": 240, "y": 420})).id
    followed = (await add("relay", "Follow-up turn", {"script": new_text("rules", VOICE_RULES, "end") + FOLLOW_UP_NOTE.replace("EARS", ears)},
                          at={"x": 240, "y": 540})).id
    answer = (await add("relay", "Answer", {}, at={"x": 1200, "y": 420})).id
    first = "charter" if "charter" in domain.cards else None
    model, chat = find("model"), "chat" if "chat" in domain.cards else None
    wires = [w for w in domain.wires if not (model and chat and w == {"from": f"{model}.out", "to": f"{chat}.answers"})]
    wires += [{"from": f"{ears}.out", "to": f"{stop}.in"}, {"from": f"{ears}.out", "to": f"{wake}.in"}, {"from": f"{ears}.out", "to": f"{follow}.in"},
              {"from": f"{wake}.yes", "to": f"{spoken}.in"}, {"from": f"{follow}.yes", "to": f"{followed}.in"},
              {"from": f"{answer}.out", "to": f"{voice}.in"}, {"from": f"{voice}.out", "to": f"{speaker}.in"}]
    if first:
        wires += [{"from": f"{stop}.stop", "to": f"{first}.in"}, {"from": f"{spoken}.out", "to": f"{first}.in"},
                  {"from": f"{followed}.out", "to": f"{first}.in"}]
    if chat:
        wires += [{"from": f"{chat}.provides", "to": f"{spoken}.reads"}, {"from": f"{chat}.provides", "to": f"{followed}.reads"},
                  {"from": f"{answer}.out", "to": f"{chat}.answers"}]
    if model:
        wires.append({"from": f"{model}.out", "to": f"{answer}.in"})
    domain.set_wires(wires)
