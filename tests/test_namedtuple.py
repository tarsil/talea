from __future__ import annotations

from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import (
    Annotated,
    Generic,
    NamedTuple,
    NotRequired,
    ReadOnly as TypingReadOnly,
    Required,
    TypeVar,
)

import pytest
from hypothesis import given, strategies as st
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi

from talea import Alias, Contract, Ge, ReadOnly, Sensitive, WriteOnly
from talea.declaration.policies import (
    _schema_directions_are_available,
    schema_contains_representation,
    schema_contains_sensitive_metadata,
    schema_contains_tagged_union,
    schema_values_are_immutable,
)
from talea.introspection import _representation_infos, inspect_contract
from talea.json_schema.projection import OPENAPI_DIALECT, _StandardsProjector
from talea.metadata import EMPTY_METADATA
from talea.schema import NamedReferenceSchema, NamedTupleField, NamedTupleSchema, PrimitiveSchema
from talea.schema.resolution import _resolve_named_tuple
from talea.validation import ValidationError


class Point(NamedTuple):
    x: int
    y: str


class Record(NamedTuple):
    required: int
    optional: str = "default"


T = TypeVar("T")


class Pair(NamedTuple, Generic[T]):
    first: T
    second: T


class Node(NamedTuple):
    value: int
    children: tuple[Node, ...] = ()


class MutualLeft(NamedTuple):
    right: MutualRight | None


class MutualRight(NamedTuple):
    left: MutualLeft | None


type PointAlias = Point


def test_schema_is_the_single_immutable_positional_owner() -> None:
    schema = Contract(Record)._artifacts.schema

    assert isinstance(schema, NamedTupleSchema)
    assert schema.named_tuple_type is Record
    assert tuple(field.name for field in schema.fields) == ("required", "optional")
    assert tuple(field.schema for field in schema.fields) == (PrimitiveSchema("int"), PrimitiveSchema("str"))
    assert schema.required_count == 1
    assert schema.fields[0].has_default is False
    assert schema.fields[1].has_default is True
    assert schema.fields[1].default == "default"
    assert inspect_contract(Contract(Record)).schema == schema
    with pytest.raises(AttributeError):
        schema.required_count = 2  # type: ignore[misc]


def test_detection_excludes_unannotated_and_derived_tuple_classes() -> None:
    Legacy = namedtuple("Legacy", "x y")

    class TupleSubclass(tuple):
        pass

    class PointSubclass(Point):
        pass

    for annotation in (Legacy, TupleSubclass, PointSubclass):
        with pytest.raises(TypeError, match="Unsupported annotation"):
            Contract(annotation)


def test_strict_validation_requires_exact_identity_and_preserves_it() -> None:
    contract = Contract(Point)
    point = Point(1, "north")

    assert contract.validate(point) is point
    for value in ((1, "north"), [1, "north"]):
        with pytest.raises(ValidationError) as captured:
            contract.validate(value)  # type: ignore[arg-type]
        assert captured.value.errors()[0]["location"] == []
        assert captured.value.errors()[0]["code"] == "type"

    class Other(NamedTuple):
        x: int
        y: str

    with pytest.raises(ValidationError):
        contract.validate(Other(1, "north"))  # type: ignore[arg-type]


def test_strict_slots_are_revalidated_with_positional_locations() -> None:
    invalid = Point(1, 2)  # type: ignore[arg-type]

    with pytest.raises(ValidationError) as captured:
        Contract(Point).validate(invalid)

    assert captured.value.errors() == [
        {
            "code": "type",
            "location": [1],
            "message": "Expected str",
            "expected": "str",
            "received": "int",
            "input": 2,
        }
    ]


@pytest.mark.parametrize("items", ((1,), (1, "north", "extra")))
def test_forged_exact_instances_with_invalid_arity_fail_closed(items: tuple[object, ...]) -> None:
    forged = tuple.__new__(Point, items)
    contract = Contract(Point)

    for operation in (contract.validate, contract.from_python, contract.to_python):
        with pytest.raises(ValidationError) as captured:
            operation(forged)
        assert captured.value.errors()[0]["code"] == "type"
        assert captured.value.errors()[0]["location"] == []


