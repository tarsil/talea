"""Normalize nested output selection against canonical schema truth."""

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Literal, cast

from talea.declaration.models import SerializationHook, SpecField, SpecSchema
from talea.schema.nodes import (
    AliasSchema,
    ConstrainedSchema,
    DataclassSchema,
    FixedTupleSchema,
    MappingSchema,
    NamedReferenceSchema,
    RepresentationSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    TaggedUnionSchema,
    TypedDictSchema,
    UnionSchema,
    VariadicTupleSchema,
)

type SerializationSelection = Set[str] | Mapping[str, Literal[True] | SerializationSelection]


@dataclass(frozen=True, slots=True)
class _Selection:
    """Retain one immutable canonical-name selection tree."""

    entries: tuple[tuple[str, "_Selection | None"], ...]

    @property
    def descends(self) -> bool:
        """Return whether any selected field has a child projection."""

        return any(child is not None for _, child in self.entries)

    def fields(self) -> frozenset[str]:
        """Return this level as the legacy immutable field-name selection."""

        return frozenset(name for name, _ in self.entries)


def normalize_selection(
    selection: SerializationSelection | None,
    schema: SpecSchema,
    parameter: Literal["include", "exclude"],
) -> _Selection | None:
    """Validate and freeze one operation-local Spec selection."""

    if selection is None:
        return None
    return _normalize_object(selection, schema.fields, schema.serializers, parameter, ())


def _normalize_object(
    selection: SerializationSelection,
    fields: tuple[SpecField, ...] | tuple[object, ...],
    serializers: tuple[SerializationHook, ...],
    parameter: Literal["include", "exclude"],
    path: tuple[str, ...],
) -> _Selection:
    supplied = _selection_items(selection, parameter, path)
    field_by_name = {field.name: field for field in fields}  # ty: ignore[unresolved-attribute]
    serializer_by_field = {serializer.field: serializer for serializer in serializers}
    entries: list[tuple[str, _Selection | None]] = []
    for name, child_selection in sorted(supplied.items()):
        field = field_by_name.get(name)
        if field is None:
            raise ValueError(f"{parameter} contains unknown field {_render_path((*path, name))}")
        if child_selection is None:
            entries.append((name, None))
            continue
        serializer = serializer_by_field.get(name)
        if serializer is not None and serializer.output_schema is None:
            raise ValueError(f"{parameter} cannot descend through serializer field {_render_path((*path, name))}")
        child_schema = serializer.output_schema if serializer is not None else field.schema  # ty: ignore[unresolved-attribute]
        assert child_schema is not None
        child = _normalize_descendant(child_selection, child_schema, parameter, (*path, name))
        entries.append((name, child))
    return _Selection(tuple(entries))


def _normalize_descendant(
    selection: SerializationSelection,
    schema: Schema,
    parameter: Literal["include", "exclude"],
    path: tuple[str, ...],
) -> _Selection:
    while isinstance(schema, (AliasSchema, ConstrainedSchema)):
        schema = schema.schema
    if isinstance(schema, NamedReferenceSchema):
        return _normalize_descendant(selection, schema.target, parameter, path)
    if isinstance(schema, RepresentationSchema):
        if schema.output is None:
            raise ValueError(f"{parameter} cannot descend through a Representation without output")
        return _normalize_descendant(selection, schema.output, parameter, path)
    if isinstance(schema, SpecReferenceSchema):
        artifacts = vars(schema.spec_type)["__talea_artifacts__"]
        return _normalize_object(
            selection,
            artifacts.schema.fields,
            artifacts.schema.serializers,
            parameter,
            path,
        )
    if isinstance(schema, DataclassSchema):
        return _normalize_object(selection, schema.fields, (), parameter, path)
    if isinstance(schema, TypedDictSchema):
        return _normalize_object(selection, schema.fields, (), parameter, path)
    if isinstance(schema, (SequenceSchema, VariadicTupleSchema)):
        return _normalize_descendant(selection, schema.item, parameter, path)
    if isinstance(schema, MappingSchema):
        return _normalize_descendant(selection, schema.value, parameter, path)
    if isinstance(schema, FixedTupleSchema):
        normalized = tuple(_normalize_descendant(selection, item, parameter, path) for item in schema.items)
        return normalized[0]
    if isinstance(schema, UnionSchema):
        return _normalize_union(selection, tuple(schema.options), parameter, path)
    if isinstance(schema, TaggedUnionSchema):
        normalized = _normalize_union(
            selection,
            tuple(branch.schema for branch in schema.branches),
            parameter,
            path,
        )
        selected = dict(normalized.entries)
        if parameter == "include" and selected.get(schema.discriminator, ...) is not None:
            raise ValueError(
                f"include must retain tagged-union discriminator {_render_path((*path, schema.discriminator))}"
            )
        if parameter == "exclude" and schema.discriminator in selected:
            raise ValueError(
                f"exclude cannot remove tagged-union discriminator {_render_path((*path, schema.discriminator))}"
            )
        return normalized
    raise ValueError(f"{parameter} cannot descend into scalar field {_render_path(path)}")


