import gc
import io
import itertools
import sys
import weakref
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, dataclass
from typing import Annotated, Literal, TypedDict

import pytest
from hypothesis import given, strategies as st

import talea
from talea import (
    Alias,
    Contract,
    Discriminator,
    Representation,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
)
from talea.contract import ItemPolicy
from talea.jsonl import JsonlError, JsonlPolicy
from talea.validation.errors import ValidationError


class Record(Spec):
    identifier: int
    name: str


class Payload(TypedDict):
    value: int


@dataclass
class Event:
    value: int


def test_jsonl_policy_and_error_are_stable_domain_public_contracts() -> None:
    policy = JsonlPolicy()
    error = JsonlError("invalid_json", 3, record_line=1, column=7)

    assert (policy.max_line_bytes, policy.max_total_bytes) == (8 * 1024 * 1024, None)
    assert (error.code, error.line, error.record_line, error.column) == ("invalid_json", 3, 1, 7)
    assert str(error) == "JSONL line 3: invalid JSON at record line 1, column 7"
    assert error.__cause__ is None and error.__context__ is None
    assert not hasattr(talea, "JsonlPolicy")
    assert not hasattr(talea, "JsonlError")
    with pytest.raises(FrozenInstanceError):
        policy.max_line_bytes = 1  # type: ignore[misc]
    for name in ("max_line_bytes", "max_total_bytes"):
        with pytest.raises(ValueError, match=name):
            JsonlPolicy(**{name: 0})  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=name):
            JsonlPolicy(**{name: True})  # type: ignore[arg-type]


def test_iterator_is_lazy_and_accepts_text_bytes_newlines_and_scalars() -> None:
    pulled: list[int] = []

    def source() -> Iterator[str]:
        pulled.append(1)
        yield "1\n"
        pulled.append(2)
        yield "2\r\n"
        pulled.append(3)
        yield "3"

    iterator = Contract(int).iter_jsonl(source())
    assert pulled == []
    assert iter(iterator) is iterator
    assert [next(iterator), next(iterator), next(iterator)] == [1, 2, 3]
    assert pulled == [1, 2, 3]

    assert list(Contract(str).iter_jsonl((b'"hello"\n',))) == ["hello"]
    assert list(Contract[None](None).iter_jsonl(("null",))) == [None]
    assert list(Contract(bool).iter_jsonl(("true\r",))) == [True]
    assert list(Contract[list[int]](list[int]).iter_jsonl(("[1,2]",))) == [[1, 2]]
    assert list(Contract(int).iter_jsonl(())) == []


@pytest.mark.parametrize(
    ("record", "code"),
    [
        ("", "blank"),
        ("\n", "blank"),
        ("\r\n", "blank"),
        (b"", "blank"),
        (b"\n", "blank"),
        ("\ufeff1", "bom"),
        (b"\xef\xbb\xbf1", "bom"),
        (b'"\xff"', "invalid_encoding"),
        ('{"value":\n1}\n', "invalid_json"),
        ("   \n", "invalid_json"),
        ("\t\n", "invalid_json"),
    ],
)
def test_framing_failures_are_safe_jsonl_errors(record: str | bytes, code: str) -> None:
    secret = "do-not-retain"
    if isinstance(record, str) and record.startswith("{"):
        record = record.replace("value", secret)
    with pytest.raises(JsonlError) as raised:
        next(Contract(int).iter_jsonl((record,)))

    error = raised.value
    assert error.code == code
    assert error.line == 1
    assert secret not in str(error)
    assert error.__cause__ is None and error.__context__ is None
    assert vars(error) == {}


