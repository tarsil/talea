from collections.abc import Callable
from dataclasses import FrozenInstanceError
from enum import Enum
from typing import cast

import pytest

from talea import Ge, Spec
from talea.schema import (
    ConstrainedSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    LiteralValue,
    MappingSchema,
    PrimitiveSchema,
    SequenceSchema,
    SpecReferenceSchema,
    TypeCheckMode,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
    nodes as schema_nodes,
)


def test_internal_representation_schema_is_not_a_public_schema_export() -> None:
    assert "RepresentationSchema" not in schema_nodes.__all__


@pytest.mark.parametrize(
    ("schema", "attribute", "replacement"),
    [
        (
            SequenceSchema("list", PrimitiveSchema("int")),
            "item",
            PrimitiveSchema("str"),
        ),
        (
            MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")),
            "value",
            PrimitiveSchema("float"),
        ),
        (VariadicTupleSchema(PrimitiveSchema("int")), "item", PrimitiveSchema("str")),
        (
            FixedTupleSchema((PrimitiveSchema("int"), PrimitiveSchema("str"))),
            "items",
            (PrimitiveSchema("bool"),),
        ),
        (
            UnionSchema(frozenset({PrimitiveSchema("int"), PrimitiveSchema("str")})),
            "options",
            frozenset({PrimitiveSchema("bool"), PrimitiveSchema("bytes")}),
        ),
        (SpecReferenceSchema(Spec), "spec_type", int),
        (TypeSchema(int, "exact"), "mode", "nominal"),
        (LiteralSchema(frozenset({LiteralValue(int, 1)})), "values", frozenset()),
        (ConstrainedSchema(PrimitiveSchema("int"), (Ge(0),)), "constraints", (Ge(1),)),
    ],
)
def test_structural_schema_nodes_are_immutable(schema: object, attribute: str, replacement: object) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(schema, attribute, replacement)


def test_union_options_cannot_be_mutated() -> None:
    schema = UnionSchema(frozenset({PrimitiveSchema("int"), PrimitiveSchema("str")}))

    with pytest.raises(AttributeError):
        schema.options.add(PrimitiveSchema("bool"))  # type: ignore[attr-defined]


def test_primitive_schema_is_immutable() -> None:
    schema = PrimitiveSchema("int")

    with pytest.raises(FrozenInstanceError):
        schema.kind = "str"

    with pytest.raises((FrozenInstanceError, TypeError)):
        schema.extra = True

    assert not hasattr(schema, "extra")


@pytest.mark.parametrize(
    "construction",
    [
        lambda: FixedTupleSchema(()),
        lambda: UnionSchema(frozenset({PrimitiveSchema("int")})),
        lambda: LiteralSchema(frozenset()),
        lambda: TypeSchema(cast(type[object], 1), "exact"),
        lambda: TypeSchema(int, cast(TypeCheckMode, "adaptive")),
        lambda: EnumSchema(cast(type[Enum], int), ()),
        lambda: ConstrainedSchema(PrimitiveSchema("int"), ()),
        lambda: ConstrainedSchema(
            ConstrainedSchema(PrimitiveSchema("int"), (Ge(0),)),
            (Ge(1),),
        ),
    ],
)
def test_schema_nodes_reject_invalid_invariants(construction: Callable[[], object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        construction()


def test_structural_nodes_do_not_have_per_instance_dictionaries() -> None:
    schema = SequenceSchema("list", PrimitiveSchema("int"))

    assert not hasattr(schema, "__dict__")


def test_spec_reference_schema_requires_a_declared_spec_class() -> None:
    with pytest.raises(TypeError, match="requires a declared Spec class"):
        SpecReferenceSchema(object)
