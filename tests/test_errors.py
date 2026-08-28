import dis
import json
from dataclasses import FrozenInstanceError
from typing import Annotated, Literal, cast
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

import talea
import talea.errors.models as error_models
import talea.errors.safety as error_safety
from talea import ErrorCode, Ge, Spec, ValidationError, check, field, transform
from talea.errors.models import _ErrorDetail
from talea.schema import resolve_annotation
from talea.validation import CustomValidationError, compile_validator


def test_error_code_vocabulary_is_public_stable_and_string_serializable() -> None:
    assert {code.value for code in ErrorCode} == {
        "type",
        "literal",
        "union",
        "missing",
        "unexpected",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "multiple_of",
        "min_length",
        "max_length",
        "pattern",
        "transform",
        "field_check",
        "spec_check",
        "factory",
        "representation_load",
        "representation_result",
        "json_invalid",
        "json_duplicate",
        "cycle",
        "discriminator_missing",
        "discriminator_unknown",
    }
    assert ErrorCode.TYPE == "type"
    assert json.dumps(ErrorCode.TYPE) == '"type"'


def test_structural_failure_has_immutable_truth_and_fresh_json_projection() -> None:
    class Payload(Spec):
        members: list[dict[str, int]]

    with pytest.raises(ValidationError) as raised:
        Payload(members=[{"identifier": "1"}])  # type: ignore[dict-item]

    error = raised.value
    expected = {
        "code": "type",
        "location": ["members", 0, "identifier"],
        "message": "Expected int",
        "expected": "int",
        "received": "str",
        "input": "1",
    }
    assert error.location == ("members", 0, "identifier")
    assert error.errors() == [expected]
    assert json.loads(json.dumps(error.errors())) == [expected]

    first = error.errors()
    first[0]["location"].append("changed")
    first[0]["message"] = "changed"
    assert error.errors() == [expected]
    with pytest.raises(FrozenInstanceError):
        error._details[0].location = ()  # type: ignore[misc]


def test_constraint_detail_separates_code_context_and_human_wording() -> None:
    class User(Spec):
        age: Annotated[int, Ge(18)]

    with pytest.raises(ValidationError) as raised:
        User(age=15)

    error = raised.value
    assert str(error) == "User\n  age\n    Expected value >= 18\n    received: 15 (int)"
    assert error.errors() == [
        {
            "code": "greater_than_or_equal",
            "location": ["age"],
            "message": "Expected value >= 18",
            "expected": "int satisfying Ge(18)",
            "received": "int",
            "input": 15,
            "context": {"limit": 18},
        }
    ]


@pytest.mark.parametrize(
    ("code", "context", "message"),
    [
        (ErrorCode.LITERAL, (), "Expected contract"),
        (ErrorCode.MISSING, (), "Required field is missing"),
        (ErrorCode.UNEXPECTED, (), "Unexpected field"),
        (ErrorCode.GREATER_THAN, (("limit", 1),), "Expected value > 1"),
        (ErrorCode.LESS_THAN, (("limit", 2),), "Expected value < 2"),
        (ErrorCode.LESS_THAN_OR_EQUAL, (("limit", 2),), "Expected value <= 2"),
        (ErrorCode.MULTIPLE_OF, (("multiple_of", 3),), "Expected a multiple of 3"),
        (ErrorCode.MIN_LENGTH, (("minimum", 1),), "Expected length >= 1"),
        (ErrorCode.MAX_LENGTH, (("maximum", 4),), "Expected length <= 4"),
        (ErrorCode.PATTERN, (("pattern", "^[a-z]+$"),), "Expected a match for pattern '^[a-z]+$'"),
        (ErrorCode.JSON_INVALID, (), "Invalid JSON input"),
        (ErrorCode.JSON_DUPLICATE, (), "Duplicate JSON object key"),
    ],
)
def test_every_structural_and_constraint_code_owns_its_message(
    code: ErrorCode,
    context: tuple[tuple[str, object], ...],
    message: str,
) -> None:
    error = ValidationError("contract", "bad", (), code, context=context)

    assert error.errors()[0]["message"] == message


def test_impossible_noncanonical_detail_code_is_rejected_by_message_projection() -> None:
    detail = _ErrorDetail(
        cast(ErrorCode, object()),
        (),
        None,
        None,
        None,
    )

    with pytest.raises(AssertionError, match="unknown canonical"):
        _ = detail.message