@pytest.mark.parametrize(
    ("record", "code"),
    [
        ('{"identifier":1,"identifier":2,"name":"x"}', "duplicate_key"),
        ('{"identifier":NaN,"name":"x"}', "non_finite_number"),
        ('{"identifier":Infinity,"name":"x"}', "non_finite_number"),
        ('{"identifier":-Infinity,"name":"x"}', "non_finite_number"),
        ('{"identifier":,"name":"x"}', "invalid_json"),
        ("[1,", "invalid_json"),
        ('"unterminated', "invalid_json"),
        ('"\u0000"', "invalid_json"),
    ],
)
def test_strict_json_failures_match_canonical_decoder_policy(record: str, code: str) -> None:
    with pytest.raises(JsonlError) as raised:
        next(Contract(Record).iter_jsonl((record,)))
    assert raised.value.code == code

    if code == "invalid_json":
        assert raised.value.record_line == 1
        assert raised.value.column is not None


def test_python_integer_digit_protection_remains_terminal_json_syntax_policy() -> None:
    with pytest.raises(JsonlError) as huge_integer:
        next(Contract(int).iter_jsonl(("9" * 5_000,)))
    assert huge_integer.value.code == "invalid_json"


def test_framing_and_validation_callbacks_have_separate_index_truths() -> None:
    framing: list[tuple[int, str]] = []
    validation: list[tuple[int, tuple[object, ...]]] = []

    def on_jsonl_error(line: int, error: JsonlError) -> None:
        assert sys.exception() is None
        framing.append((line, error.code))

    def on_error(index: int, error: ValidationError) -> None:
        validation.append((index, error.location))

    values = list(
        Contract(int).iter_jsonl(
            ("1\n", "{", '"wrong"\n', "4"),
            on_jsonl_error=on_jsonl_error,
            on_error=on_error,
        )
    )

    assert values == [1, 4]
    assert framing == [(2, "invalid_json")]
    assert validation == [(2, (2,))]

    with pytest.raises(JsonlError):
        next(Contract(int).iter_jsonl(("{",), on_error=on_error))
    with pytest.raises(ValidationError):
        next(Contract(int).iter_jsonl(('"wrong"',), on_jsonl_error=on_jsonl_error))


def test_item_policy_counts_all_records_and_both_invalid_domains_once() -> None:
    seen: list[tuple[str, int]] = []
    iterator = Contract(int).iter_jsonl(
        ("{", '"bad"', "3", "4"),
        on_jsonl_error=lambda line, _error: seen.append(("jsonl", line)),
        on_error=lambda index, _error: seen.append(("validation", index)),
        item_policy=ItemPolicy(max_items=3, max_invalid_items=2),
    )

    assert next(iterator) == 3
    with pytest.raises(ResourceLimitError) as items:
        next(iterator)
    assert seen == [("jsonl", 1), ("validation", 1)]
    assert (items.value.code, items.value.limit, items.value.observed) == ("items", 3, 4)

    invalid = Contract(int).iter_jsonl(
        ("{", '"bad"', "3"),
        on_jsonl_error=lambda _line, _error: None,
        on_error=lambda _index, _error: None,
        item_policy=ItemPolicy(max_items=None, max_invalid_items=1),
    )
    with pytest.raises(ResourceLimitError) as invalid_items:
        next(invalid)
    assert (invalid_items.value.code, invalid_items.value.limit, invalid_items.value.observed) == (
        "invalid_items",
        1,
        2,
    )
    assert invalid_items.value.__cause__ is None
    assert invalid_items.value.__suppress_context__


def test_jsonl_byte_limits_count_utf8_and_terminators_before_decode() -> None:
    assert list(Contract(str).iter_jsonl(('"é"\n',), jsonl_policy=JsonlPolicy(max_line_bytes=5))) == ["é"]
    with pytest.raises(ResourceLimitError) as line:
        next(Contract(str).iter_jsonl(('"é"\n',), jsonl_policy=JsonlPolicy(max_line_bytes=4)))
    assert (line.value.code, line.value.limit, line.value.observed) == ("jsonl_line_size", 4, 5)

    records = (b"1\n", b"2\r\n")
    assert list(Contract(int).iter_jsonl(records, jsonl_policy=JsonlPolicy(max_total_bytes=6))) == [1, 2]
    with pytest.raises(ResourceLimitError) as total:
        list(Contract(int).iter_jsonl(records, jsonl_policy=JsonlPolicy(max_total_bytes=4)))
    assert (total.value.code, total.value.limit, total.value.observed) == ("jsonl_total_size", 4, 5)


