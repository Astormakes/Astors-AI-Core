"""The context shaped like a model request (docs/v2 page 3): containers, calls along the wires, members of a chat."""

from homeassistant.core import HomeAssistant

from custom_components.aac.context import Context

from .conftest import model_answers, reply, setup_aac

TIME = 'def now_text():\n    """The time."""\n    return "It is 08:41."\n'


async def test_cards_put_things_where_a_model_expects_them(hass: HomeAssistant, mock_entry):
    """System parts, messages with their attachments, events: and the flat list reads them in order."""
    c = Context()
    c.add("charter", "system", "Be kind.")
    said = c.add("chat", "said", "where is the drill?")
    c.add("memory", "note", "The drill is on shelf 2.", ["recall"])
    c.add("voice", "trailer", "Answer aloud.")
    c.add("chat", "signal", "stop")
    assert [e["text"] for e in c.system] == ["Be kind."] and c.messages == [said]
    assert [a["kind"] for a in said["attachments"]] == ["note", "trailer"] and c.events[0]["at"] == 1
    assert [e["kind"] for e in c.entries] == ["system", "note", "said", "trailer", "signal"]
    assert c.stopped
    c.put("memory", "note", "The drill is in the car.", "recall")  # a standing attachment: only one, on the newest
    c.put("memory", "note", "The drill is in the car.", "recall")
    assert [a["text"] for a in said["attachments"] if a["by"] == "memory"] == ["The drill is in the car."]


async def test_two_calls_travel_one_after_the_other(hass: HomeAssistant, mock_entry, aioclient_mock):
    """One answer with two calls: the context goes to the first card and back, then to the second and back, then the
    model is asked again with both results; the tools are in the context, the wires lit."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    await d.update_card("time", settings={"script": TIME, "functions": {"now_text": {"on": True, "name": "get_time",
                                                                                "help": "The current local time."}}})
    d.set_wires([w for w in d.wires if w != {"from": "time.out", "to": "model.in"} and w != {"from": "charter.out", "to": "time.in"}]
                + [{"from": "charter.out", "to": "model.in"}, {"from": "model.calls", "to": "time.in"}, {"from": "time.out", "to": "model.in"}])
    asked = model_answers(aioclient_mock, reply(calls=[("get_time", {}), ("lists", {})]), reply("08:41, and no lists."))
    chat = d.cards["chat"]
    chat_id, answer = await chat.send("time and lists?")
    assert answer == "08:41, and no lists." and len(asked) == 2  # asked once more only after both results
    tools = {t["function"]["name"]: t["function"]["description"] for t in asked[0]["tools"]}
    assert tools["get_time"] == "The current local time."
    second = asked[1]["messages"]
    assert [m["role"] for m in second[-3:]] == ["assistant", "tool", "tool"]
    assert second[-2]["content"] == "It is 08:41." and second[-1]["content"].startswith("[")  # what Lists has
    assert {"model.calls", "time.out", "lists.out"} <= set(d.flow.last)  # every hop went along a wire
    kept = chat.get(chat_id)
    assert kept["messages"][-1]["text"] == "08:41, and no lists."
    assert [c["name"] for c in kept["messages"][-2]["calls"]] == ["get_time", "lists"]  # the chat can show the calls
    assert {t["name"] for t in chat.data["chats"][chat_id]["last"]["tools"]} >= {"get_time", "lists"}


async def test_another_model_is_a_member_of_the_chat(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Model → Critic: the critic sees the first model's answer as a message from that member, by name; each sees its
    own answers as its own. Errors and stops never reach a model."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    await d.add_card("model", "Critic")
    d.set_wires([w for w in d.wires if not (w["from"] == "model.out" and w["to"] in ("chat.answers", "answer.in"))]
                + [{"from": "model.out", "to": "critic.in"}, {"from": "critic.out", "to": "chat.answers"}])
    asked = model_answers(aioclient_mock, reply("Paris."), reply("Correct: Paris."))
    _, answer = await d.cards["chat"].send("capital of France?")
    assert answer == "Correct: Paris."
    seen = asked[1]["messages"]
    assert seen[-1]["role"] == "user" and "Model: Paris." in seen[-1]["content"] and seen[-1].get("name") is None  # merged
    c = Context()
    c.add("chat", "said", "hi")
    c.add("model", "answer", "Hello.")
    c.add("model", "error", "Error: boom")
    c.add("critic", "answer", "Fine.")
    from custom_components.aac.cards.model import conversation

    _, mine = conversation(c, "critic", {"model": "Model"})
    assert [m["role"] for m in mine] == ["user", "assistant"] and mine[0]["content"] == "hi\n\nModel: Hello."
    assert "boom" not in str(mine)


async def test_plain_text_is_the_request_as_the_model_read_it(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The chat keeps the newest request; its Plain text is what the model server writes with the model's template, or
    the request as plain text when the server cannot."""
    from .conftest import BASE_URL

    core = await setup_aac(hass, mock_entry)
    chat = core.domain.cards["chat"]
    model_answers(aioclient_mock, reply("Hi."))
    chat_id, _ = await chat.send("hello")
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
    """An answer gets a new text only; a line of yours too (Keep the rest), or what came after it goes and the turn runs
    again from there with the new text (Resend)."""
    core = await setup_aac(hass, mock_entry)
    chat = core.domain.cards["chat"]
    asked = model_answers(aioclient_mock, reply("Paris."), reply("Lyon is second."), reply("Berlin."))
    chat_id, _ = await chat.send("capital of France?")
    await chat.send("and the second city?", chat_id)
    shown = chat.get(chat_id)["messages"]
    await chat.ui_call("edit", {"chat_id": chat_id, "id": shown[1]["id"], "text": "Paris, of course."})
    await chat.ui_call("edit", {"chat_id": chat_id, "id": shown[2]["id"], "text": "and the third city?"})
    assert [m["text"] for m in chat.get(chat_id)["messages"]] == ["capital of France?", "Paris, of course.", "and the third city?", "Lyon is second."]
    result = await chat.ui_call("edit", {"chat_id": chat_id, "id": shown[0]["id"], "text": "capital of Germany?", "resend": True})
    assert result == {"chat_id": chat_id, "answer": "Berlin."}
    assert [m["text"] for m in chat.get(chat_id)["messages"]] == ["capital of Germany?", "Berlin."]  # what came after went
    assert asked[-1]["messages"][-1]["content"].startswith("capital of Germany?")  # the turn ran again from there


