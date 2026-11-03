"""Constants."""

import logging

DOMAIN = "aac"
LOGGER = logging.getLogger(__package__)
PANEL_URL = "/aac_static"
SIGNAL = f"{DOMAIN}_event"
DRAFT = "4"  # the slot a model builds in before it switches to it (slots 1-3 are the owner's)
