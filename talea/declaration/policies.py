"""Derive trust and override compatibility from canonical schema truth."""

from decimal import Decimal
from typing import assert_never

from talea.constraints import Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf
from talea.schema.nodes import (
    AliasSchema,
    ConstrainedSchema,
    DataclassSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    MappingSchema,
    NamedReferenceSchema,
    PrimitiveSchema,
    RepresentationSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    TaggedUnionSchema,
    TypedDictSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
)


def schema_values_are_immutable(schema: Schema, visiting: frozenset[object] = frozenset()) -> bool:
    """Project whether valid values can change without field reassignment."""

    if isinstance(schema, (PrimitiveSchema, TypeSchema, LiteralSchema, EnumSchema)):
        return True
    if isinstance(schema, ConstrainedSchema):
        return schema_values_are_immutable(schema.schema, visiting)
    if isinstance(schema, RepresentationSchema):
        return not schema.opaque_internal and schema_values_are_immutable(schema.internal, visiting)
    if isinstance(schema, AliasSchema):
        return schema_values_are_immutable(schema.schema, visiting)
    if isinstance(schema, NamedReferenceSchema):
        if schema.identity.kind == "typed_dict":
            return False
        if schema.identity in visiting:
            return True
        return schema_values_are_immutable(schema.target, visiting | {schema.identity})
    if isinstance(schema, SpecReferenceSchema):
        declaration = vars(schema.spec_type)["__talea_declaration__"]
        if schema.spec_type in visiting:
            return True
        return declaration.values_are_immutable(visiting | {schema.spec_type})
    if isinstance(schema, DataclassSchema):
        if not schema.frozen:
            return False
        identity = schema.identity or schema.dataclass_type
        if identity in visiting:
            return True
        return all(schema_values_are_immutable(field.schema, visiting | {identity}) for field in schema.fields)
    if isinstance(schema, SequenceSchema):
        return schema.kind == "frozenset" and schema_values_are_immutable(schema.item, visiting)
    if isinstance(schema, MappingSchema):
        return False
    if isinstance(schema, TypedDictSchema):
        return False
    if isinstance(schema, TaggedUnionSchema):
        return all(schema_values_are_immutable(branch.schema, visiting) for branch in schema.branches)
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
    if isinstance(candidate, TaggedUnionSchema) and isinstance(inherited, TaggedUnionSchema):
        return candidate == inherited
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


def schema_contains_sensitive_metadata(
    schema: Schema,
    visiting: frozenset[object] = frozenset(),
) -> bool:
    """Return whether reachable canonical declaration truth is sensitive."""

    if isinstance(schema, (PrimitiveSchema, TypeSchema, LiteralSchema, EnumSchema)):
        return False
    if isinstance(schema, ConstrainedSchema):
        return schema_contains_sensitive_metadata(schema.schema, visiting)
    if isinstance(schema, RepresentationSchema):
        directions = tuple(item for item in (schema.input, schema.output) if item is not None)
        return schema_contains_sensitive_metadata(schema.internal, visiting) or any(
            schema_contains_sensitive_metadata(item, visiting) for item in directions
        )
    if isinstance(schema, AliasSchema):
        return bool(schema.metadata.sensitive) or schema_contains_sensitive_metadata(schema.schema, visiting)
    if isinstance(schema, NamedReferenceSchema):
        if schema.identity in visiting:
            return False
        return schema_contains_sensitive_metadata(schema.target, visiting | {schema.identity})
    if isinstance(schema, SpecReferenceSchema):
        if schema.spec_type in visiting:
            return False
        declaration = vars(schema.spec_type)["__talea_declaration__"]
        artifacts = vars(schema.spec_type).get("__talea_artifacts__")
        fields = artifacts.schema.fields if artifacts is not None else declaration.prepared_fields
        if fields is None:
            return False
        return any(
            bool(field.metadata.sensitive)
            or schema_contains_sensitive_metadata(field.schema, visiting | {schema.spec_type})
            for field in fields
        )
    if isinstance(schema, DataclassSchema):
        identity = schema.identity or schema.dataclass_type
        if identity in visiting:
            return False
        return any(
            bool(field.metadata.sensitive) or schema_contains_sensitive_metadata(field.schema, visiting | {identity})
            for field in schema.fields
        )
    if isinstance(schema, SequenceSchema):
        return schema_contains_sensitive_metadata(schema.item, visiting)
    if isinstance(schema, MappingSchema):
        return schema_contains_sensitive_metadata(schema.key, visiting) or schema_contains_sensitive_metadata(
            schema.value,
            visiting,
        )
    if isinstance(schema, TypedDictSchema):
        return any(
            bool(field.metadata.sensitive) or schema_contains_sensitive_metadata(field.schema, visiting)
            for field in schema.fields
        )
    if isinstance(schema, TaggedUnionSchema):
        return schema.sensitive or any(
            schema_contains_sensitive_metadata(branch.schema, visiting) for branch in schema.branches
        )
    if isinstance(schema, VariadicTupleSchema):
        return schema_contains_sensitive_metadata(schema.item, visiting)
    if isinstance(schema, FixedTupleSchema):
        return any(schema_contains_sensitive_metadata(item, visiting) for item in schema.items)
    if isinstance(schema, UnionSchema):
        return any(schema_contains_sensitive_metadata(option, visiting) for option in schema.options)
    assert_never(schema)