def test_resource_failures_are_terminal_and_callbacks_cannot_suppress_them() -> None:
    framing: list[int] = []
    validation: list[int] = []
    iterator = Contract[list[int]](list[int]).iter_jsonl(
        ("[1,2]",),
        on_jsonl_error=lambda line, _error: framing.append(line),
        on_error=lambda index, _error: validation.append(index),
        policy=ResourcePolicy(max_nodes=2),
    )

    with pytest.raises(ResourceLimitError) as raised:
        next(iterator)
    assert raised.value.code == "nodes"
    assert framing == [] and validation == []


def test_source_callback_type_and_callback_exceptions_propagate() -> None:
    source_failure = OSError("source")
    callback_failure = RuntimeError("callback")

    def source() -> Iterator[str]:
        yield "1"
        raise source_failure

    iterator = Contract(int).iter_jsonl(source())
    assert next(iterator) == 1
    with pytest.raises(OSError) as source_error:
        next(iterator)
    assert source_error.value is source_failure

    def reject(_line: int, _error: JsonlError) -> None:
        raise callback_failure

    with pytest.raises(RuntimeError) as callback_error:
        next(Contract(int).iter_jsonl(("{",), on_jsonl_error=reject))
    assert callback_error.value is callback_failure

    with pytest.raises(TypeError, match="str or bytes"):
        next(Contract(int).iter_jsonl((1,)))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must not mix"):
        list(Contract(int).iter_jsonl(("1", b"2")))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="on_jsonl_error"):
        Contract(int).iter_jsonl((), on_jsonl_error=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="on_error"):
        Contract(int).iter_jsonl((), on_error=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="JsonlPolicy"):
        Contract(int).iter_jsonl((), jsonl_policy=object())  # type: ignore[arg-type]


def test_invalid_text_and_bytes_encoding_may_continue_without_decoder_context() -> None:
    failures: list[tuple[int, str]] = []

    def observe(line: int, error: JsonlError) -> None:
        assert sys.exception() is None
        failures.append((line, error.code))

    assert list(Contract(int).iter_jsonl(("\ud800", "1"), on_jsonl_error=observe)) == [1]
    assert list(Contract(int).iter_jsonl((b"\xff", b"2"), on_jsonl_error=observe)) == [2]
    assert failures == [(1, "invalid_encoding"), (1, "invalid_encoding")]

    with pytest.raises(JsonlError) as text:
        next(Contract(int).iter_jsonl(("\ud800",)))
    assert text.value.code == "invalid_encoding"

    with pytest.raises(JsonlError) as truncated:
        next(Contract(str).iter_jsonl((b'"\xf0\x9f"',)))
    assert truncated.value.code == "invalid_encoding"


def test_invalid_text_encoding_is_charged_to_raw_byte_budgets() -> None:
    records = ("\ud800", "\ud800")
    iterator = Contract(int).iter_jsonl(
        records,
        on_jsonl_error=lambda _line, _error: None,
        item_policy=ItemPolicy(max_invalid_items=None),
        jsonl_policy=JsonlPolicy(max_total_bytes=5),
    )

    with pytest.raises(ResourceLimitError) as raised:
        next(iterator)
    assert (raised.value.code, raised.value.limit, raised.value.observed) == ("jsonl_total_size", 5, 6)


@pytest.mark.parametrize("invalid", [None, 1, bytearray(b"1"), memoryview(b"1"), object()])
def test_unsupported_record_representations_are_never_stringified(invalid: object) -> None:
    with pytest.raises(TypeError) as raised:
        next(Contract(int).iter_jsonl((invalid,)))  # type: ignore[arg-type]
    assert "JSONL source items must be str or bytes" == str(raised.value)