def test_literal_and_union_failures_have_distinct_codes_and_compact_branches() -> None:
    literal = compile_validator(resolve_annotation(Literal["open", "closed"]))
    union = compile_validator(resolve_annotation(int | UUID))

    with pytest.raises(ValidationError) as literal_failure:
        literal("other")
    with pytest.raises(ValidationError) as union_failure:
        union("hello")

    assert literal_failure.value.code is ErrorCode.LITERAL
    projected = union_failure.value.errors()[0]
    assert projected["code"] == "union"
    assert projected["location"] == []
    assert [(branch["label"], branch["errors"][0]["code"]) for branch in projected["branches"]] == [
        ("int", "type"),
        ("UUID", "type"),
    ]
    assert "alternatives:" in str(union_failure.value)


def test_structurally_plausible_union_branches_retain_deep_locations() -> None:
    class User(Spec):
        values: list[int]

    class Address(Spec):
        lines: list[str]

    user = User(values=[1])
    user.values.append("bad")  # type: ignore[arg-type]
    validator = compile_validator(resolve_annotation(User | Address))

    with pytest.raises(ValidationError) as raised:
        validator(user)

    projected = raised.value.errors()[0]
    assert projected["code"] == "union"
    assert len(projected["branches"]) == 1
    assert projected["branches"][0]["label"] == "User"
    assert projected["branches"][0]["errors"][0]["location"] == ["values", 1]


def test_custom_stages_share_the_public_interface_and_preserve_causes() -> None:
    transform_cause = ValueError("cannot parse\nprivate detail")
    field_cause = ValueError("not accepted")
    spec_cause = ValueError("not ordered")

    class Payload(Spec):
        start: int
        end: int

        @transform("start")
        def parse(start: object) -> object:
            if start == "bad":
                raise transform_cause
            return start

        @check("start")
        def accepted(start: int) -> None:
            if start == 0:
                raise field_cause

        @check("start", "end")
        def ordered(start: int, end: int) -> None:
            if end < start:
                raise spec_cause

    cases = (
        ({"start": "bad", "end": 1}, ErrorCode.TRANSFORM, "parse", transform_cause, [["start"]]),
        ({"start": 0, "end": 1}, ErrorCode.FIELD_CHECK, "accepted", field_cause, [["start"]]),
        ({"start": 2, "end": 1}, ErrorCode.SPEC_CHECK, "ordered", spec_cause, [["start"], ["end"]]),
    )
    for values, code, hook, cause, locations in cases:
        with pytest.raises(ValidationError) as raised:
            Payload(**values)  # type: ignore[arg-type]
        error = raised.value
        detail = error.errors()[0]
        assert isinstance(error, CustomValidationError)
        assert detail["code"] == code
        assert detail["hook"] == hook
        assert detail.get("locations", [detail["location"]]) == locations
        assert cause is error.__cause__
        assert str(cause) not in detail["message"]
        assert str(cause) not in str(error)
        assert hook in str(error)


def test_union_preserves_one_shared_custom_callback_cause() -> None:
    cause = ValueError("nested invariant")

    class Choice(Spec):
        values: list[int]

        @check("values")
        def nonempty(values: list[int]) -> None:
            if not values:
                raise cause

    choice = Choice(values=[1])
    choice.values.clear()
    validator = compile_validator(resolve_annotation(Choice | UUID))

    with pytest.raises(ValidationError) as raised:
        validator(choice)

    assert raised.value.code is ErrorCode.UNION
    assert raised.value.__cause__ is cause


def test_factory_execution_and_invalid_factory_output_are_distinct() -> None:
    factory_cause = RuntimeError("service unavailable")

    def unavailable() -> int:
        raise factory_cause

    class FailedFactory(Spec):
        value: int = field(default_factory=unavailable)

    class InvalidOutput(Spec):
        value: int = field(default_factory=lambda: "1")  # type: ignore[arg-type]

    with pytest.raises(ValidationError) as execution:
        FailedFactory()
    with pytest.raises(ValidationError) as output:
        InvalidOutput()

    assert execution.value.code is ErrorCode.FACTORY
    assert execution.value.__cause__ is factory_cause
    assert output.value.code is ErrorCode.TYPE
    assert output.value.__cause__ is None


def test_constructor_misuse_stays_native_and_field_validation_is_fail_fast() -> None:
    later_calls = 0

    class User(Spec):
        age: int
        email: str

        @transform("email")
        def observe(value: object) -> object:
            nonlocal later_calls
            later_calls += 1
            return value

    with pytest.raises(TypeError) as missing:
        User(age=1)  # type: ignore[missing-argument]
    with pytest.raises(TypeError) as unexpected:
        User(age=1, email="a@example.com", extra=True)  # type: ignore[unknown-argument]
    with pytest.raises(ValidationError) as invalid:
        User(age="bad", email=1)  # type: ignore[arg-type]

    assert not isinstance(missing.value, ValidationError)
    assert not isinstance(unexpected.value, ValidationError)
    assert invalid.value.location == ("age",)
    assert later_calls == 0


