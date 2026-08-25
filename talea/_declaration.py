"""Canonical declaration-level structure for Talea Specs.

This module owns the ordered relationship between a declared field name, its
canonical type ``Schema``, and its required or default-producing lifecycle.
It deliberately does not own annotation resolution, validation execution, or
instance state.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, assert_never, cast

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

    @classmethod
    def compose(
        cls,
        inherited: tuple["SpecSchema", ...],
        declared: tuple[SpecField, ...],
    ) -> "SpecSchema":
        """Create one effective declaration from bases and local contributions.

        Base schemas are consumed in declared-base order.  The first occurrence
        of an inherited name owns its MRO-selected semantics.  A local override
        replaces that field at its established position; a new local field is
        appended in class-body order.

        Raises:
            TypeError: If an override widens or otherwise conflicts with the
                immutable inherited field type.
        """

        fields: dict[str, SpecField] = {}
        for schema in inherited:
            for inherited_field in schema.fields:
                fields.setdefault(inherited_field.name, inherited_field)
        for declared_field in declared:
            inherited_field = fields.get(declared_field.name)
            if inherited_field is not None and not _schema_is_covariant_override(
                declared_field.schema, inherited_field.schema
            ):
                raise TypeError(f"Spec field {declared_field.name!r} override is not type-compatible")
            fields[declared_field.name] = declared_field
        return cls(tuple(fields.values()))


def _schema_values_are_immutable(schema: Schema) -> bool:
    """Project whether valid values can change without field reassignment."""

    if isinstance(schema, PrimitiveSchema):
        return True
    if isinstance(schema, SpecReferenceSchema):
        artifacts = vars(schema.spec_type)["__talea_artifacts__"]
        declaration = cast(SpecSchema, artifacts.schema)
        return declaration.instances_are_permanently_trusted
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


def _schema_is_covariant_override(candidate: Schema, inherited: Schema) -> bool:
    """Return whether an immutable field may safely narrow its inherited type."""

    if candidate == inherited:
        return True
    if isinstance(inherited, UnionSchema):
        candidates = candidate.options if isinstance(candidate, UnionSchema) else frozenset({candidate})
        return all(
            any(_schema_is_covariant_override(option, inherited_option) for inherited_option in inherited.options)
            for option in candidates
        )
    if isinstance(candidate, SpecReferenceSchema) and isinstance(inherited, SpecReferenceSchema):
        return issubclass(candidate.spec_type, inherited.spec_type)
    if isinstance(candidate, SequenceSchema) and isinstance(inherited, SequenceSchema):
        return candidate.kind == inherited.kind == "frozenset" and _schema_is_covariant_override(
            candidate.item, inherited.item
        )
    if isinstance(candidate, VariadicTupleSchema) and isinstance(inherited, VariadicTupleSchema):
        return _schema_is_covariant_override(candidate.item, inherited.item)
    if isinstance(candidate, FixedTupleSchema) and isinstance(inherited, FixedTupleSchema):
        return len(candidate.items) == len(inherited.items) and all(
            _schema_is_covariant_override(candidate_item, inherited_item)
            for candidate_item, inherited_item in zip(candidate.items, inherited.items, strict=True)
        )
    return False