@pytest.mark.parametrize("value", ([1, "east"], (1, "east")))
def test_external_python_accepts_only_concrete_positional_families(value: object) -> None:
    result = Contract(Point).from_python(value)

    assert type(result) is Point
    assert result == Point(1, "east")


@pytest.mark.parametrize("value", ({"x": 1, "y": "east"}, "1,east", b"1,east"))
def test_external_python_rejects_non_positional_boundaries(value: object) -> None:
    with pytest.raises(ValidationError) as captured:
        Contract(Point).from_python(value)

    assert captured.value.errors()[0]["code"] == "type"
    assert captured.value.errors()[0]["location"] == []


def test_external_input_rejects_subclasses_without_invoking_custom_sequence_protocols() -> None:
    calls = 0

    class HostileList(list[object]):
        def __len__(self) -> int:
            nonlocal calls
            calls += 1
            return super().__len__()

        def __getitem__(self, index: int) -> object:
            nonlocal calls
            calls += 1
            return super().__getitem__(index)

    with pytest.raises(ValidationError):
        Contract(Point).from_python(HostileList([1, "north"]))
    assert calls == 0


def test_arity_defaults_and_nullable_values_are_distinct() -> None:
    class OptionalValue(NamedTuple):
        value: int | None
        label: str = "ready"

    contract = Contract(OptionalValue)

    assert contract.from_python([None]) == OptionalValue(None, "ready")
    assert contract.from_python([1, "set"]) == OptionalValue(1, "set")
    with pytest.raises(ValidationError) as missing:
        contract.from_python([])
    with pytest.raises(ValidationError) as extra:
        contract.from_python([1, "set", False])
    assert missing.value.errors()[0]["location"] == [0]
    assert missing.value.errors()[0]["code"] == "missing"
    assert extra.value.errors()[0]["location"] == [2]
    assert extra.value.errors()[0]["code"] == "unexpected"


def test_defaults_validate_at_compilation_and_again_when_mutated_before_use() -> None:
    class InvalidDefault(NamedTuple):
        value: int = "bad"  # type: ignore[assignment]

    with pytest.raises(TypeError, match="field 'value' has an invalid default"):
        Contract(InvalidDefault)

    default: list[int] = []

    class MutableDefault(NamedTuple):
        values: list[int] = default

    contract = Contract(MutableDefault)
    default.append("bad")  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as captured:
        contract.from_python([])
    assert captured.value.errors()[0]["location"] == [0, 0]


def test_constructor_runs_exactly_once_after_slots_validate() -> None:
    class Counted(NamedTuple):
        value: int

    contract = Contract(Counted)
    generated = Counted.__new__
    calls = 0

    def counting(cls: type[Counted], value: int) -> Counted:
        nonlocal calls
        calls += 1
        return generated(cls, value)

    Counted.__new__ = staticmethod(counting)  # type: ignore[method-assign]
    try:
        result = contract.from_python([1])
        assert type(result) is Counted
        assert tuple(result) == (1,)
        assert calls == 1
    finally:
        Counted.__new__ = staticmethod(generated)  # type: ignore[method-assign]


def test_mutated_constructor_is_rejected_at_schema_creation() -> None:
    class Mutated(NamedTuple):
        value: int

    original = Mutated.__new__
    Mutated.__new__ = staticmethod(lambda cls, value: original(cls, value))  # type: ignore[method-assign]
    with pytest.raises(TypeError, match="incompatible constructor"):
        Contract(Mutated)


