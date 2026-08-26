import dis
import math
import re
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from decimal import Decimal
from typing import Annotated, cast
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

from talea import Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern, Spec
from talea.constraints import Constraint
from talea.schema import (
    AnnotationResolutionError,
    ConstrainedSchema,
    ConstraintDeclarationError,
    PrimitiveSchema,
    resolve_annotation,
)
from talea.schema.resolution import _apply_constraints
from talea.validation import ValidationError, compile_validator
from talea.validation.emission import _GeneratedNames, _ValidationEmitter
from talea.validation.failure_contracts import (
    constraint_code,
    constraint_context,
    constraint_label,
)

CONSTRAINT_TYPES = (Gt, Ge, Lt, Le, MultipleOf, MinLength, MaxLength, Pattern)


@pytest.mark.parametrize(
    "constraint",
    [Gt(0), Ge(0), Lt(10), Le(10), MultipleOf(2), MinLength(1), MaxLength(5), Pattern("value")],
)
def test_constraint_declarations_are_immutable_compact_and_reusable(constraint: Constraint) -> None:
    assert not hasattr(constraint, "__dict__")
    assert hash(constraint) == hash(constraint)
    with pytest.raises(FrozenInstanceError):
        if isinstance(constraint, Pattern):
            constraint.pattern = "other"  # type: ignore[misc]
        else:
            constraint.value = 2  # type: ignore[unresolved-attribute]


