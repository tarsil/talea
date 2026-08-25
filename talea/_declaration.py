"""Canonical declaration-level structure for Talea Specs.

This module owns the ordered relationship between a declared field name, its
canonical type ``Schema``, and its required or default-producing lifecycle.
It deliberately does not own annotation resolution, validation execution, or
instance state.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, assert_never

from talea.schema import (
    FixedTupleSchema,
    MappingSchema,
    PrimitiveSchema,
    Schema,
    SequenceSchema,
    UnionSchema,
    VariadicTupleSchema,
)


class _MissingDefault:
    """Represent required-field state without conflating it with ``None``."""

    __slots__ = ()


MISSING_DEFAULT: Final = _MissingDefault()


@dataclass(frozen=True, slots=True)
class SpecField:
    """Describe one named field in a Spec declaration.

    Args:
        name: The Python attribute name declared by the Spec body.
        schema: The already-resolved canonical type structure for the field.
        default: The validated static default, or ``MISSING_DEFAULT``.
        default_factory: The zero-argument producer used when the field is
            omitted, or ``None``.

    The value is immutable and contains no validator or original annotation.
    Exactly one instance owns the complete declaration truth for a field;
    declaration order belongs to the containing ``SpecSchema``.

    Raises:
        ValueError: If both a static default and factory are provided.
    """

    name: str
    schema: Schema
    default: object = MISSING_DEFAULT
    default_factory: Callable[[], object] | None = None

    def __post_init__(self) -> None:
        if self.default is not MISSING_DEFAULT and self.default_factory is not None:
            raise ValueError("a Spec field cannot have both a static default and a default factory")

    @property
    def required(self) -> bool:
        """Return whether construction requires an explicit field value."""

        return self.default is MISSING_DEFAULT and self.default_factory is None

    @property
    def has_static_default(self) -> bool:
        """Return whether this field owns a retained static default."""

        return self.default is not MISSING_DEFAULT


@dataclass(frozen=True, slots=True)
class SpecSchema:
    """Own the ordered canonical field structure of one Spec declaration.

    Args:
        fields: Immutable field definitions in class-body declaration order.
        instances_are_permanently_trusted: Whether immutable field bindings
            are sufficient to keep every value graph schema-valid.  This is
            derived once from ``fields`` and cannot be supplied independently.

    Field names must be unique.  The schema is reusable by validation,
    introspection, serialization, and other future projections without any of
    those consumers reconstructing declaration truth from annotations.

    Raises:
        ValueError: If more than one field has the same name.
    """

    fields: tuple[SpecField, ...]
    instances_are_permanently_trusted: bool = field(init=False)

    def __post_init__(self) -> None:
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("a Spec schema requires unique field names")
        object.__setattr__(
            self,
            "instances_are_permanently_trusted",
            all(_schema_values_are_immutable(field.schema) for field in self.fields),
        )


def _schema_values_are_immutable(schema: Schema) -> bool:
    """Project whether valid values can change without field reassignment."""

    if isinstance(schema, PrimitiveSchema):
        return True
    if isinstance(schema, SequenceSchema):
        return schema.kind == "frozenset" and _schema_values_are_immutable(schema.item)
    if isinstance(schema, MappingSchema):
        return False
    if isinstance(schema, VariadicTupleSchema):
        return _schema_values_are_immutable(schema.item)
    if isinstance(schema, FixedTupleSchema):
        return all(_schema_values_are_immutable(item) for item in schema.items)
    if isinstance(schema, UnionSchema):
        return all(_schema_values_are_immutable(option) for option in schema.options)
    assert_never(schema)
