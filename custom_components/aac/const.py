"""Constants, and the errors every part of AAC knows (no Home Assistant here: the card worker imports it too)."""

import logging

DOMAIN = "aac"
LOGGER = logging.getLogger(__package__)
PANEL_URL = "/aac_static"
SIGNAL = f"{DOMAIN}_event"
DRAFT = "4"  # the slot a model builds in before it switches to it (slots 1-3 are the owner's)
MODEL_TAGS = ["text", "tools", "images", "audio in", "audio out", "video", "embeddings", "speech to text", "sound tags",
              "voice prints"]  # what a model can do; a card offers only the models with the tag it needs


class ModelError(Exception):
    """A model server failed."""


class ModelUnreachable(ModelError):
    """The machine does not answer (it may simply be off)."""
