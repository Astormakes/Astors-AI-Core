# AAC – progress

Read this first. v1 (1.0.0-dev.75) is history: git tag `v1-final`.
The plan lives in `docs/v2/` (private): page 1 "What AAC is" (settled), page 2 "The cards" (in progress), page 5
"The rules" (AAC 3, settled; replaces pages 3 and 4).

## The move to the new server (2026-11-08) – 3.1.8 live on the new HA

The owner's new server (i7-8700, RX 7800 XT + RTX 2070) runs a fresh HA at **192.168.1.11** (static); AAC was set up
again "as it was, without the deadweight". Hosts, add-ons and what was carried or left out: `docs/dev-environment.md`
and `~/cluade/proxmox/docs/host-inventory.md`. Addresses are static and counted (.9 old host, .10 host, .11 HA, .12 CT 101
llm-amd, .13 CT 102 llm-nvidia); the PC stays on DHCP and is reached as `cachyos-x8664.local`.
- AAC: the Assistant domain, its card stores (chats, lists, memory, schedule), `/config/aac/{cards,data/assistant,skins}`
  carried; test domains (test123, Build test, Build test Qwen) and `bak-*` left out. The Model card: `m_server_model`
  (Gemma 4 26B on the 7800 XT) first, the PC's Gemma second. Pipeline "AAC" is HA's only one. Satellite 0.3.6 runs
  (BT600 off-hook, connected as ha_app). Checked in a real browser: a typed message got its answer (time via `localtime`).
- **Open, the owner's part:** the PC's ufw (allow .11 → 8080, 8082, 8083: Whisper/Senses/PC model are blocked until then;
  speech to text has no other model now), the phone app (pair again: `notify.mobile_app_cph2581` for asks/Notify), Google
  calendar (reauth: token refresh 400), the UGREEN stick hangs (`Opcode 0x0c03 failed: -110`, batmon sees no device; was
  timing out since 11-02 already): replug it. Whisper on the 2070 needs the owner's OK to build whisper.cpp from GitHub.
- The Home card's access list still names some old entities (the old VM's `homeassistent_*` buttons, `notify.cph2581`,
  `tts.piper_2`, `stt.faster_whisper_2`, the PICO and the old voice satellite's media player): to tidy with the owner.
- Gemma answered "no rooms" although `find` returns the areas (Bedroom: bed light, aquarium): model behaviour, not the move.

## Before the move (2026-11-03 06:45) – 3.1.8 on `main` (not pushed), live on the owner's HA

**Paused (owner 2026-11-03 morning: "leave it for now … just use it")** — open, for when the owner picks it up:
- The Schedule counts a silent answer as none: "Bed light at the alarm" ("Don't speak") switched the light at 06:38 and
  still sent "nobody answered" at 07:12. Idea: a turn whose model used a tool counts as answered.
- The owner saw "a context with Gemma doesn't work on Qwen and the other way around": Qwen → Gemma replayed fine
  (18 messages, 18k tokens); the Assistant's log shows no failed turn after 07:00. Likely the dead Qwen process or
  Qwen's 16k context; Gemma → Qwen not tried (the server is tight on memory). Ask what the owner saw.
- Offered: AAC loads a model again when its router's process does not answer; Qwen's preset with less cache-ram;
  shorter guide / `help relay`; the Relay check refusing defs that name `context`; the Card editor's building set
  ticked off for the Assistant's everyday chat.
- The owner reads the Card editor's help and guide (builder.py) and the cards' helps.
- **A Relay trap found live (2026-11-04):** the owner's own AnswerOut Relay spoke every first sentence twice: a script runs
  again for every streamed sentence, and `output("speech", text)` made a new `answerout.said` each time. Fixed in the
  owner's board (the script now cleans `model.said` in place and sends that context; Text to speech Speaks `model.said`).
  To talk through: `output(port, text)` keeps writing the same entry while an answer streams (then the obvious script
  works), or the Relay shows the re-runs plainly (owner: the designer himself read it the obvious way).