class _ExplodingRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr failed")


class _HugeRepr:
    def __repr__(self) -> str:
        return "x" * 100_000


@pytest.mark.parametrize(
    "value",
    [
        _ExplodingRepr(),
        _HugeRepr(),
        "x" * 10_000,
        b"x" * 10_000,
        "line one\nline two\x00",
    ],
)
def test_input_representation_is_bounded_safe_and_repeatable(value: object) -> None:
    error = ValidationError("int", value, ("payload",))

    first_render = str(error)
    first_projection = error.errors()
    assert first_render == str(error)
    assert first_projection == error.errors()
    assert len(first_render) < 1_000
    assert len(str(first_projection[0]["input"])) <= 160
    json.dumps(first_projection, ensure_ascii=False)


def test_recursive_containers_and_hostile_location_members_do_not_recurse_or_escape() -> None:
    recursive: list[object] = []
    recursive.append(recursive)
    error = ValidationError("int", recursive, (_ExplodingRepr(),))

    projected = error.errors()[0]
    assert len(projected["location"]) == 1
    assert isinstance(projected["location"][0], str)
    assert "[[...]]" in str(error) or "[...]" in str(error)
    json.dumps(projected)
    assert "[None]" in str(ValidationError("int", "bad", (None,)))


def test_representation_exceptions_and_control_characters_are_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(value: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(error_safety._REPR, "repr", fail)
    error = ValidationError("line\ncarriage\rtable\tcontrol\x00", object(), ("payload", 2, object()))

    rendered = str(error)
    assert "\\n" in rendered
    assert "\\r" in rendered
    assert "\\t" in rendered
    assert "\\x00" in rendered
    assert "<unrepresentable object: KeyboardInterrupt>" in rendered


def test_input_and_location_representations_are_captured_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def changing(value: object) -> str:
        nonlocal calls
        calls += 1
        return f"<changing {calls}>"

    monkeypatch.setattr(error_safety._REPR, "repr", changing)
    error = ValidationError("int", object(), ("payload", object()))
    captured_calls = calls

    assert str(error) == str(error)
    assert error.errors() == error.errors()
    assert calls == captured_calls


def test_multiple_canonical_details_render_a_count_without_sharing_projection_state() -> None:
    first = ValidationError("int", "one", ("first",))
    second = ValidationError("str", 2, ("second",))
    combined = ValidationError.__new__(ValidationError)
    combined._initialize(
        (*first._details, *second._details),
        (first.value, second.value),
        "Payload",
    )

    assert str(combined).startswith("Payload (2 errors)\n")
    assert [detail["location"] for detail in combined.errors()] == [["first"], ["second"]]

    with pytest.raises(ValueError, match="at least one failure"):
        ValidationError._aggregate((), title="Payload")


@given(st.text(max_size=300))
def test_unicode_input_always_has_json_compatible_error_projection(value: str) -> None:
    validator = compile_validator(resolve_annotation(int))

    with pytest.raises(ValidationError) as raised:
        validator(value)

    json.dumps(raised.value.errors(), ensure_ascii=False)


def test_success_paths_do_not_snapshot_or_allocate_error_collections(monkeypatch: pytest.MonkeyPatch) -> None:
    class Point(Spec):
        x: int
        y: int

    def forbidden(value: object) -> object:
        raise AssertionError(f"successful validation represented {value!r}")

    monkeypatch.setattr(error_models, "snapshot_input", forbidden)
    point = Point(x=1, y=2)
    initializer = vars(Point)["__init__"]
    opnames = {instruction.opname for instruction in dis.get_instructions(initializer)}

    assert (point.x, point.y) == (1, 2)
    assert "BUILD_LIST" not in opnames
    assert "errors" not in initializer.__code__.co_names


def test_public_root_exposes_handling_api_without_internal_implementations() -> None:
    assert talea.ValidationError is ValidationError
    assert talea.ErrorCode is ErrorCode
    assert talea.ErrorData.__name__ == "ErrorData"
    assert not hasattr(talea, "CustomValidationError")
    assert not hasattr(talea, "_ErrorDetail")
    assert _ErrorDetail.__module__ == "talea.errors.models"
