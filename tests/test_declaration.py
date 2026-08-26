from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from talea import Spec
from talea.declaration import MISSING_DEFAULT, SpecField, SpecSchema
from talea.schema import (
    FixedTupleSchema,
    MappingSchema,
    PrimitiveSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    UnionSchema,
    VariadicTupleSchema,
)


def test_spec_schema_owns_ordered_field_structure() -> None:
    identifier = SpecField("id", PrimitiveSchema("int"))
    name = SpecField("name", PrimitiveSchema("str"))
    schema = SpecSchema((identifier, name))

    assert schema.fields == (identifier, name)
    assert schema.fields[0].schema is identifier.schema
    assert identifier.required
    assert identifier.default is MISSING_DEFAULT
    assert identifier.default_factory is None
    assert schema.instances_are_permanently_trusted


def test_spec_declaration_values_are_immutable_and_slotted() -> None:
    field = SpecField("id", PrimitiveSchema("int"))
    schema = SpecSchema((field,))

    with pytest.raises(FrozenInstanceError):
        field.name = "identifier"
    with pytest.raises(FrozenInstanceError):
        schema.fields = ()

    assert not hasattr(field, "__dict__")
    assert not hasattr(schema, "__dict__")


def test_spec_schema_rejects_duplicate_field_names() -> None:
    with pytest.raises(ValueError, match="a Spec schema requires unique field names"):
        SpecSchema(
            (
                SpecField("value", PrimitiveSchema("int")),
                SpecField("value", PrimitiveSchema("str")),
            )
        )


def test_spec_field_owns_static_default_and_factory_states() -> None:
    static = SpecField("active", PrimitiveSchema("bool"), default=True)
    factory: Callable[[], object] = list
    produced = SpecField("items", PrimitiveSchema("int"), default_factory=factory)

    assert not static.required
    assert static.has_static_default
    assert static.default is True
    assert not produced.required
    assert not produced.has_static_default
    assert produced.default_factory is factory


def test_spec_field_rejects_ambiguous_default_ownership() -> None:
    with pytest.raises(ValueError, match="both a static default and a default factory"):
        SpecField("value", PrimitiveSchema("int"), default=1, default_factory=lambda: 1)


@pytest.mark.parametrize(
    "schema",
    [
        SequenceSchema("list", PrimitiveSchema("int")),
        SequenceSchema("set", PrimitiveSchema("int")),
        MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")),
        VariadicTupleSchema(SequenceSchema("list", PrimitiveSchema("int"))),
        FixedTupleSchema((PrimitiveSchema("int"), SequenceSchema("set", PrimitiveSchema("str")))),
        UnionSchema(
            frozenset(
                {
                    PrimitiveSchema("none"),
                    MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")),
                }
            )
        ),
        SequenceSchema("frozenset", SequenceSchema("list", PrimitiveSchema("int"))),
    ],
)
def test_spec_schema_does_not_permanently_trust_mutable_value_graphs(schema: object) -> None:
    declaration = SpecSchema((SpecField("value", schema),))  # type: ignore[invalid-argument-type]

    assert not declaration.instances_are_permanently_trusted


def test_spec_schema_permanently_trusts_transitively_immutable_values() -> None:
    schema = SpecSchema(
        (
            SpecField("pair", FixedTupleSchema((PrimitiveSchema("int"), PrimitiveSchema("str")))),
            SpecField("values", SequenceSchema("frozenset", PrimitiveSchema("int"))),
            SpecField(
                "choice",
                UnionSchema(frozenset({PrimitiveSchema("int"), PrimitiveSchema("none")})),
            ),
        )
    )

    assert schema.instances_are_permanently_trusted


def test_spec_schema_rejects_values_outside_the_canonical_schema_union() -> None:
    with pytest.raises(AssertionError):
        SpecSchema((SpecField("value", cast(Schema, object())),))


def test_spec_schema_composes_inherited_fields_and_local_overrides_in_place() -> None:
    inherited = SpecSchema(
        (
            SpecField(
                "identifier",
                UnionSchema(frozenset({PrimitiveSchema("int"), PrimitiveSchema("str")})),
            ),
            SpecField("active", PrimitiveSchema("bool"), default=True),
        )
    )
    override = SpecField("identifier", PrimitiveSchema("str"))
    added = SpecField("name", PrimitiveSchema("str"))

    composed = SpecSchema.compose((inherited,), (override, added))

    assert composed.fields == (override, inherited.fields[1], added)


def test_spec_reference_trust_propagates_from_the_canonical_target_declaration() -> None:
    class Stable(Spec):
        value: int

    class Mutable(Spec):
        values: list[int]

    stable = SpecSchema((SpecField("nested", SpecReferenceSchema(Stable)),))
    mutable = SpecSchema((SpecField("nested", SpecReferenceSchema(Mutable)),))

    assert stable.instances_are_permanently_trusted
    assert not mutable.instances_are_permanently_trusted


def test_spec_schema_accepts_covariant_immutable_field_overrides() -> None:
    class Person(Spec):
        name: str

    class Employee(Person):
        identifier: int

    person = SpecReferenceSchema(Person)
    employee = SpecReferenceSchema(Employee)
    cases = (
        (employee, person),
        (SequenceSchema("frozenset", employee), SequenceSchema("frozenset", person)),
        (VariadicTupleSchema(employee), VariadicTupleSchema(person)),
        (FixedTupleSchema((employee, PrimitiveSchema("str"))), FixedTupleSchema((person, PrimitiveSchema("str")))),
        (
            UnionSchema(frozenset({PrimitiveSchema("str"), PrimitiveSchema("none")})),
            UnionSchema(
                frozenset(
                    {
                        PrimitiveSchema("int"),
                        PrimitiveSchema("str"),
                        PrimitiveSchema("none"),
                    }
                )
            ),
        ),
    )

    for candidate, inherited in cases:
        override = SpecField("value", candidate)
        composed = SpecSchema.compose(
            (SpecSchema((SpecField("value", inherited),)),),
            (override,),
        )

        assert composed.fields == (override,)