def _normalize_union(
    selection: SerializationSelection,
    options: tuple[Schema, ...],
    parameter: Literal["include", "exclude"],
    path: tuple[str, ...],
) -> _Selection:
    supplied = _selection_items(selection, parameter, path)
    selectable = tuple((option, _object_variants(option)) for option in options)
    selectable = tuple((option, variants) for option, variants in selectable if variants)
    if not selectable:
        raise ValueError(f"{parameter} cannot descend into scalar field {_render_path(path)}")
    known: set[str] = set()
    for _, variants in selectable:
        for fields, _ in variants:
            known.update(field.name for field in fields)  # ty: ignore[unresolved-attribute]
    unknown = supplied.keys() - known
    if unknown:
        name = min(unknown)
        raise ValueError(f"{parameter} contains unknown field {_render_path((*path, name))}")
    for option, variants in selectable:
        if not isinstance(_unwrap_schema(option), FixedTupleSchema):
            continue
        option_names = {
            field.name  # ty: ignore[unresolved-attribute]
            for fields, _ in variants
            for field in fields
        }
        fixed_selection: dict[str, Literal[True] | SerializationSelection] = {}
        for name, child in supplied.items():
            if name in option_names:
                fixed_selection[name] = True if child is None else child
        if fixed_selection:
            _normalize_descendant(fixed_selection, option, parameter, path)
    entries = []
    for name, child_selection in sorted(supplied.items()):
        if child_selection is None:
            entries.append((name, None))
            continue
        owners = tuple(
            (field, serializers)
            for _, variants in selectable
            for fields, serializers in variants
            for field in fields
            if field.name == name  # ty: ignore[unresolved-attribute]
        )
        serializers = tuple(
            serializer
            for _, owner_serializers in owners
            for serializer in owner_serializers
            if serializer.field == name
        )
        if any(serializer.output_schema is None for serializer in serializers):
            raise ValueError(f"{parameter} cannot descend through serializer field {_render_path((*path, name))}")
        schemas = tuple(
            next(
                (serializer.output_schema for serializer in owner_serializers if serializer.field == name),
                field.schema,  # ty: ignore[unresolved-attribute]
            )
            for field, owner_serializers in owners
        )
        assert all(schema is not None for schema in schemas)
        resolved_schemas = cast(tuple[Schema, ...], schemas)
        if len(schemas) == 1:
            child = _normalize_descendant(child_selection, resolved_schemas[0], parameter, (*path, name))
        else:
            child = _normalize_union(child_selection, resolved_schemas, parameter, (*path, name))
        entries.append((name, child))
    return _Selection(tuple(entries))


def _selection_items(
    selection: SerializationSelection,
    parameter: Literal["include", "exclude"],
    path: tuple[str, ...],
) -> dict[str, SerializationSelection | None]:
    if isinstance(selection, Set) and not isinstance(selection, (str, bytes)):
        names = frozenset(selection)
        if any(type(name) is not str for name in names):
            raise TypeError(f"{parameter} field names must be exact strings")
        return dict.fromkeys(names)
    if not isinstance(selection, Mapping):
        raise TypeError(f"{parameter} must be a set or mapping of canonical field names")
    items: dict[str, SerializationSelection | None] = {}
    for name, child in selection.items():
        if type(name) is not str:
            raise TypeError(f"{parameter} field names must be exact strings")
        if child is True:
            items[name] = None
        elif isinstance(child, Mapping) or (isinstance(child, Set) and not isinstance(child, (str, bytes))):
            if not child:
                raise ValueError(f"{parameter} nested selection {_render_path((*path, name))} cannot be empty")
            items[name] = child
        else:
            raise TypeError(f"{parameter} field {name!r} must map to True or a nested selection")
    return items


def _object_variants(
    schema: Schema,
    active: set[int] | None = None,
) -> tuple[tuple[tuple[object, ...], tuple[SerializationHook, ...]], ...]:
    """Return reachable structural field owners without following graph cycles."""

    if active is None:
        active = set()
    while isinstance(schema, (AliasSchema, ConstrainedSchema)):
        schema = schema.schema
    identity = id(schema)
    if identity in active:
        return ()
    active.add(identity)
    try:
        if isinstance(schema, NamedReferenceSchema):
            return _object_variants(schema.target, active)
        if isinstance(schema, RepresentationSchema):
            return () if schema.output is None else _object_variants(schema.output, active)
        if isinstance(schema, SpecReferenceSchema):
            artifacts = vars(schema.spec_type)["__talea_artifacts__"]
            return ((artifacts.schema.fields, artifacts.schema.serializers),)
        if isinstance(schema, (DataclassSchema, TypedDictSchema)):
            return ((schema.fields, ()),)
        if isinstance(schema, TaggedUnionSchema):
            return tuple(variant for branch in schema.branches for variant in _object_variants(branch.schema, active))
        if isinstance(schema, (SequenceSchema, VariadicTupleSchema)):
            return _object_variants(schema.item, active)
        if isinstance(schema, MappingSchema):
            return _object_variants(schema.value, active)
        if isinstance(schema, FixedTupleSchema):
            return tuple(variant for item in schema.items for variant in _object_variants(item, active))
        if isinstance(schema, UnionSchema):
            return tuple(option for item in schema.options for option in _object_variants(item, active))
        return ()
    finally:
        active.remove(identity)


def _unwrap_schema(schema: Schema) -> Schema:
    """Return one non-metadata schema without expanding named graph edges."""

    while isinstance(schema, (AliasSchema, ConstrainedSchema)):
        schema = schema.schema
    return schema


def _render_path(path: tuple[str, ...]) -> str:
    return repr(".".join(path))
