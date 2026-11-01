"""The rules (docs/v2 page 5): a context is a list of entries; a card acts when the context holds something for it and
sends only when it did something; calls travel the wires like every context, one after the other."""

from homeassistant.core import HomeAssistant

from custom_components.aac.cards.model import conversation
from custom_components.aac.context import Context

from .conftest import model_answers, reply, setup_aac

TIME = 'def now_text():\n    """The time."""\n    return "It is 08:41."\n'


async def test_a_context_is_a_list_of_entries(hass: HomeAssistant, mock_entry):
    """System and tools always first; entries added at either end of the conversation, found by tag; system and tools
    always stay; the call to make now is read from the entries."""
    c = Context()
    assert [e["tag"] for e in c.entries] == ["context.system", "context.tools"] and c.newest is None
    said = c.add("chat.said", "where is the drill?")
    c.add("memory.recall", "The drill is on shelf 2.")
    c.add("wake_word.spokenturn", "Answer aloud.", at="first")
    assert [e["tag"] for e in c.conversation] == ["wake_word.spokenturn", "chat.said", "memory.recall"]
    assert c.get("*.said") is said and [e["tag"] for e in c.find("*.re*")] == ["memory.recall"]
    try:
        c.remove(c.system)
        raise AssertionError("the system prompt must stay")
    except ValueError:
        pass
    again = Context.from_dict(c.to_dict())
    assert again.entries == c.entries and again.entries is not c.entries
    calls = [{"id": "a", "card": "lists", "function": "lists", "name": "lists", "args": {}},
             {"id": "b", "card": "memory", "function": "recall", "name": "recall", "args": {}}]
    asking = c.add("model.said", "", calls=calls)
    assert c.next_call() == (asking, calls[0]) and c.waiting
    c.add("lists.result", "[]", call_id="a")
    assert c.next_call() == (asking, calls[1])
    c.add("memory.result", "[]", call_id="b")
    assert c.next_call() is None
    left = Context()
    left.add("model.said", "", calls=calls)
    left.add("chat.said", "never mind")
    assert left.next_call() is None  # a call left behind (a line said after it) is not made any more


async def test_the_model_reads_by_tags(hass: HomeAssistant, mock_entry):
    """Its own said lines are its answers, results of its calls tool messages, every other entry with text a user
    message (two in a row are one, an error too); an entry without text is for cards only."""
    c = Context()
    c.system["text"] = "Be kind."
    c.add("chat.id.x")
    c.add("chat.said", "hi")
    c.add("model.said", "Hello.")
    c.add("critic.said", "Too short.")
    c.add("model.error", "Error: boom")
    c.add("critic.said", "", calls=[{"id": "k", "card": "lists", "function": "lists", "name": "lists", "args": {}}])
    c.add("lists.result", "[]", call_id="k")
    system, mine = conversation(c, "critic")
    assert system == "Be kind."
    assert [m["role"] for m in mine] == ["user", "assistant", "user", "assistant", "tool"]
    assert mine[0]["content"] == "hi\n\nHello." and mine[2]["content"] == "Error: boom" and mine[4]["content"] == "[]"