async def test_a_chat_without_a_model(hass: HomeAssistant, mock_entry):
    """Chat → Relay that posts into the chat (no model): the call travels there and back, the turn comes back to the chat
    with the posted line; no "no answer" error (only a chat wired to nothing says so); Plain text is the raw context."""
    core = await setup_aac(hass, mock_entry)
    await core.create("Play")
    d = await core.switch(next(x["id"] for x in core.data["domains"] if x["name"] == "Play"))
    chat = await d.add_card("chat", "Chat")
    relay = await d.add_card("relay", "Relay", {"script": 'context = input("in")\nchat.post("pong")\noutput("out", context)\n'})
    _, answer = await chat.send("ping")
    assert answer.startswith("Nothing is wired")  # nothing at said yet
    d.set_wires([{"from": "chat.said", "to": f"{relay.id}.in"}, {"from": f"{relay.id}.out", "to": "chat.answers"}])
    chat_id, answer = await chat.send("ping")
    shown = chat.get(chat_id)["messages"]
    assert answer == "pong" and [m["text"] for m in shown if m["text"]] == ["ping", "pong"]
    assert [c["name"] for m in shown for c in m.get("calls", [])] == ["post"] and not any(m.get("error") for m in shown)
    assert {f"{relay.id}.out", "chat.said"} <= set(d.flow.last)  # the call went there and back along the wires
    read = await chat.ui_call("as_read", {"chat_id": chat_id})
    held = read["context"]["messages"]  # AAC's context itself
    assert read["raw"] and held[0]["text"] == "ping" and held[1]["data"]["calls"][0]["name"] == "post"


