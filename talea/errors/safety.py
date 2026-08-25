"""Capture bounded error-facing representations of hostile input values."""

import math
import reprlib
import unicodedata
from dataclasses import dataclass
from typing import cast

_MAX_REPRESENTATION = 160
_TRUNCATION = "... <truncated>"

_REPR = reprlib.Repr()
_REPR.maxlevel = 2
_REPR.maxdict = 4
_REPR.maxlist = 6
_REPR.maxtuple = 6
_REPR.maxset = 6
_REPR.maxfrozenset = 6
_REPR.maxdeque = 6
_REPR.maxarray = 6
_REPR.maxstring = 96
_REPR.maxlong = 96
_REPR.maxother = 96

type JsonScalar = None | bool | int | float | str


@dataclass(frozen=True, slots=True)
class _InputSnapshot:
    """Retain one bounded JSON scalar and its human display text."""

    projection: JsonScalar
    rendered: str


def _truncate(text: str, maximum: int = _MAX_REPRESENTATION) -> str:
    if len(text) <= maximum:
        return text
    return f"{text[: maximum - len(_TRUNCATION)]}{_TRUNCATION}"


def safe_text(text: str, maximum: int = _MAX_REPRESENTATION) -> str:
    """Escape control characters and bound user-influenced labels."""

    pieces: list[str] = []
    for character in text:
        if character == "\n":
            pieces.append(r"\n")
        elif character == "\r":
            pieces.append(r"\r")
        elif character == "\t":
            pieces.append(r"\t")
        elif unicodedata.category(character).startswith("C"):
            pieces.append(character.encode("unicode_escape").decode("ascii"))
        else:
            pieces.append(character)
    return _truncate("".join(pieces), maximum)


def safe_type_name(value: object) -> str:
    """Return a bounded concrete type name without invoking instance code."""

    value_type = type(value)
    return safe_text(value_type.__qualname__)


def snapshot_input(value: object) -> _InputSnapshot:
    """Capture a bounded, repeatable, JSON-compatible view of ``value``.

    Exact JSON scalar types remain scalars unless their textual form needs
    truncation or JSON cannot represent it portably. Every other object becomes
    bounded repr text. Representation failures, recursion, and very large
    containers cannot escape error construction.
    """

    if value is None or type(value) in (bool, int):
        return _InputSnapshot(cast(JsonScalar, value), repr(value))
    if type(value) is float:
        if math.isfinite(value):
            return _InputSnapshot(value, repr(value))
        rendered = repr(value)
        return _InputSnapshot(rendered, rendered)
    if type(value) is str:
        projection = _truncate(value)
        return _InputSnapshot(projection, _truncate(repr(projection)))

    try:
        rendered = _REPR.repr(value)
    except BaseException as error:
        error_name = safe_text(type(error).__qualname__, 48)
        rendered = f"<unrepresentable {safe_type_name(value)}: {error_name}>"
    rendered = safe_text(_truncate(rendered))
    return _InputSnapshot(rendered, rendered)
