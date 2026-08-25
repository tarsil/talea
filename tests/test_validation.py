import inspect
from collections.abc import Callable
from typing import cast

import pytest

import talea
import talea.annotations
from talea.annotations import resolve_annotation
from talea.schema import (
    FixedTupleSchema,
    MappingSchema,
    PrimitiveSchema,
    Schema,
    SequenceKind,
    SequenceSchema,
    UnionSchema,
    VariadicTupleSchema,
)
from talea.validation import ValidationError, _identity_index, compile_validator


@pytest.mark.parametrize(
    ("schema", "value"),
    [
        (PrimitiveSchema("int"), 1),
        (PrimitiveSchema("float"), 1.5),
        (PrimitiveSchema("str"), "value"),
        (PrimitiveSchema("bool"), True),
        (PrimitiveSchema("bytes"), b"value"),
        (PrimitiveSchema("none"), None),
    ],
)
def test_primitive_validation_returns_original_value(schema: Schema, value: object) -> None:
    validator = compile_validator(schema)

    assert validator(value) is value


@pytest.mark.parametrize(
    ("schema", "value", "expected"),
    [
        (PrimitiveSchema("int"), True, "int"),
        (PrimitiveSchema("float"), 1, "float"),
        (PrimitiveSchema("str"), b"value", "str"),
        (PrimitiveSchema("bool"), 1, "bool"),
        (PrimitiveSchema("bytes"), "value", "bytes"),
        (PrimitiveSchema("none"), 0, "None"),
    ],
)
def test_primitive_validation_is_strict(schema: Schema, value: object, expected: str) -> None:
    with pytest.raises(ValidationError) as raised:
        compile_validator(schema)(value)

    error = raised.value
    assert error.expected == expected
    assert error.value is value
    assert error.received_type is type(value)
    assert error.location == ()


def test_validation_error_has_deterministic_root_description() -> None:
    error = ValidationError("int", "1", ())

    assert str(error) == "Validation failed at <root>: expected int, received str ('1')"


@pytest.mark.parametrize(
    ("annotation", "value"),
    [
        (list[int], [1, 2]),
        (set[int], {1, 2}),
        (frozenset[int], frozenset({1, 2})),
        (dict[str, int], {"one": 1}),
    ],
)
def test_containers_return_the_original_object(annotation: object, value: object) -> None:
    validator = compile_validator(resolve_annotation(annotation))

    assert validator(value) is value


@pytest.mark.parametrize(
    ("annotation", "value", "location", "expected"),
    [
        (list[int], [1, "2"], (1,), "int"),
        (set[int], {1, "2"}, ("2",), "int"),
        (frozenset[int], frozenset({1, "2"}), ("2",), "int"),
        (dict[str, int], {1: 2}, (1,), "str"),
        (dict[str, int], {"one": "1"}, ("one",), "int"),
    ],
)
def test_container_member_failures_report_location(
    annotation: object,
    value: object,
    location: tuple[object, ...],
    expected: str,
) -> None:
    with pytest.raises(ValidationError) as raised:
        compile_validator(resolve_annotation(annotation))(value)

    assert raised.value.location == location
    assert raised.value.expected == expected


@pytest.mark.parametrize(
    ("annotation", "value"),
    [
        (list[int], (1, 2)),
        (set[int], frozenset({1, 2})),
        (frozenset[int], {1, 2}),
        (dict[str, int], [("one", 1)]),
    ],
)
def test_container_types_are_not_converted(annotation: object, value: object) -> None:
    with pytest.raises(ValidationError) as raised:
        compile_validator(resolve_annotation(annotation))(value)

    assert raised.value.location == ()


def test_strict_validation_rejects_primitive_and_container_subclasses() -> None:
    class IntegerSubclass(int):
        pass

    class ListSubclass(list[int]):
        pass

    with pytest.raises(ValidationError):
        compile_validator(resolve_annotation(int))(IntegerSubclass(1))
    with pytest.raises(ValidationError):
        compile_validator(resolve_annotation(list[int]))(ListSubclass([1]))


