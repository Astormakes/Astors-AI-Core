"""AAC's public interface for cards (spec/cards.md; docs/v2 page 5). A card file, AAC's own or one in
`<config>/aac/cards/`, imports from here and nothing else of AAC:

    from custom_components.aac.api import Card, Context, register

What a card uses, and nothing else (so cards can run in a sandbox later):
- its context: `Context` (a list of entries), `entry`, `matches`, `new_id`, `SYSTEM`, `TOOLS`; the rules: `RULES`
- the flow, as methods of `Card`: `handle` (a context arrives), `send` (out at an output), `read` (a dotted input),
  `held` (what its dotted outputs give), `call` (its functions), `stop` (the core's stop reaches it), `stop_all`
  (stop every card), `tools`, `wired`, `wired_into`, `reaches`, `all_cards` (other cards, used only by what they
  offer: functions, values, held()), `stream_out` / `clip_out` (audio), `live` (text still growing), `show` (its view)
- its own data and files: `data`, `load`, `save`, `folder`; the log: `log`, `read_log` (log at read)
- outside AAC, only as far as its permissions reach (docs/v2 page 6: the lower of what the domain allows and its own
  May use, for the kinds in its `needs`): `level`, `may`, `need`, `act` (before every action: at ask it waits for the
  owner's yes); `ha` (`entities`, `state`, `call`, `watch`, `calendar_events`: plain data, awaited), `fetch` (the web),
  `notification`, `read_file`, `write_file`, `listen` (the satellites' microphones), `play`, `stop_playing` (their
  speakers); `request_access` is a function of every card with needs, answered by the core (`run`)
- time and tasks only through the core: `task`, `later`, `every_minute`, `blocking`, `loop`
- the core's services through `self.core` (plain data in and out: in the worker they answer over its channel):
  `models` (`chat`, `embed`, `template`), `subscribe(channel, card, handler)` (channels: "audio", "ha_conversation"),
  `calendar` (`offer`, `entries`, `add`, `change`, `remove`; `parse_when` below), `audio` (`say`, `play`, `stop`,
  `spoken`, `tts`, `stt`, `voices`, `mark`, `clip_url`), `notify`
- the setup, for cards that build (the Card editor), setup at read:
  `read_setup`; at full: `add_card`, `change_card` (its own settings always), `remove_card`, `set_wires`, `unblock`;
  a permission setting never (the owner's alone); the model servers: `servers` (read), `server` (full)
- open panels: `tell` (something of this card changed: its page, the sidebar)
"""

from .sound import SAMPLE_RATE, Segmenter, level, make_vad, pcm_to_wav, resample, wav_to_pcm
from .calendars import parse as parse_when
from .cards import CARD_TYPES, CATEGORIES, Card, register, tool_spec
from .context import SYSTEM, TOOLS, Context, entry, matches, new_id
from .const import MODEL_TAGS, ModelError
from .rules import RULES

__all__ = ["CARD_TYPES", "CATEGORIES", "MODEL_TAGS", "SAMPLE_RATE", "SYSTEM", "TOOLS", "Card", "Context", "ModelError",
           "Segmenter", "entry", "level", "make_vad", "matches", "new_id", "parse_when", "pcm_to_wav", "RULES", "register",
           "resample", "tool_spec", "wav_to_pcm"]
