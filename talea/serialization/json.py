"""Encode an already projected JSON-native tree through one selected codec."""

import json
from collections.abc import Callable

from talea.serialization.errors import SerializationError

type JsonDumps = Callable[[object], str | bytes | bytearray]


def _default_dumps(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def encode_json(value: object, dumps: JsonDumps | None) -> str:
    """Encode a JSON-native tree and normalize the selected codec to ``str``.

    The default standard-library path emits compact strict JSON. A custom
    one-argument callable receives the same Talea-projected tree. Text output is
    returned directly; bytes and bytearray output must contain UTF-8 JSON and
    are decoded. A wrong return type or codec ``ValueError`` becomes
    :class:`SerializationError`; other codec exceptions propagate unchanged.
    """

    encoder = _default_dumps if dumps is None else dumps
    try:
        encoded = encoder(value)
    except (ValueError, TypeError) as error:
        if dumps is not None and isinstance(error, TypeError):
            raise
        raise SerializationError(f"JSON encoder {type(encoder).__qualname__} rejected the projected value") from error
    if isinstance(encoded, str):
        return encoded
    if type(encoded) in (bytes, bytearray):
        try:
            return bytes(encoded).decode("utf-8")
        except UnicodeDecodeError as error:
            raise SerializationError("JSON encoder returned non-UTF-8 bytes") from error
    raise SerializationError("JSON encoder must return str, bytes, or bytearray")
