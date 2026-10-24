# AAC – progress

Read this first. v1 (1.0.0-dev.75) is history: git tag `v1-final`.
The plan for v2 lives in `docs/v2/` (private): page 1 "What AAC is" (settled), page 2 "The cards" (in progress).

## Current state (2026-10-24) – 2.1.20, live on the owner's HA

Rebuilt from scratch on the owner's design (docs/v2): everything is a card instance in a domain, cards own their data,
the owner wires them (no automatic wiring), the message is a document of entries with `<card>.<tag>` tags.

- **Core** (`core.py`): the model pool (shared by all domains, `models.py`: OpenAI-compatible client, streaming, tool
  calls, a model that does not answer is skipped a minute); domains (one runs at a time; clone, switch); each domain
  keeps its cards, wires, positions, Undo (20 steps) and 3 save slots on the server; each card's data in its own store
  (`aac2.card.<domain>.<card>`).
- **Flow** (`flow.py`): round outputs copy, square ones choose; a card's tools are the cards wired out of it and back
  in (functions called directly, call + result written into the message); the newest message per output is kept for
  the monitor; `flow` events pulse the wires.
- **Cards** (`cards/`): Chat, Model, Pool, Relay (nothing / start / note / after, replace tick, add text to tagged
  entries), Conditional (if … then: edit text, tag, call a wired card's function, stop, pick an output), Buffer, Lists
  (with edit and undo of a removal), Home Assistant (own access list: hidden / read / full).
- **More cards (alpha.3):** Schedule, HA event, Heartbeat (start a chain, keep a log page; Schedule entries can be
  added by a model), HA Assist (AAC is HA's conversation agent again, entity `conversation.aac_qwen3_8b`), Decide,
  Memory (by meaning with an embedding model, else by words), File, Notify, Timer, Web (DuckDuckGo or SearXNG),
  Node editor (type builder: a model changes cards and wires, within its settings).
- **Audio (alpha.3, `audio.py` + `cards/audio.py`):** satellites on `/api/aac/audio` (same protocol: the add-on works
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
- The one backup of v2 storage: `/config/aac/bak-v2-alpha36/` (before Voice out was replaced). v1's storage and the
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
4. **Audio delay**: measure each stage first (clip end → text → model → speech → playing), then: the satellite
   plays a stream instead of whole WAVs; sentence-wise speech (item 1); the Audio clipper's pause. Found: the
   Microphone → Speaker loop sounded broken because AAC ignores a satellite's microphone while it plays (no talking
   to itself) and the Speaker plays a stream in separate 2 s pieces; needs echo cancellation (best in the satellite)
   and stream playback. Owner: muting while playing becomes a Microphone setting (a stop word must work while AAC
   speaks); look at echo cancellation (the speaker's own sound filtered out of the microphone: WebRTC AEC in the
   satellite; the Poly may do it in hardware); Bluetooth may add delay.
5. ~~Editable instructions for models~~ **done (2.1.18).**
6. **Relay and Conditional UI** (later).
7. ~~Card folders~~ **done (2.1.1):** `chat/card.py` + `page.js` (+ `view.js`); old flat files go when AAC starts.
8. **Card files reload while AAC runs** (owner): AAC notices a card file whose VERSION went up and loads it again, and
   remakes that type's cards when they are not busy (no restart). Changes to the core still need one.
9. **Tool menus (v0's idea)** (owner): instead of every function at once, a model sees one entry per card and opens it
   to get that card's functions; fewer tools per turn, maybe better for weaker models (the 35B still makes many tool
   mistakes). v0.40.0 did it as areas first, then `open_area` + `use_tool`, the tool list staying fixed so the prompt
   cache holds. Try it as a Model setting and compare.
10. ~~Backups checked by tests~~ **done (2.1.0):** tests/backups/ keeps one backup (made-up data), from the first
    release of the current Z.Y; `tests/test_backups.py` loads it and runs a turn. Passes: raise X. Fails: raise Y,
    then `AAC_SAVE_BACKUP=1 pytest tests/test_backups.py`
    replaces the backup. Every test also runs twice by itself (conftest `start`): on a fresh install and on one that
    starts from that backup (dated as an old install); `@pytest.mark.fresh` for the few that only make sense fresh.
11. **Edit messages in a chat** (owner, as in v1, often used to resend): a pencil under a message (yours or the
    model's), visible when hovering the message or near the pencil. Editing keeps the message where it is on screen,
    the chat moves up and the box grows upwards; send is a checkmark (grey, green on hover), so resending is a double
    click. Editing your line runs the turn again from there; first a warning: Resend (what came after it goes) / Keep
    the rest (only the text changes) / Cancel. Editing an answer only changes the text.
12. **Own popups and message boxes** (owner): AAC's own dialogs instead of the browser's confirm / prompt / alert.
13. **Archive for chats** (owner, as in v1): a chat can be archived; the sidebar shows a trashcan next to the "⋯" menu
    of each chat (deleting there asks nothing), archiving in the "⋯" menu. A model's `chats` still lists archived
    chats (marked), but `read` and `continue` refuse them.
14. **Switchable models on the PC** (owner, as planned in v1): llama-server's router mode (`--models-dir ~/models
    --models-max 1` instead of `-m …$MODEL` in `aac-llm`, the unit reads `~/.config/aac/llm.env`) offers every model in
    ~/models (now Ornith-1.5-35B and Qwen3.6-35B-A3B); AAC adds each as a model on the same address. A switch loads the
    other one (~15–30 s). Later AAC itself decides what to load (the broker idea from v1).
15. **Smaller undo history**: the domain's store is ~720 KB because every undo step and slot is a whole copy of the
    setup (the Home card's access list alone is big), and it is written on every change. Keep changes instead of
    copies, or leave big settings out of the undo steps. Also: a removed card's data stays stored (for Undo); stores of
    cards that are long gone should go (owner's no-dead-weight rule).
- Presets; docs/v2 page 2 details for the remaining cards.
