"""Derive trust and override compatibility from canonical schema truth."""

from decimal import Decimal
from typing import assert_never

from talea.constraints import Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf
from talea.schema.nodes import (
    AliasSchema,
    ConstrainedSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    MappingSchema,
    PrimitiveSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    TypedDictSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
)


def schema_values_are_immutable(schema: Schema, visiting: frozenset[type[object]] = frozenset()) -> bool:
    """Project whether valid values can change without field reassignment."""

    if isinstance(schema, (PrimitiveSchema, TypeSchema, LiteralSchema, EnumSchema)):
        return True
    if isinstance(schema, ConstrainedSchema):
        return schema_values_are_immutable(schema.schema, visiting)
    if isinstance(schema, AliasSchema):
        return schema_values_are_immutable(schema.schema, visiting)
    if isinstance(schema, SpecReferenceSchema):
        declaration = vars(schema.spec_type)["__talea_declaration__"]
        if schema.spec_type in visiting:
            return True
        return declaration.values_are_immutable(visiting | {schema.spec_type})
    if isinstance(schema, SequenceSchema):
        return schema.kind == "frozenset" and schema_values_are_immutable(schema.item, visiting)
    if isinstance(schema, MappingSchema):
        return False
    if isinstance(schema, TypedDictSchema):
        return False
    if isinstance(schema, VariadicTupleSchema):
        return schema_values_are_immutable(schema.item, visiting)
    if isinstance(schema, FixedTupleSchema):
        return all(schema_values_are_immutable(item, visiting) for item in schema.items)
    if isinstance(schema, UnionSchema):
        return all(schema_values_are_immutable(option, visiting) for option in schema.options)
    assert_never(schema)


def schema_is_covariant_override(candidate: Schema, inherited: Schema) -> bool:
    """Return whether ``candidate`` is provably no wider than ``inherited``."""

    if candidate == inherited:
        return True
    candidate_base, candidate_constraints = _unwrap(candidate)
    inherited_base, inherited_constraints = _unwrap(inherited)
    if candidate_constraints or inherited_constraints:
        return schema_is_covariant_override(candidate_base, inherited_base) and _constraints_imply(
            candidate_constraints,
            inherited_constraints,
        )
    if isinstance(inherited, UnionSchema):
        candidates = candidate.options if isinstance(candidate, UnionSchema) else frozenset({candidate})
        return all(
            any(schema_is_covariant_override(option, inherited_option) for inherited_option in inherited.options)
            for option in candidates
        )
    if isinstance(candidate, SpecReferenceSchema) and isinstance(inherited, SpecReferenceSchema):
        return issubclass(candidate.spec_type, inherited.spec_type)
    if isinstance(candidate, SequenceSchema) and isinstance(inherited, SequenceSchema):
        return candidate.kind == inherited.kind == "frozenset" and schema_is_covariant_override(
            candidate.item, inherited.item
        )
    if isinstance(candidate, VariadicTupleSchema) and isinstance(inherited, VariadicTupleSchema):
        return schema_is_covariant_override(candidate.item, inherited.item)
    if isinstance(candidate, FixedTupleSchema) and isinstance(inherited, FixedTupleSchema):
        return len(candidate.items) == len(inherited.items) and all(
            schema_is_covariant_override(candidate_item, inherited_item)
            for candidate_item, inherited_item in zip(candidate.items, inherited.items, strict=True)
        )
    return False


def _unwrap(schema: Schema) -> tuple[Schema, tuple[object, ...]]:
    if isinstance(schema, AliasSchema):
        return _unwrap(schema.schema)
    if isinstance(schema, ConstrainedSchema):
        return schema.schema, schema.constraints
    return schema, ()


def _constraints_imply(candidate: tuple[object, ...], inherited: tuple[object, ...]) -> bool:
    return all(any(_constraint_implies(option, requirement) for option in candidate) for requirement in inherited)


def _constraint_implies(candidate: object, inherited: object) -> bool:
    if candidate == inherited:
        return True
    if isinstance(candidate, (Gt, Ge)) and isinstance(inherited, (Gt, Ge)):
        return candidate.value > inherited.value or (
            candidate.value == inherited.value and (isinstance(candidate, Gt) or isinstance(inherited, Ge))
        )
    if isinstance(candidate, (Lt, Le)) and isinstance(inherited, (Lt, Le)):
        return candidate.value < inherited.value or (
            candidate.value == inherited.value and (isinstance(candidate, Lt) or isinstance(inherited, Le))
        )
    if isinstance(candidate, MinLength) and isinstance(inherited, MinLength):
        return candidate.value >= inherited.value
    if isinstance(candidate, MaxLength) and isinstance(inherited, MaxLength):
        return candidate.value <= inherited.value
    if isinstance(candidate, MultipleOf) and isinstance(inherited, MultipleOf):
        if type(candidate.value) is int:
            return candidate.value % inherited.value == 0
        if type(candidate.value) is Decimal:
            candidate_numerator, candidate_denominator = candidate.value.as_integer_ratio()
            inherited_numerator, inherited_denominator = inherited.value.as_integer_ratio()
            return (candidate_numerator * inherited_denominator) % (candidate_denominator * inherited_numerator) == 0
        return False
    return False