def schema_contains_tagged_union(
    schema: Schema,
    visiting: frozenset[object] = frozenset(),
) -> bool:
    """Return whether a serializer replacement could bypass tagged truth."""

    if isinstance(schema, TaggedUnionSchema):
        return True
    if isinstance(schema, RepresentationSchema):
        directions = tuple(item for item in (schema.input, schema.output) if item is not None)
        return schema_contains_tagged_union(schema.internal, visiting) or any(
            schema_contains_tagged_union(item, visiting) for item in directions
        )
    if isinstance(schema, (PrimitiveSchema, TypeSchema, LiteralSchema, EnumSchema)):
        return False
    if isinstance(schema, SpecReferenceSchema):
        if schema.spec_type in visiting:
            return False
        declaration = vars(schema.spec_type)["__talea_declaration__"]
        artifacts = vars(schema.spec_type).get("__talea_artifacts__")
        fields = artifacts.schema.fields if artifacts is not None else declaration.prepared_fields
        if fields is None:
            return False
        return any(schema_contains_tagged_union(field.schema, visiting | {schema.spec_type}) for field in fields)
    if isinstance(schema, DataclassSchema):
        identity = schema.identity or schema.dataclass_type
        if identity in visiting:
            return False
        return any(schema_contains_tagged_union(field.schema, visiting | {identity}) for field in schema.fields)
    if isinstance(schema, NamedReferenceSchema):
        if schema.identity in visiting:
            return False
        return schema_contains_tagged_union(schema.target, visiting | {schema.identity})
    if isinstance(schema, (ConstrainedSchema, AliasSchema)):
        return schema_contains_tagged_union(schema.schema, visiting)
    if isinstance(schema, SequenceSchema):
        return schema_contains_tagged_union(schema.item, visiting)
    if isinstance(schema, MappingSchema):
        return schema_contains_tagged_union(schema.key, visiting) or schema_contains_tagged_union(
            schema.value,
            visiting,
        )
    if isinstance(schema, TypedDictSchema):
        return any(schema_contains_tagged_union(field.schema, visiting) for field in schema.fields)
    if isinstance(schema, VariadicTupleSchema):
        return schema_contains_tagged_union(schema.item, visiting)
    if isinstance(schema, FixedTupleSchema):
        return any(schema_contains_tagged_union(item, visiting) for item in schema.items)
    assert isinstance(schema, UnionSchema)
    return any(schema_contains_tagged_union(option, visiting) for option in schema.options)


