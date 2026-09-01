"""Executable incremental trade-processing examples."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from typing import Annotated

from talea import Contract, ResourceLimitError, Sensitive, Spec, ValidationError
from talea.contract import ItemPolicy

type AccountToken = Annotated[int, Sensitive()]


class Trade(Spec):
    """One validated trade accepted by the persistence boundary."""

    trade_id: int
    account_token: AccountToken
    quantity: int


trade_contract = Contract(Trade)


def database_cursor() -> Iterator[dict[str, object]]:
    """Stand in for an application-owned database cursor."""

    yield {"trade_id": 1, "account_token": 801, "quantity": 5}
    yield {"trade_id": 2, "account_token": 802, "quantity": 8}


# Iterator creation consumes no source item. Each mapping is pulled, converted,
# persisted, and released before the next one is requested.
converted = trade_contract.iter_python(database_cursor())
assert iter(converted) is converted
assert next(converted).trade_id == 1
assert next(converted).trade_id == 2

# Strict item validation is a separate boundary and preserves object identity.
strict_trade = Trade(trade_id=3, account_token=803, quantity=13)
assert next(trade_contract.iter_validate((strict_trade,))) is strict_trade

# Fail-fast is the default and locations start with the zero-based source index.
try:
    list(
        trade_contract.iter_python(
            (
                {"trade_id": 4, "account_token": 804, "quantity": 3},
                {"trade_id": 5, "account_token": 805, "quantity": "invalid"},
            )
        )
    )
except ValidationError as error:
    assert error.errors()[0]["location"] == [1, "quantity"]
else:
    raise AssertionError("invalid trade should fail fast")

# Continuation is an explicit application recovery decision. The callback gets
# the index and located canonical error, but no separate raw-mapping argument.
reported: list[tuple[int, tuple[object, ...]]] = []


def report_invalid(index: int, error: ValidationError) -> None:
    reported.append((index, tuple(error.errors()[0]["location"])))


accepted = trade_contract.iter_python(
    (
        {"trade_id": 6, "account_token": 806, "quantity": 2},
        {"trade_id": 7, "account_token": 807, "quantity": "invalid"},
        {"trade_id": 8, "account_token": 808, "quantity": 4},
    ),
    on_error=report_invalid,
)
assert [trade.trade_id for trade in accepted] == [6, 8]
assert reported == [(1, (1, "quantity"))]

# Sensitive failure details stay redacted after the index is prefixed.
try:
    next(trade_contract.iter_python(({"trade_id": 9, "account_token": "private-token", "quantity": 1},)))
except ValidationError as error:
    detail = error.errors()[0]
    assert detail["location"] == [0, "account_token"]
    assert detail["input"] == "<redacted>"
    assert "private-token" not in str(error)
else:
    raise AssertionError("sensitive invalid trade should fail")

# Every pulled source item counts, including invalid items. Invalid-item limits
# count source records, not the number of nested validation details.
bounded = trade_contract.iter_python(database_cursor(), item_policy=ItemPolicy(max_items=1))
assert next(bounded).trade_id == 1
try:
    next(bounded)
except ResourceLimitError as error:
    assert (error.code, error.limit, error.observed) == ("items", 1, 2)
else:
    raise AssertionError("record budget should terminate consumption")

invalid_bounded = trade_contract.iter_python(
    (
        {"trade_id": "bad", "account_token": 1, "quantity": 1},
        {"trade_id": "bad", "account_token": 2, "quantity": 2},
    ),
    on_error=lambda _index, _error: None,
    item_policy=ItemPolicy(max_invalid_items=1),
)
try:
    list(invalid_bounded)
except ResourceLimitError as error:
    assert (error.code, error.limit, error.observed) == ("invalid_items", 1, 2)
else:
    raise AssertionError("invalid-record budget should terminate continuation")

# Consumer early termination performs no lookahead or drain.
pulls: list[int] = []


def event_source() -> Iterator[Trade]:
    for index in range(100):
        pulls.append(index)
        yield Trade(trade_id=index, account_token=index, quantity=1)


early = trade_contract.iter_validate(event_source())
assert isinstance(early, Generator)
assert next(early).trade_id == 0
early.close()
assert pulls == [0]

# Source failures remain application exceptions.
source_failure = OSError("cursor disconnected")


def failing_cursor() -> Iterator[dict[str, object]]:
    yield {"trade_id": 10, "account_token": 810, "quantity": 1}
    raise source_failure


failing = trade_contract.iter_python(failing_cursor())
assert next(failing).trade_id == 10
try:
    next(failing)
except OSError as error:
    assert error is source_failure
else:
    raise AssertionError("source exception should propagate unchanged")

# A memory-friendly consumer keeps only application state, not a result batch.
persisted_count = 0
for _trade in trade_contract.iter_python(database_cursor()):
    persisted_count += 1  # persist(trade)
assert persisted_count == 2
