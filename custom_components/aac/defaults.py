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
    Assist conversations land in the Chat (conversation.py)."""
    add = domain.add_card
    await add("chat", "Chat", {"ha_agent": True}, card_id="chat", at={"x": 0, "y": 0})
    await add("relay", "Charter", {"script": new_text("charter", CHARTER)}, card_id="charter", at={"x": 290, "y": 0})
    await add("relay", "Time", {"script": TIME}, card_id="time", at={"x": 580, "y": 0})
    await add("model", "Model", card_id="model", at={"x": 870, "y": 0})
    await add("lists", "Lists", card_id="lists", at={"x": 870, "y": 140})
    await add("homeassistant", "Home", {"default": "hidden"}, card_id="home", at={"x": 870, "y": 260})
    await add("calendar", "Calendar", card_id="calendar", at={"x": 870, "y": 380})
    domain.set_wires([
        {"from": "chat.said", "to": "charter.in"},
        {"from": "charter.out", "to": "time.in"}, {"from": "time.out", "to": "model.in"},
        {"from": "model.out", "to": "chat.answers"},
        *[w for tool in ("lists", "home", "calendar") for w in ({"from": "model.out", "to": f"{tool}.in"}, {"from": f"{tool}.out", "to": "model.in"})],
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
@on("ears.heard")
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
    await add("microphone", "Microphone", {"source": "any"}, card_id="mic", at={"x": -720, "y": 420})
    await add("pause_cut", "Audio clipper", {"pause": 800}, card_id="cut", at={"x": -480, "y": 420})
    await add("speech_to_text", "Speech to text", card_id="ears", at={"x": -240, "y": 420})
    await add("text_to_speech", "Text to speech", card_id="voice", at={"x": 1440, "y": 600})
    await add("speaker", "Speaker", card_id="speaker", at={"x": 1730, "y": 600})
    domain.set_wires([*domain.wires, {"from": "mic.out", "to": "cut.in"}, {"from": "cut.out", "to": "ears.in"}])
    await _voice_cards(domain, ["computer"])
    domain.undo = []
    LOGGER.info("AAC: the voice chain was added to the default domain")


async def _voice_cards(domain, names: list[str]) -> None:
    """The voice cards after Speech to text (`ears`) and their wires (Text to speech `voice` → Speaker `speaker`)."""
    words = ", ".join(f'"{n}"' for n in names)
    add = domain.add_card
    await add("relay", "Stop word", {"script": STOP_WORD}, card_id="stop_word", at={"x": 0, "y": 300})
    await add("relay", "Wake word", {"script": WAKE_WORD.replace("WORDS", words)}, card_id="wake", at={"x": 0, "y": 420})
    await add("relay", "Follow-up", {"script": FOLLOW_UP_CHECK.replace("WORDS", f'"stop", {words}')}, card_id="follow_up", at={"x": 0, "y": 540})
    await add("relay", "Spoken turn", {"script": new_text("rules", VOICE_RULES, "end")}, card_id="spoken", at={"x": 240, "y": 420})
    await add("relay", "Follow-up turn", {"script": new_text("rules", VOICE_RULES, "end") + FOLLOW_UP_NOTE}, card_id="followed", at={"x": 240, "y": 540})
    await add("relay", "Answer", {}, card_id="answer", at={"x": 1200, "y": 420})
    first = "charter" if "charter" in domain.cards else None
    model = next((c.id for c in domain.cards.values() if c.type == "model"), None)
    chat = "chat" if "chat" in domain.cards else None
    wires = [w for w in domain.wires if not (model and chat and w == {"from": f"{model}.out", "to": f"{chat}.answers"})]
    wires += [{"from": "ears.out", "to": "stop_word.in"}, {"from": "ears.out", "to": "wake.in"}, {"from": "ears.out", "to": "follow_up.in"},
              {"from": "wake.yes", "to": "spoken.in"}, {"from": "follow_up.yes", "to": "followed.in"},
              {"from": "answer.out", "to": "voice.in"}, {"from": "voice.out", "to": "speaker.in"}]
    if first:
        wires += [{"from": "stop_word.stop", "to": f"{first}.in"}, {"from": "spoken.out", "to": f"{first}.in"},
                  {"from": "followed.out", "to": f"{first}.in"}]
    if chat:
        wires += [{"from": f"{chat}.provides", "to": "spoken.reads"}, {"from": f"{chat}.provides", "to": "followed.reads"},
                  {"from": "answer.out", "to": f"{chat}.answers"}]
    if model:
        wires.append({"from": f"{model}.out", "to": "answer.in"})
    domain.set_wires(wires)
