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
  May use, for the kinds in its `needs`): `level`, `may`, `need`; `ha` (`entities`, `state`, `call`, `watch`,
  `calendar_events`), `web()`, `notification`, `files(write)`, `listen` (the satellites' microphones), `play`,
  `stop_playing` (their speakers)
- the core's services through `self.core`: `subscribe(channel, card, handler)` (channels: "audio",
  "ha_conversation"), `calendar` (`offer`, `entries`, `add`, `change`, `remove`; `parse_when` below), `pool` (models:
  `entries`, `slot`, `tags`, `find`, `offers`; with `run` and `Client` below), `save_model`, `audio` (`say`, `play`,
  `stop`, `spoken`, `tts`, `stt`), `notify`
- the setup, for cards that build (the Card editor, an Audio clipper adding its Phrase joiner): `self.domain`'s
  `add_card`, `update_card`, `remove_card`, `set_wires`, `wires`, `export`
"""

from .audio import SAMPLE_RATE, Segmenter, level, make_vad, pcm_to_wav, resample, wav_to_pcm
from .calendars import parse as parse_when
from .cards import CARD_TYPES, CATEGORIES, Card, register, tool_spec
from .context import SYSTEM, TOOLS, Context, entry, matches, new_id
from .models import MODEL_TAGS, Client, ModelError, ModelUnreachable, run
from .rules import RULES

__all__ = ["CARD_TYPES", "CATEGORIES", "MODEL_TAGS", "SAMPLE_RATE", "SYSTEM", "TOOLS", "Card", "Client", "Context",
           "ModelError", "ModelUnreachable", "Segmenter", "entry", "level", "make_vad", "matches", "new_id", "parse_when",
           "pcm_to_wav", "RULES", "register", "resample", "run", "tool_spec", "wav_to_pcm"]
