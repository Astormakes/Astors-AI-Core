"""Pool: which models a Model card may use (from the core's model pool), in the owner's order; the next one when
one does not answer. Wire its dotted output into a Model's pool input."""

from __future__ import annotations

from . import Card, register


@register
class Pool(Card):
    """A choice of models."""

    type = "pool"
    title = "Pool"
    category = "Models"
    help = "Which models a Model card may use, in your order (the next one when a machine is off). Wire its dotted output into a Model."
    inputs: dict[str, str] = {}
    outputs = {"models": "pool"}
    settings_schema = {
        "models": {"type": "models", "title": "Models (in order)", "default": [],
                   "help": "Picked from Settings → Models; the first that answers is used."},
    }