@pytest.mark.parametrize(
    "construction",
    [
        lambda: Ge(True),
        lambda: Gt(complex(1, 2)),
        lambda: Le(float("nan")),
        lambda: Lt(float("inf")),
        lambda: Ge(Decimal("NaN")),
        lambda: MultipleOf(0),
        lambda: MultipleOf(-0.0),
        lambda: MinLength(True),
        lambda: MinLength(-1),
        lambda: MaxLength(1.5),
        lambda: MaxLength(-1),
    ],
)
def test_invalid_constraint_declarations_fail_immediately(construction: Callable[[], object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        construction()


def test_pattern_accepts_string_or_compiled_string_expression_and_rejects_invalid_forms() -> None:
    from_string = Pattern(r"^value\d+$")
    compiled = re.compile(r"^value\d+$", re.IGNORECASE)
    from_compiled = Pattern(compiled)

    assert from_string.compiled.pattern == r"^value\d+$"
    assert from_compiled.compiled is compiled
    with pytest.raises(re.error):
        Pattern("[")
    with pytest.raises(TypeError):
        Pattern(re.compile(b"bytes"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Pattern(1)  # type: ignore[arg-type]


def test_annotated_constraints_normalize_redundancy_and_nested_layers() -> None:
    schema = resolve_annotation(
        Annotated[
            Annotated[int, Ge(0), Ge(10), MultipleOf(-2)],
            Le(100),
            Le(50),
            MultipleOf(2),
        ]
    )

    assert schema == ConstrainedSchema(
        PrimitiveSchema("int"),
        (Ge(10), Le(50), MultipleOf(2)),
    )

    combined = _apply_constraints(ConstrainedSchema(PrimitiveSchema("int"), (Ge(0),)), (Le(10),))
    assert combined == ConstrainedSchema(PrimitiveSchema("int"), (Ge(0), Le(10)))


def test_union_options_that_resolve_identically_collapse_to_one_schema() -> None:
    assert resolve_annotation(Annotated[int, "external"] | int) == PrimitiveSchema("int")


@pytest.mark.parametrize(
    "annotation",
    [
        Annotated[int, Ge(10), Le(5)],
        Annotated[int, Gt(10), Lt(11)],
        Annotated[int, Ge(10), Lt(10)],
        Annotated[float, Gt(10.0), Le(10.0)],
        Annotated[Decimal, Ge(Decimal("10")), Lt(Decimal("10"))],
        Annotated[str, MinLength(10), MaxLength(2)],
        Annotated[tuple[int, str], MinLength(3)],
    ],
)
def test_obvious_constraint_contradictions_fail_during_resolution(annotation: object) -> None:
    with pytest.raises(ConstraintDeclarationError, match="contradict"):
        resolve_annotation(annotation)


def test_inclusive_single_point_ranges_and_fixed_tuple_lengths_are_legal() -> None:
    integer = resolve_annotation(Annotated[int, Ge(10), Le(10)])
    fixed = resolve_annotation(Annotated[tuple[int, str], MinLength(2), MaxLength(2)])

    assert compile_validator(integer)(10) == 10
    assert fixed == resolve_annotation(tuple[int, str])


@pytest.mark.parametrize(
    "annotation",
    [
        Annotated[int, Pattern("x")],
        Annotated[int, MinLength(1)],
        Annotated[str, Ge(1)],
        Annotated[list[int], Ge(1)],
        Annotated[UUID, MinLength(2)],
        Annotated[object, Ge(1)],
    ],
)
def test_constraint_applicability_is_enforced_during_resolution(annotation: object) -> None:
    with pytest.raises((AnnotationResolutionError, ConstraintDeclarationError)):
        resolve_annotation(annotation)


def test_numeric_constraint_boundary_behavior_and_codes() -> None:
    validator = compile_validator(resolve_annotation(Annotated[int, Gt(-2), Le(2), MultipleOf(2)]))

    assert validator(0) == 0
    assert validator(2) == 2
    for value, code in [(-2, "greater_than"), (3, "less_than_or_equal"), (1, "multiple_of")]:
        with pytest.raises(ValidationError) as raised:
            validator(value)
        assert raised.value.code == code

    strict_upper = compile_validator(resolve_annotation(Annotated[int, Lt(2)]))
    assert strict_upper(1) == 1
    with pytest.raises(ValidationError) as raised:
        strict_upper(2)
    assert raised.value.code == "less_than"


def test_float_special_values_and_multiple_semantics_are_explicit() -> None:
    ordinary = compile_validator(resolve_annotation(float))
    lower = compile_validator(resolve_annotation(Annotated[float, Ge(0.0)]))
    upper = compile_validator(resolve_annotation(Annotated[float, Le(0.0)]))
    multiple = compile_validator(resolve_annotation(Annotated[float, MultipleOf(0.1)]))

    assert math.isnan(ordinary(float("nan")))
    assert ordinary(float("inf")) == float("inf")
    assert lower(float("inf")) == float("inf")
    assert upper(float("-inf")) == float("-inf")
    assert multiple(0.3) == 0.3
    for validator, value in [
        (lower, float("nan")),
        (lower, float("-inf")),
        (upper, float("inf")),
        (multiple, float("nan")),
        (multiple, float("inf")),
        (multiple, 0.31),
    ]:
        with pytest.raises(ValidationError):
            validator(value)


def test_decimal_constraints_use_exact_family_and_context_independent_multiple_checks() -> None:
    validator = compile_validator(
        resolve_annotation(Annotated[Decimal, Gt(Decimal("0")), Le(Decimal("1")), MultipleOf(Decimal("0.1"))])
    )

    value = Decimal("0.3")
    assert validator(value) is value
    for rejected in (Decimal("0.31"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValidationError):
            validator(rejected)
    with pytest.raises(ConstraintDeclarationError, match="exact Decimal"):
        resolve_annotation(Annotated[Decimal, Ge(0)])


@pytest.mark.parametrize(
    ("annotation", "accepted", "short", "long"),
    [
        (str, "abc", "a", "abcde"),
        (bytes, b"abc", b"a", b"abcde"),
        (list[int], [1, 2, 3], [1], [1, 2, 3, 4, 5]),
        (set[int], {1, 2, 3}, {1}, {1, 2, 3, 4, 5}),
        (frozenset[int], frozenset({1, 2, 3}), frozenset({1}), frozenset(range(5))),
        (dict[str, int], {"a": 1, "b": 2, "c": 3}, {"a": 1}, dict.fromkeys("abcde", 1)),
        (tuple[int, ...], (1, 2, 3), (1,), (1, 2, 3, 4, 5)),
    ],
)
def test_length_constraints_apply_before_supported_container_contents(
    annotation: object,
    accepted: object,
    short: object,
    long: object,
) -> None:
    constrained = Annotated[annotation, MinLength(2), MaxLength(4)]
    validator = compile_validator(resolve_annotation(constrained))

    assert validator(accepted) is accepted
    for value, code in ((short, "min_length"), (long, "max_length")):
        with pytest.raises(ValidationError) as raised:
            validator(value)
        assert raised.value.code == code


def test_pattern_uses_search_supports_unicode_and_never_recompiles_at_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declaration = Pattern(re.compile(r"café\s+\d+", re.IGNORECASE))
    validator = compile_validator(resolve_annotation(Annotated[str, MinLength(4), declaration]))

    monkeypatch.setattr(re, "compile", lambda *args, **kwargs: pytest.fail("runtime regex compilation"))
    assert validator("CAFÉ 42") == "CAFÉ 42"
    with pytest.raises(ValidationError) as raised:
        validator("tea 42")
    assert raised.value.code == "pattern"
    assert any(value is declaration.compiled for value in validator.__globals__.values())


def test_unknown_annotated_metadata_is_ignored_without_execution_or_retention() -> None:
    class Metadata:
        def __repr__(self) -> str:
            raise AssertionError("metadata repr executed")

        def validate(self, value: object) -> object:
            raise AssertionError("metadata executed")

    metadata = Metadata()
    schema = resolve_annotation(Annotated[int, metadata])
    validator = compile_validator(schema)

    assert schema == PrimitiveSchema("int")
    assert validator(1) == 1
    assert metadata not in validator.__globals__.values()
    with pytest.raises(AnnotationResolutionError):
        resolve_annotation(Ge(0))


def test_constraints_compose_with_specs_nested_items_and_failure_locations() -> None:
    class Payload(Spec):
        score: Annotated[int, Ge(0), Le(100)]
        tags: Annotated[list[Annotated[str, MinLength(2)]], MinLength(1)]

    payload = Payload(score=50, tags=["ok"])

    assert payload.score == 50
    with pytest.raises(ValidationError) as raised:
        Payload(score=50, tags=["x"])
    assert raised.value.location == ("tags", 0)
    assert raised.value.code == "min_length"


def test_constrained_override_covariance_accepts_narrowing_and_rejects_widening() -> None:
    class Base(Spec):
        score: Annotated[int, Ge(0), Le(100)]
        name: Annotated[str, MinLength(2), MaxLength(20)]
        even: Annotated[int, MultipleOf(2)]
        amount: Annotated[Decimal, MultipleOf(Decimal("0.1"))]
        ratio: Annotated[float, MultipleOf(0.1)]
        code: Annotated[str, Pattern(r"^[A-Z]+$")]

    class Narrow(Base):
        score: Annotated[int, Gt(10), Le(90)]
        name: Annotated[str, MinLength(5), MaxLength(10)]
        even: Annotated[int, MultipleOf(4)]
        amount: Annotated[Decimal, MultipleOf(Decimal("0.2"))]
        ratio: Annotated[float, Ge(0.0), MultipleOf(0.1)]
        code: Annotated[str, MinLength(2), Pattern(r"^[A-Z]+$")]

    assert (
        Narrow(
            score=12,
            name="valid",
            even=8,
            amount=Decimal("0.4"),
            ratio=0.2,
            code="OK",
        ).score
        == 12
    )
    with pytest.raises(TypeError, match="not type-compatible"):

        class WiderLower(Base):
            score: Annotated[int, Ge(-1), Le(100)]

    with pytest.raises(TypeError, match="not type-compatible"):

        class RemovedConstraint(Base):
            name: str

    with pytest.raises(TypeError, match="not type-compatible"):

        class IncompatibleMultiple(Base):
            even: Annotated[int, MultipleOf(3)]

    with pytest.raises(TypeError, match="not type-compatible"):

        class UnprovableFloatMultiple(Base):
            ratio: Annotated[float, MultipleOf(0.2)]


def test_constraints_preserve_permanent_trust_and_mutable_boundary_revalidation() -> None:
    class Immutable(Spec):
        score: Annotated[int, Ge(0)]

    class Basket(Spec):
        items: Annotated[list[int], MinLength(1)]

    class Order(Spec):
        basket: Basket

    immutable_schema = vars(Immutable)["__talea_artifacts__"].schema
    basket = Basket(items=[1])
    basket.items.clear()

    assert immutable_schema.instances_are_permanently_trusted is True
    with pytest.raises(ValidationError) as raised:
        Order(basket=basket)
    assert raised.value.location == ("basket", "items")
    assert raised.value.code == "min_length"


def test_generated_constraints_bind_values_safely_and_avoid_name_collisions() -> None:
    class Generated(Spec):
        _talea_bound_1: Annotated[int, Ge(0)]
        text: Annotated[str, Pattern("['\"\\\n]+")]

    generated = Generated(_talea_bound_1=1, text="'")
    initializer = vars(Generated)["__init__"]

    assert generated._talea_bound_1 == 1
    assert "raise" not in initializer.__code__.co_names
    assert not any(isinstance(value, CONSTRAINT_TYPES) for value in initializer.__globals__.values())


def test_constrained_unions_use_specialized_top_level_selection() -> None:
    class Choice(Spec):
        value: Annotated[int, Ge(0)] | str

    assert Choice(value=1).value == 1
    assert Choice(value="one").value == "one"


@pytest.mark.parametrize(
    ("constraint", "code"),
    [
        (Gt(0), "greater_than"),
        (Ge(0), "greater_than_or_equal"),
        (Lt(1), "less_than"),
        (Le(1), "less_than_or_equal"),
        (MultipleOf(1), "multiple_of"),
        (MinLength(1), "min_length"),
        (MaxLength(1), "max_length"),
        (Pattern("x"), "pattern"),
    ],
)
def test_constraint_failure_codes_are_owned_by_constraint_types(constraint: object, code: str) -> None:
    assert constraint_code(constraint) == code


def test_emitter_rejects_values_outside_canonical_constraint_and_schema_unions() -> None:
    malformed_constraint = ConstrainedSchema(
        PrimitiveSchema("int"),
        cast(tuple[Constraint, ...], (object(),)),
    )
    malformed_multiple = ConstrainedSchema(
        PrimitiveSchema("bool"),
        (MultipleOf(1),),
    )
    for schema in (malformed_constraint, malformed_multiple):
        with pytest.raises(AssertionError):
            compile_validator(schema)

    emitter = _ValidationEmitter([], _GeneratedNames(), {})
    with pytest.raises(AssertionError):
        emitter.top_level_condition(cast(object, object()), "value")  # type: ignore[arg-type]
    with pytest.raises(AssertionError):
        constraint_label(object())
    with pytest.raises(AssertionError):
        constraint_code(object())
    with pytest.raises(AssertionError):
        constraint_context(object())


def test_unconstrained_spec_bytecode_pays_no_campaign_6_feature_tax() -> None:
    class Point(Spec):
        x: int
        y: int

    initializer = vars(Point)["__init__"]
    names = set(initializer.__code__.co_names)
    opnames = {instruction.opname for instruction in dis.get_instructions(initializer)}

    assert names.isdisjoint({"constraints", "registry", "adapter", "search", "isfinite", "remainder"})
    assert not any(isinstance(value, CONSTRAINT_TYPES) for value in initializer.__globals__.values())
    assert "FOR_ITER" not in opnames
    assert Point(x=1, y=2).x == 1


@given(lower=st.integers(-10_000, 10_000), width=st.integers(0, 1_000))
def test_generated_integer_ranges_preserve_every_boundary(lower: int, width: int) -> None:
    upper = lower + width
    validator = compile_validator(resolve_annotation(Annotated[int, Ge(lower), Le(upper)]))

    assert validator(lower) == lower
    assert validator(upper) == upper
    with pytest.raises(ValidationError):
        validator(lower - 1)
    with pytest.raises(ValidationError):
        validator(upper + 1)


@given(minimum=st.integers(0, 20), extra=st.integers(0, 20))
def test_generated_length_ranges_preserve_every_boundary(minimum: int, extra: int) -> None:
    maximum = minimum + extra
    validator = compile_validator(resolve_annotation(Annotated[list[int], MinLength(minimum), MaxLength(maximum)]))

    assert validator([0] * minimum) == [0] * minimum
    assert validator([0] * maximum) == [0] * maximum
    if minimum:
        with pytest.raises(ValidationError):
            validator([0] * (minimum - 1))
    with pytest.raises(ValidationError):
        validator([0] * (maximum + 1))
