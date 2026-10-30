"""The context shaped like a model request (docs/v2 page 3): containers, calls along the wires, members of a chat."""

from homeassistant.core import HomeAssistant

from custom_components.aac.context import Context

from .conftest import model_answers, reply, setup_aac

TIME = '@new("now")\ndef now_text(e):\n    e.text = "It is 08:41."\n'


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
    await d.update_card("time", settings={"script": TIME, "functions": {"now": {"on": True, "name": "get_time",
                                                                                "help": "The current local time."}}})
    d.set_wires([w for w in d.wires if w != {"from": "time.out", "to": "model.in"} and w != {"from": "charter.out", "to": "time.in"}]
                + [{"from": "charter.out", "to": "model.in"}, {"from": "model.out", "to": "time.in"}, {"from": "time.out", "to": "model.in"}])
    asked = model_answers(aioclient_mock, reply(calls=[("get_time", {}), ("lists", {})]), reply("08:41, and no lists."))
    chat = d.cards["chat"]
    chat_id, answer = await chat.send("time and lists?")
    assert answer == "08:41, and no lists." and len(asked) == 2  # asked once more only after both results
    tools = {t["function"]["name"]: t["function"]["description"] for t in asked[0]["tools"]}
    assert tools["get_time"] == "The current local time."
    second = asked[1]["messages"]
    assert [m["role"] for m in second[-3:]] == ["assistant", "tool", "tool"]
    assert second[-2]["content"] == "It is 08:41." and second[-1]["content"].startswith("[")  # what Lists has
    assert {"model.out", "time.out", "lists.out"} <= set(d.flow.last)  # every hop went along a wire
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
    assert read == {"text": "<|turn>user\nhello<turn|>", "template": True, "model": "Big", "reply": {"content": "Hi.", "calls": []}}
    sent = aioclient_mock.mock_calls[-1][2]
    assert sent["messages"][-1]["content"].startswith("hello") and sent["model"] == "big"  # the request as it was sent
    aioclient_mock.clear_requests()
    aioclient_mock.post(BASE_URL.removesuffix("/v1") + "/apply-template", status=404)
    read = await chat.ui_call("as_read", {"chat_id": chat_id})
    assert not read["template"] and read["text"].startswith("[system]\nBe kind.") and "[user]\nhello" in read["text"]