async def test_cut_off_answers_say_why_and_the_context_fills(hass: HomeAssistant, mock_entry, aioclient_mock):
    """An answer the server stopped early is marked with the reason (context full, or the Longest answer); its thinking is
    kept; the chat knows the tokens; when the context is nearly full the model gets a note attached to the turn."""
    from .conftest import BASE_URL

    def long(text, total):
        return {"choices": [{"message": {"role": "assistant", "content": text, "reasoning_content": "Let me think."},
                             "finish_reason": "length"}], "usage": {"prompt_tokens": total - 20, "completion_tokens": 20, "total_tokens": total}}
    aioclient_mock.get(BASE_URL.removesuffix("/v1") + "/props", json={"default_generation_settings": {"n_ctx": 170}})
    asked = model_answers(aioclient_mock, long("The history of FPV", 150), reply("Short."), long("Part one", 60))
    core = await setup_aac(hass, mock_entry)
    chat = core.domain.cards["chat"]
    chat_id, _ = await chat.send("tell me everything")
    first = chat.get(chat_id)
    assert first["messages"][-1]["cut"] == "context full" and first["messages"][-1]["thinking"] == "Let me think."
    assert first["usage"]["total_tokens"] == 150 and first["usage"]["context"] == 170
    assert "max_tokens" not in asked[0]  # no cap unless set
    await chat.send("short please", chat_id)
    last_user = asked[-1]["messages"][-1]["content"]
    assert "fills 88 % of your context" in last_user  # the note, attached to the turn
    assert "[cut off: context full]" in str(asked[-1]["messages"])  # the model reads why its answer ended
    await core.domain.update_card("model", settings={"max_tokens": 20})
    other, _ = await chat.send("again")
    assert chat.get(other)["messages"][-1]["cut"] == "Longest answer reached (20 tokens)"


async def test_tool_rounds_have_a_setting(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The Model card gives up after its Most tool rounds without an answer (8 by default); 0 means no limit."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    chat = d.cards["chat"]
    asked = model_answers(aioclient_mock, *[reply(calls=[("lists", {})])] * 13, reply("Done at last."))
    await d.update_card("model", settings={"max_rounds": 2})
    await chat.send("lists forever")
    assert len(asked) == 2
    await d.update_card("model", settings={"max_rounds": 0})
    _, answer = await chat.send("lists, many times")
    assert answer == "Done at last." and len(asked) == 14  # 11 rounds of calls: past the old fixed 8

async def test_a_call_travels_through_other_cards(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Chat → Charter → Model → Answer → Chat, and the Model's calls into Answer too: the Chat is reachable from its
    calls output, so its functions are tools; a call takes the shortest way there (Model → Answer → Chat) and the result
    the shortest way back (Chat → Charter → Model); the cards between only pass it on (Answer's script does not run on
    it); every wire lights."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    marks = 'context = input("in")\nanswer = context.get("model.answer")\nanswer.text = answer.text + " (passed Answer)"\noutput("out", context)\n'
    a = (await d.add_card("relay", "Answer", {"script": marks})).id  # (a backup brings an Answer card of its own)
    d.set_wires([w for w in d.wires if not (w["from"] in ("model.out", "model.calls") and w["to"] == "chat.answers")]
                + [{"from": "model.out", "to": f"{a}.in"}, {"from": "model.calls", "to": f"{a}.in"}, {"from": f"{a}.out", "to": "chat.answers"}])
    assert "chat" in {c.id for c in d.flow.tools("model")}
    asked = model_answers(aioclient_mock, reply(calls=[("chats", {})]), reply("You have one chat."))
    chat_id, answer = await d.cards["chat"].send("which chats do I have?")
    assert answer == "You have one chat. (passed Answer)"  # the answer went through Answer once, the call did not run it
    assert asked[1]["messages"][-1]["role"] == "tool" and "which chats" in asked[1]["messages"][-1]["content"]
    assert {"model.calls", "model.out", f"{a}.out", "chat.said", "charter.out"} <= set(d.flow.last)  # the way there and back lit