def test_generic_specializations_and_open_generic_policy() -> None:
    integers = Contract(Pair[int])
    strings = Contract(Pair[str])

    assert integers.from_python([1, 2]) == Pair(1, 2)
    assert strings.from_json('["a", "b"]') == Pair("a", "b")
    assert integers._artifacts.schema != strings._artifacts.schema
    with pytest.raises(TypeError, match="Unsupported annotation"):
        Contract(Pair)


def test_recursive_namedtuple_reuses_named_reference_graph() -> None:
    contract = Contract(Node)
    schema = contract._artifacts.schema

    assert isinstance(schema, NamedTupleSchema)
    children = schema.fields[1].schema
    assert isinstance(children.item, NamedReferenceSchema)  # type: ignore[union-attr]
    expected = Node(1, (Node(2),))
    assert contract.from_python([1, ([2],)]) == expected
    assert contract.from_json("[1, [[2]]]") == expected
    assert contract.to_python(expected) == (1, ((2, ()),))


def test_pep695_alias_and_mutual_recursion_reuse_named_identity() -> None:
    assert Contract(PointAlias).from_python([1, "north"]) == Point(1, "north")

    contract = Contract(MutualLeft)
    assert contract.from_python([[None]]) == MutualLeft(MutualRight(None))
    document = contract.json_schema()
    assert document["$defs"]["MutualLeft"]["prefixItems"][0]["anyOf"][1] == {  # type: ignore[index]
        "$ref": "#/$defs/MutualRight"
    }
    assert document["$defs"]["MutualRight"]["prefixItems"][0]["anyOf"][1] == {  # type: ignore[index]
        "$ref": "#/$defs/MutualLeft"
    }


def test_json_and_python_outputs_are_positional() -> None:
    contract = Contract(Point)
    point = Point(1, "west")

    result = contract.to_python(point)
    assert type(result) is tuple
    assert result == (1, "west")
    assert contract.to_json(point) == '[1,"west"]'


def test_json_schema_and_openapi_reuse_array_projection() -> None:
    contract = Contract(Record)
    document = contract.json_schema()
    definition = document["$defs"]["Record"]  # type: ignore[index]

    assert definition == {
        "type": "array",
        "prefixItems": [{"type": "integer"}, {"type": "string", "default": "default"}],
        "items": False,
        "minItems": 1,
        "maxItems": 2,
    }
    validator = Draft202012Validator(document)
    assert validator.is_valid([1])
    assert validator.is_valid([1, "set"])
    assert not validator.is_valid([])
    assert not validator.is_valid([1, "set", False])
    openapi = contract.openapi_schema()
    assert openapi["components"]["schemas"]["Record"] == definition  # type: ignore[index]
    validate_openapi(
        {
            "openapi": "3.1.2",
            "jsonSchemaDialect": OPENAPI_DIALECT,
            "info": {"title": "NamedTuple projection", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {
                    **openapi["components"]["schemas"],  # type: ignore[index]
                    "ProjectionRoot": openapi["schema"],
                }
            },
        }
    )


def test_empty_namedtuple_projects_without_invalid_empty_prefix_items() -> None:
    class Empty(NamedTuple):
        pass

    document = Contract(Empty).json_schema()
    definition = document["$defs"]["Empty"]  # type: ignore[index]

    assert definition == {"type": "array", "items": False, "minItems": 0, "maxItems": 0}
    Draft202012Validator.check_schema(document)


def test_large_arity_execution_scales_through_one_hundred_slots() -> None:
    Fifty = NamedTuple(  # ty: ignore[invalid-named-tuple]
        "Fifty", [(f"field_{index}", int) for index in range(50)]
    )
    Hundred = NamedTuple(  # ty: ignore[invalid-named-tuple]
        "Hundred", [(f"field_{index}", int) for index in range(100)]
    )

    for declared, size in ((Fifty, 50), (Hundred, 100)):
        values = list(range(size))
        contract = Contract(declared)
        result = contract.from_python(values)
        assert type(result) is declared
        assert tuple(result) == tuple(values)
        assert contract.validate(result) is result
        definition = contract.json_schema()["$defs"][declared.__name__]  # type: ignore[index]
        assert definition["minItems"] == size  # type: ignore[index]
        assert definition["maxItems"] == size  # type: ignore[index]


