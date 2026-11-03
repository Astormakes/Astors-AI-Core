"""The channel's format (docs/v2 page 6): one JSON message per line, both ways. Bytes (audio, a WAV) go as
{"__bytes__": base64}; an error goes by its kind, and arrives as the same kind when it is one every part of AAC knows
(else RuntimeError; `known` adds AAC's own), so a card's `except PermissionError` works the same on either side.
Standard library only: the walled worker imports it before the wall."""

from __future__ import annotations

import base64
import json
from typing import Any

KINDS: dict[str, type[Exception]] = {e.__name__: e for e in (PermissionError, ValueError, KeyError, TypeError, ConnectionError,
                                                             TimeoutError, LookupError)}


def known(*kinds: type[Exception]) -> None:
    """More kinds that arrive as themselves (AAC's own: ModelError, ModelUnreachable)."""
    KINDS.update({k.__name__: k for k in kinds})


def _default(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes__": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _hook(found: dict[str, Any]) -> Any:
    return base64.b64decode(found["__bytes__"]) if len(found) == 1 and "__bytes__" in found else found


def dumps(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, ensure_ascii=False, default=_default) + "\n").encode()


def loads(line: bytes) -> dict[str, Any]:
    return json.loads(line, object_hook=_hook)


def error(err: BaseException) -> dict[str, str]:
    """An error as it travels."""
    return {"error": str(err) if not isinstance(err, KeyError) else str(err.args[0] if err.args else ""), "kind": type(err).__name__}


def raised(message: dict[str, Any]) -> Exception:
    """The error a message carries, as its kind."""
    return KINDS.get(str(message.get("kind")), RuntimeError)(message.get("error") or "")