def test_bom_and_source_failure_after_continuation_preserve_physical_progress() -> None:
    source_failure = RuntimeError("source stopped")
    seen: list[tuple[int, str]] = []

    def source() -> Iterator[str]:
        yield "1"
        yield "\ufeff2"
        yield "{"
        raise source_failure

    iterator = Contract(int).iter_jsonl(
        source(),
        on_jsonl_error=lambda line, error: seen.append((line, error.code)),
    )
    assert next(iterator) == 1
    with pytest.raises(RuntimeError) as raised:
        next(iterator)
    assert raised.value is source_failure
    assert seen == [(2, "bom"), (3, "invalid_json")]


def test_validation_callback_exception_propagates_unchanged() -> None:
    failure = LookupError("validation callback")

    def reject(_index: int, _error: ValidationError) -> None:
        raise failure

    with pytest.raises(LookupError) as raised:
        next(Contract(int).iter_jsonl(('"bad"',), on_error=reject))
    assert raised.value is failure


def test_infinite_sources_stop_at_their_canonical_item_or_invalid_budget() -> None:
    valid = Contract(int).iter_jsonl(
        itertools.repeat("1"),
        item_policy=ItemPolicy(max_items=3),
    )
    assert [next(valid), next(valid), next(valid)] == [1, 1, 1]
    with pytest.raises(ResourceLimitError) as items:
        next(valid)
    assert (items.value.code, items.value.observed) == ("items", 4)

    malformed = Contract(int).iter_jsonl(
        itertools.repeat("{"),
        on_jsonl_error=lambda _line, _error: None,
        item_policy=ItemPolicy(max_items=None, max_invalid_items=2),
    )
    with pytest.raises(ResourceLimitError) as invalid:
        next(malformed)
    assert (invalid.value.code, invalid.value.observed) == ("invalid_items", 3)


def test_byte_resources_remain_terminal_before_decode_and_count_rejections() -> None:
    callbacks: list[int] = []
    oversized = Contract(int).iter_jsonl(
        (b"{",),
        on_jsonl_error=lambda line, _error: callbacks.append(line),
        jsonl_policy=JsonlPolicy(max_line_bytes=1),
    )
    assert list(oversized) == []
    assert callbacks == [1]

    terminal = Contract(int).iter_jsonl(
        (b"{{",),
        on_jsonl_error=lambda line, _error: callbacks.append(line),
        jsonl_policy=JsonlPolicy(max_line_bytes=1),
    )
    with pytest.raises(ResourceLimitError) as line:
        next(terminal)
    assert line.value.code == "jsonl_line_size"
    assert callbacks == [1]

    total = Contract(int).iter_jsonl(
        ("{", "1"),
        on_jsonl_error=lambda line, _error: callbacks.append(line),
        jsonl_policy=JsonlPolicy(max_total_bytes=1),
    )
    with pytest.raises(ResourceLimitError) as aggregate:
        next(total)
    assert aggregate.value.code == "jsonl_total_size"
    assert callbacks == [1, 1]


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (" 1 ", 1),
        ("\t1\r", 1),
        ('"line 1\\nline 2"\n', "line 1\nline 2"),
        ('"é"', "é"),
        ('"😀"', "😀"),
    ],
)
def test_legitimate_json_whitespace_escapes_and_unicode(record: str, expected: object) -> None:
    assert next(Contract(type(expected)).iter_jsonl((record,))) == expected


def test_per_item_depth_and_error_limits_preserve_resource_semantics() -> None:
    with pytest.raises(ResourceLimitError) as depth:
        next(
            Contract[list[list[int]]](list[list[int]]).iter_jsonl(
                ("[[1]]",),
                policy=ResourcePolicy(max_depth=1),
            )
        )
    assert depth.value.code == "depth"

    class Wide(Spec):
        left: int
        right: int

    with pytest.raises(ValidationError) as errors:
        next(
            Contract(Wide).iter_jsonl(
                ('{"left":"bad","right":"bad"}',),
                policy=ResourcePolicy(max_errors=1),
            )
        )
    assert errors.value.truncated