def test_retained_contract_is_safe_for_concurrent_first_use_and_execution() -> None:
    contract = Contract(Point)

    with ThreadPoolExecutor(max_workers=8) as executor:
        first = tuple(executor.map(contract.from_python, ([index, str(index)] for index in range(32))))
        retained = tuple(executor.map(contract.validate, first))

    assert retained == first
    assert all(value is retained[index] for index, value in enumerate(first))


def test_schema_documents_are_deterministic_and_fresh() -> None:
    contract = Contract(Record)
    first = contract.json_schema()
    second = contract.json_schema()

    assert first == second
    assert first is not second
    first["$defs"]["Record"]["minItems"] = 99  # type: ignore[index]
    assert contract.json_schema()["$defs"]["Record"]["minItems"] == 1  # type: ignore[index]


def test_general_union_input_uses_namedtuple_positional_shape() -> None:
    contract = Contract[Point | str](Point | str)

    assert contract.from_python([1, "north"]) == Point(1, "north")
    assert contract.from_python("north") == "north"
    assert contract.from_json('[1,"north"]') == Point(1, "north")


def test_namedtuple_schema_invariants_reject_competing_truth() -> None:
    field = NamedTupleField("value", PrimitiveSchema("int"))
    defaulted = NamedTupleField("other", PrimitiveSchema("int"), 1)

    with pytest.raises(ValueError, match="unique"):
        NamedTupleSchema(Point, (field, field), 2)
    with pytest.raises(ValueError, match="arity"):
        NamedTupleSchema(Point, (field,), 2)
    with pytest.raises(ValueError, match="trailing"):
        NamedTupleSchema(Point, (defaulted, field), 1)
    with pytest.raises(ValueError, match="trailing"):
        NamedTupleSchema(Point, (field, defaulted), 2)
    with pytest.raises(ValueError, match="trailing"):
        NamedTupleSchema(Point, (field,), 0)


def test_recursive_policy_and_projection_visitors_stop_at_named_identity() -> None:
    schema = Contract(Node)._artifacts.schema
    assert isinstance(schema, NamedTupleSchema)
    assert schema.identity is not None
    visiting = frozenset({schema.identity})

    assert schema_values_are_immutable(schema, visiting)
    assert not schema_contains_sensitive_metadata(schema, visiting)
    assert not schema_contains_tagged_union(schema, visiting)
    assert not schema_contains_representation(schema, visiting)
    assert _schema_directions_are_available(schema, "input", visiting)
    assert _representation_infos((schema, schema)) == ()
    assert not _StandardsProjector("input", "json_schema")._contains_serializer(schema, visiting)
    assert not schema_contains_tagged_union(schema)
    assert _schema_directions_are_available(schema, "output", frozenset())
    assert not _StandardsProjector("output", "json_schema")._contains_serializer(schema)

    unnamed = replace(schema, identity=None)
    projected = _StandardsProjector("input", "json_schema").document(unnamed, EMPTY_METADATA)
    assert projected["type"] == "array"


def test_malformed_namedtuple_metadata_is_rejected_without_becoming_runtime_truth() -> None:
    class Inconsistent(NamedTuple):
        value: int

    Inconsistent._fields = ("other",)  # type: ignore[misc]
    with pytest.raises(TypeError, match="inconsistent field metadata"):
        _resolve_named_tuple(Inconsistent, (), {})

    class NonTrailing(NamedTuple):
        first: int
        second: int = 2

    NonTrailing._field_defaults = {"first": 1}  # type: ignore[misc]
    with pytest.raises(TypeError, match="non-trailing defaults"):
        _resolve_named_tuple(NonTrailing, (), {})