def schema_contains_named_reference(schema: Schema) -> bool:
    """Return whether one finite schema graph contains a named back-edge."""

    if isinstance(schema, NamedReferenceSchema):
        return True
    if isinstance(schema, RepresentationSchema):
        directions = tuple(item for item in (schema.input, schema.output) if item is not None)
        return schema_contains_named_reference(schema.internal) or any(
            schema_contains_named_reference(item) for item in directions
        )
    if isinstance(schema, (PrimitiveSchema, TypeSchema, LiteralSchema, EnumSchema, SpecReferenceSchema)):
        return False
    if isinstance(schema, (ConstrainedSchema, AliasSchema)):
        return schema_contains_named_reference(schema.schema)
    if isinstance(schema, SequenceSchema):
        return schema_contains_named_reference(schema.item)
    if isinstance(schema, MappingSchema):
        return schema_contains_named_reference(schema.key) or schema_contains_named_reference(schema.value)
    if isinstance(schema, TypedDictSchema):
        return any(schema_contains_named_reference(field.schema) for field in schema.fields)
    if isinstance(schema, DataclassSchema):
        return any(schema_contains_named_reference(field.schema) for field in schema.fields)
    if isinstance(schema, TaggedUnionSchema):
        return any(schema_contains_named_reference(branch.schema) for branch in schema.branches)
    if isinstance(schema, VariadicTupleSchema):
        return schema_contains_named_reference(schema.item)
    if isinstance(schema, FixedTupleSchema):
        return any(schema_contains_named_reference(item) for item in schema.items)
    assert isinstance(schema, UnionSchema)
    return any(schema_contains_named_reference(option) for option in schema.options)


def schema_contains_representation(
    schema: Schema,
    visiting: frozenset[object] = frozenset(),
) -> bool:
    """Return whether one schema graph reaches representation truth."""

    if isinstance(schema, RepresentationSchema):
        return True
    if isinstance(schema, (ConstrainedSchema, AliasSchema)):
        return schema_contains_representation(schema.schema, visiting)
    if isinstance(schema, NamedReferenceSchema):
        if schema.identity in visiting:
            return False
        return schema_contains_representation(schema.target, visiting | {schema.identity})
    if isinstance(schema, DataclassSchema):
        identity = schema.identity or schema.dataclass_type
        if identity in visiting:
            return False
        return any(schema_contains_representation(field.schema, visiting | {identity}) for field in schema.fields)
    if isinstance(schema, SequenceSchema):
        return schema_contains_representation(schema.item, visiting)
    if isinstance(schema, MappingSchema):
        return schema_contains_representation(schema.key, visiting) or schema_contains_representation(
            schema.value, visiting
        )
    if isinstance(schema, TypedDictSchema):
        return any(schema_contains_representation(field.schema, visiting) for field in schema.fields)
    if isinstance(schema, TaggedUnionSchema):
        return any(schema_contains_representation(branch.schema, visiting) for branch in schema.branches)
    if isinstance(schema, VariadicTupleSchema):
        return schema_contains_representation(schema.item, visiting)
    if isinstance(schema, FixedTupleSchema):
        return any(schema_contains_representation(item, visiting) for item in schema.items)
    if isinstance(schema, UnionSchema):
        return any(schema_contains_representation(option, visiting) for option in schema.options)
    return False


def schema_input_directions_are_available(
    schema: Schema,
    visiting: frozenset[object] = frozenset(),
) -> bool:
    """Return whether every representation reached by input has that direction."""

    if isinstance(schema, RepresentationSchema):
        return schema.input is not None and schema_input_directions_are_available(schema.input, visiting)
    if isinstance(schema, (ConstrainedSchema, AliasSchema)):
        return schema_input_directions_are_available(schema.schema, visiting)
    if isinstance(schema, NamedReferenceSchema):
        if schema.identity in visiting:
            return True
        return schema_input_directions_are_available(schema.target, visiting | {schema.identity})
    if isinstance(schema, DataclassSchema):
        identity = schema.identity or schema.dataclass_type
        if identity in visiting:
            return True
        return all(
            schema_input_directions_are_available(field.schema, visiting | {identity}) for field in schema.fields
        )
    if isinstance(schema, SequenceSchema):
        return schema_input_directions_are_available(schema.item, visiting)
    if isinstance(schema, MappingSchema):
        return schema_input_directions_are_available(schema.key, visiting) and schema_input_directions_are_available(
            schema.value, visiting
        )
    if isinstance(schema, TypedDictSchema):
        return all(schema_input_directions_are_available(field.schema, visiting) for field in schema.fields)
    if isinstance(schema, TaggedUnionSchema):
        return all(schema_input_directions_are_available(branch.schema, visiting) for branch in schema.branches)
    if isinstance(schema, VariadicTupleSchema):
        return schema_input_directions_are_available(schema.item, visiting)
    if isinstance(schema, FixedTupleSchema):
        return all(schema_input_directions_are_available(item, visiting) for item in schema.items)
    if isinstance(schema, UnionSchema):
        return all(schema_input_directions_are_available(option, visiting) for option in schema.options)
    return True


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