async def test_two_calls_travel_one_after_the_other(hass: HomeAssistant, mock_entry, aioclient_mock):
    """One answer with two calls: the context goes out at calls, the card the first call names answers and it comes
    back, then the second; then the model is asked again with both results. The tools are in context.tools."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    await d.update_card("time", settings={"script": TIME, "functions": {"now_text": {"on": True, "name": "get_time",
                                                                                "help": "The current local time."}}})
    d.set_wires([w for w in d.wires if w != {"from": "time.out", "to": "model.in"} and w != {"from": "charter.out", "to": "time.in"}]
                + [{"from": "charter.out", "to": "model.in"}, {"from": "model.calls", "to": "time.in"}, {"from": "time.out", "to": "model.in"}])
    asked = model_answers(aioclient_mock, reply(calls=[("get_time", {}), ("lists", {})]), reply("08:41, and no lists."))
    chat = d.cards["chat"]
    chat_id, answer = await chat.write("time and lists?")
    assert answer == "08:41, and no lists." and len(asked) == 2  # asked once more only after both results
    tools = {t["function"]["name"]: t["function"]["description"] for t in asked[0]["tools"]}
    assert tools["get_time"] == "The current local time."
    second = asked[1]["messages"]
    assert [m["role"] for m in second[-3:]] == ["assistant", "tool", "tool"]
    assert second[-2]["content"] == "It is 08:41." and second[-1]["content"].startswith("[")  # what Lists has
    assert {"model.calls", "time.out", "lists.out"} <= set(d.flow.last)  # every hop went along a wire
    kept = chat.get(chat_id)
    assert kept["messages"][-1]["text"] == "08:41, and no lists."
    assert [c["name"] for c in kept["messages"][-2]["calls"]] == ["get_time", "lists"]  # the chat shows the calls
    held = Context.from_dict(chat.data["chats"][chat_id]["context"])
    assert {t["name"] for t in held.tools["data"]["tools"]} >= {"get_time", "lists"}


async def test_a_card_sends_only_when_it_did_something(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Wired in circles on purpose: the Model's answer reaches Lists (no call for it: nothing sent) and comes back to the
    Model through a Relay that only passes it (its own line is newest: nothing for it). Asked once, no wire turns red."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    back = (await d.add_card("relay", "Back")).id
    d.set_wires([*d.wires, {"from": "model.out", "to": "lists.in"}, {"from": "model.out", "to": f"{back}.in"},
                 {"from": f"{back}.out", "to": "model.in"}])
    asked = model_answers(aioclient_mock, reply("Hi."))
    _, answer = await d.cards["chat"].write("hello")
    assert answer == "Hi." and len(asked) == 1 and d.red == []


async def test_no_answer_is_no_answer(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A model that answers only its "Means no answer" word sends nothing: the chat keeps only what you wrote."""
    core = await setup_aac(hass, mock_entry)
    chat = core.domain.cards["chat"]
    model_answers(aioclient_mock, reply("Nothing."))
    chat_id, answer = await chat.write("(someone talks to someone else)")
    assert answer == "" and [m["text"] for m in chat.get(chat_id)["messages"]] == ["(someone talks to someone else)"]