def test_incomplete_namedtuple_markers_and_lost_local_names_fail_closed() -> None:
    class Broken(NamedTuple):
        value: int

    Broken._field_defaults = ()  # type: ignore[misc]
    with pytest.raises(TypeError, match="Unsupported annotation"):
        Contract(Broken)

    def declaration() -> type[tuple[object, ...]]:
        class Local:
            pass

        class Lost(NamedTuple):
            value: Local

        return Lost

    with pytest.raises(NameError):
        Contract(declaration())


def test_json_syntax_and_nested_json_security_remain_owned_by_strict_decoder() -> None:
    class Payload(NamedTuple):
        values: dict[str, float]

    contract = Contract(Payload)
    for document in ('{"values":{}}', "[", '[{"value":1,"value":2}]', '[{"value":NaN}]'):
        with pytest.raises(ValidationError):
            contract.from_json(document)


def test_typeddict_only_slot_wrappers_are_rejected() -> None:
    for wrapper in (Required[int], NotRequired[int], TypingReadOnly[int]):
        Declared = NamedTuple(  # ty: ignore[invalid-named-tuple]
            "Declared", [("value", wrapper)]
        )
        with pytest.raises(TypeError, match="Unsupported annotation"):
            Contract(Declared)


def test_custom_methods_are_not_structural_or_executed() -> None:
    calls = 0

    class WithMethod(NamedTuple):
        value: int

        def application_method(self) -> int:
            nonlocal calls
            calls += 1
            return self.value

    contract = Contract(WithMethod)
    value = contract.from_python([1])
    assert contract.validate(value) is value
    assert contract.to_python(value) == (1,)
    assert calls == 0


def test_warm_paths_do_not_reread_annotations_or_call_asdict(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = Contract(Point)
    point = Point(1, "north")

    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("warm declaration metadata was reread")

    monkeypatch.setattr("talea.schema.resolution.get_type_hints", fail)
    monkeypatch.setattr(Point, "_asdict", fail)
    assert contract.validate(point) is point
    assert contract.from_python([1, "north"]) == point
    assert contract.to_python(point) == (1, "north")


def test_hostile_invalid_default_repr_failure_is_contained() -> None:
    calls = 0

    class Hostile:
        def __repr__(self) -> str:
            nonlocal calls
            calls += 1
            raise AssertionError("default repr executed")

    hostile = Hostile()

    class Invalid(NamedTuple):
        value: int = hostile  # type: ignore[assignment]

    with pytest.raises(TypeError, match="invalid default"):
        Contract(Invalid)
    assert calls == 1


def test_positional_metadata_composes_or_rejects_by_meaning() -> None:
    class Constrained(NamedTuple):
        value: Annotated[int, Ge(0)]

    assert Contract(Constrained).from_python([0]) == Constrained(0)
    with pytest.raises(ValidationError) as captured:
        Contract(Constrained).from_python([-1])
    assert captured.value.errors()[0]["location"] == [0]

    for marker in (Alias("renamed"), ReadOnly(), WriteOnly()):
        annotation = Annotated[int, marker]
        Unsupported = NamedTuple("Unsupported", [("value", annotation)])
        with pytest.raises(TypeError, match="NamedTuple fields do not support"):
            Contract(Unsupported)


def test_sensitive_slots_redact_positional_failures() -> None:
    class Secret(NamedTuple):
        token: Annotated[str, Sensitive()]

    rejected = object()
    with pytest.raises(ValidationError) as captured:
        Contract(Secret).from_python([rejected])
    error = captured.value.errors()[0]
    assert error["location"] == [0]
    assert error["input"] == "<redacted>"


@given(st.integers(), st.one_of(st.none(), st.text()))
def test_positional_round_trip_matches_independent_constructor(value: int, supplied: str | None) -> None:
    source: list[object] = [value] if supplied is None else [value, supplied]
    expected = Record(value) if supplied is None else Record(value, supplied)
    contract = Contract(Record)

    result = contract.from_python(source)

    assert result == expected
    assert contract.to_python(result) == tuple(expected)
