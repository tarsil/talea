from dataclasses import FrozenInstanceError

import pytest

from talea._declaration import SpecField, SpecSchema
from talea.schema import PrimitiveSchema


def test_spec_schema_owns_ordered_field_structure() -> None:
    identifier = SpecField("id", PrimitiveSchema("int"))
    name = SpecField("name", PrimitiveSchema("str"))
    schema = SpecSchema((identifier, name))

    assert schema.fields == (identifier, name)
    assert schema.fields[0].schema is identifier.schema


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
