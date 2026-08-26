"""Decode JSON syntax independently of Talea's schema-aware input compiler."""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import NoReturn

from talea.errors import ErrorCode
from talea.errors.models import ValidationError
from talea.errors.safety import REDACTED

type JsonInput = str | bytes | bytearray
type JsonLoads = Callable[[JsonInput], object]

_MAX_CAUSE_INPUT = 4096


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


def _raise_decoder_failure(
    failure: ValidationError,
    cause: BaseException,
    data: JsonInput,
    sensitive: bool,
) -> NoReturn:
    if not sensitive and len(data) <= _MAX_CAUSE_INPUT:
        raise failure from cause
    raise failure from None


def decode_json(
    data: JsonInput,
    loads: JsonLoads | None,
    *,
    title: str,
    sensitive: bool = False,
) -> object:
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
        failure = ValidationError(
            None,
            error.key,
            (),
            ErrorCode.JSON_DUPLICATE,
            title=title,
            context=(("key", REDACTED if sensitive else error.key),),
            sensitive=sensitive,
        )
        _raise_decoder_failure(failure, error, data, sensitive)
    except _NonFiniteNumberError as error:
        failure = ValidationError(
            None,
            error.token,
            (),
            ErrorCode.JSON_INVALID,
            title=title,
            context=(("reason", "non_finite_number"),),
            sensitive=sensitive,
        )
        _raise_decoder_failure(failure, error, data, sensitive)
    except json.JSONDecodeError as error:
        failure = ValidationError(
            None,
            data,
            (),
            ErrorCode.JSON_INVALID,
            title=title,
            context=(("line", error.lineno), ("column", error.colno), ("position", error.pos)),
            sensitive=sensitive,
        )
        _raise_decoder_failure(failure, error, data, sensitive)
    except UnicodeDecodeError as error:
        failure = ValidationError(
            None,
            data[:160],
            (),
            ErrorCode.JSON_INVALID,
            title=title,
            context=(("start", error.start), ("end", error.end), ("reason", error.reason)),
            sensitive=sensitive,
        )
        _raise_decoder_failure(failure, error, data, sensitive)
    except ValueError as error:
        failure = ValidationError(
            None,
            data[:160],
            (),
            ErrorCode.JSON_INVALID,
            title=title,
            context=(("decoder", type(decoder).__qualname__),),
            sensitive=sensitive,
        )
        _raise_decoder_failure(failure, error, data, sensitive)
