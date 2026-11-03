"""A model builds (docs/v2 page 6, step 4): straight in what runs, in a slot, or in the draft (slot 4) it switches to and
must keep in time, else the setup before comes back by itself."""

import asyncio
import time

from homeassistant.core import HomeAssistant

from .conftest import allow, model_answers, reply, setup_aac


async def _editor(core, level: str = "full"):
    """A Card editor wired as the model's tool."""
    d = core.domain
    editor = await d.add_card("builder", "Card editor")
    d.set_wires([*d.wires, {"from": "model.calls", "to": f"{editor.id}.in"}, {"from": f"{editor.id}.out", "to": "model.in"}])
    return await allow(d, editor.id, {"setup": level})


async def _until(test, seconds: float = 8.0) -> None:
    end = time.monotonic() + seconds
    while not test():
        assert time.monotonic() < end, "it did not happen in time"
        await asyncio.sleep(0.05)


async def test_build_in_the_draft_switch_keep_and_go_back(hass: HomeAssistant, mock_entry, monkeypatch):
    """The draft changes nothing that runs; check finds what is wrong; a switch runs the draft once the domain is idle;
    keep keeps it; no keep in time brings the setup before back (the draft stays)."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    editor = await _editor(core)
    running = set(d.cards)

    assert (await editor.call("copy", {"from": "now", "to": "4"}))["copied"] == "now -> 4"
    added = await editor.call("add_card", {"type": "timer", "name": "Pause", "where": "4"})
    assert added == {"id": "pause", "slot": "4"} and "pause" not in d.cards and set(d.cards) == running
    assert "pause" in [c["id"] for c in (await editor.call("cards", {"where": "4"}))["cards"]]
    assert (await editor.call("wire", {"from": "chat.said", "to": "pause.in", "where": "4"}))["wired"]
    assert "no card 'nothing' in slot 4" in (await editor.call("wire", {"from": "chat.said", "to": "nothing.in", "where": "4"}))["error"]
    assert "chat has no output 'out' (its outputs: said, provides)" in (await editor.call("wire", {"from": "chat.out", "to": "pause.in", "where": "4"}))["error"]
    assert "no setting prompt" in (await editor.call("add_card", {"type": "relay", "settings": {"prompt": "x"}, "where": "4"}))["error"]
    assert (await editor.call("change_card", {"card": "pause", "settings": {"seconds": 3}, "where": "4"}))["settings"]["seconds"] == 3
    assert (await editor.call("check", {"where": "4"}))["problems"] == []
    assert {"from": "chat.said", "to": "pause.in"} not in d.wires  # nothing that runs changed

    d.slots["2"] = {"cards": {}, "wires": [], "positions": {}}
    assert "model is in what runs: copy from now to 2" in (await editor.call("wire", {"from": "model.out", "to": "chat.answers", "where": "2"}))["error"]
    d.slots["4"]["cards"]["odd"] = {"type": "no_such_type", "title": "Odd", "settings": {}}
    d.slots["4"]["wires"].append({"from": "pause.out", "to": "pause.nowhere"})
    found = (await editor.call("check", {"where": "4"}))["problems"]
    assert any("no card type 'no_such_type'" in p for p in found) and any("pause.out -> pause.nowhere" in p for p in found)
    assert "not switched" in (await editor.call("switch", {"where": "4"}))["error"]
    await editor.call("remove_card", {"card": "odd", "where": "4"})  # removing in a slot: still the owner's May remove
    d.slots["4"]["cards"].pop("odd")
    d.slots["4"]["wires"].pop()

    assert (await editor.call("switch", {"where": "4"}))["switching"] == "4"
    await _until(lambda: d.trial is not None)
    assert "pause" in d.cards and d.cards["pause"].settings["seconds"] == 3 and d.summary()["trial"]["slot"] == "4"
    editor = d.cards[editor.id]  # made again
    assert "error" in await editor.call("switch", {"where": "4"})  # one at a time
    assert (await editor.call("keep", {}))["kept"] and d.trial is None
    assert (await editor.call("keep", {}))["error"]

    monkeypatch.setattr("custom_components.aac.domain.KEEP_WAIT", 0.3)
    await editor.call("remove_card", {"card": "pause", "where": "4"})
    d.slots["4"]["cards"].pop("pause")
    d.slots["4"]["wires"] = [w for w in d.slots["4"]["wires"] if "pause." not in w["from"] + w["to"]]
    await editor.call("switch", {"where": "4"})
    await _until(lambda: d.trial is not None and "pause" not in d.cards)
    await _until(lambda: d.trial is None and "pause" in d.cards)  # no keep: back
    assert "pause" not in d.slots["4"]["cards"] and d.log.read(what="switch")


async def test_what_asks_and_what_not(hass: HomeAssistant, mock_entry, monkeypatch):
    """At ask the draft is built without asking (nothing that runs changes); the switch, what runs and the owner's
    slots wait for the owner's yes. Without the editor wired in and out, a draft is never switched to."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    editor = await _editor(core, "ask")
    asked = []

    async def answer(card, thing, what):
        asked.append(what)
        return False

    monkeypatch.setattr(core.asks, "ask", answer)
    await editor.call("copy", {"from": "now", "to": "4"})
    assert (await editor.call("add_card", {"type": "timer", "name": "Pause", "where": "4"}))["id"] == "pause" and not asked
    assert "did not allow" in (await editor.call("add_card", {"type": "timer", "where": "1"}))["error"] and len(asked) == 1
    assert "did not allow" in (await editor.call("add_card", {"type": "timer"}))["error"] and len(asked) == 2
    assert "did not allow" in (await editor.call("switch", {"where": "4"}))["error"] and "switch to slot 4" in asked[-1]
    assert d.trial is None and "pause" not in d.cards

    d.slots["4"]["wires"] = [w for w in d.slots["4"]["wires"] if editor.id not in w["from"]]
    assert any("not wired in and out" in p for p in (await editor.call("check", {"where": "4"}))["problems"])

    other = await d.add_card("lists", "Shopping")
    assert await editor.call("try_card", {"card": other.id, "function": "lists"}) == []  # trying a card: it has no lists yet
    assert "offers no function" in (await editor.call("try_card", {"card": other.id, "function": "nothing"}))["error"]


async def test_a_model_switches_and_keeps_in_the_new_setup(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The switch waits for the model's answer; the Card editor in the new setup hands the same conversation back to
    the model with the news; the model calls keep; the chat holds it all."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    editor = await _editor(core)
    await editor.call("copy", {"from": "now", "to": "4"})
    await editor.call("add_card", {"type": "timer", "name": "Pause", "where": "4"})
    asked = model_answers(aioclient_mock, reply("", [("switch", {"where": "4"})]), reply("", [("guide", {})]),
                          reply("", [("switch", {"where": "4"})]), reply("Switching to the draft."),
                          reply("", [("keep", {})]), reply("Kept: it works."))
    chat = d.cards["chat"]
    chat_id, answer = await chat.write("switch to your draft")
    assert answer == "Switching to the draft."
    assert "Read the guide first" in asked[1]["messages"][-1]["content"]  # building waits for the guide
    await _until(lambda: d.trial is None and "pause" in d.cards and len(asked) >= 6)
    await _until(lambda: "Kept: it works." in str(d.cards["chat"].data))
    note = asked[4]["messages"][-1]["content"]
    assert "The switch to slot 4 is done" in note and "keep" in note
