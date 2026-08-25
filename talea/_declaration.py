"""Canonical declaration-level structure for Talea Specs.

This module owns the ordered relationship between a declared field name and
the canonical type ``Schema`` resolved for that field.  It deliberately does
not own annotation resolution, validation execution, or instance state.
"""

from dataclasses import dataclass

from talea.schema import Schema


@dataclass(frozen=True, slots=True)
class SpecField:
    """Describe one named field in a Spec declaration.

    Args:
        name: The Python attribute name declared by the Spec body.
        schema: The already-resolved canonical type structure for the field.

    The value is immutable and contains no validator or original annotation.
    Declaration order belongs to the containing ``SpecSchema``.
    """

    name: str
    schema: Schema


@dataclass(frozen=True, slots=True)
class SpecSchema:
    """Own the ordered canonical field structure of one Spec declaration.

    Args:
        fields: Immutable field definitions in class-body declaration order.

    Field names must be unique.  The schema is reusable by validation,
    introspection, serialization, and other future projections without any of
    those consumers reconstructing declaration truth from annotations.

    Raises:
        ValueError: If more than one field has the same name.
    """

    fields: tuple[SpecField, ...]

    def __post_init__(self) -> None:
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("a Spec schema requires unique field names")