def test_variadic_tuple_success_and_failure() -> None:
    value = (1, 2, 3)
    validator = compile_validator(resolve_annotation(tuple[int, ...]))

    assert validator(value) is value
    with pytest.raises(ValidationError) as raised:
        validator((1, "2", 3))

    assert raised.value.location == (1,)
    assert raised.value.expected == "int"

    with pytest.raises(ValidationError):
        validator([1, 2, 3])


@pytest.mark.parametrize(
    ("value", "location", "expected"),
    [
        ((1,), (), "tuple[int, str]"),
        (("1", "two"), (0,), "int"),
        ((1, 2), (1,), "str"),
    ],
)
def test_fixed_tuple_failures(value: object, location: tuple[object, ...], expected: str) -> None:
    validator = compile_validator(resolve_annotation(tuple[int, str]))

    with pytest.raises(ValidationError) as raised:
        validator(value)

    assert raised.value.location == location
    assert raised.value.expected == expected


def test_fixed_tuple_success_returns_original_object() -> None:
    value = (1, "two")

    assert compile_validator(resolve_annotation(tuple[int, str]))(value) is value


@pytest.mark.parametrize("value", [1, "one"])
def test_union_accepts_each_member(value: object) -> None:
    assert compile_validator(resolve_annotation(int | str))(value) is value


def test_union_failure_is_deterministic() -> None:
    validator = compile_validator(resolve_annotation(str | int))

    with pytest.raises(ValidationError) as raised:
        validator(1.5)

    assert raised.value.expected == "int | str"
    assert raised.value.location == ()


@pytest.mark.parametrize("value", [1, None])
def test_optional_accepts_value_and_none(value: object) -> None:
    assert compile_validator(resolve_annotation(int | None))(value) is value


def test_nested_union_failure_reports_outer_location() -> None:
    validator = compile_validator(resolve_annotation(list[int | None]))

    with pytest.raises(ValidationError) as raised:
        validator([1, None, "three"])

    assert raised.value.expected == "int | None"
    assert raised.value.location == (2,)


def test_nested_validation_reports_deep_location() -> None:
    value = [{"one": 1}, {"two": 2}, {"name": "wrong"}]
    validator = compile_validator(resolve_annotation(list[dict[str, int | None]]))

    with pytest.raises(ValidationError) as raised:
        validator(value)

    assert raised.value.location == (2, "name")
    assert str(raised.value) == ("Validation failed at [2]['name']: expected int | None, received str ('wrong')")


def test_nested_container_union_preserves_deepest_failure() -> None:
    schema = UnionSchema(
        frozenset(
            {
                SequenceSchema("list", PrimitiveSchema("int")),
                MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")),
            }
        )
    )
    validator = compile_validator(schema)

    with pytest.raises(ValidationError) as raised:
        validator([1, "wrong"])

    assert raised.value.expected == "int"
    assert raised.value.location == (1,)


def test_nested_container_union_reports_union_when_no_shape_matches() -> None:
    schema = UnionSchema(
        frozenset(
            {
                SequenceSchema("list", PrimitiveSchema("int")),
                MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")),
            }
        )
    )

    with pytest.raises(ValidationError) as raised:
        compile_validator(schema)("wrong")

    assert raised.value.expected == "dict[str, int] | list[int]"
    assert raised.value.location == ()


@pytest.mark.parametrize("value", [[1, 2], {"one": 1}])
def test_disjoint_container_union_success_does_not_construct_errors(value: object) -> None:
    schema = UnionSchema(
        frozenset(
            {
                SequenceSchema("list", PrimitiveSchema("int")),
                MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")),
            }
        )
    )
    validator = compile_validator(schema)

    def unexpected_error(*args: object) -> BaseException:
        raise AssertionError(f"successful validation constructed an error: {args!r}")

    validator.__globals__["ValidationError"] = unexpected_error
    assert validator(value) is value


