from __future__ import annotations

import gc
import weakref
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, dataclass
from typing import Annotated, TypedDict

import pytest
from hypothesis import given, strategies as st

import talea
from talea import (
    Contract,
    Representation,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
    ValidationError,
)
from talea.contract import ItemPolicy


class Record(Spec):
    value: int


class Payload(TypedDict):
    value: int


type SensitiveInt = Annotated[int, Sensitive()]


class SecretRecord(Spec):
    secret: SensitiveInt


class WideRecord(Spec):
    value: int


@dataclass(slots=True, weakref_slot=True)
class RetainedRecord:
    value: int


def test_item_policy_is_finite_immutable_and_domain_public_only() -> None:
    policy = ItemPolicy()

    assert (policy.max_items, policy.max_invalid_items) == (1_000_000, 100)
    assert not hasattr(talea, "ItemPolicy")
    with pytest.raises(FrozenInstanceError):
        policy.max_items = 1  # type: ignore[misc]
    for name in ("max_items", "max_invalid_items"):
        with pytest.raises(ValueError, match=name):
            ItemPolicy(**{name: 0})  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=name):
            ItemPolicy(**{name: True})  # type: ignore[arg-type]


def test_iterator_configuration_is_eager_but_source_consumption_is_lazy() -> None:
    events: list[str] = []

    def source() -> Iterator[int]:
        events.append("first")
        yield 1
        events.append("second")
        yield 2

    contract = Contract(int)
    iterator = contract.iter_validate(source())

    assert events == []
    assert iter(iterator) is iterator
    assert next(iterator) == 1
    assert events == ["first"]
    iterator.close()  # type: ignore[attr-defined]
    assert events == ["first"]

    with pytest.raises(TypeError, match="on_error"):
        contract.iter_validate([], on_error=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ItemPolicy"):
        contract.iter_validate([], item_policy=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        next(contract.iter_validate(1))  # type: ignore[arg-type]


def test_strict_iteration_preserves_identity_and_prefixes_fail_fast_error() -> None:
    first = Record(value=1)
    invalid = object()
    source = iter((first, invalid, Record(value=3)))
    iterator = Contract(Record).iter_validate(source)

    assert next(iterator) is first
    with pytest.raises(ValidationError) as raised:
        next(iterator)

    assert raised.value.errors()[0]["location"] == [1]
    assert next(source).value == 3


def test_external_iteration_converts_once_and_continues_explicitly() -> None:
    calls: list[str] = []

    @dataclass
    class External:
        value: int

    def load(value: str) -> External:
        calls.append(value)
        if value == "bad":
            raise ValueError("rejected")
        return External(int(value))

    type ExternalValue = Annotated[External, Representation(input=str, load=load)]
    failures: list[tuple[int, ValidationError]] = []
    contract = Contract[External](ExternalValue)
    iterator = contract.iter_python(("1", "bad", "2"), on_error=lambda index, error: failures.append((index, error)))

    assert [item.value for item in iterator] == [1, 2]
    assert calls == ["1", "bad", "2"]
    assert failures[0][0] == 1
    assert failures[0][1].errors()[0]["location"] == [1]
    assert failures[0][1].__cause__ is not None
    assert contract._artifacts.python_input is not None


def test_nested_locations_details_truncation_and_sensitive_redaction_are_preserved() -> None:
    failures: list[ValidationError] = []
    contract = Contract[Payload](Payload)
    list(contract.iter_python(({"value": "bad"},), on_error=lambda _index, error: failures.append(error)))

    detail = failures[0].errors()[0]
    assert detail["location"] == [0, "value"]
    assert detail["code"] == "type"

    with pytest.raises(ValidationError) as sensitive:
        next(Contract(SecretRecord).iter_python(({"secret": "do-not-leak"},)))
    secret = sensitive.value.errors()[0]
    assert secret["location"] == [0, "secret"]
    assert secret["input"] == "<redacted>"
    assert "do-not-leak" not in str(sensitive.value)

    with pytest.raises(ValidationError) as truncated:
        next(
            Contract(WideRecord).iter_python(
                ({"value": "bad", "extra": 1},),
                policy=ResourcePolicy(max_errors=1),
            )
        )
    assert truncated.value.truncated
    assert truncated.value.errors()[0]["location"][0] == 0


def test_item_and_invalid_item_budgets_have_distinct_exact_counts() -> None:
    seen: list[int] = []
    item_iterator = Contract(int).iter_validate((1, 2, 3), item_policy=ItemPolicy(max_items=2))

    assert next(item_iterator) == 1
    assert next(item_iterator) == 2
    with pytest.raises(ResourceLimitError) as items:
        next(item_iterator)
    assert (items.value.code, items.value.limit, items.value.observed) == ("items", 2, 3)

    invalid_iterator = Contract(int).iter_validate(
        ("one", "two", 3, "four"),
        on_error=lambda index, _error: seen.append(index),
        item_policy=ItemPolicy(max_items=None, max_invalid_items=2),
    )
    assert next(invalid_iterator) == 3
    with pytest.raises(ResourceLimitError) as invalid:
        next(invalid_iterator)
    assert seen == [0, 1]
    assert (invalid.value.code, invalid.value.limit, invalid.value.observed) == ("invalid_items", 2, 3)
    assert invalid.value.__cause__ is None
    assert invalid.value.__suppress_context__


def test_per_item_resource_failure_is_terminal_and_never_calls_callback() -> None:
    failures: list[ValidationError] = []
    iterator = Contract[list[int]](list[int]).iter_python(
        ([1, 2],),
        on_error=lambda _index, error: failures.append(error),
        policy=ResourcePolicy(max_nodes=2),
    )

    with pytest.raises(ResourceLimitError) as raised:
        next(iterator)
    assert raised.value.code == "nodes"
    assert failures == []


def test_source_and_callback_exceptions_propagate_unchanged_without_lookahead() -> None:
    source_failure = RuntimeError("source-secret")
    callback_failure = OSError("callback-secret")
    consumed: list[int] = []

    def source() -> Iterator[object]:
        consumed.append(0)
        yield 1
        consumed.append(1)
        yield "bad"
        consumed.append(2)
        raise source_failure

    def reject(_index: int, _error: ValidationError) -> None:
        raise callback_failure

    iterator = Contract(int).iter_validate(source(), on_error=reject)
    assert next(iterator) == 1
    with pytest.raises(OSError) as callback:
        next(iterator)
    assert callback.value is callback_failure
    assert consumed == [0, 1]

    def broken() -> Iterator[object]:
        yield 1
        raise source_failure

    iterator = Contract(int).iter_validate(broken())
    assert next(iterator) == 1
    with pytest.raises(RuntimeError) as source_error:
        next(iterator)
    assert source_error.value is source_failure


def test_continuation_is_reentrant_and_iterator_state_is_independent() -> None:
    contract = Contract(int)
    failures: list[tuple[int, int]] = []

    def on_error(index: int, _error: ValidationError) -> None:
        failures.append((index, next(contract.iter_validate((index,)))))

    def consume(values: tuple[object, ...]) -> list[int]:
        return list(contract.iter_validate(values, on_error=on_error))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consume, ((1, "bad", 2), (3, "bad", 4))))

    assert results == [[1, 2], [3, 4]]
    assert failures == [(1, 1), (1, 1)]


