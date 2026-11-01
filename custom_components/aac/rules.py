"""The rules every card follows (docs/v2 page 5), in one place: the Card editor gives them to models, the panel's
"How AAC works" page shows them."""

RULES = """How AAC works: a few rules every card follows, the same way every time.

1. An entry is id, tag, text, data, time, state.
   - The tag is <card>.<name> (optionally .<extra>): the card in front wrote it (chat.said, model.said, chat.id.<chat id>).
   - The id is its own (a result names the call it answers by it).
   - The state is streaming (still being written), ended, or cut (a stop ended it).
   - The time stays in the context; a model never reads it.
2. A context is a list of entries in order: always first context.system (the system prompt), second context.tools
   (what a model may call), then the conversation. Nothing else. Entries are read one after the other, and added,
   changed and removed anywhere.
3. A card acts when the context holds something for it.
4. A card sends only when it did something: from its outputs, to every card wired there. Nothing to do: nothing is
   sent, the context ends there.
5. Shared meanings are tag names, not code: everything anyone says is <card>.said. A card that shows or speaks does it
   by the tags in its settings.
6. One limit: a wire that carries more contexts within a second than the storm limit turns red (blocked until
   unblocked). A slow loop (through a Timer) may run for ever.

What follows from them:
- Contexts move as a wave: what a card sends reaches every card wired there before anything those cards send moves
  on, so a copy sent earlier never arrives after what came of it. A snapshot goes as a wave of its own, at once.
- A model acts when the newest entry is not its own. It reads context.system as its system prompt, its own .said
  entries as its answers, the results of its calls as tool messages, every other entry with text as a user message.
  An answer that is nothing (or only its "Means no answer" word) is no answer: nothing is sent.
- A call is in the data of the entry that makes it. It goes out like every context; the card it names answers with a
  <card>.result entry and sends the context on; every other card it passes may look at it. Calls go one after the other.
- While a model writes, the same context goes out after every finished sentence with its answer streaming; the last
  time it has ended.
- Stop is a core function: every card gets it at once. A busy card cuts what it writes and sends its context on. A stop
  travels nowhere.
- A Chat keeps every context that reaches it: in the chat whose chat.id.<id> entry it holds, else as a new chat once it
  is finished (nothing streaming, no call waiting).

The mindset (not a rule): a card only needs to write its own entries. It may change others' (a Relay's script is the
owner's hand), but nothing is built so that it has to.
"""