def test_tuple_union_selects_only_matching_top_level_shape() -> None:
    schema = UnionSchema(
        frozenset(
            {
                VariadicTupleSchema(PrimitiveSchema("int")),
                SequenceSchema("list", PrimitiveSchema("int")),
            }
        )
    )
    value = (1, 2)

    assert compile_validator(schema)(value) is value


def test_nested_union_schema_compiles_without_frozenset_priority() -> None:
    inner = UnionSchema(frozenset({PrimitiveSchema("int"), PrimitiveSchema("str")}))
    outer = UnionSchema(frozenset({inner, PrimitiveSchema("bytes")}))

    assert compile_validator(outer)("value") == "value"


@pytest.mark.parametrize("value", [None, [1, 2]])
def test_optional_container_union_selects_none_or_container(value: object) -> None:
    assert compile_validator(resolve_annotation(list[int] | None))(value) is value


def test_nested_supported_combination_returns_original_object() -> None:
    value = ([1, 2], {"data": b"value"})
    annotation = tuple[list[int], dict[str, bytes]]

    assert compile_validator(resolve_annotation(annotation))(value) is value


def test_compilation_requires_only_schema_and_runtime_retains_no_schema() -> None:
    schema = resolve_annotation(list[dict[str, int | None]])
    validator = compile_validator(schema)
    del schema

    assert validator([{"one": 1, "none": None}]) == [{"one": 1, "none": None}]
    assert validator.__closure__ is None
    assert not any(
        isinstance(
            value, (PrimitiveSchema, SequenceSchema, MappingSchema, VariadicTupleSchema, FixedTupleSchema, UnionSchema)
        )
        for value in validator.__globals__.values()
    )


def test_runtime_validation_does_not_use_annotation_reflection(monkeypatch: pytest.MonkeyPatch) -> None:
    primitive = compile_validator(resolve_annotation(int))
    nested = compile_validator(resolve_annotation(list[dict[str, int | None]]))

    def fail_reflection(annotation: object) -> object:
        raise AssertionError(f"runtime reflected {annotation!r}")

    monkeypatch.setattr(talea.annotations, "get_origin", fail_reflection)
    monkeypatch.setattr(talea.annotations, "get_args", fail_reflection)

    assert primitive(1) == 1
    assert nested([{"one": 1, "none": None}]) == [{"one": 1, "none": None}]


def test_compiled_validator_has_single_argument_contract() -> None:
    validator = compile_validator(PrimitiveSchema("int"))

    assert isinstance(validator, Callable)
    assert list(inspect.signature(validator).parameters) == ["value"]
    assert validator.__doc__ is not None


def test_validator_compiler_is_not_exported_from_the_root_package() -> None:
    assert not hasattr(talea, "compile_validator")
    assert not hasattr(talea, "ValidationError")


def test_compiler_rejects_values_outside_the_schema_union() -> None:
    with pytest.raises(AssertionError):
        compile_validator(cast(Schema, object()))

    malformed = SequenceSchema("list", cast(Schema, object()))
    with pytest.raises(AssertionError):
        compile_validator(malformed)

    malformed_union = UnionSchema(frozenset({PrimitiveSchema("int"), cast(Schema, object())}))
    with pytest.raises(AssertionError):
        compile_validator(malformed_union)

    unsafe_kind = cast(SequenceKind, "list: raise RuntimeError")
    unsafe_sequence = SequenceSchema(unsafe_kind, PrimitiveSchema("int"))
    with pytest.raises(KeyError):
        compile_validator(unsafe_sequence)


def test_failure_location_lookup_rejects_a_missing_member() -> None:
    with pytest.raises(RuntimeError, match="validated sequence changed during validation"):
        _identity_index([], object())