- **Voice played ~7x too fast (2026-11-04 18:3x):** not AAC (its WAV checked: 22050 Hz, right length); PulseAudio played
  each clip in a seventh of its time after the BT300C dropped off USB and came back. A Proxmox restart fixed it. The
  UGREEN (hci0, only for the battery's Bluetooth) times out every 2 s since at least 2026-11-02 23:42.

**Where to pick up (2026-11-03, the night of steps 4 and 5):**
- **Step 4 done (3.1.4, live):** drafts. The Card editor (3.0.x) builds straight in what runs, in a slot, or in the
  draft (slot 4, greyed after 1–3 on the Cards page); `switch` waits for the model's answer, the new setup runs on
  trial, the Card editor hands the conversation back with the news, `keep` within 120 s or the setup before comes
  back (Keep / Back on the Cards page). Its `guide` (read first: building calls are refused until it is read; `help`
  of a type before setting one up), `call` (try a card's function), wire errors that say why, unknown settings
  refused, a repeated call told so. Live in the test domain "Build test" (kept, owner: "keep the domain"): Qwen built
  charter + web + a File memory read into its system prompt, switched, kept; flaws it made (a Relay "memory tool" whose
  defs use `context`, the Web/File cards without May use, fixes made in slot 4 after the switch) went into the guide.
  Its last turn was cut by a deploy. Next there (owner): after step 5, the same with Gemma A4B in another domain.
- **Step 5 done (3.1.6, live 04:59; 3.1.7): the cards run in the walled worker.** How AAC works shows it ("In their
  own process, walled in"). The 05:25 schedule fired on the dot from the worker's clock. The wall caught one real case
  at once: the Scope's spectrum (numpy loads numpy.fft only when first used): the worker now imports numpy's late parts
  before its wall, the spec says a card imports everything at its top.
- **3.1.8 (live 06:38): no tool named `call`.** With the Card editor's new `call` among the Assistant's 52 tools,
  Gemma 4 planned its call in its thinking and stopped (finish stop, no call): every tool turn since 3.1.4 was silent,
  so was the 05:25 alarm (the Schedule's "nobody answered" at 05:55 reached the phone). Found by replaying the captured
  request on the PC: leaving out only `call` fixed it; renamed `try_card` 4 of 4. The Model card never offers a plain
  `call` (TAKEN: the card's name goes in front). Live: "Bed light at the alarm" run at 06:38, the bed light went on.
  Yesterday's request had the same 46 tools minus the editor's six new ones. Thinking stays on (owner: it helps Gemma a
  lot, only slower). The Schedule entry says "the control tool" (none has that name; Home's is call_service).
- **Model tests (owner's next) — correction: every build in "Build test" was Gemma 4 26B-A4B on the PC** (AAC's one
  text model entry m_355fb306 points there; the server's Qwen is in no AAC model entry), not Qwen as written before.
  With the domain's 24 tools `call` did not stop it (replayed 3 of 3 both ways). Its own mistakes: a Relay "memory
  tool" whose defs use `context` (the guide says a def gets no context), tried nothing, asked for no permission; slow
  with thinking (about one tool round a minute). Ideas: the Relay's check refuses a def that names `context`; the
  guide's memory shape as a ready example to copy.
- **Qwen3.8 27B on the server (2026-11-03 06:49, owner: "it's Gemma then"):** AAC entry `m_qwen3_8_27b_server`, domain
  "Build test Qwen" (kept). Its server context is 16384: reading guide, rules and helps went over it (17907 tokens), no
  change made. Its process then died without a log line (router still said loaded: "proxy error: Could not establish
  connection"); reloaded by hand, fast and streaming (first token 0.6 s; it calls a tool even when none is needed). At
  07:32 the host (11.7 GB) was down to 550 MiB, earlyoom's SIGTERM to Qwen's llama-server did not take; it recovered.
  The Assistant is on Gemma again, Qwen unloaded (host 5.4 GB free). Offered, not built: AAC loads a model again once
  when its router's process does not answer ("proxy error"); Qwen's preset with cache-ram 0/256. The new server (i7,
  16+ GB, RTX 2070 next to the 7800 XT) is the real answer.
- **Step 5, how it went (owner: "finish the move before continuing with qwen and Gemma"):** docs/v2 page 6.
  Groundwork (`worker/`): the walled worker (seccomp; checked on the owner's HA), the channel, the 3 s watchdog.
  Move phase 1 done (3.1.5, live 04:05, checked: Home find/get_state and a Web search through the new services, a
  typed answer): the card layer (domain.py, flow, log, cards) reaches the world only through the core's services
  (`home.HomeService`, `services.Services`: web, files, notifications, the data folder; `models.ModelService`; time
  and tasks; stores; notes) and imports without Home Assistant (only its dt helper). Next: phase 2, the card layer
  in the worker (the core's services answer over the channel; the panel's commands go to the worker; the core holds
  the domain's sheet; tests keep the in-process run).
- **Found live 04:20 (for the owner):** the Assistant's Model card has Thinking on; Gemma 4 26B then often plans its
  tool calls in its thinking and stops (finish stop, no text, no call), so a tool question gets no answer at all
  (an empty answer counts as none). Thinking off, or AAC asking again once when a model only thought: the owner's
  call. Also from last night (23:54): Piper took over 30 s on every line, and the owner's Answer out Relay hands tool
  results (JSON) to Text to speech too.
- Branches: none any more (owner: "don't work in branches"); `v3` was merged, the local one is deleted (origin/v3 is
  the owner's to delete).

**Before (2026-11-02, owner: "v3 was a success"):**
- **Safety (items 19, 20, 21, 47) is under way: docs/v2 page 6** (settled points, open points, build order). Done:
  1 (Relay: a script stops itself when its own time is up: 3 s since 3.1.2, waiting for called cards not counted),
  2 (3.1.0: permissions in two layers, the domain's sheet on top; 3.1.1: everything a card does goes through its
  interface, the setup and model servers too), 3 (3.1.3: `ask`, every action waits for the owner's yes: panel popup
  top right, phone with Allow / Deny via `notify.mobile_app_cph2581` (AAC's settings); requests via
  `request_access`; on a card's own sheet not set is list now). Live 11:36: an ask from the Card editor denied in a
  real browser, nothing added, the editor back at full. Next: 4 (drafts: switch, keep or roll back; auto / ask), 5
  (one worker process for all cards, separate from HA; a hung card: ask it to stop, raise an error into its thread,
  last restart the worker; time limit per card 3 s: a card holding the worker that long without giving it back).
- **3.1.2 (owner's idea): the Audio clipper and the Phrase joiner are one card** (`pause_cut` 4.0.0: in, lines → out,
  said, cuts); no card builds by itself any more. The owner's board converted live 10:48 (stores before:
  `/config/aac/bak-3.1.1-storage/`); the Chat's Shown as you holds `Follow-up_turn.said` with a capital F (never
  matches: the card is follow_up_turn).
- **Postponed until the owner is home (voice):** test the voice settings and the day chat (and 3.1.2's Audio clipper
  that joins its own phrases: not yet heard live); put the wire `audio_clipper.said → follow_up_turn.in` back (the
  Phrase joiner is gone); suggested: the Follow-up turn skips what AAC itself said
  (`heard.data.get("aac_spoke")`) and names today's chat like the Wake word. Offered, no answer yet: tag matching
  without capitals (`matches()`; the owner's `answerOut.*` never matched).
- **Models:** the owner switched the PC to Qwen3.8 27B (does Gemma 4 do well, or did AAC get clearer?); it did a
  multi-turn tool use without trouble. Next the owner tries a small model again. The AAC model entry is still named
  "Gemma4-26bA4" (it follows the server). Item 9 (tool menus) waits: tools seem no big issue now.

- **Chat 3.1.0: a context can name its chat** (owner 2026-11-02: one chat per day for voice and the Schedule): a
  context whose id entry names a chat that is not there yet makes it with that id; one built on the chat (holds its
  first line) replaces what it holds, one that only names it is added to its end. Relay 4.1.0: `remove("*")` empties
  the conversation (system and tools stay).
  Live 00:3x: the Wake word names today's chat (`chat.id.<YYYY-MM-DD>`; the chat it read is not today's: emptied
  first; its script before in the scratchpad, wake_word_before_day.py), a new Relay "Day chat" between Schedule and
  Charter does the same for scheduled runs. Not yet tried live (the owner tests next time); the Follow-up turn does not
  name the day (it lands in the chat it read).
- **Text to speech 3.2.0:** each entry once, by time: only entries written after the card started and after the
  newest one it spoke to the end (live 2026-11-01 23:53: Speaks matched every entry, its 50-entry memory let a
  100-entry chat be read out from the start on every turn; minutes of speech, the Microphone kept listening, Whisper
  heard "Thank you." in what got past the Calisto, the owner's Follow-up turn answered each one: a loop through the
  air. The Follow-up turn's input wire was taken off to stop it; the owner puts it back.)
  2026-11-02 00:07, live settings: Text to speech Speaks `answerout.said` (it was empty: nothing spoken), Microphone
  "While AAC speaks there" mute. Not yet tested by the owner (next time); the Follow-up wire is still off. Offered:
  tag matching without capitals.
- **3.0.1:** a text-to-speech request is given up after Text to speech's setting Gives up after (default 30 s), with a log warning (live 23:35: the whole
  context went to Piper as one text, it never finished, and every sentence after it waited behind it).
- **3.0.2 (2026-11-02):** Home Assistant `call_service` waits up to 2 s for the entity's new state before it reports it
  (live 05:25: the bed light went on, but it reported "off": the Matter switch answers after the service returns). The
  panel's sidebar reloads when the page is shown again and when the connection comes back (the owner's app listed only
  2 of 6 chats: changes while it was hidden reached no page). Owner, on the day chat's first morning: a silent answer
  (the schedule's "Don't speak", a "nothing") sending nothing is expected, so is "Test" getting "nothing".

- **2026-11-01 (3.0.0): the rules from the ground up** (owner: "lets scrap the old rules and build from the ground
  up"; docs/v2 page 5, the rules in `custom_components/aac/rules.py`: the Card editor's `rules`, the panel's Settings →
  How AAC works). A context is a list of entries `{id, tag, text, data, time, state}`, first `context.system`, then
  `context.tools`, then the conversation; no kind, no by, no attachments, no events, no context id or way. A card acts
  when the context holds something for it and sends only when it did something (`Card.send`); one way to arrive
  (`flow.enter`: a call that names the card is answered by the core, everything else is `handle`); contexts move as a
  wave (found live: the Chat's copy of a call arrived after the answer and replaced it). Calls travel the
  wires like every context, one after the other (`Context.next_call`). Everything said is `<card>.said`. Stop is a
  core function (`stop_all`), no stop entry. One limit: the storm limit, an AAC-wide setting (`aac/settings`); the step
  limit went (it would end a heartbeat loop; the Timer holds on its own now). The Model acts when the newest entry is
  not its own, builds its request from the tags, "Means no answer" (default nothing: nothing is sent). The Chat was
  rebuilt: its id entry `chat.id.<id>`, a context without one filed once finished (until then "Being answered…"),
  Shown / Shown as you settings,
  `continue` dropped; its page kept. Text to speech speaks by a tag setting; the Speaker plays a clip without a
  satellite where its new setting says (else the log says so). Buffer: no system position. The card interface is
  written in `api.py` (cards stop reaching into the flow; a sandbox later). Every card file got a major version; the
  backup is 3.0.0 (stored chats are not converted: the owner lets them go). Live 22:46: the board saved first
  (scratchpad board_before_3.json), deployed, the old chats deleted, three scripts converted (Charter:
  `context.set("context.system", text())`; Follow-up turn and Stop word: `*.said`; the Stop word's stop output and its
  wire into the Charter went), two browser messages answered (one with a tool call). Not yet tried live: a spoken
  turn. The Speaker's "A clip without a satellite plays at" is set to Home Assistant (`ha_app`, owner 23:11: fix A):
  typed answers and heard turns whose script copied only the text (Wake word's `context.add("said", heard)`) play
  there.


- **2026-11-01 (2.6.3): the storm stop** (owner's design). HA Core froze at 15:56:25: the owner said "stop the vacuum",
  the Stop word sent a stop, it ran forward through the Model's out into Chat and Schedule, both wired back into the
  Charter, and doubled every round (a probe: past 5000 card visits; `MAX_STEPS` bounds each copy, not their number).
  The earlier "satellite hang" was most likely the same (HA frozen); the satellite reconnected by itself after HA's
  restart. Fixed: a stop passes every card once; `flow.enter` yields to HA between two cards; the storm stop: more
  than `STORM` (30) contexts through one wire within a second make it red (`flow.passes`, `domain.red`, kept in the
  domain's store), it carries nothing until unblocked (Cards page: click the wire, Unblock; `aac/wire/unblock`; the
  Card editor's `unblock`), an HA notification, an error in the log, every Card editor answer carries `red_wires`. A
  stack trace of the frozen HA was not possible (no guest agent on VM 100, the SSH app cannot see HA's processes).
  Live: Cards page draws, no red wires, a browser message answered, no console errors; the test chat deleted. Open:
  the owner's Stop word fires on any line containing "stop" (his script).

- **2026-11-01 (2.6.2, satellite 0.3.6): audio died again** (owner). The satellite had hung since between 11:46 and
  11:58: no reconnect after HA's restarts at 11:58 and 14:23 (no "Disconnected" logged), its event loop still alive
  (it hung up the BT600 cleanly when restarted). Where exactly is not known; every wait on a player or recorder that
  had no limit has one now (`GRACE`, `ended()`, a player gets the WAV's length + 10 s) and logs when it fires: the
  next hang names its place. Restarted 15:24, updated 15:29, connected, the mic hears the room. Found on the way, a
  real AAC bug: at 11:44:57 a context went Model ↔ Home 400 times in a second. The Chat had kept a Follow-up turn
  half-way (an answer whose Home call had no result yet); Wake word read it with "Okay, computer." on top; the Model
  took the old call as still waiting, the flow did not. One rule now: `Context.open_calls` counts a call only while
  its answer is the newest thing said or answered (test `test_a_call_left_behind_is_not_made_again`).

- **2026-11-01 (2.6.1): a chat is a tag, plainly in the context** (owner: he unwired Wake word's read and expected a
  new chat per spoken turn, got nothing in the Chat; "nothing should be hidden, mechanisms have to be discoverable and
  look-up-able with common sense"). The Context's hidden fields `origin` and `chat_id` are gone; a context has only
  `id` and `way` besides its containers (the card it started at is put first on its way when it first leaves). Every
  message a Chat holds carries `chat.<chat id>`; the Chat keeps every context reaching `answers` in the chat its
  messages are tagged with, else in a new chat ("Voice · mic · time" for a heard line). The "nothing" answer and the
  Relay's `_picks` rule stay (owner: necessary rules); `_picks` uses the first card on the way. The Model knows a
  conversation by its first message (`conversation_key`). A growing answer (delta) goes to every open page, the
  chat's finds its turn by the tag. Backup survived: 2.6.1. Live: a real browser chat of two turns stayed one chat,
  both turns tagged, the answer grew live, no console errors; the test chat deleted. Not yet tried by voice. On the
  owner's board: Wake word reads nothing now, so each wake-word turn is a chat of its own; Follow-up turn reads the
  chat, so it continues the newest one; the weekday 05:25 schedule run now shows as a chat too (`model.out →
  chat.answers`; a Relay in front of `chat.answers` would filter it).

- **2026-11-01 (2.6.0): the Model's routing is only what is wired** (owner: "the rules are breaking things"). The
  Model has `out` (its answer, to every card wired there, streamed) and `calls` (square: its tool calls). Its tools are
  the cards with functions reachable from `calls` that have a way back (`flow.reach`, `Card.calls_from`; the loop scan
  `flow.loop` is gone, a Relay's card calls check reach too). Gone: "the answer only back to where the turn started"
  (it made the owner's spoken answers silent: Wake word's context belongs to the Chat, so the answer went only there,
  never to Text to speech) and "cards wired straight back only get calls". A turn that comes back to a Model with
  nothing new ends there (`_done`: a card wired from out back into it, as Lists passes a context on); a Model passes
  no pieces from upstream on (as its old square out). The default voice chain lost its Answer Relay (the Model's out
  reaches Chat and Text to speech). Backup broke: 2.6.0. Live: the owner's wires converted once (scratchpad
  `convert26.py`: a tool wired straight back moved from out to calls; Schedule and the Chat, tools on the way to
  others, got a calls wire next to their out wire); the Model's 10 tools the same as before; a real browser message
  used the time tool and was answered (not spoken: typed); the test chat deleted. Open: the domain `test123` still
  has the old wiring (convert when it is used); the Relay still has its own rule "a card wired to it and back only
  gets calls" (`_picks`); what is spoken can now be chosen by a Relay in front of Text to speech (owner's idea).

- **2026-11-01 (satellite 0.3.5): hunting the Calisto's noise blasts** (item 61). The satellite app runs with the
  new option `debug` (`addons/aac_satellite/debug.py`), on the live HA since 09:25.

- **2026-10-31 (2.5.0): where a Relay writes says what a model reads it as** (owner: his Wake word's `attach_first`
  landed in the system prompt, visible only in Plain text). `add(tag, text, at="last"|"first", role="user"|"system")`
  makes a new entry (a user message, or a part of the system prompt, which a model always reads first);
  `attach(tag, text, at=)` adds onto the last / first entry a model reads, in its role (no more silent fallback to the
  system prompt); `set(…, at=, role=)` adds like `add` when the tag is missing; `remove(tag, at="all"|"first"|"last")`;
  `e.role`. One pattern (`at=`) for every "where"; `read=True` stays a bool. No `role="tool"` (only an answer to a
  call) or `"assistant"` (a model reads another card's answer as a user message). Docstrings stay plain Python, not
  marked (owner). Backup broke: 2.5.0 (I had said 2.4.1; the backup test decided). Live: the owner's Charter, Spoken
  turn, Follow-up turn and Wake word got only the new names, same behaviour (`attach_first` → `add(…, at="first",
  role="system")`, `attach_last` → `attach`, the Charter's `set` with `role="system"`); a real message in the browser
  answered, Plain text starts with the charter as the system prompt; the test chat deleted.

- **2026-10-31 (2.4.0): item 58, the Relay rework** (owner's design, talked through first: docs/v2/04-relay.md). A
  Relay's script is plain Python that runs top to bottom every time something arrives, every piece of a model's
  answer too: `context = input("in")`, `output("out", context)`; nothing leaves unless an output line runs (no
  pass-through, no fallback to out; a new Relay starts with these two lines, an empty script sends nothing). Its ports
  are what it names (`input("x")`, `input("x", read=True)` dotted, `output("x", …)`; none named: in, out). The
  context reads as a list of tagged texts (`get`, `[tag]`, `in`, `all(…).text`, `text`, `attach_first`,
  `attach_last`, `set(…, at=)`, `remove*`, `a + b`); a tag without a dot is the Relay's own, `*` any. `output` takes a
  context as it is, anything else as the Relay's answer in the context that arrived. Every `def` is a function (a call
  runs only that def; another Relay calls `relay_2.time()`); card calls take arguments in order or by name, not on a
  piece (`context.finished`). `print` shows under Printed in the editor (half an hour), `print_log` writes into AAC's
  log. The script's own names win over a card's id (`cards["output"]`). The Card editor (1.6.0) got `help(type)`: a
  type's setup help from what it declares (the Relay's: its script reference); `types` is one line each. Editor
  rewritten (suggestions per name: context / entry / text; status says when nothing leaves; Simple view rows: text,
  if, send). Backup broke: new 2.4.0 backup. The owner's six Relays were converted by a scratchpad script, tried
  first on a copy of the live stores (a real turn: charter as system, `localtime` called); live: a real message in
  the browser (Time's `localtime` answered), the editor, Wide, ?, Printed and suggestions checked, the test chat
  deleted. Time is now a toolbox (only `def time()`), Charter offers `text()` as before. The 20 Undo steps from before
  hold old-format scripts: undoing past the conversion brings back scripts that no longer run.

- **2026-10-31 (2.3.36): Schedule 1.3.1, and a Notify card on live.** Run now rings like an entry at its time (the
  same instruction, watched for an answer; a one-time entry stays). Nothing wired to its output: it goes out at
  'unanswered' at once ("nothing is wired to its output"). Owner: models may make entries (allowed stays); live got a
  Notify card (notify.mobile_app_cph2581, the owner's phone) wired from schedule.unanswered; the test notification
  arrived.
- **2026-10-31 (2.3.35): item 39, Schedule like v1, and AAC's calendar in the core** (owner: cards only use interfaces
  the core offers; the calendar was hardwired). `calendars.py`: the core keeps no entries, only who offers them
  (`core.calendar.offer(card, query, create, change, remove)`; `entries`, `add`, `change`, `remove` by uid
  "<card>:<key>"); HA's `calendar.aac_calendar`, the Calendar card and models ask there. The `ha_calendar` channel and
  `dated()` are gone; the Calendar card (1.3.0) offers its own entries (all four ways) and lost its "lists" setting
  (every card's entries show); Lists (1.3.0) offers its dated entries (read only). Schedule 1.3.0: entries are data (no
  Undo step; `upgrade_data`, a new generic card hook, moved them out of the settings as the owner's), each says who
  made it (owner / aac); a model may add, change, remove only its own, at most 20, as the setting "Entries a model
  makes" allows (allowed / ask: waits for Allow on its page / off: no add/change/remove offered; a permission);
  days as names ("Mon-Fri", "weekend"); quiet hours (fold) make AAC's entries wait; the fired line says what to do
  with a reminder; an entry nobody answered within "Unanswered after" (30 min) or that a model failed on goes out at
  the new output 'unanswered' (an answer, for a Notify card) and into the log; its times are on AAC's calendar (read
  only there). Its editor moved from the core panel to its page (Allow / Refuse, Run now). Found by the new log: a
  200 answer without choices crashed the client (KeyError); now a model error. Cooldown (v1, for state triggers) does
  not apply to fixed times; the HA event card has its own.
- **2026-10-31 (2.3.34): the log rotates in two lanes.** Live, the voice chain wrote ~40 entries a minute (mostly the
  Audio clipper hearing the room): one rotation of 5,000 would have pushed errors and changes out within two hours. Now
  the newest 5,000 of what passed the wires (flow) stay, and the newest 5,000 of everything else. A context with only
  signals (the clipper's cuts) is written as its newest signal's kind and tags instead of "(no text)".
  Owner (2026-10-31): Schedule, Heartbeat and HA event keep their own history of what fired and what came back (on their pages),
  apart from the log.

- **2026-10-31 (2.3.33): item 38, AAC's own log** (owner and a friend: a proper logging mechanism in AAC itself).
  `log.py` `DomainLog` per domain, `<config>/aac/data/<domain>/log.jsonl` (appended every 2 s, written anew past 6,000
  lines; moves with a rename, goes with the domain, a copy starts its own). Rotates at 5,000 entries (two lanes since
  2.3.34); whole contexts
  stay 10 minutes in memory only; a repeat counts up (`n`). Levels error, warning, info, flow. Every card has the fold
  "Log" (`log`: what changed / whole context / nothing; `log_at`: out / in / both; default what changed going out);
  `self.log(...)`, `self.read_log(...)` (own entries always, the rest with `needs = {"read_log"}` → permission
  `may_read_log`, the start of item 20's card layer). The core writes failures with their trace, models not answering,
  loops, no way back, failing functions, and what the owner changes; HA's log keeps only card files loading and the
  first start. Wire monitors read the log (`aac/monitor` says `off` when the card writes nothing going out). Settings →
  Logs: level / card filters, search, Show older, Save as text, Clear; live. Log card 1.3.0: writes into the log,
  allowed to read it shows all of it on its page, function `read`, output `provides`; its own data store is gone.
  A setting fold opens when a setting in it differs from its default (For models was always open). Tested locally
  (a real message, Logs page, monitor, a throwaway Log card with and without permission, phone width).
- **2026-10-31 (2.3.31, 2.3.32): item 29, Models page.** The top list has headings (Name in AAC, Server, The server's
  model, Can do, Now; hidden on a phone, where rows wrap), each AAC model shows what the server's model is (parameters,
  context) and its state in words (✓ ok / ✗ not answering; idle / busy n of m / waiting). A router's numbers for a
  loaded model (params, size, quant) are kept in core data `facts` ("<address> <model>", like `benches`) and shown
  while it is not loaded; a model deleted on the server loses its facts and bench (`pool.forget`). Models never loaded
  since show none (only llama.cpp says it). Item 28 dropped (owner: the order and queues are set in the Model card).
  Item 56 (context per model): the router cannot load a model with another context (`/models/load` takes only the
  name; the context is the preset's `ctx-size`, read at start), and only the machine can estimate memory
  (`llama-fit-params --fit-print`, tried on the PC: Gemma 26B-A4B 13.6 GB + context 0.9 GB at 32 k, 2.9 GB at 128 k,
  5.4 GB at 262 k + 0.6–0.8 GB compute). The owner does not want a helper on the machines ("maybe that can be a card at
  some point"): the page shows the context and the model's maximum, and that is it. GPUs: PC = RTX 5070 Ti, CT 110 =
  RX 7800 XT.

- **2026-10-31 (2.3.30): items 15 and 52.** Undo steps stay whole in memory; stored, each keeps only what differs from
  the setup after it (`packed` / `unpacked` in core.py: its card ids, changed cards and wires, positions as they are; a
  step stored whole loads as it is). Owner's store computed offline: Undo 453 KB → 31 KB (the Home card's 16 KB access
  list was in every step); local test HA 112 KB → 30 KB. A removed card's data and files stay while the setup, Undo,
  Redo or a slot knows it, then go (`_on_top` trims, `_tidy` → `_drop_data(only_unused)`, after the change: HA starts
  tasks eagerly, so `eager_start=False`); Redo is not kept over unload. The owner removing cards whose data differs from
  `default_data()` (summary `has_data`; the panel reloads first) is asked: Cancel / Keep (Undo brings it back with it) /
  Delete the data (`aac/card/remove` `drop_data`: Undo brings the card back empty). A model removing a card keeps it.
  Tried in the local test HA and live in a throwaway copy of the domain (question, Keep + Undo, Delete + Undo, a real
  message answered, copy deleted). Backup before: `/config/aac/bak-v2-2.3.29/` (2.3.27 removed). The owner's store
  packs at its next change. Left over from before: `aac2.card.assistant.log` (a Log card removed before Undo knew it,
  Oct 29): removed on the owner's word.

- **2026-10-31 (2.3.29): a domain's id is its name too** (owner: "it's always ids and show names or the name is the id";
  things work by the default rules, as few special cases as possible). `Core`: `fits(id, name)` (shared with cards),
  `_move(old, new)` moves the domain's store, its cards' stores (setup, Undo steps, slots) and files, the active mark; a
  running domain starts again under the new id. Rename (`aac/domain/rename` answers the id) and load (an id that does not
  fit) move it. Cards get one generic folder, `self.folder` = `<config>/aac/data/<domain>/<card>`, moved with a card's or
  a domain's rename, copied with Copy, gone with Delete; the Log card keeps its clips there (was `aac/log/<domain>.<card>`,
  a special case a rename left behind; no such folder existed anywhere). The browser's wire monitors and sidebar folds
  follow a card or domain rename (panel `_moved`). Tried in the local test HA, then live with a throwaway domain through
  the real menu: Copy "ZZ try", Rename "ZZ renamed" (5 stores and a monitor moved), a real message answered, Delete left
  nothing. The owner's two domains already fit their names: nothing moved. Owner confirmed 32 and 46.

- **2026-10-31 (2.3.28): a card's id is its name** (owner: "clean all the way, consistent"; after 2.3.27 kept the
  id `node_editor` for the renamed Card editor). `slug(title)`, a number after it when two share a name. `Domain`:
  `follow_names()` (at load, after Undo / Redo / a slot / a file), `_wanted()`, `_move(old, new)`: the setup, Undo
  and Redo steps, slots, the card's store (moved), the other cards' settings (class method `renamed_settings`: by
  default a script's `old.tag` and `cards['old']`, not a word alone: `e.data.get("mic")` is a field; the Calendar
  adds its `lists`, also at its default) and data (`renamed`: entries' `by`, `tags`, a call's `card`; not keys: Lists
  keeps a field "lists"), the sidebar fold, `flow.held`. New ids never meet one in the history (a removed card coming
  back). `add_card` lost `card_id`; defaults.py builds from the ids cards get. The panel follows a rename (`sel`) and
  lets go of a selection whose card is gone. Live: tried first on a copy of the owner's domain in the local test HA
  (23 cards, 34 wires, 20 Undo steps; mic, cut, ears, spoken, followed, node_editor moved; the chat store's 74
  references all carried over; rename + Undo through the real Name field), then deployed: the same 6 moved, a real
  message and an Assist turn answered (both test chats deleted). test123 moves when it is next opened. Backup before:
  `/config/aac/bak-v2-2.3.27/` (the older 2.2.2 one removed: newest only). Backup test compares cards by name.
  Item 58 added (Relay rework, owner).

- **2026-10-31 (2.3.27): items 37, 30, 41** (owner: do them; 51 needs a talk first). **37:** cards are only called
  cards: the Nodes page is the **Cards** page (view kind `cards`, `_cardsView()`, CSS `.cardsview`, a card on the
  grid `.cardbox` / `data-cardbox`, the neon skin too; `core.cards_page_open`), the Node editor is the **Card
  editor** (type `builder` unchanged; the owner's card retitled, its id `node_editor` kept; test123's not); help
  texts, docstrings, spec, README, docs/v2. The Relay's "node" words are Python syntax-tree nodes and stay. **30:**
  each server on the Models page says when it last answered and when a request last failed, and why (red when the
  failure is newer; since AAC started, in memory: `pool.heard`, set by `mark_up` / `mark_down` / `heard_from`).
  **41:** the File card has a read output `provides` (the file's text as one entry, the last 8,000 characters, kept
  in memory: loaded at start, renewed by every write and read, so `held()` never touches the disk). **Found:**
  pytest's shared test config (`…/pytest_homeassistant_custom_component/testing_config/aac`) still held card files
  from before 2.2.1 (`tools/`, `auto/`, `audio.py`, `conditional.py`); the old `tools/` File replaced the new one,
  so some tests ran old card code. `conftest.clean_aac_folder` empties it before every test. The local test HA had
  the same leftovers (removed); the live HA is clean. Checked: local test HA (Cards page by real clicks, a File card
  wired dotted into a Relay, a failing model shown red), live HA (Cards page, a real message, its chat deleted,
  "Last answered" per server).

- **2026-10-31 (2.3.26): open items 31, 32, 42–46.** Lists (43, 44): Undo remove and editing (a click on the text)
  were there and work (real clicks, desktop and touch); Undo now puts entries back where they were (a sub-entry under
  its parent, at its place; a deleted list at its place; before, all at the end of the top level), the text shows
  "Click to change". Sidebar (45): a card's `nav()` may say `"fold": "arrow"` (stays open until its arrow closes it);
  Chat and Lists do (the core's hidden `key === "chat"` is gone). Access list (42): a redraw keeps its own scroll (it
  jumped to the top on every click). Phone (46): narrower than 380 px (side panel, phone) a row puts the name on its
  own line, the buttons below; the list does not hand its scroll to the page. Permissions page: "For models: [object
  Object]" → "4 of 4 functions for models". Models page (31): a line between the models and the hovered row marked
  (the Delete far right was hard to match); on a phone the top table wraps and server headers wrap (it scrolled
  sideways). 32: not reproduced on either router (PC build 11429, CT 110 529ac89; download, stop, delete, the next
  download: no cross-talk). What did happen: AAC listens to the live events 1.5 s per poll and they come every
  1–1.9 s, so a poll without an event showed 0 % (the jumping); the pool keeps the last progress (`pool.progress`).
  On the PC's build Stop throws the download away (no "incomplete" entry), on CT 110 it stays incomplete:
  "Pause" became "Stop" with a tooltip saying so. Checked: local test HA (taps at 390 px, list scroll, Lists clicks),
  live HA (sidebar, Permissions, scroll on a redraw, a real message sent and its chat deleted).

- **2026-10-31 (2.3.25): no card on another, snapping while dragging (items 35, 36).** One check (`_hit`, `_under`):
  a card may not lie on or within 10 px of another in its column range; the one that would goes below. Dragging: the
  card (or the selection) snaps into its column and below what it covers while it moves, so it lands where it is shown.
  A new card lands below what it would cover (the ghost shows it). `_settle()` runs whenever the Nodes page draws (not
  while dragging): cards on each other (a card grew, positions from elsewhere) – the lower one moves down; cards
  without a place (the Node editor and the voice chain add them without one, they all sat at 0,0) get the first column,
  below what is there; saved if anything moved. Checked on the local test HA (drag onto a card, a new card onto one,
  two cards on one spot, a card without a place) and on the live HA (the owner's 23 cards: none touched, none moved).

- **2026-10-31 (2.3.24): the grid's round buttons on the right, smaller (30 px), drawn line icons** (owner: plain
  symbols, not the emoji bin; `ICON` in aac.js). Their box is `.gridtools`: 2.3.21–2.3.23 called it `.tools`, which
  also matched the chat Context view's tools part (`.cvpart.tools`, `.cvlab.tools`) and pulled it out of place.

- **2026-10-31 (2.3.23): test: every card survives remove + Undo** (owner's idea). `test_every_card_survives_remove_and_undo`:
  one card of every type, wired and placed, Lists and Chat with data; all removed at once, one Undo: setup, wires,
  positions, titles and each card's data as before, and the chat and Lists work again (fresh and from the backup).
  It found a real bug: AAC's unload passed `watcher.cancel` to `entry.async_on_unload`, its `True` was taken for a
  task ("Error unloading entry AAC" whenever AAC is reloaded); now a small function that returns nothing.
  Also run on the live HA on a copy of the owner's domain (23 cards, 37 wires): all removed, one Undo, cards, wires,
  positions and every card's data as before, Lists and Chat answer; the copy deleted, the main domain untouched (4 s).

- **2026-10-31 (2.3.22): 🗑 and Del remove the selection** (owner: deleting belongs with the tool buttons; Del was
  pressed by habit). A round 🗑 under Select and Cut (greyed out when nothing is selected) and Del / Backspace remove the
  selected cards or the selected wire. `aac/card/remove` takes `cards` (a list) and `Domain.remove_card(*ids)` removes
  them in one Undo step that also keeps their positions (before, Undo brought a removed card back at 0,0). Test in
  test_core; checked on the local test HA (Del on two framed cards, one Undo back with wires and positions; 🗑 on a card
  and on a wire) and on the live HA (the button follows the selection; nothing removed).

- **2026-10-31 (2.3.21): round tool buttons on the grid, long press** (owner: the mouse drags the view; round buttons
  for actions such as select; a cut line for wires; long press on a phone did not select). Left-drag pans again (2.3.20's
  frame on the left button is gone). Top left of the grid: **Select** (⬚: a drag on the empty grid draws the frame, a
  click on a card adds or removes it) and **Cut** (✂: a drawn line, freehand, removes every wire it crosses; the wires
  turn red while drawing; one `aac/wires`, Undo brings them back). A tool stays on until pressed again or Esc. Without
  one, Ctrl+click and Ctrl+drag still select. On a phone a long press (500 ms, the finger held within 8 px) adds or
  removes a card (a short buzz); a finger that moves still drags the card. Checked with real mouse and touch input on
  the local test HA (cut, long press, moving) and the live HA (pan, Select, Esc; nothing changed there).

- **2026-10-31 (2.3.20): select and move several cards (item 34).** A frame drawn with the mouse on the empty grid
  selects the cards it touches (Ctrl held: adds them); Ctrl+click adds or removes one; dragging a selected card moves
  them all and they snap as one block (`_snap(id, group)`: the dragged card into its column, the rest keep their places,
  pushed down together if they would cover another card). With several selected the side panel lists them (no settings);
  Esc or a click on the empty grid lets go. (2.3.21: the frame and Ctrl+click moved to the Select button.) `sel` is a setter: picking one card or none clears the several (`many`). Delete only removes a
  single selected card. Checked with real mouse input on the local test HA (moving) and the live HA (selecting only).

- **2026-10-31 (2.3.19): bug 57 fixed.** On a phone the SETTINGS heading went straight to the Audio page (its `data-go`;
  a page closes the sidebar), so Models and Appearance could not be reached. Now on a phone it only opens the group in
  the sidebar (`navPeek`, cleared by `go()`); a computer still opens the first settings page. Checked with real touch
  taps at 390×844 on the live HA (☰ → SETTINGS opens, again closes, Models opens the Models page).

- **2026-10-30 (2.3.18): Model card setting "Most tool rounds without an answer"** (owner: may be unlimited). Was the
  fixed MAX_ROUNDS 8; now `max_rounds`, default 8, 0 or empty = no limit (Stop still ends it; flow's MAX_STEPS 400 stays).
- **2026-10-30, outside the code:** HA's own AI removed (GLaDOS pipeline, llama.cpp, ai_memory, LVA); AAC is HA's
  preferred pipeline "AAC" (text). Voice: Calisto via the satellite app, Wyoming `tts.piper` (CT 110, own announcer
  voice) and `stt.faster_whisper` (CT 110, fallback after the PC Whisper). CT 110 has no chat model any more (the owner
  deleted the 12B, 26B, Ornith). Open: the Text to speech card's en voice → `en_US-announcer-medium` (language `en-us`)
  once the Assistant domain is active again; first spoken test when the Calisto is on. Details: docs/home-assistant.md.

- **2026-10-30 (2.3.17): tools are the loop again, and calls travel through it** (owner: "everything always moves
  through a context through wires", not "tools only when wired directly"; 2.3.0's pair rule had taken the Chat's
  functions from his model). `flow.loop()` / `tools()`: cards with functions on a way from a card's outputs back into
  it; `flow.way()` finds the shortest way along text wires, `flow.carry()` takes a call there and its result back (the
  cards between only pass it on, every wire lights). A Model's answer goes to every wired card but those wired straight
  back (pure tools), so a second Model or the Answer Relay further round still gets it. MAX_STEPS 400.

- **2026-10-30 (2.3.16): thinking, cut-offs, tokens** (owner). The client reads the server's thinking
  (`reasoning_content`), why it stopped (`finish_reason`) and the tokens (`stream_options.include_usage`); the context
  size once per model from `/props`. Thinking streams into the chat (a fold) and stays on the answer; a cut-off answer
  says why (stopped, context full, Longest answer reached, the model failed), for the chat and for the model next turn;
  Longest answer has no cap by default (the owner's essays were cut at 1024 with thinking on); tokens in the chat header
  and Plain text; from 80 % a note to the model (Model card setting); a too-long chat gets a plain error.

- **2026-10-30 (2.3.13–2.3.15):** Plain text of a turn no model read shows AAC's context itself (container by
  container, one entry per line, keys and brackets dim, answers blue). The Relay's help is written for people new to
  Python (owner): "How a script works" at the top of the ? panel (blocks, def, @on, @new, output), a Basics group in
  the reference (def, if, quotes, = vs ==, for, #), output explained as picking the way out (only there, not at out).

- **2026-10-30 (2.3.11, 2.3.12): no model needed** (owner, building Chat → Relay → Chat in a test domain to show that
  anything is possible). A chat no longer says "no answer came back" when something is wired and does not answer (only
  when nothing is wired to `said`), and never hands back "(no answer)". A Relay script's card calls **travel** like a
  model's (`Relay.travel`: an answer of the Relay with the call, the result back along the card's wire, `_answered`
  wakes the script; the old `call` event kind is gone); a Relay sends the turn back to the card it started at even when
  that card is its tool; `chat.post` during a turn of that chat writes into the turn. Plain text shows the raw context
  when no model read the turn. Checked by sending through the real page in the owner's test123 domain.

- **2026-10-30 (2.3.3–2.3.10):** Plain text shows "The model answered:" below the text and the model's earlier answers
  blue; Context reads top to bottom (a colour per part, only connected things fold, events set apart); the pencil
  (item 11); the sidebar ⋯ closes on a click elsewhere. **2.3.7–2.3.9 broke sending in every chat** (the owner hit it
  showing AAC to a friend): a line shown before it has an id matched "nothing being edited", drawing threw before the
  message went out. Fixed in 2.3.10, checked by sending in a real browser (now the rule for page changes).

- **2026-10-30 (2.3.1, 2.3.2):** 2.3.1 fixed the Nodes page (a removed panel helper was still called by the card type
  info: every click there crashed). 2.3.2: the chat has three views (owner): **Chat**, **Context** (the sectioned view)
  and **Plain text**: the newest request exactly as the model read it, written by llama-server with the model's chat
  template (`/apply-template`, `Client.template()`; markers dimmed, each tool on its own line; else the request as plain
  text). The Chat keeps the newest request with each chat (`last.request`, set by the Model as `context.request`).

- **2026-10-30 (2.3.0): the context as a model request** (docs/v2 page 3, owner's design talk). Containers system /
  messages (said, answer with its calls, result; notes and trailers attached to their message) / tools / events
  (never sent to a model). A model's tools are the cards wired from it and straight back (the loop search is gone: it
  had made every card in the big loop everyone's tool); calls **travel**: along the wire to the card, the core runs the
  function there, the result comes back along the card's wire (several one after the other; the wires light). Every
  Model card is a member of the chat (others' answers by name). For models per function (tick, name, description,
  Reset) replaced offers / instructions / About your tools; a Relay offers one function per @new block (the owner's
  Time: `get_time`, "call to get the time"). Chat: Chat + Behind the scenes (the context in sections, `contextView`
  in aac.js, also the wire monitor) replaced Document and Request (the Model's in-memory requests and
  `aac/chat/request` are gone); tool calls as small lines; bugs 22, 23, 25 fixed. Schedule / HA event / Heartbeat
  ignore snapshots (part of 34). The owner's data was converted by a scratchpad script (8 chats, 10 calls, the Relays'
  old settings dropped, Undo steps too) with HA stopped ~2 min; checked in the test environment first with the
  exported setup and a copy of the chats. Backup before: `/config/aac/bak-v2-2.2.2/`. Test backup now 2.3.0.
  Open from it: in the owner's wiring the Chat is not wired straight to the Model, so the model no longer has the
  Chat's functions (chats, continue, read, post): talk through. Chats from before 2.3.0 show no system parts and tools
  in Behind the scenes until their next turn. JS is checked with headless Firefox (scratchpad jscheck.py).

- **2026-10-30 (2.2.2): review fixes.** A review of the whole code against docs/v2 page 1 (owner's worry: things that
  seem to work but go against the idea). Fixed: the Model card's `ask` never worked (a relative import; now tested);
  the Chat card stored a copy of every opened chat (`get()` wrote `messages` into its data; the 8 live chats were
  cleaned with jq while HA was stopped, 119 → 84 KB); Memory's `forget` deleted every memory containing the text (now
  one memory by id or whole text); leftovers removed (Home card upgrade, the loader's flat-file removal, v1 device
  removal, the default Home card's unused option, stale comments, an unused import); tests no longer depend on the
  date, two races fixed (suite green 8× in a row). Open review points: items 48–53. Backup before:
  `/config/aac/bak-v2-2.2.1/`.

- **2026-10-30 (2.2.1): one card type per folder (owner rule: "Cards in their own files always").** `audio.py` (7
  cards), `tools/` (File, Notify, Timer, Web, Model servers) and `auto/` (Schedule, HA event, Heartbeat) became 15
  folders `cards/<type>/card.py`; the auto cards' base class and log page are copied into each (spec/cards.md "A card
  folder"). The old files were removed from `<config>/aac/cards` by hand at the deploy; backup before:
  `/config/aac/bak-v2-2.2.0/` (storage + cards.tar). The flat single-card files (buffer, builder, decide,
  homeassistant, model) stay files. Owner, same day: work on the live AAC; changes that could break data are tried in a
  separate domain first (the live AAC now holds real data).

Rebuilt from scratch on the owner's design (docs/v2): everything is a card instance in a domain, cards own their data,
the owner wires them, or a card does (the Node editor, for a model; never the core on its own), the message is a document of entries with `<card>.<tag>` tags.

- **2026-10-25 (2.1.30–2.1.48):** model servers as records with their models; both machines run llama-server routers
  (switch, download, delete, Bench, follow what is loaded; Next 14); the Model servers card; a spoken turn shows in
  the chat at once; the Nodes page works on a phone (folding side panels, touch, pinch, Back); echo subtraction with
  a Waveforms view (Next 16); the panel keeps its scroll position on a redraw. Models now (every model tried and the tests: `docs/models.md`): PC Gemma 4 26B-A4B, server
  (RX 7800 XT) Gemma 4 12B as `qwen3-8b`; Qwen and Ornith dropped by the owner. The server froze 4× that day (the
  7800 XT's runtime power management copied VRAM into RAM): fixed with `amdgpu.runpm=0` (CT 110 notes).

- **2026-10-25 (2.2.0): Relay scripts (Next 6).** Relay and Conditional became one card, the Relay, with a script in
  Python (owner's design, talked through first): `@new("name", at=/before=/after=)` writes an entry of its own,
  `@on("tag", which=, kind=, live=)` works on entries (`*.said` any card, `"context"` the whole context); `chat.said.text`,
  `context`, card values and functions of cards in its loop, `now()`, `stop()`, `output("x")` (the output appears).
  Real Python syntax on purpose (owner: models, small ones too, must edit it naturally); checked with `ast` (no
  imports / while / `_`), runs in a worker thread, card calls go back to HA's loop. Streams: untouched entries pass,
  touched ones are held until closed (`live=True`: every piece); side effects once per turn. Everything lives in the
  card folder `cards/relay/` (card.py, script.py, editor.js: owner: every card can be removed, the core stays light);
  the core only got a generic setting type `element` (a card brings its own editor; spec/cards.md). Editor: colours,
  line numbers, suggestions, Wide, ? panel (reference with examples, cache and stream notes, the domain's tags,
  values, functions), Last run, Simple view (rows that write the code). The Node editor's `types` now shows each
  setting's help (models read the script reference there). The owner's Relays got their scripts and the two Conditionals
  became Relays once, through AAC's websocket (card update / remove / add / wires; Wake word is now `wake_word`), by a
  scratchpad script (no converter in AAC); `conditional.py` removed from `<config>/aac/cards`; backup before:
  `/config/aac/bak-v2-2.1.48/` (the 2.1.35 one removed). Test turn: Charter and Time ran, the answer had the time; the test backup is now 2.2.0. docs/v2 page 2 was
  corrected against the code (no Voice chat, Pool or Conditional cards any more; every card listed).

- **Core** (`core.py`): the model pool (shared by all domains, `models.py`: OpenAI-compatible client, streaming, tool
  calls, a model that does not answer is skipped a minute); domains (one runs at a time; clone, switch); each domain
  keeps its cards, wires, positions, Undo (20 steps) and 3 save slots on the server; each card's data in its own store
  (`aac2.card.<domain>.<card>`).
- **Flow** (`flow.py`): round outputs copy, square ones choose; a card's tools are the cards wired out of it and back
  in (functions called directly, call + result written into the message); the newest message per output is kept for
  the monitor; `flow` events pulse the wires.
- **Cards** (`cards/`): Chat, Model, Relay (a Python script, 2.2.0), Buffer, Lists
  (with edit and undo of a removal), Home Assistant (own access list: hidden / read / full).
- **More cards (alpha.3):** Schedule, HA event, Heartbeat (start a chain, keep a log page; Schedule entries can be
  added by a model), HA Assist (AAC is HA's conversation agent again, entity `conversation.aac_qwen3_8b`), Decide,
  Memory (by meaning with an embedding model, else by words), File, Notify, Timer, Web (DuckDuckGo or SearXNG),
  Node editor (type builder: a model changes cards and wires, within its settings).
- **Audio (alpha.3, `audio.py` + the audio card folders):** satellites on `/api/aac/audio` (same protocol: the add-on works
  unchanged, its HA user taken over from v1), STT in the order on the Audio page (Whisper on the PC, then HA's),
  TTS (Piper, voices per language), volume; cards Microphone, Noise gate, Audio clipper, Speech to text, Voice chat
  (sessions per satellite, follow-up window after Voice out's "finished"), Voice out. Audio wires carry a stream
  (thickness = loudness), the Audio clipper (type pause_cut) makes audio clips (`clip` wires; a clip is any finished bit of audio).
- **Calendar (alpha.4, `cards/calendar.py` + `calendar.py`):** its own entries plus the open entries of Lists cards
  with a field when/date/due; HA shows them as `calendar.aac_calendar` (v1's unique id kept; create/change/delete from
  HA work); functions calendar/add/change/remove; one-time: a Calendar card as the default model's tool.
- **Request view (alpha.5):** a chat's Request button shows what the model got for the newest turn: each message with
  the cards its text came from, and the tools it had (`aac/chat/request`; kept in memory, 30 chats per Model card).
- **Grid like v1 (alpha.6):** sidebar, card list and settings resize by dragging their edge (double-click resets;
  kept per browser); cards snap into columns (a column per step); Arrange puts them in v1's slots (tools stacked
  under their card, separate chains below each other; undoable, only positions change); wires use v1's routes
  (curves forward, side lanes for tool loops, lanes below the cards for replies and far wires). alpha.12: a card
  wired only to cards above it in its column (a tool under its model) is mirrored (in right, out left; v1).
- **Rules editor (alpha.7):** Conditional rules are edited as forms (IF which entries + test, THEN actions with the
  fields each needs: card and function pickers for calls, output picker; reorder, remove); JSON stays under "As JSON".
- **Monitor windows (alpha.9):** a wire's Monitor button pins a window on the wire (live text and count, flashes);
  a click shows the newest message (entries, way) and what passed in the last 10 minutes (`flow.records`).
- **Card values in templates (alpha.10):** cards declare values (Lists: lists/open/total, Calendar: today/next,
  Buffer: text/lines/count, Memory: count); a Relay's template reads `{{ lists.total }}` or `{{ cards['id'].x }}`;
  the settings panel lists each card's values.
- **Setup files and own tags (alpha.11):** Save file / Load file in the node view (cards, settings, wires, positions;
  the cards' own data stays; loading is undoable); with no card selected the side panel lists the domain's tags and
  adds/removes the owner's own (`user.…`).
- **Stop as a context (alpha.15):** a stop is a context whose newest entry is a stop signal (`<card>.stop`); it runs
  forward on the wires; a busy card (a model writing) cuts its block, appends the stop (marked done: visible history)
  and sends its own context on, the stop ends there; idle cards pass it on. The chat's Stop button sends the chat's
  context with a stop; the next turn shows the model "[stopped]". 
- **Live wires (alpha.15):** a push lights the wires it took (brighter, thicker, fading over 4 s) with a -_-_ wave
  running along; audio streams keep the wave running while sound flows.
- **Contexts and wires (alpha.16, owner 2026-10-24):** solid wire = push (a context arrives, the card runs), dotted
  wire = read (looked at when the card runs, never starts anything). Chat holds a context per chat: typing adds the
  line and sends the whole context; its input replaces what it holds; what it shows is read from that context. A
  Relay with a dotted wire into `reads` starts from what it reads (a Chat's current conversation; new after the quiet
  time) and puts the arriving lines on top. Core: when AAC last spoke (`{{ audio.last… }}`, per satellite
  `{{ audio.satellites[id]… }}`); Conditional has a template test.
  Read ports are triangles (alpha.25): a read input takes only a read output; the Chat provides its current context
  at `provides`.
- **Panel (alpha.28–29):** the palette shows a card type's Takes / Outputs (ports_help); Delete removes the selected
  card or wire (no question, Undo brings it back); Values button: the value pool (`aac/values`), live, with each
  card's descriptions. Pause cut is titled Audio clipper (type `pause_cut` kept).
- **Settings (alpha.30):** sidebar bottom: Nodes, then SETTINGS (Audio, Models, Appearance). Appearance: skins
  (Home Assistant = base, Neon built in, own CSS in `<config>/aac/skins`, served at /aac_skins). Ghost of the picked
  card type in the grid; Esc / Cancel / clicking the type again puts it back; a click on empty grid lets go of the
  selection. Builder is titled Node editor (type `builder`).
- **Cards bring their pages (alpha.31, owner: "every card should show its own UI, chat as well"):** `ui = True` +
  `<stem>.js` next to the card file (spec/cards.md "A card's page"); `nav()` = its sidebar entries (chats, lists as
  sub-entries, folding), `ui_call()` = its page's requests (`aac/card/ui`); `aac/nav` builds the sidebar. Pages:
  chat.js, lists.js (one list at a time, or the lists to pick), calendar.js (v1's week grid / table / month / agenda,
  plus HA calendars from the card's settings), memory.js, tools.js (File), auto.js (Schedule/HA event/Heartbeat log).
  The panel no longer knows any card type; `aac/chat/*` commands are gone except `aac/chat/request`.
- **Languages and voices (alpha.32–36):** the core's `languages()` (languages.py: from HA's language + country until
  set on the Audio page; `aac/languages/set`); Speech to text uses them when its own language is empty (one: that one;
  several: detected, heard again in the main one when the model heard another); `{{ languages }}`. The voice per
  language moved from the core into Voice out (settings `engine`, `voices`; functions `voices`, `set_voice`); the Audio
  page keeps the home languages, the default engine and the volume. The owner's Danny (en_US) / Thorsten low (de) are
  in the Text to speech card.
- **Output like input (alpha.37, owner):** Voice out is gone: Text to speech (context in, clip out: voices per
  language, functions `voices` / `set_voice`; a stop goes on as a stop clip) → Speaker (input `audio|clip`, drawn half
  green half purple: plays clips or a live stream in 2 s pieces at the clip's satellite or a picked satellite / HA
  media player via `core.audio.clip_url()`). An input may take several kinds ("audio|clip"). Microphone and Speaker
  are ordinary cards on the core's interface (the `audio` channel, `core.audio.play/stop`). Wires flash white when
  something passes (clips too).
- **Sidebar folding (alpha.33):** per group (each card with entries, and Settings) in Appearance: "opens while in use"
  (lit, closes when you go elsewhere; default) or "arrow keeps it open or closed" (default for Chat); `aac/appearance`
  `folds`.
- **Voice chain (alpha.16):** Speech to text → three Conditionals side by side: Stop word (sends stop forward), Wake
  word ("computer"/"assistent" → Spoken turn), Follow-up (AAC spoke there < 10 s ago → Follow-up turn: a note "maybe not
  meant for you, then answer nothing"); both Relays read the Chat dotted and go to the Charter; Model → Answer (Relay) →
  Chat and Voice out. The Voice chat card and Voice out's status port are gone; voice chats moved into the Chat card.
  Ports have descriptions (folded "Ports" section); rules, functions, values, tags fold.
- **Satellites are HA media players (alpha.17):** `media_player.aac_satellite_<name>` per known satellite (play any
  media / TTS, volume = AAC's volume, stop, browse); Voice out speaks at the line's satellite or on any media player
  (HA's tts.speak) and a stop cuts it off (the add-on 0.1.2 understands `{"type": "stop"}`; protocol in
  spec/audio-source.md). Live test: TTS on the satellite, HA's stop cut it.
- **Words (alpha.17):** what runs through the wires is a **context** (`context.py`, class `Context`), in code, panel
  and docs/v2.
- **No dead weight (alpha.18, owner rule):** the v1 import and every one-time upgrade step are gone (they ran on the
  owner's HA; a fresh install builds the whole default domain: Chat, HA Assist, Charter, Time, Pool, Model, Lists, Home,
  Calendar; the voice chain once satellites are known). An empty Pool means every model. Offline satellites can be
  forgotten on the Audio page (their media player goes too). Removed on the HA: the test satellite, conversation.aac_test,
  calendar.aac_test_calendar, v1's AAC device.
- **Models (alpha.20, owner 2026-10-24):** no Pool card and no pool wires (they added nothing). Models carry tags
  (text, tools, images, audio in/out, video, embeddings, speech to text, sound tags, voice prints; guessed from the
  name until set on the Models page) and "runs at once" (default 1). Each card that uses models (Model, Decide,
  Memory, Speech to text) has its own list of {model, Queue} offering only models with the tag it needs; empty =
  every model with the tag, queued. Down the list: a free model takes the context, a busy one queues it if ticked and
  the next is tried, the first to get free takes it (it leaves the other queues, `ModelPool.slot`/`run`); none
  ticked: a red warning, and a context is dropped when all are busy. The card shows its first model in the grid.
  Speech to text: its models, then HA's engines from the Audio page. On the HA: Model 35B → qwen3-8b, Memory card
  (bge-m3 on CT 110, a tool of the Model), Speech to text Whisper.
- **Card files (alpha.24, owner 2026-10-24):** every card type is a file in `<config>/aac/cards/`, loaded from there;
  AAC's own card files are copied there at each start unless a newer VERSION of the file is there (a download later).
  Cards import only `custom_components.aac.api`; a saved card remembers its version (`upgrade(settings, made_with)`
  for newer ones); the panel shows card versions. Contract for card authors (people and models): spec/cards.md.
- **Interchangeable cards (alpha.22, owner design rule, docs/v2 rule 10):** the core never checks a card's type.
  Incoming: channels a card subscribes to (`core.subscribe(channel, card, handler)`: `audio` → Microphone,
  `ha_conversation` → a Chat with "Takes Home Assistant's conversations", `ha_calendar` → Calendar); outgoing: core
  functions. The HA Assist card is gone: `conversation.aac` hands each turn to the channel (a chat "Assist · where ·
  time" in the Chat; tested live through HA's conversation API). Calendar reads any card offering `dated()`; the
  panel's chat commands take any card with a chat page.
- **Phone (alpha.21):** ☰ in the top bar opens AAC's sidebar (picking a page or tapping beside it closes it); ⌂ in
  the sidebar opens Home Assistant's menu (back to the overview).
- **Grid (alpha.20):** a small legend lower right (context, read, sound, speech); read inputs are triangles.
- **One-time steps that ran on the owner's HA (code removed):** v1's 05:25 trigger → Schedule card; v1's voice settings; the voice chain
  (Wake word = a Conditional: "computer" or a follow-up; Voice rules = a Relay after the turn); an HA Assist card.
- **First start** (`defaults.py`): the default domain (Chat → Charter → Time → Model → Chat; Pool "Models"; Lists and
  Home as the model's tools), taking over v1's models, master order, charter, lists and entity access.
- **Panel** (`www/aac.js`): sidebar (domain switch + clone, a section per Chat card with its chats, per Lists card its
  lists, Nodes, Models), chat (live answer, document view, stop), lists page, models page (find a server's models),
  node view (types left with info, click in the grid to place, drag to wire, Delete/button to unwire, settings right
  with functions and tags, Undo, slots 1–3 (Shift-click loads), Send stop, monitor on a wire).
- Tests: 35, each run fresh and from the backup (`../.venv/bin/python -m pytest -q -p no:logging`).

## Versions (owner, 2026-10-24)

Z.Y.X: X = changes the backup survives (fixes and new features alike), Y = a backup no longer loads, Z = a rebuild.
`tests/test_backups.py` decides X or Y (the backup of the current Z.Y must load). Card files have their own VERSION
(and `upgrade()` for their saved settings). Bump the manifest version with every deploy (the browser caches the panel
by it). 2.2.0 and 2.3.0 were misnamed (minor instead of X): they are 2.1.1 and 2.1.2; from 2.1.3 on as above.

- 2.1.29: "keep listening" made AAC answer its own voice (Speex leaves too much: an endless thanking loop): speech
  that began while AAC spoke there is marked (`aac_spoke`, clipper → Speech to text → joiner); the Follow-up ignores it,
  the Stop word (owner: wired straight behind Speech to text) still gets it.
- 2.1.28 + satellite 0.2.1: muting while AAC speaks moved into the Microphone card ("While AAC speaks there: mute /
  keep listening"); the core passes audio on marked `speaking`; the satellite always streams. Satellite option
  `echo_cancel`: PulseAudio's module-echo-cancel (WebRTC where available; HA's audio plugin has only Speex: on on the
  owner's HA). Not yet tested by voice (a stop word while AAC speaks needs the Microphone set to keep listening).
- 2.1.27: Redo (Nodes header; `aac/redo`; brings back what Undo took until the next change, not kept over a
  restart); the Buffer has a `provides` read output (one entry per line, stable ids).
- 2.1.26: Undo broke the owner's setup: while it made the cards one by one, the Audio clipper (phrase pause) did not
  find its joiner among the cards made so far, added a second one, and the loop over the cards broke ("dictionary
  changed size"); its rewiring dropped every wire to cards not made yet. Fixed: the clipper reads the setup (not the
  running cards), and loops over cards work on a copy. Repaired from the undo history (26 cards, 40 wires, positions
  moved back), the Follow-up rule set again; one backup: `/config/aac/bak-v2-2.1.26/`.
- 2.1.25: the Follow-up counts from where you started talking: (seconds since AAC stopped) − (how long the line
  was) < 6 s, so long answers stay in (owner: answers over 20 s fell out of the window).
- 2.1.24: the cards folder is looked at every 10 s.
- 2.1.23: point 8: card files load again while AAC runs when their VERSION goes up (or they are new; deleted ones
  leave the palette); running cards are made again when not `busy` (Model, Text to speech); card files are compiled
  fresh (no cached bytecode). The watcher is a plain task (HA's background tasks are awaited in tests).
- 2.1.21: points 12 and 13: AAC's own dialogs (`dialog` / `confirm` / `prompt` / `alert`, also for card pages;
  no browser dialogs left); chat archive: sidebar entries can carry actions (icon at once, the rest in ⋯; `nav_action`),
  the Chat's are delete (no question), archive, rename; "Archive (n)" page with restore / delete; archived chats are
  never current, models see them marked but cannot read or continue them.
- 2.1.18: point 5 done: every card with functions has "Instructions for models" (fold "For models"); a model
  reads them under "About your tools". The Node editor's list of cards counts long settings (an access list had cut
  the 8000-character result: the model said "6 cards"), `card` gives one in full. Voice rule: "call the tool first,
  then answer" (the voice turns had promised "I'll check" without calling). Owner raised the pause to 1.5 s.
- 2.1.17 (owner): a model that does not answer is flagged until it answers again (`pool.failing`, event
  "models"): a red line above the chat's input, SETTINGS blinks red, Models gets a red !, the Models page says so.
  The PC's NVIDIA driver refused a second process after Whisper's crash loop (OOM: the 35B's --fit took the card;
  now `LLAMA_ARG_FIT_TARGET=2048` in llm.env keeps 2 GB free); needs a PC restart, then start aac-whisper.
- 2.1.16: the Log card keeps clips as audio (config/aac/log/<domain>.<card>/, newest N) and plays them on its
  page (▶). The PC's Whisper (aac-whisper) had been off since 2026-10-22 20:09: speech to text fell back to HA's
  faster_whisper on the HA machine (rough). Started again; the 35B is Qwen3.6-35B-A3B again (owner: Ornith is worse;
  `~/.config/aac/llm.env` MODEL).
- 2.1.15: the Log card (Data): one white input of the new kind `any` (takes every kind; core and panel); writes
  down contexts (newest entry, from where), snapshots, clips, audio start / stop, and what is new on a dotted wire
  (looks every 2 s); its page shows the log (Clear, entries kept).
- 2.1.14: access lists (owner): a setting of type `access` shows the card's own entries (`ui_call("access_items")`,
  Home: every HA entity with name and area) with hidden / read-only / full per row, a search, and Hide / Read-only /
  Full all (shown); anything not set is hidden (the Home card's "everything else" level is gone, `upgrade()` drops
  it). The Permissions page folds its cards, one open at a time, each with a short summary.
- 2.1.12: the domain menu (owner, as in v1): switch, New domain (empty), Copy, Rename, Permissions, Save to a file,
  Load a file, Delete; a card setting marked `permission` shows on the domain's Permissions page (Home: access,
  Node editor: may change / remove, every card with functions: offers to models). Save/Load file left the Nodes header.
- 2.1.11: the clipper reuses a joiner already behind Speech to text (after the port rename it had added a second;
  cleaned up on the HA).
- 2.1.10 (owner's idea): the Audio clipper's second output is `cuts`: small contexts with one signal entry tagged
  start (data: ago) / phrase (part) / end (parts), usable by any card; the Phrase joiner acts on end; the Level card
  has `in` (audio, the graph) and `cuts` (text, the lines: green start, purple phrase, red end).
- 2.1.9: the Level card has its own `ends` input (and output) for the sentence signals.
- 2.1.8: the Level view moves with the clock (it stood still without audio coming in).
- 2.1.7: the Level card takes audio and clips (input `audio|clip`, clips go on at `clips`) and draws a line where
  clips begin (dashed) and end: whole clips green, phrases purple, a sentence's end red; settings: height (0 = in the
  port row), seconds shown, points per second. A card can set its own view size (`self.view` per card).
- 2.1.6: a setting can say `min` / `off` and `confirm` (asked before it is first turned on); the phrase pause asks
  first, needs at least 100 ms (the owner's 1 ms rounded to off), and the joiner makes room (cards right of Speech to
  text move one column over).
- 2.1.5: phrases (owner's design): the Audio clipper's "Phrase pause" sends each phrase at a short pause inside
  the speech (tagged with its sentence and number) and says at its new output `ends` when the sentence is done;
  Speech to text stays as it is (passes the tags on); the new Phrase joiner puts the sentence together. Setting a
  phrase pause adds the joiner after Speech to text and wires it. Measured before: Whisper ~0.8 s after the 0.8 s
  pause, model's first sentence ~1 s, Piper ~0.3 s.
- 2.1.4: timing of spoken turns (`core.audio.mark`; speech ended → text ready → first sentence → voice ready →
  playing), shown on the Audio page. Owner: tapping the mic shows in a Level card near instantly (the way in is fast).
- 2.1.3: the version numbers follow Z.Y.X.
- 2.1.2 (deployed as 2.3.0): the Level card (loudness as a small graph moving right to left; an inline view in its port row, so it
  keeps the size of any card).
- 2.1.1 (deployed as 2.2.0): card folders, cards with a view (Scope), live wires 10×/s only while Nodes is open.
- 2.1.0: a kept-alive connection the server had closed is tried once more (a model was taken for off after every tool
  call that came quickly); errors ("Error: no model was available.") are their own entries: shown and spoken, never
  read by a model (it had copied the old message as its answer); Chat search ignores punctuation ("13.13" finds 13:13).

## Owner's data

- The lists (Open Issues, Project Portfolio) live in the default domain's Lists card.
- HA's own Assist pipeline uses `conversation.qwen3_8b` (not AAC).
- The one backup of v2 storage: `/config/aac/bak-v2-2.1.35/` (before servers became records). v1's storage and the
  old backups are gone (owner ran the cleanup, 2026-10-24).
- Docs cleanup (2026-10-24): v0/v1 docs deleted (history, v1 progress, the old framework and UI design, the lists
  export, the Calisto handoff: built into the satellite's plugin), v1 contracts removed from spec/ (tag `v1-final`).
  One backup of docs/ and spec/ before that: `~/cluade/AAC/docs-backup-2026-10-24.tar.gz`.

## Deploy and test

- `AAC_HA_SSH=root@192.168.1.107 AAC_SSH_KEY=~/.ssh/claude_proxmox scripts/deploy_ha.sh` (bump the manifest version for
  every panel change). Local test HA: `~/cluade/AAC/dev-ha` (port 8124), runs from the repo.
- Screenshots: `scripts/ha_screenshot.py` (FIND_PANEL is the `aac-panel` element; `p.go('nodes')`, `p.go('lists','lists')`).

## Next (owner's list, 2026-10-24; talk each through first, then build, one at a time)

1. ~~A model that talks while it works~~ **done (alpha.38):** what a model says before its tool calls stays; the Model
   sends a snapshot after every finished sentence and tool result (`Step.progress`, `Card.progress()`: passes on by
   default; Chat holds the turn once something is finished; Text to speech speaks each finished sentence, in order);
   the Speaker queues clips per satellite (`core.audio.play(queue=True)`, a stop drops the queue).
- ~~Tools in the loop~~ **done (alpha.39, owner's call):** a Model's (and a Conditional's) tools are all cards with
  functions in its wiring loop (straight back, or through Chat and Relays); each card has the tick "Offers its
  functions to models in its loop" (Relays: off by default); the Model card lists its tools. Chat got `chats`
  (search), `continue` and `read` of any chat. `continue` (alpha.40, owner): when the turn is done, the other chat
  is the current one; the asking turn and the switch are not kept (a chat that was only that turn goes); the panel
  opens the other chat.
2. ~~Cards with a view~~ **done (2.1.1):** `view = {"height": …}` + `view.js` in the card's folder; `self.show(data)`
   sends frames only while a Nodes page shows the card (`aac/view/watch`, said every 8 s). First one: Scope (Audio:
   level bars, waveform, spectrum with numpy; passes the audio on). Next: Gain, Compressor.
3. ~~Faster lights~~ **done (2.1.1):** wire levels 10×/s, only while a Nodes page is open.
4. ~~**Audio delay**~~ **done (owner, 2026-10-31: 2.1.4 timing, 2.1.5 phrases, 2.1.28 muting in the Microphone, 2.1.32 the spoken turn at once, 2.1.44–2.1.48 echo).** (2.1.32: a spoken turn shows in the chat at once, the answer grows live there; a turn answered "nothing" goes again): measure each stage first (clip end → text → model → speech → playing), then: the satellite
   plays a stream instead of whole WAVs; sentence-wise speech (item 1); the Audio clipper's pause. Found: the
   Microphone → Speaker loop sounded broken because AAC ignores a satellite's microphone while it plays (no talking
   to itself) and the Speaker plays a stream in separate 2 s pieces; needs echo cancellation (best in the satellite)
   and stream playback. Owner: muting while playing becomes a Microphone setting (a stop word must work while AAC
   speaks); look at echo cancellation (the speaker's own sound filtered out of the microphone: WebRTC AEC in the
   satellite; the Poly may do it in hardware); Bluetooth may add delay.
5. ~~Editable instructions for models~~ **done (2.1.18).**
6. ~~Relay and Conditional UI~~ **done (2.2.0): Relay scripts** (see Current state). (2.1.40, owner: the Nodes page's side panels fold away: arrows at the grid's edges, a click on a card opens the settings; on a phone they lie over the grid, Back closes the right one, then the left, then goes to the last opened chat.)
7. ~~Card folders~~ **done (2.1.1):** `chat/card.py` + `page.js` (+ `view.js`); old flat files go when AAC starts.
8. ~~Card files reload while AAC runs~~ **done (2.1.23).**
9. **Tool menus (v0's idea)** (owner; 2026-11-02: waits, tools seem no big issue with Qwen3.8 27B / Gemma 4 26B; the
   owner tries a small model first): instead of every function at once, a model sees one entry per card and opens it
   to get that card's functions; fewer tools per turn, maybe better for weaker models (the 35B still makes many tool
   mistakes). v0.40.0 did it as areas first, then `open_area` + `use_tool`, the tool list staying fixed so the prompt
   cache holds. Try it as a Model setting and compare.
10. ~~Backups checked by tests~~ **done (2.1.0):** tests/backups/ keeps one backup (made-up data), from the first
    release of the current Z.Y; `tests/test_backups.py` loads it and runs a turn. Passes: raise X. Fails: raise Y,
    then `AAC_SAVE_BACKUP=1 pytest tests/test_backups.py`
    replaces the backup. Every test also runs twice by itself (conftest `start`): on a fresh install and on one that
    starts from that backup (dated as an old install); `@pytest.mark.fresh` for the few that only make sense fresh.
11. ~~**Edit messages in a chat**~~ **done (2.3.7):** a pencil on what you said and on answers (on hover; always faint on a phone); edit in place, ✓ (Enter) sends, Esc cancels; your line asks Resend / Keep the rest / Cancel (`edit` in the Chat card: Resend drops what came after and runs the turn again, its attachments come again); an answer only changes. Edited lines carry `data.edited`. (owner, as in v1, often used to resend; raised again 2026-10-29: "the pencil edit is missing"): a pencil under a message (yours or the
    model's), visible when hovering the message or near the pencil. Editing keeps the message where it is on screen,
    the chat moves up and the box grows upwards; send is a checkmark (grey, green on hover), so resending is a double
    click. Editing your line runs the turn again from there; first a warning: Resend (what came after it goes) / Keep
    the rest (only the text changes) / Cancel. Editing an answer only changes the text.
12. ~~Own dialogs~~ **done (2.1.21).**
13. ~~Archive for chats~~ **done (2.1.21).**
14. ~~Switchable models~~ (owner, 2026-10-25; as planned in v1) **done (2.1.31–2.1.39), PC and CT 110:** Models page → Servers (each server behind the pool: a router lists its models with state, quant, size, context, tools, tokens/s; Load, Unload, Use for <AAC model>, Delete, Download user/repo:quant with vision/mtp; live every 3 s), card *Model servers* (`servers`, `use`, `load`, `unload`), `Client.router()/router_do()`, `pool.offers()/find()`. CT 110 runs llama-server as a router (`/etc/llama-models.ini`, `--models-max 1`, fit 2 GB margin, downloads in /opt/models/cache; preset `qwen3-8b` = Gemma 4 12B for HA's GLaDOS pipeline). PC: aac-llm is a router too (`~/.config/aac/llm-models.ini`, default Gemma 4 26B-A4B; AAC model "Main PC"). Download progress comes from the router's `/models/sse`. 2.1.34: **Bench** button on a loaded model (reads ~1,500 tokens without cache, writes 256; kept per server and model in core data `benches`, shown under it; also the card's `bench`), an AAC model can **follow the server** (asks for whatever the router has loaded; both routers' models do), and reading a model's numbers no longer loads it again (`autoload=false`). 2.1.36: **servers are records** (core `servers`: name, address, key; every model has `server`, its address and key copied from it; a model saved with only an address gets the server there, so older backups load); the Models page shows only the models on top, then **+ Add a server** and one card per server: its models with a *loaded* tick, Bench, Delete (greyed out for preset models), AAC's models on it with their settings (Edit, Remove, **+ model**), Download. RAM/VRAM: llama-server's API has no memory figures (would need a helper on each machine). Real HA backup before this: `/config/aac/bak-v2-2.1.35/`. The idea: AAC asks each llama-server which models it offers
    and shows them; the owner picks one on the fly (Models page) or a card does it (a function: load / unload, so a
    model or a Conditional can switch too). That way the owner tests models on their own. Even better: AAC downloads
    a model (a Hugging Face repo + file) to the machine. Base: llama-server's router mode (`--models-dir ~/models
    --models-max 1`; `--models-max 1` unloads before it loads, so never two models on one card). On both machines:
    the PC (`aac-llm`, `~/.config/aac/llm.env`) and CT 110 (the RX 7800 XT). Needs per model its own settings
    (context, `-ub`) and a VRAM check before loading: on the 7800 XT a model without 1–2 GB of headroom froze the host
    three times (2026-10-25, see the CT 110 notes). To check: whether the router can fetch from Hugging Face itself
    (`-hf`), else a small helper on the machine. Later AAC decides what to load (the broker idea from v1).
15. ~~**Smaller undo history**~~ **done (2.3.30).** (review 2026-10-30: the undo steps are 462 KB of the 712 KB domain store): the domain's store is ~720 KB because every undo step and slot is a whole copy of the
    setup (the Home card's access list alone is big), and it is written on every change. Keep changes instead of
    copies, or leave big settings out of the undo steps. Also: a removed card's data stays stored (for Undo); stores of
    cards that are long gone should go (owner's no-dead-weight rule).
16. ~~Echo subtraction~~ **done (2.1.44–2.1.48)** (owner: mic and speaker in one housing): satellite option `reference`
    (add-on 0.3.0, `--reference`) sends what its speaker plays (PulseAudio monitor) as a 2nd channel; the Microphone's
    fold *Echo*: "Subtract the speaker of" (this satellite or another), delay (to 1 s, in samples), gain, inverted, and
    the **Waveforms** view (microphone + shifted speaker on one lane, the result below; zoom/drag/pinch; test sound =
    three 1 kHz beeps + a 100–500 Hz sweep, waits until played; last 5 s; line up automatically; save). Test 2026-10-25
    on the Plantronics: ~250 ms delay (Bluetooth); with the add-on's Speex `echo_cancel` the microphone only gets a
    processed residue (the sweep gone, beeps ringing; no linear relation), so subtraction can't help there and isn't
    needed: Speex already stops AAC hearing itself. The add-on runs `echo_cancel` on, `reference` off. For devices that
    give the raw echo (a cheap USB speakerphone on a Pi).
17. ~~**Ideas, not started:**~~ **settled (2026-10-31):** RAM/VRAM needs a helper on the machines (owner: no, maybe a card some day, see 56); the echo reference is not needed while the satellite's Speex echo cancellation keeps AAC from hearing itself (16). Was: AAC's own output as the echo reference, lined up per answer by correlation (any satellite,
    and another room's speaker); RAM/VRAM on the Models page (llama-server's API has none: a helper on each machine).
18. ~~**Chat rework**~~ **done as far as known (2.3.0: items 22–27, Chat + Behind the scenes; 2.3.7: item 11).** (owner, 2026-10-26: "needs to be reworked a little"; what exactly: talk through first). Related
    and still open: item 11 (edit messages, resend).
19. **Measures against code injection** (owner, 2026-10-26). Since 2.2.0 Relay scripts are code a model can write
    (the Node editor's `change_card`), and models read untrusted text (web pages, mails, chat lines), so a planted
    instruction could make a model write a script. In place now: `ast` check (no imports / while / `_` names /
    `.format`), no builtins beyond a list, card calls only to cards in the Relay's loop, a script runs at most 20 s.
    To talk through: scripts changed by a model need the owner's OK (or a Node editor setting "may change scripts",
    off by default); a mark and a diff in the panel for scripts a model changed; limits on size and loops (a cap on
    loop steps / string length); Relays that may not call certain cards (Home Assistant, Notify); a review of the
    sandbox (str/regex blow-ups, what `e.data` and card values can reach).
20. **Permission rework, as in v0/v1** (owner, 2026-10-26: "this time it needs to be actually like it is in V0 and
    V1"). Two layers: **first the domain** allows or disallows privileges at all: individual Home Assistant entities
    (invisible / read / control), the web, and the other outside things (notify, files, servers …); what the domain
    does not allow does not exist for any of its cards. **Then each card** asks for what it needs within that, and the
    owner controls it per card separately. Today (2.x) it is only per card: the Home Assistant card's own access list
    (hidden / read-only / full) and the `permission` ticks gathered on the Permissions page; no domain layer, no asking.
    v1 for reference (tag `v1-final`, `spec/requests.md`): what a domain can see (`visible`, tick list per room /
    integration, new domains see nothing) × read / write per entity, the effective level where both meet; requests
    (`request_access(entity_ids, level, reason)`, `request_visibility`) wait outside the chat (panel badge + HA
    notification) until the owner allows all, some or none; locks, alarms, covers, valves, sirens only with the owner
    in the chat; HA's "expose to Assist" as the starting point. Ties in with 19 (what a script may call). Talk through
    first. (2026-10-31, owner) The per-card layer is also where a card's permission to read AAC's log goes (38); the
    domain layer lists permissions per accessible thing and decides whether cards can even see that a thing exists
    before they can ask for it. Needs a general look: a Home Assistant card cannot know the entities it should ask
    for (today the owner fills its access list beforehand and a model finds only what is not hidden).
    Owner (2026-10-31): the domain layer comes first, on top and separate from the cards: per thing **list** (cards may
    know it exists), **read** or **full**. A domain loaded from the internet with its own cards and wires must be under
    precise control: what it can see and do.
21. **Card sandboxing** (owner, 2026-10-26). Today a card file in `<config>/aac/cards/` is plain Python run inside
    Home Assistant's process (`cards/__init__.py` `_load`: `exec`): it can do anything HA can (files, network, every
    entity, other cards' data), and "only uses `api`" is a convention, not enforced. Fine for AAC's own cards, not for
    downloaded or model-made ones. v1's plan (`spec/module.md`): code modules never active without the owner, run
    isolated, only what their declared `permissions` allow. Options to talk through: cards declare what they need
    (entities, web, notify, files …) and get only those core services, checked against 20's domain and per-card
    grants; untrusted cards in a separate process (a subprocess or the add-on, talking to the core over a narrow
    API) instead of `exec` in HA; signed / reviewed AAC cards vs. unchecked ones marked in the palette; a new or
    changed card file waits for the owner's OK before it loads. Ties in with 19 and 20.
    **Owner's direction (2026-10-26):** cards already reach everything through the api (needed so cards stay unified
    and interchangeable, nothing hardwired); so wall them in and expose only those interfaces. Note: inside HA's
    Python process this cannot be enforced (a card can always `import os`, reach `hass`, or walk objects to the
    core); a real wall means running cards outside it (a worker process per card or per domain) with the api as the
    only channel (calls and events over a pipe; `self.core` / `self.domain` become proxies). Costs to weigh: every
    call crosses the process boundary (audio streams! maybe audio cards stay inside, trusted), data copied not shared,
    a card's page / view files unchanged. AAC's own cards could run the same way, so the api is the real contract.
- Presets; docs/v2 page 2 details for the remaining cards.

Items 22–46: the owner's open issues, moved here on 2026-10-30 from the AAC list "Open Issues in AAC" (written
2026-10-20 to 10-29; the owner's words, spelling fixed). "Tools as one file" from that list is done (2.2.1); "the
pencil edit is missing" is item 11.

22. ~~**Chat: messages shown twice.**~~ **fixed (2.3.0).** Messages are shown twice even if the model answers once.
23. ~~**Chat: old message in a new chat.**~~ **fixed (2.3.0).** When opening a new chat, a message of the last chat is still visible.
24. ~~**Chat: Chat / Document / Request unclear.**~~ **done (2.3.0): Chat + Behind the scenes.** Where the differences between Chat, Document and Request are is not
    clear; Document feels like a lesser Request.
25. ~~**Chat: switching views loses the chat.**~~ **fixed (2.3.0).** Switching between Chat, Document, Request … loses the chat in between.
26. ~~**Chat: tool usage as in v1.**~~ **done (2.3.0).** Tool calls as small, less prominent lines that unfold to a still compressed
    version of what the model called and got back ("you might literally take the v1 Chat"). Ties in with 18.
27. ~~**Models: tools more natural.**~~ **done (2.3.0): For models, tools without ids.** Check whether the way models use tools can be made more "natural" to them: a
    model without a tool still often says "yes, I'll do x, y" and then quietly does nothing. Owner's notes: (a) "For
    models" was meant to be the instructions, so the owner can change them for specific needs; the default should be
    in there and resettable when messed up. (b) Asked for the time, the model said 11:41; told to use the Time card
    (wired as its tool, out to in and back) it could, so it was not aware of the card: fix that if possible. (c)
    `card: name = "ID"`? (d) The model called `time__text({})` to get the Time card to append the time, then answered
    with it: correct, but a cumbersome way to call it.
28. ~~**Models page: model order and unavailable servers.**~~ **dropped (owner, 2026-10-31: set in the Model card).** A clearer way to set the order in which models are used,
    and a tick that it is no issue when a compute resource is not available.
29. ~~**Models page: top list and parameter counts.**~~ **done (2.3.31).** The top list is not really clear; models in the server lists
    should show their parameter count if llama.cpp gives it.
30. ~~**Models page: last spoken to per server.**~~ **done (2.3.27).** A "last spoken to" per compute server, so it is visible when RAM
    overfills and a server stops answering.
31. ~~**Models page: Delete misaligned.**~~ **done (2.3.26):** rows with lines, the hovered row marked. The Delete per model does not seem to line up with the actual models.
32. ~~**Models page: deleted downloads go on.**~~ **fixed (2.3.26, the owner confirmed it on 2026-10-31):** a poll without a live event showed 0 %. A stopped and deleted download seems to go on: the next model's
    progress jumps between 0 % and ~12 %. Happens with every model (first seen after stopping a base Gemma 3 31B
    download for a quantised one).
33. ~~**Nodes page: lit wires wrong.**~~ **fixed (2.3.0, the owner confirmed it on 2026-10-31).** (2.3.0: tool calls travel along the wires now, snapshots no longer go through Schedule; check again live) The lit wires do not seem right: a question that makes Gemma search the web
    reliably ("lithium is used in many antidepressant medications, how does it work…") always lights the way to
    Schedule and Chat.
34. ~~**Nodes page: select and move several cards.**~~ **done (2.3.20).** With several cards selected, dragging one moves only that one, not
    all selected.
    Owner, 2026-10-30: box select (drag a frame on the empty grid), Ctrl+click to add or remove cards from the
    selection, and drag and drop the whole selection at once.
35. ~~**Nodes page: overlapping cards.**~~ **done (2.3.25).** Cards can be placed overlapping; they are only moved apart when a card near
    another is moved. The "too close" check must happen consistently.
36. ~~**Nodes page: snap while dragging.**~~ **done (2.3.25).** Snapping to columns only happens on release; while dragging, cards still
    move in the old raster (a leftover).
37. ~~**Naming: cards, not nodes.**~~ **done (2.3.27).** Cards are called cards or nodes interchangeably; they should just be called cards
    (with their interchangeability now).
38. ~~**AAC's own log**~~ **done (2.3.33).** (owner, 2026-10-31, widened from "logging per card" after a friend's advice: a proper logging
    mechanism in AAC itself, so it can be checked when something goes wrong). Agreed so far:
    - One log per domain, in the domain's data folder (`Core.folder(domain)`: moves on rename, goes on delete).
    - AAC's log is its own; HA's log only gets what belongs to the integration running on HA (start, stop, failing
      to load), not what happens inside AAC.
    - A rotating log (about 5,000 entries per domain; owner: a rotating log is not up for question). Whole
      contexts in entries age out after about 10 minutes (privacy, space); the entry's line stays.
    - Cards write through one call in the api; every card has the 38 settings (what: whole context / only changed
      text; when: in, out or both). Wire monitors read from it.
    - Settings → Logs shows it. Cards may read the log per permission (a per-card grant, see 20); the Log card puts
      entries into the log and reads it as a function and through an output.
39. ~~**Schedule: like v1.**~~ **done (2.3.35).** Schedule should work more like it did in v1 (talk through: what exactly). Review: its
    entries are settings, so every reminder a model adds or a one-time entry that fires makes an Undo step and remakes
    the card (reminders push the owner's undo steps out).
40. **File: several files.** The File card has only one file. Owner imagined accessing different files, but likes the
    single file too: maybe a File system card (with a whitelist of paths) next to the File card.
41. ~~**File: provides output.**~~ **done (2.3.27).** The File card should have a provides (read) output.
42. ~~**Home card: list jumps.**~~ **fixed (2.3.26).** Clicking something in the Home card makes the list jump.
43. ~~**Lists: undo a removal.**~~ **done (was there; 2.3.26 puts entries back in place).** Removing an entry cannot be undone; maybe show an Undo button (a function
    `undo_remove` exists: check the page).
44. ~~**Lists: edit entries.**~~ **done (was there: a click on the text; 2.3.26 says so on hover).** Entries cannot be edited (check against the current page).
45. ~~**Lists: sidebar folded open.**~~ **done (2.3.26).** The Lists entry in the sidebar should be folded open.
46. ~~**Settings: entities UI on the phone.**~~ **done (2.3.26, the owner confirmed it on 2026-10-31).** The entities UI in settings is broken on the phone.
47. **Node editor: draft mode** (owner idea, 2026-10-30; talk through first). A model plans a new card setup as a
    draft, puts it live to test it, and then decides whether it works: the Node editor keeps the context while the
    cards are rebuilt to the model's design, and expects the model to say "keep" within ~120 s, otherwise the old
    setup comes back. Maybe instead of the 3 save slots: the Node editor (and the Nodes page) with a panel of its own.
48. **Review: card editors out of the panel** (talk through first). The panel (core) has editors for specific cards:
    Schedule entries, HA event watches, the Home access list, Decide's outputs, voices, the speaker and satellite
    pickers, the Microphone's whole Waveforms view, and checks for the types microphone / model / chat. Against rule
    10 and "the core stays light"; each card should bring its editor from its folder (the `element` setting type, like
    the Relay's script editor).
49. **Review: the voice chain as a preset** (talk through first). `defaults.py` builds the voice chain (11 cards and
    their wires) by itself when a satellite first connects, picking cards by type. Against rules 1, 4 and 10; it
    should be a preset the owner applies (ties in with "Presets").
50. ~~**Review: the Request view goes through the card.**~~ **done (2.3.0): the Request view is gone.** The core's `aac/chat/request` reads `card.requests` inside the
    Model card (a hidden contract); it should be the card's own ui_call.
51. **Review: cards may not import each other** (owner, 2026-10-31: talk through first). The loader makes `aac_cards` a package so card files can import one
    another; that invites cards to depend on each other. Remove it (folders keep their own relative imports).
52. ~~**Review: removing a card asks about its data**~~ **done (2.3.30).** (page 1, rule 3). Today its data stays stored forever, silently.
    Ties in with 15 (stores of long-gone cards).
53. ~~**Review: signals besides stop.**~~ **done (2.3.0): events.** Page 1 says stop is the only signal, but Decide writes its choice and the Audio
    clipper its cuts as `signal` entries (the Level card reads the cuts). Either page 1 or the code changes.
54. ~~**The context as a model request**~~ **done (2.3.0).** (owner, 2026-10-30; design: docs/v2/03-the-context.md, waiting for the owner's
    OK). Containers system / messages (user, assistant, tool; attachments on user messages) / tools / events; cards
    put their output where a model expects it; "For models" per function (tick, name, description, Reset); the chat's
    Behind the scenes view. Breaks the backup (2.3.0). Closes 24, 26, 27, 50, 53 and the chat part of 18.
55. **Tools: one name, declared in Python** (owner, 2026-10-30, "just thinking, later"; talk through once the owner has
    looked at Behind the scenes). (a) A card should have only one name: the card's name is the name the model sees
    (today a function has its own tool name, e.g. the Time card's `text` renamed `get_time`). (b) Tools declared in the
    card in Python would fit better than the For models form (for a Relay: in its script).
56. ~~**Models page: context size per model**~~ **settled (2.3.31): shown, not set** (no helper on the machines; maybe a card some day). (owner, 2026-10-30). Show each model's context and let it be set with a
    slider up to what the GPU can hold; the default close to the maximum. To find out first: how a llama-server router
    loads a model with another context size (its presets set `--ctx-size`), and how to tell the GPU's maximum (VRAM,
    the model's own limit). The chat already shows the tokens used of the context (2.3.16).
57. ~~**Bug: on a phone, tapping Settings also presses Audio**~~ **fixed (2.3.19).** (owner, 2026-10-31). Tapping the sidebar's SETTINGS
    group opens it and the Audio entry, which then lies under the finger, gets pressed at once. To check: the group
    re-renders the nav on the first event of the tap and the rest of the tap (or a ghost click) lands on the new
    entry; fix so one tap does one thing, then try it on the real page on a phone.
58. ~~**Relay rework**~~ **done (2.4.0)** (owner, 2026-10-31; `docs/v2/04-relay.md`). The script becomes plain Python
    that runs top to bottom per arriving context (`context = input("in")`, `output("out", context)`); nothing leaves
    unless sent, the context reads as a list of tagged texts (`attach_first`, `attach_last`, `set`, `remove` …),
    `print` on the card, `print_log`. With it the Card editor's `help(type)` (any card's setup help, as it declares it;
    the Relay's script reference is its `script` setting's help).
59. **Card types: where they came from** (owner, 2026-10-31; talk through first). Card files sit in one shared
    `<config>/aac/cards/` for all domains, while domain data now has its own folder. Every card type should carry a
    tag saying what brought it in (shipped with AAC, downloaded, made by a model, the owner …) and when; a view that
    manages card types shows it. Ties in with 21 (unchecked cards) and 51.
    Owner, 2026-10-31: the Card editor will let a model look into cards, edit them and make new ones. Then (a) a card
    keeps its own version: the Card editor raises `VERSION` on every save, not the model (today a file is loaded again
    only when its VERSION goes up, so an edit without it never runs); (b) a version alone cannot tell "newer" from
    "changed": with the origin tag, a shipped card someone changed is never replaced by an AAC update without asking
    (today a shipped 2.3.0 would overwrite an edited 2.2.4), and a high number on an edit does not block AAC's fixes;
    (c) editing card code is riskier than a setting (it runs inside HA, item 21): looking is free, a model's code
    edit or new card waits for the owner's Allow (like Schedule's "ask").
60. **Calendar: what v1 had beyond 39** (2026-10-31; talk through first). Since 2.3.35 the core gathers the entries
    cards offer. Still missing against v1: Home Assistant's calendars (Google, CalDAV …) as an offer models can read and
    write (today only the Calendar card's page shows them, calling HA itself; ties in with 20); one AAC calendar per
    domain in HA (today one, the running domain's); moving or cancelling AAC's own Schedule entries from the calendar
    (read only there now); the next 24 hours for runs nobody asked for.
61. **Hardware: the Calisto blares white noise by itself** (owner, 2026-11-01; open, being caught). Twice now, left on
    overnight, the speaker suddenly played loud white noise, in bursts ("white noise, then cuts, then again"); this
    morning about 07:27, the owner switched it off within about 8 s. What is known:
    - **AAC played nothing**: the satellite's log has no "AAC: …" line all night (only the later sound tests).
    - **No command from the satellite before it**: the BT600 watchdog last sent off-hook at 05:50:38; the noise
      started 07:27:07 (the microphone's clip start), the watchdog only started again at 07:27:15 (switched off).
    - **Batmon (BMS monitor app) restarts every ~5.5 min, all day and night**: its Bluetooth stick (UGREEN BT6.0,
      33fa:0012, hci0) hangs (kernel: `hci0: command 0x2042 tx timeout`, since 2026-10-30 12:40; the owner has to
      replug it often), Batmon fails 200 times and its watchdog restarts it. Of this morning's 12 loud moments on
      the microphone, three fell within 2 s of a Batmon container exit (05:47:00 −2.1 s, 06:14:52 +0.5 s,
      07:27:07 −0.1 s); by chance about 1 in 80 each. The other nine did not line up.
    - **Both dongles sit side by side** on the PC's front USB 3.0 header, on one controller of the Proxmox host
      (bus 1, ports 5 and 6; passed to VM 100 as `usb0: host=33fa:0012`, `usb1: host=047f:0417`). On 2026-10-29
      19:20 the UGREEN's replug made the kernel reset the BT600 twice: trouble on one reaches the other. No USB
      errors on the Proxmox host or in HA at 07:27.
    - **Debug on (satellite 0.3.5, option `debug`)**: logs every loud stretch (≥ −40 dBFS for 0.3 s) on the raw
      microphone (before echo cancellation) or on the BT300C sink's monitor, with zero-crossing rate (white noise
      ≈ 0.5, speech ≈ 0.1) and whether AAC was playing, and saves it as a stereo WAV (left mic, right monitor) in
      `/share/aac_satellite_debug/`; PulseAudio's card/sink/source events; levels every 5 min; the BT600's HID
      reports. The Calisto cancels its own playback in hardware (a test sentence: monitor −26 dBFS, raw mic −66):
      a loud mic with a silent monitor means the noise did not come from this machine's software.
    - **When it happens again** (the owner says when): the satellite's log around that time (`ha apps logs
      local_aac_satellite`), the WAV, HA's host journal (`ha host logs`: Batmon's container exits, kernel), the
      Proxmox host's `journalctl -k` (`claude@192.168.1.10`).
    - **2026-11-01 09:52: the UGREEN reset in software** on the Proxmox host (`sudo usbreset 33fa:0012`, see
      ~/cluade/proxmox/docs/host-inventory.md): HA got it back, Batmon read the battery again (65 %), the BT600 was not
      touched. Batmon no longer restarts every 5.5 min: if the noise stops now, that points at it. Idea: the host
      resets the UGREEN by itself when HA's kernel logs `hci0 … tx timeout`.
    - **`debug` is off for now** (09:47): with it on, the microphone seemed to hear nothing and the link dropped
      often; to be ruled out before it goes on again (it records the raw microphone a second time).
    - **Ideas once caught**: move the BT600 to a rear port (another controller, away from the UGREEN); hang up the
      BT600 on noise. Afterwards set `debug` off again.
