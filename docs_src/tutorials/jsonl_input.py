"""Executable bounded JSON Lines trade-import workflow."""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from talea import Contract, ResourceLimitError, Sensitive, Spec, ValidationError
from talea.contract import ItemPolicy
from talea.jsonl import JsonlError, JsonlPolicy

type AccountToken = Annotated[str, Sensitive()]


class Trade(Spec):
    """One trade accepted by an application import boundary."""

    trade_id: int
    account_token: AccountToken
    symbol: str
    quantity: int


trade_contract = Contract(Trade)

# Text and bytes are record iterables rather than arbitrary chunks. LF, CRLF,
# and an omitted final terminator all preserve one value per source item.
text_records = (
    '{"trade_id":1,"account_token":"a1","symbol":"TAL","quantity":3}\n',
    '{"trade_id":2,"account_token":"a2","symbol":"TAL","quantity":5}\r\n',
    '{"trade_id":3,"account_token":"a3","symbol":"TAL","quantity":8}',
)
assert [trade.trade_id for trade in trade_contract.iter_jsonl(text_records)] == [1, 2, 3]

byte_records = tuple(record.encode() for record in text_records)
assert [trade.quantity for trade in trade_contract.iter_jsonl(byte_records)] == [3, 5, 8]

# Iterator creation is lazy. One next() pulls only enough records to produce
# one result, and closing the Talea wrapper performs no lookahead.
pulls: list[int] = []


def lazy_records() -> Iterator[str]:
    for index, record in enumerate(text_records):
        pulls.append(index)
        yield record


lazy = trade_contract.iter_jsonl(lazy_records())
assert pulls == []
assert next(lazy).trade_id == 1
assert pulls == [0]
lazy.close()  # ty: ignore[unresolved-attribute]
assert pulls == [0]

# Malformed JSON is a framing/decode failure, not a ValidationError. Its safe
# error has a one-based physical line and never exposes the rejected record.
try:
    next(trade_contract.iter_jsonl(('{"account_token":"secret",',)))
except JsonlError as error:
    assert (error.line, error.code) == (1, "invalid_json")
    assert "secret" not in str(error)
    assert error.__cause__ is None
else:
    raise AssertionError("malformed JSON should fail fast")

# Explicit JSONL continuation receives one-based lines. The malformed record
# produces no placeholder; the next complete record becomes the next result.
malformed_lines: list[tuple[int, str]] = []


def report_malformed(line: int, error: JsonlError) -> None:
    malformed_lines.append((line, error.code))


continued = trade_contract.iter_jsonl(
    (
        text_records[0],
        "{",
        text_records[2],
    ),
    on_jsonl_error=report_malformed,
)
assert [trade.trade_id for trade in continued] == [1, 3]
assert malformed_lines == [(2, "invalid_json")]

# Decoded-value failures keep the accepted zero-based item callback and
# canonical ValidationError locations. For JSONL, index + 1 is its line.
invalid_items: list[tuple[int, tuple[object, ...]]] = []


def report_invalid(index: int, error: ValidationError) -> None:
    invalid_items.append((index, error.location))


validated = trade_contract.iter_jsonl(
    (
        text_records[0],
        '{"trade_id":2,"account_token":"a2","symbol":"TAL","quantity":"bad"}',
        text_records[2],
    ),
    on_error=report_invalid,
)
assert [trade.trade_id for trade in validated] == [1, 3]
assert invalid_items == [(1, (1, "quantity"))]
assert invalid_items[0][0] + 1 == 2

# Sensitive applies after successful JSON decoding reaches Contract input.
try:
    next(trade_contract.iter_jsonl(('{"trade_id":4,"account_token":4,"symbol":"TAL","quantity":1}',)))
except ValidationError as error:
    detail = error.errors()[0]
    assert detail["location"] == [0, "account_token"]
    assert detail["input"] == "<redacted>"
else:
    raise AssertionError("invalid sensitive value should fail")

# ItemPolicy counts every pulled physical record, including malformed ones.
bounded = trade_contract.iter_jsonl(text_records, item_policy=ItemPolicy(max_items=1))
assert next(bounded).trade_id == 1
try:
    next(bounded)
except ResourceLimitError as error:
    assert (error.code, error.limit, error.observed) == ("items", 1, 2)
else:
    raise AssertionError("item budget should terminate the iterator")

# One invalid budget spans malformed framing and decoded validation failures.
invalid_bounded = trade_contract.iter_jsonl(
    (
        "{",
        '{"trade_id":2,"account_token":"a2","symbol":"TAL","quantity":"bad"}',
    ),
    on_jsonl_error=lambda _line, _error: None,
    on_error=lambda _index, _error: None,
    item_policy=ItemPolicy(max_invalid_items=1),
)
try:
    list(invalid_bounded)
except ResourceLimitError as error:
    assert (error.code, error.limit, error.observed) == ("invalid_items", 1, 2)
else:
    raise AssertionError("shared invalid budget should terminate continuation")

# Transport bytes include the record terminator and are checked before JSON
# parsing. Five UTF-8 bytes encode this record: quote, two-byte é, quote, LF.
assert list(Contract(str).iter_jsonl(('"é"\n',), jsonl_policy=JsonlPolicy(max_line_bytes=5))) == ["é"]
try:
    next(Contract(str).iter_jsonl(('"é"\n',), jsonl_policy=JsonlPolicy(max_line_bytes=4)))
except ResourceLimitError as error:
    assert (error.code, error.limit, error.observed) == ("jsonl_line_size", 4, 5)
else:
    raise AssertionError("line byte budget should reject before parsing")

# Source exceptions are application failures and preserve identity.
source_failure = OSError("feed disconnected")


def failing_source() -> Iterator[str]:
    yield text_records[0]
    raise source_failure


failing = trade_contract.iter_jsonl(failing_source())
assert next(failing).trade_id == 1
try:
    next(failing)
except OSError as error:
    assert error is source_failure
else:
    raise AssertionError("source failure should propagate unchanged")

# A real file is opened and closed by the caller. Talea only consumes its
# bytes-record iterator and never materializes a list of Trade objects.
with TemporaryDirectory() as directory:
    path = Path(directory) / "trades.jsonl"
    path.write_bytes(b"".join(byte_records))
    processed = 0
    with path.open("rb") as stream:
        for _trade in trade_contract.iter_jsonl(stream):
            processed += 1  # process(_trade)
        assert not stream.closed
    assert stream.closed
    assert processed == 3
