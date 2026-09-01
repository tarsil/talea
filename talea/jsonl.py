"""Frame and decode bounded JSON Lines records for retained Contracts."""

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Literal, TypeVar

from talea.contract.items import ItemPolicy, _ItemState, resolve_item_policy
from talea.input import json as json_input
from talea.resources.errors import ResourceLimitError
from talea.resources.policy import DEFAULT_MAX_INPUT_BYTES
from talea.validation.errors import ValidationError

__all__ = ["JsonlError", "JsonlErrorCode", "JsonlPolicy"]

T = TypeVar("T")

type JsonlErrorCode = Literal[
    "blank",
    "bom",
    "duplicate_key",
    "invalid_encoding",
    "invalid_json",
    "non_finite_number",
]


def _limit(value: int | None, name: str) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{name} must be a positive int or None")


@dataclass(frozen=True, slots=True)
class JsonlPolicy:
    """Bound raw bytes consumed by one JSON Lines input operation.

    Args:
        max_line_bytes: Maximum UTF-8 bytes in one source record, including
            its LF or CRLF terminator. The finite default matches Talea's
            ordinary JSON transport limit. ``None`` disables this bound.
        max_total_bytes: Maximum UTF-8 bytes across all pulled records,
            including terminators and rejected records. ``None`` explicitly
            permits caller-bounded streams of arbitrary total size.
    """

    max_line_bytes: int | None = DEFAULT_MAX_INPUT_BYTES
    max_total_bytes: int | None = None

    def __post_init__(self) -> None:
        _limit(self.max_line_bytes, "max_line_bytes")
        _limit(self.max_total_bytes, "max_total_bytes")


DEFAULT_JSONL_POLICY = JsonlPolicy()


class JsonlError(ValueError):
    """Report one safe JSON Lines framing or strict-decoding failure.

    ``line`` is the one-based physical record number. ``record_line`` and
    ``column`` describe the strict decoder location when it is available.
    The exception never stores the rejected text, bytes, key, numeric token,
    or underlying decoder exception.
    """

    __slots__ = ("__weakref__", "code", "column", "line", "record_line")

    def __init__(
        self,
        code: JsonlErrorCode,
        line: int,
        *,
        record_line: int | None = None,
        column: int | None = None,
    ) -> None:
        self.code = code
        self.line = line
        self.record_line = record_line
        self.column = column
        message = {
            "blank": "blank record",
            "bom": "byte order mark",
            "duplicate_key": "duplicate JSON object key",
            "invalid_encoding": "invalid UTF-8",
            "invalid_json": "invalid JSON",
            "non_finite_number": "non-finite JSON number",
        }[code]
        location = ""
        if record_line is not None:
            location = f" at record line {record_line}"
            if column is not None:
                location += f", column {column}"
        super().__init__(f"JSONL line {line}: {message}{location}")


def _resolve_policy(policy: JsonlPolicy | None) -> JsonlPolicy:
    if policy is None:
        return DEFAULT_JSONL_POLICY
    if not isinstance(policy, JsonlPolicy):
        raise TypeError("jsonl_policy must be a JsonlPolicy or None")
    return policy


def _text_size(value: str, limit: int | None) -> tuple[int, bool]:
    characters = len(value)
    if value.isascii():
        return characters, True
    observed = 0
    valid = True
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            valid = False
        observed += 1 + (codepoint > 0x7F) + (codepoint > 0x7FF) + (codepoint > 0xFFFF)
        if limit is not None and observed > limit:
            return observed, valid
    return observed, valid


def _decode_utf8(value: bytes, line: int) -> str | JsonlError:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return JsonlError("invalid_encoding", line)


def _decode_record(text: str, line: int) -> object | JsonlError:
    payload_end = len(text)
    if text.endswith("\n"):
        payload_end -= 1
        if payload_end and text[payload_end - 1] == "\r":
            payload_end -= 1
    if payload_end == 0:
        return JsonlError("blank", line)
    if text[0] == "\ufeff":
        return JsonlError("bom", line)
    embedded_newline = text.find("\n", 0, payload_end)
    if embedded_newline >= 0:
        return JsonlError("invalid_json", line, record_line=2, column=1)
    try:
        return json_input._decode_strict_json(text)
    except json_input._DuplicateKeyError:
        return JsonlError("duplicate_key", line)
    except json_input._NonFiniteNumberError:
        return JsonlError("non_finite_number", line)
    except json.JSONDecodeError as error:
        return JsonlError("invalid_json", line, record_line=error.lineno, column=error.colno)
    except ValueError:
        return JsonlError("invalid_json", line)