def test_wrapper_close_does_not_close_or_drain_caller_owned_source() -> None:
    closed = False

    def source() -> Iterator[int]:
        nonlocal closed
        try:
            yield 1
            yield 2
        finally:
            closed = True

    owned = source()
    wrapper = Contract(int).iter_validate(owned)
    assert next(wrapper) == 1
    wrapper.close()  # type: ignore[attr-defined]
    assert not closed
    assert next(owned) == 2
    owned.close()
    assert closed


def test_prior_success_and_continued_error_are_not_retained() -> None:
    contract = Contract(RetainedRecord)
    references: list[weakref.ReferenceType[RetainedRecord]] = []

    def source() -> Iterator[RetainedRecord]:
        first = RetainedRecord(value=1)
        references.append(weakref.ref(first))
        yield first
        del first
        yield RetainedRecord(value=2)

    iterator = contract.iter_validate(source())
    assert next(iterator).value == 1
    second = next(iterator)
    gc.collect()
    assert references[0]() is None
    assert second.value == 2

    error_ref: weakref.ReferenceType[ValidationError] | None = None

    def observe(_index: int, error: ValidationError) -> None:
        nonlocal error_ref
        error_ref = weakref.ref(error)

    continued = Contract(int).iter_validate(("bad", 1), on_error=observe)
    assert next(continued) == 1
    gc.collect()
    assert error_ref is not None and error_ref() is None


@given(st.lists(st.one_of(st.integers(), st.text()), max_size=30))
def test_incremental_fail_fast_matches_manual_retained_contract_loop(values: list[object]) -> None:
    contract = Contract(int)
    yielded: list[int] = []
    expected: list[int] = []
    actual_error: ValidationError | None = None
    expected_error: ValidationError | None = None

    try:
        yielded.extend(contract.iter_validate(values, item_policy=ItemPolicy(max_items=None)))
    except ValidationError as error:
        actual_error = error

    for index, value in enumerate(values):
        try:
            expected.append(contract.validate(value))
        except ValidationError as error:
            expected_error = error.prefixed((index,))
            break

    assert yielded == expected
    assert (actual_error is None) == (expected_error is None)
    if actual_error is not None and expected_error is not None:
        assert actual_error.errors() == expected_error.errors()


def test_schema_projection_and_single_value_contract_operations_are_unchanged() -> None:
    contract = Contract(int)
    schema = contract.json_schema()
    openapi = contract.openapi_schema()

    assert list(contract.iter_validate((), item_policy=ItemPolicy(max_items=None, max_invalid_items=None))) == []
    assert contract.validate(1) == 1
    assert contract.from_python(1) == 1
    assert contract.from_json("1") == 1
    assert contract.to_python(1) == 1
    assert contract.to_json(1) == "1"
    assert contract.json_schema() == schema
    assert contract.openapi_schema() == openapi
