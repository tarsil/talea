"""Decode JSON syntax independently of Talea's schema-aware input compiler."""

import json
from collections.abc import Callable
from decimal import Decimal

from talea.errors import ErrorCode
from talea.errors.models import ValidationError

type JsonInput = str | bytes | bytearray
type JsonLoads = Callable[[JsonInput], object]


class _DuplicateKeyError(ValueError):
    __slots__ = ("key",)

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class _NonFiniteNumberError(ValueError):
    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(token)


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_constant(token: str) -> object:
    raise _NonFiniteNumberError(token)


def _default_loads(data: JsonInput) -> object:
    return json.loads(
        data,
        parse_float=Decimal,
        parse_constant=_reject_constant,
        object_pairs_hook=_object_from_pairs,
    )


def decode_json(data: JsonInput, loads: JsonLoads | None, *, title: str) -> object:
    """Decode JSON syntax with strict stdlib defaults or one explicit callable.

    The default preserves fractional number tokens as :class:`Decimal`, rejects
    duplicate object keys, and rejects the non-standard NaN and Infinity tokens.
    A custom callable owns its parser behavior; Talea still applies the same
    compiled schema-aware conversion and validation to its returned object.
    """

    decoder = _default_loads if loads is None else loads
    try:
        return decoder(data)
    except _DuplicateKeyError as error:
        raise ValidationError(
            None,
            error.key,
            (),
            ErrorCode.JSON_DUPLICATE,
            title=title,
            context=(("key", error.key),),
        ) from error
    except _NonFiniteNumberError as error:
        raise ValidationError(
            None,
            error.token,
            (),
            ErrorCode.JSON_INVALID,
            title=title,
            context=(("reason", "non_finite_number"),),
        ) from error
    except json.JSONDecodeError as error:
        failure = ValidationError(
            None,
            data,
            (),
            ErrorCode.JSON_INVALID,
            title=title,
            context=(("line", error.lineno), ("column", error.colno), ("position", error.pos)),
        )
        if len(data) <= 4096:
            raise failure from error
        raise failure from None
    except UnicodeDecodeError as error:
        raise ValidationError(
            None,
            data[:160],
            (),
            ErrorCode.JSON_INVALID,
            title=title,
            context=(("start", error.start), ("end", error.end), ("reason", error.reason)),
        ) from error
    except ValueError as error:
        raise ValidationError(
            None,
            data[:160],
            (),
            ErrorCode.JSON_INVALID,
            title=title,
            context=(("decoder", type(decoder).__qualname__),),
        ) from error