async def test_a_turn_that_began_a_chat_finds_it_again(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A heard line with no chat in it: its snapshots, its calls and its answer land in one new chat, which writes its
    id entry; a turn built on that chat (read through provides) goes on there."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    voice = (await d.add_card("relay", "Voice", {"script": 'context = input("chat", read=True) + input("in")\noutput("out", context)\n'})).id
    d.set_wires([*d.wires, {"from": "chat.provides", "to": f"{voice}.chat"}, {"from": f"{voice}.out", "to": "charter.in"}])
    chat = d.cards["chat"]
    for c in list(chat.data["chats"]):  # the chats of a backup are not the current one
        chat.data["chats"][c]["updated"] = "2020-01-01T00:00:00+00:00"
    model_answers(aioclient_mock, reply(calls=[("lists", {})]), reply("You have no lists. Want one?"), reply("Done."))
    heard = Context()
    heard.add("speech_to_text.said", "what lists do I have?", mic="pc", mic_name="PC")
    await d.cards[voice].handle(heard, "in")
    voiced = [c for c in chat.chats() if c["title"].startswith("Voice · PC")]
    assert len(voiced) == 1
    shown = chat.get(voiced[0]["id"])["messages"]
    assert [m["text"] for m in shown if m["text"]] == ["what lists do I have?", "You have no lists. Want one?"]
    assert shown[0]["role"] == "user"
    again = Context()
    again.add("speech_to_text.said", "yes please", mic="pc", mic_name="PC")
    await d.cards[voice].handle(again, "in")
    assert len([c for c in chat.chats() if c["title"].startswith("Voice · PC")]) == 1
    assert [m["text"] for m in chat.get(voiced[0]["id"])["messages"] if m["text"]][-2:] == ["yes please", "Done."]


async def test_plain_text_is_the_request_as_the_model_read_it(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The newest answer keeps its request; Plain text is what the model server writes with the model's template, or the
    request as plain text when the server cannot."""
    from .conftest import BASE_URL

    core = await setup_aac(hass, mock_entry)
    chat = core.domain.cards["chat"]
    model_answers(aioclient_mock, reply("Hi."))
    chat_id, _ = await chat.write("hello")
    aioclient_mock.post(BASE_URL.removesuffix("/v1") + "/apply-template", json={"prompt": "<|turn>user\nhello<turn|>"})
    read = await chat.ui_call("as_read", {"chat_id": chat_id})
    assert read == {"text": "<|turn>user\nhello<turn|>", "template": True, "model": "Big", "reply": {"content": "Hi.", "calls": []}, "answers": [], "usage": None}
    sent = aioclient_mock.mock_calls[-1][2]
    assert sent["messages"][-1]["content"].startswith("hello") and sent["model"] == "big"  # the request as it was sent
    aioclient_mock.clear_requests()
    aioclient_mock.post(BASE_URL.removesuffix("/v1") + "/apply-template", status=404)
    read = await chat.ui_call("as_read", {"chat_id": chat_id})
    assert not read["template"] and read["text"].startswith("[system]\nBe kind.") and "[user]\nhello" in read["text"]


async def test_the_pencil_edits_and_resends(hass: HomeAssistant, mock_entry, aioclient_mock):
    """An answer gets a new text only; a line of yours too (Keep the rest), or what came after it goes and it goes out
    again from there with the new text (Resend)."""
    core = await setup_aac(hass, mock_entry)
    chat = core.domain.cards["chat"]
    asked = model_answers(aioclient_mock, reply("Paris."), reply("Lyon is second."), reply("Berlin."))
    chat_id, _ = await chat.write("capital of France?")
    await chat.write("and the second city?", chat_id)
    shown = chat.get(chat_id)["messages"]
    await chat.ui_call("edit", {"chat_id": chat_id, "id": shown[1]["id"], "text": "Paris, of course."})
    await chat.ui_call("edit", {"chat_id": chat_id, "id": shown[2]["id"], "text": "and the third city?"})
    assert [m["text"] for m in chat.get(chat_id)["messages"]] == ["capital of France?", "Paris, of course.", "and the third city?", "Lyon is second."]
    result = await chat.ui_call("edit", {"chat_id": chat_id, "id": shown[0]["id"], "text": "capital of Germany?", "resend": True})
    assert result == {"chat_id": chat_id, "answer": "Berlin."}
    assert [m["text"] for m in chat.get(chat_id)["messages"]] == ["capital of Germany?", "Berlin."]  # what came after went
    assert asked[-1]["messages"][-1]["content"].startswith("capital of Germany?")


async def test_a_chat_without_a_model(hass: HomeAssistant, mock_entry):
    """Chat → Relay that posts into the chat (no model): the call goes to the chat and back, the turn comes back to the
    chat with the posted line; Plain text shows the entries themselves."""
    core = await setup_aac(hass, mock_entry)
    await core.create("Play")
    d = await core.switch(next(x["id"] for x in core.data["domains"] if x["name"] == "Play"))
    chat = await d.add_card("chat", "Chat")
    relay = await d.add_card("relay", "Relay", {"script": 'context = input("in")\nchat.post("pong")\noutput("out", context)\n'})
    _, answer = await chat.write("ping")
    assert answer.startswith("Nothing is wired")  # nothing at said yet
    d.set_wires([{"from": "chat.said", "to": f"{relay.id}.in"}, {"from": f"{relay.id}.out", "to": "chat.answers"}])
    chat_id, answer = await chat.write("ping")
    shown = chat.get(chat_id)["messages"]
    assert answer == "pong" and [m["text"] for m in shown] == ["ping", "pong"]
    read = await chat.ui_call("as_read", {"chat_id": chat_id})
    assert read["raw"] and [e["tag"] for e in read["entries"]][:3] == ["context.system", "context.tools", "chat.id." + chat_id]
    assert any(e["data"].get("calls", [{}])[0].get("name") == "post" for e in read["entries"] if e["data"].get("calls"))


async def test_cut_off_answers_say_why_and_the_context_fills(hass: HomeAssistant, mock_entry, aioclient_mock):
    """An answer the server stopped early is marked with the reason (context full, or the Longest answer); its thinking is
    kept; the chat knows the tokens; when the context is nearly full the model gets a note at the end."""
    from .conftest import BASE_URL

    def long(text, total):
        return {"choices": [{"message": {"role": "assistant", "content": text, "reasoning_content": "Let me think."},
                             "finish_reason": "length"}], "usage": {"prompt_tokens": total - 20, "completion_tokens": 20, "total_tokens": total}}
    aioclient_mock.get(BASE_URL.removesuffix("/v1") + "/props", json={"default_generation_settings": {"n_ctx": 170}})
    asked = model_answers(aioclient_mock, long("The history of FPV", 150), reply("Short."), long("Part one", 60))
    core = await setup_aac(hass, mock_entry)
    chat = core.domain.cards["chat"]
    chat_id, _ = await chat.write("tell me everything")
    first = chat.get(chat_id)
    assert first["messages"][-1]["cut"] == "context full" and first["messages"][-1]["thinking"] == "Let me think."
    assert first["usage"]["total_tokens"] == 150 and first["usage"]["context"] == 170
    assert "max_tokens" not in asked[0]  # no cap unless set
    await chat.write("short please", chat_id)
    assert "fills 88 % of your context" in asked[-1]["messages"][-1]["content"]  # the note, at the end
    assert "[cut off: context full]" in str(asked[-1]["messages"])  # the model reads why its answer ended
    await core.domain.update_card("model", settings={"max_tokens": 20})
    other, _ = await chat.write("again")
    assert chat.get(other)["messages"][-1]["cut"] == "Longest answer reached (20 tokens)"


async def test_tool_rounds_have_a_setting(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The Model card gives up after its Most tool rounds without an answer (8 by default); 0 means no limit."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    chat = d.cards["chat"]
    asked = model_answers(aioclient_mock, *[reply(calls=[("lists", {})])] * 13, reply("Done at last."))
    await d.update_card("model", settings={"max_rounds": 2})
    await chat.write("lists forever")
    assert len(asked) == 2
    await d.update_card("model", settings={"max_rounds": 0})
    _, answer = await chat.write("lists, many times")
    assert answer == "Done at last." and len(asked) == 14  # 11 rounds of calls: past the old fixed 8


async def test_a_call_passes_the_cards_on_its_way(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Model → Pass (a Relay) → Lists → Model: the call goes through Pass like every context (its script runs on it and
    sends it on), Lists answers, the result comes back."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    count = 'context = input("in")\nprint("passed", len(context))\noutput("out", context)\n'
    p = (await d.add_card("relay", "Pass", {"script": count})).id
    d.set_wires([w for w in d.wires if w != {"from": "model.calls", "to": "lists.in"}]
                + [{"from": "model.calls", "to": f"{p}.in"}, {"from": f"{p}.out", "to": "lists.in"}])
    assert "lists" in {c.id for c in d.cards["model"].tools()}
    asked = model_answers(aioclient_mock, reply(calls=[("lists", {})]), reply("No lists."))
    _, answer = await d.cards["chat"].write("my lists?")
    assert answer == "No lists." and asked[1]["messages"][-1]["role"] == "tool"
    assert any(p_["text"].startswith("passed") for p_ in d.cards[p].printed)