def test_jsonl_controls_are_eager_without_source_consumption() -> None:
    events: list[str] = []

    def source() -> Iterator[str]:
        events.append("pulled")
        yield "1"

    contract = Contract(int)
    with pytest.raises(TypeError):
        contract.iter_jsonl(source(), on_error=object())  # type: ignore[arg-type]
    assert events == []
    with pytest.raises(TypeError):
        contract.iter_jsonl(source(), item_policy=object())  # type: ignore[arg-type]
    assert events == []


@given(
    st.lists(st.sampled_from(("valid", "malformed", "invalid")), max_size=25),
    st.integers(min_value=1, max_value=8),
)
def test_mixed_failure_differential_model_preserves_results_indexes_and_budget(
    kinds: list[str],
    invalid_limit: int,
) -> None:
    records = [
        str(index) if kind == "valid" else "{" if kind == "malformed" else '"bad"' for index, kind in enumerate(kinds)
    ]
    actual_values: list[int] = []
    actual_jsonl: list[int] = []
    actual_validation: list[int] = []
    actual_terminal: tuple[str, int] | None = None
    try:
        actual_values.extend(
            Contract(int).iter_jsonl(
                records,
                on_jsonl_error=lambda line, _error: actual_jsonl.append(line),
                on_error=lambda index, _error: actual_validation.append(index),
                item_policy=ItemPolicy(max_items=None, max_invalid_items=invalid_limit),
            )
        )
    except ResourceLimitError as error:
        actual_terminal = (error.code, error.observed)

    expected_values: list[int] = []
    expected_jsonl: list[int] = []
    expected_validation: list[int] = []
    expected_terminal: tuple[str, int] | None = None
    invalid_count = 0
    for index, kind in enumerate(kinds):
        if kind == "valid":
            expected_values.append(index)
            continue
        invalid_count += 1
        if invalid_count > invalid_limit:
            expected_terminal = ("invalid_items", invalid_count)
            break
        if kind == "malformed":
            expected_jsonl.append(index + 1)
        else:
            expected_validation.append(index)

    assert actual_values == expected_values
    assert actual_jsonl == expected_jsonl
    assert actual_validation == expected_validation
    assert actual_terminal == expected_terminal


def test_early_close_never_looks_ahead_or_closes_caller_source() -> None:
    closed = False
    pulled: list[int] = []

    def source() -> Iterator[str]:
        nonlocal closed
        try:
            pulled.append(1)
            yield "1"
            pulled.append(2)
            yield "2"
        finally:
            closed = True

    owned = source()
    wrapper = Contract(int).iter_jsonl(owned)
    assert next(wrapper) == 1
    wrapper.close()  # type: ignore[attr-defined]
    assert pulled == [1] and not closed
    assert next(owned) == "2"
    owned.close()
    assert closed


def test_structured_external_contracts_and_representation_compile_once() -> None:
    calls: list[str] = []

    class Identifier:
        def __init__(self, value: int) -> None:
            self.value = value

    def load_identifier(value: str) -> Identifier:
        calls.append(value)
        return Identifier(int(value))

    type IdentifierValue = Annotated[Identifier, Representation(input=str, load=load_identifier)]

    assert [item.identifier for item in Contract(Record).iter_jsonl(('{"identifier":1,"name":"Ada"}',))] == [1]
    assert [item.value for item in Contract(Event).iter_jsonl(('{"value":2}',))] == [2]
    assert list(Contract[Payload](Payload).iter_jsonl(('{"value":3}',))) == [{"value": 3}]
    represented = list(Contract[Identifier](IdentifierValue).iter_jsonl(('"4"', '"5"')))
    assert [item.value for item in represented] == [4, 5]
    assert calls == ["4", "5"]

    contract = Contract(Record)
    assert contract._artifacts.json_input is None
    assert len(list(contract.iter_jsonl(('{"identifier":1,"name":"a"}',)))) == 1
    artifact = contract._artifacts.json_input
    assert artifact is not None
    assert len(list(contract.iter_jsonl(('{"identifier":2,"name":"b"}',)))) == 1
    assert contract._artifacts.json_input is artifact


