"""Constants for AAC – Astor's AI Core."""

import logging

DOMAIN = "aac"
LOGGER = logging.getLogger(__package__)

CONF_BASE_URL = "base_url"
CONF_MODEL = "model"
CONF_TEMPERATURE = "temperature"
CONF_MAX_TOKENS = "max_tokens"
CONF_THINKING = "thinking"

DEFAULT_NAME = "AAC"
DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 1024
DEFAULT_THINKING = False
DEFAULT_PROMPT = (
    "You are AAC, a calm and helpful home companion. "
    "Answer briefly and honestly. If you are not sure, say so."
)

# Max model round trips per user message (tool call -> result -> answer ...)
MAX_TOOL_ITERATIONS = 10
# Automatic runs (heartbeat, events) get fewer: they should mostly just look
MAX_TOOL_ITERATIONS_AUTO = 4