def _iter_jsonl(
    source: Iterable[str] | Iterable[bytes],
    operation: Callable[[object], T],
    *,
    on_error: Callable[[int, ValidationError], None] | None,
    on_jsonl_error: Callable[[int, JsonlError], None] | None,
    item_policy: ItemPolicy | None,
    jsonl_policy: JsonlPolicy | None,
) -> Iterator[T]:
    """Validate static controls eagerly and return a lazy JSONL iterator."""

    selected_items = resolve_item_policy(item_policy)
    selected_jsonl = _resolve_policy(jsonl_policy)
    if on_error is not None and not callable(on_error):
        raise TypeError("on_error must be callable or None")
    if on_jsonl_error is not None and not callable(on_jsonl_error):
        raise TypeError("on_jsonl_error must be callable or None")
    return _consume_jsonl(source, operation, on_error, on_jsonl_error, selected_items, selected_jsonl)


def _consume_jsonl(
    source: Iterable[str] | Iterable[bytes],
    operation: Callable[[object], T],
    on_error: Callable[[int, ValidationError], None] | None,
    on_jsonl_error: Callable[[int, JsonlError], None] | None,
    item_policy: ItemPolicy,
    jsonl_policy: JsonlPolicy,
) -> Iterator[T]:
    state = _ItemState(item_policy)
    source_type: type[str] | type[bytes] | None = None
    total_bytes = 0
    for record in source:
        try:
            index = state.begin_item()
        except ResourceLimitError:
            record = ""
            raise
        record_type = type(record)
        if record_type is not str and record_type is not bytes:
            failure = TypeError("JSONL source items must be str or bytes")
            del record
            raise failure
        if source_type is None:
            source_type = record_type
        elif record_type is not source_type:
            failure = TypeError("JSONL source items must not mix str and bytes")
            del record
            raise failure

        if record_type is bytes:
            assert isinstance(record, bytes)
            line_bytes = len(record)
        else:
            assert isinstance(record, str)
            line_bytes, valid_encoding = _text_size(record, jsonl_policy.max_line_bytes)

        maximum_line = jsonl_policy.max_line_bytes
        if maximum_line is not None and line_bytes > maximum_line:
            failure = ResourceLimitError("jsonl_line_size", maximum_line, line_bytes)
            del record
            raise failure
        total_bytes += line_bytes
        maximum_total = jsonl_policy.max_total_bytes
        if maximum_total is not None and total_bytes > maximum_total:
            failure = ResourceLimitError("jsonl_total_size", maximum_total, total_bytes)
            del record
            raise failure

        if record_type is str and not valid_encoding:
            failure = JsonlError("invalid_encoding", index + 1)
            del record
            if on_jsonl_error is None:
                raise failure
            state.mark_invalid()
            on_jsonl_error(index + 1, failure)
            continue

        if record_type is bytes:
            assert isinstance(record, bytes)
            text = _decode_utf8(record, index + 1)
            if isinstance(text, JsonlError):
                del record
                if on_jsonl_error is None:
                    raise text
                state.mark_invalid()
                on_jsonl_error(index + 1, text)
                continue
        else:
            assert isinstance(record, str)
            text = record

        decoded = _decode_record(text, index + 1)
        if isinstance(decoded, JsonlError):
            del record, text
            if on_jsonl_error is None:
                raise decoded
            state.mark_invalid()
            on_jsonl_error(index + 1, decoded)
            del decoded
            continue

        try:
            result = operation(decoded)
        except ValidationError as error:
            record = ""
            text = ""
            decoded = None
            state.handle_validation_error(index, error, on_error)
        else:
            del record, text, decoded
            yield result
            del result
