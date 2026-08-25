from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest

from talea.schema import (
    FixedTupleSchema,
    MappingSchema,
    PrimitiveSchema,
    SequenceSchema,
    UnionSchema,
    VariadicTupleSchema,
)


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
    ],
)
def test_schema_nodes_reject_invalid_cardinality(construction: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        construction()


def test_structural_nodes_do_not_have_per_instance_dictionaries() -> None:
    schema = SequenceSchema("list", PrimitiveSchema("int"))

    assert not hasattr(schema, "__dict__")
