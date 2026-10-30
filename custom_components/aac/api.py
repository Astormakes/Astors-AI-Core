"""AAC's public interface for cards (spec/cards.md). A card file, AAC's own or one in `<config>/aac/cards/`, imports
from here and nothing else of AAC:

    from custom_components.aac.api import Card, Context, register

Through `self.core` a card reaches the core's services: `subscribe(channel, card, handler)` (channels: "audio",
"ha_conversation", "ha_calendar"), `pool` (models: `entries`, `slot`, `tags`, `find`, `offers`; with `run` and `Client` below), `save_model`,
`audio` (`say`, `play`, `stop`, `spoken`), `notify`. Through `self.domain` it reaches the flow (`flow.send`) and the
other cards only by what they offer (functions, values, `held()`). AAC's own cards use only this too.
"""

from .audio import SAMPLE_RATE, Segmenter, level, make_vad, pcm_to_wav, resample, wav_to_pcm
from .cards import CARD_TYPES, CATEGORIES, Card, register, tool_spec
from .const import LOGGER
from .context import EVENTS, Context, entry, is_stop, new_id
from .flow import Step
from .models import MODEL_TAGS, Client, ModelError, ModelUnreachable, run

__all__ = ["CARD_TYPES", "CATEGORIES", "EVENTS", "LOGGER", "MODEL_TAGS", "SAMPLE_RATE", "Card", "Client", "Context", "ModelError",
           "ModelUnreachable", "Segmenter", "Step", "entry", "is_stop", "level", "make_vad", "new_id", "pcm_to_wav", "register",
           "resample", "run", "wav_to_pcm",
           "tool_spec"]