def test_alias_tagged_recursive_generic_and_sensitive_semantics_compose() -> None:
    class Migrated(Spec):
        value: Annotated[int, Alias("current", legacy=("old",))]

    class Created(Spec):
        kind: Literal["created"]
        value: int

    class Deleted(Spec):
        kind: Literal["deleted"]
        value: int

    type Change = Annotated[Created | Deleted, Discriminator("kind")]
    type Tree = int | list[Tree]

    assert [item.value for item in Contract(Migrated).iter_jsonl(('{"old":1}',))] == [1]
    with pytest.raises(ValidationError) as conflict:
        next(Contract(Migrated).iter_jsonl(('{"current":1,"old":2}',)))
    assert conflict.value.code == "alias_conflict"
    assert type(next(Contract[Change](Change).iter_jsonl(('{"kind":"deleted","value":2}',)))) is Deleted
    assert next(Contract[Tree](Tree).iter_jsonl(("[1,[2]]",))) == [1, [2]]
    assert next(Contract[list[int]](list[int]).iter_jsonl(("[1,2]",))) == [1, 2]

    type Secret = Annotated[str, Sensitive()]
    with pytest.raises(ValidationError) as secret:
        next(Contract[Secret](Secret).iter_jsonl(("1",)))
    assert secret.value.errors()[0]["input"] == "<redacted>"


def test_errors_callbacks_and_results_are_released_after_advancement() -> None:
    error_ref: weakref.ReferenceType[JsonlError] | None = None

    def observe(_line: int, error: JsonlError) -> None:
        nonlocal error_ref
        error_ref = weakref.ref(error)

    iterator = Contract(int).iter_jsonl(("{", "1"), on_jsonl_error=observe)
    assert next(iterator) == 1
    gc.collect()
    assert error_ref is not None and error_ref() is None

    def callback(_line: int, _error: JsonlError) -> None:
        pass

    callback_ref = weakref.ref(callback)
    exhausted = Contract(int).iter_jsonl(("1",), on_jsonl_error=callback)
    assert list(exhausted) == [1]
    del callback, exhausted
    gc.collect()
    assert callback_ref() is None


def test_independent_iterators_can_share_one_retained_contract_concurrently() -> None:
    contract = Contract(int)

    def consume(start: int) -> list[int]:
        return list(contract.iter_jsonl((str(start), str(start + 1))))

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(consume, (1, 3))) == [[1, 2], [3, 4]]


@given(st.lists(st.one_of(st.integers(), st.just("{")), max_size=30))
def test_jsonl_continuation_matches_a_small_independent_reference(values: list[int | str]) -> None:
    records = [str(value) if isinstance(value, int) else value for value in values]
    expected = [value for value in values if isinstance(value, int)]
    expected_lines = [index + 1 for index, value in enumerate(values) if isinstance(value, str)]
    actual_lines: list[int] = []

    actual = list(
        Contract(int).iter_jsonl(
            records,
            on_jsonl_error=lambda line, _error: actual_lines.append(line),
            item_policy=ItemPolicy(max_items=None, max_invalid_items=None),
        )
    )

    assert actual == expected
    assert actual_lines == expected_lines


def test_file_like_iterables_are_consumed_without_lifetime_ownership() -> None:
    text = io.StringIO("1\n2")
    binary = io.BytesIO(b"3\r\n4")

    assert list(Contract(int).iter_jsonl(text)) == [1, 2]
    assert list(Contract(int).iter_jsonl(binary)) == [3, 4]
    assert not text.closed and not binary.closed
