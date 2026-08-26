"""Resolve, compile, and publish canonical Talea Spec declarations."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock
from types import MemberDescriptorType
from typing import Annotated, TypeVar, cast, get_args, get_origin, get_type_hints
from weakref import WeakValueDictionary

from talea.declaration.metadata import Alias
from talea.declaration.models import MISSING_DEFAULT, SpecField, SpecSchema, ValidationHook
from talea.declaration.policies import schema_contains_sensitive_metadata
from talea.input.artifacts import _InputArtifacts
from talea.metadata import EMPTY_METADATA, DeclarationMetadata, normalize_metadata
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
from talea.schema.resolution import AnnotationResolutionError, resolve_annotation
from talea.serialization.api import to_dict as _to_dict
from talea.serialization.artifacts import _OutputArtifacts
from talea.spec.construction import _ConstructorCompiler
from talea.spec.fields import _FactoryDeclaration
from talea.spec.generics import substitute_annotation, validate_annotation_strings
from talea.validation.compilation import (
    Validator,
    compile_current_state_validator,
    compile_validator,
)
from talea.validation.errors import CustomValidationError, ValidationError

_DECLARATION_LOCK = RLock()


class _UnresolvedReference(AnnotationResolutionError):
    """Delay a declaration whose Python namespace is not complete yet."""

    def __init__(self, owner: type[object], field: str, name: str) -> None:
        self.owner = owner
        self.field = field
        self.name = name
        TypeError.__init__(
            self,
            f"cannot resolve {name!r} for Spec {owner.__module__}.{owner.__qualname__}.{field}",
        )


@dataclass(frozen=True, slots=True)
class _SpecArtifacts:
    """Retain one declaration's canonical schema and compiled capabilities."""

    schema: SpecSchema
    validators: tuple[Validator, ...]
    current_validator: Validator | None
    inputs: _InputArtifacts
    outputs: _OutputArtifacts
    contains_sensitive: bool = False


@dataclass(slots=True)
class _SpecDeclaration:
    """Own one class's declaration identity before and after finalization."""

    owner: type[object]
    annotations: Mapping[str, object]
    declarations: dict[str, object]
    inherited_schemas: tuple[SpecSchema, ...]
    declared_hooks: tuple[ValidationHook, ...]
    declared_serializers: tuple[object, ...]
    shadowed_hook_names: frozenset[str]
    shadowed_serializer_names: frozenset[str]
    declares_to_dict: bool
    declared_metadata: DeclarationMetadata = EMPTY_METADATA
    prepared_fields: tuple[SpecField, ...] | None = None
    finalizing: bool = False
    type_params: tuple[TypeVar, ...] = ()
    generic_origin: type[object] | None = None
    generic_arguments: tuple[object, ...] = ()
    specializations: WeakValueDictionary[tuple[object, ...], type[object]] | None = None
    generic_bases: tuple[type[object], ...] = ()
    local_namespace: Mapping[str, object] | None = None
    requires_local_namespace: bool = False

    def artifacts(self) -> _SpecArtifacts:
        """Return the finalized artifacts owned by this declaration."""

        return _ensure_finalized(self.owner)

    def values_are_immutable(self, visiting: frozenset[type[object]]) -> bool:
        """Return graph-aware trust without requiring published artifacts."""

        artifacts = vars(self.owner).get("__talea_artifacts__")
        if artifacts is not None:
            return cast(_SpecArtifacts, artifacts).schema.instances_are_permanently_trusted
        fields = self.prepared_fields
        assert fields is not None
        from talea.declaration.policies import schema_values_are_immutable

        return all(schema_values_are_immutable(field.schema, visiting) for field in fields)

    def is_recursive(self) -> bool:
        """Return whether this declaration participates in its reachable graph."""

        return _reaches_spec(self.owner, self.owner, set(), root=True)


def _referenced_specs(schema: Schema) -> tuple[type[object], ...]:
    """Return canonical Spec targets reachable from one field schema."""

    if isinstance(schema, (PrimitiveSchema, TypeSchema, EnumSchema, LiteralSchema)):
        return ()
    if isinstance(schema, ConstrainedSchema):
        return _referenced_specs(schema.schema)
    if isinstance(schema, AliasSchema):
        return _referenced_specs(schema.schema)
    if isinstance(schema, SpecReferenceSchema):
        return (schema.spec_type,)
    if isinstance(schema, SequenceSchema):
        return _referenced_specs(schema.item)
    if isinstance(schema, MappingSchema):
        return (*_referenced_specs(schema.key), *_referenced_specs(schema.value))
    if isinstance(schema, TypedDictSchema):
        return tuple(target for field in schema.fields for target in _referenced_specs(field.schema))
    if isinstance(schema, VariadicTupleSchema):
        return _referenced_specs(schema.item)
    if isinstance(schema, FixedTupleSchema):
        return tuple(target for item in schema.items for target in _referenced_specs(item))
    assert isinstance(schema, UnionSchema)
    return tuple(target for option in schema.options for target in _referenced_specs(option))


def _reaches_spec(
    current: type[object],
    target: type[object],
    visited: set[type[object]],
    *,
    root: bool = False,
) -> bool:
    if current is target and not root:
        return True
    if current in visited:
        return False
    visited.add(current)
    declaration = cast(_SpecDeclaration, vars(current)["__talea_declaration__"])
    assert declaration.prepared_fields is not None
    return any(
        _reaches_spec(reference, target, visited)
        for field in declaration.prepared_fields
        for reference in _referenced_specs(field.schema)
    )


def _field_declaration_metadata(annotation: object) -> tuple[str | None, DeclarationMetadata]:
    """Extract one top-level Alias and normalize Talea-owned metadata."""

    if get_origin(annotation) is not Annotated:
        return None, EMPTY_METADATA
    extras = get_args(annotation)[1:]
    aliases = tuple(item for item in extras if isinstance(item, Alias))
    if len(aliases) > 1:
        raise TypeError("a Spec field can declare only one Alias")
    return aliases[0].name if aliases else None, normalize_metadata(extras)


def _prepare_declaration(cls: type[object]) -> None:
    """Resolve one declaration's local annotations without compiling artifacts."""

    declaration = cast(_SpecDeclaration, vars(cls)["__talea_declaration__"])
    if declaration.prepared_fields is not None:
        return
    if declaration.requires_local_namespace:
        for annotation in declaration.annotations.values():
            validate_annotation_strings(annotation)
        try:
            resolved_annotations = get_type_hints(
                cls,
                localns=declaration.local_namespace,
                include_extras=True,
            )
        except NameError as error:
            unresolved = error.name or "unknown"
            field_name = next(
                (name for name, annotation in declaration.annotations.items() if unresolved in repr(annotation)),
                next(iter(declaration.annotations), "<annotation>"),
            )
            raise _UnresolvedReference(cls, field_name, unresolved) from None
    else:
        resolved_annotations = declaration.annotations
    fields = []
    for field_name in declaration.annotations:
        field_declaration = declaration.declarations.get(field_name, MISSING_DEFAULT)
        annotation = resolved_annotations[field_name]
        if declaration.generic_origin is not None:
            origin_declaration = vars(declaration.generic_origin)["__talea_declaration__"]
            substitutions = dict(
                zip(
                    origin_declaration.type_params,
                    declaration.generic_arguments,
                    strict=True,
                )
            )
            annotation = substitute_annotation(annotation, substitutions)
        resolved = resolve_annotation(annotation)
        alias, metadata = _field_declaration_metadata(annotation)
        if isinstance(field_declaration, _FactoryDeclaration):
            fields.append(
                SpecField(
                    field_name,
                    resolved,
                    default_factory=field_declaration.default_factory,
                    alias=alias,
                    metadata=metadata,
                )
            )
        else:
            fields.append(
                SpecField(
                    field_name,
                    resolved,
                    default=field_declaration,
                    alias=alias,
                    metadata=metadata,
                )
            )
    declaration.prepared_fields = tuple(fields)


def _prepare_graph(cls: type[object], visiting: set[type[object]]) -> None:
    """Resolve every declaration in one reachable recursive component once."""

    if cls in visiting or "__talea_artifacts__" in vars(cls):
        return
    visiting.add(cls)
    _prepare_declaration(cls)
    declaration = cast(_SpecDeclaration, vars(cls)["__talea_declaration__"])
    assert declaration.prepared_fields is not None
    for spec_field in declaration.prepared_fields:
        for target in _referenced_specs(spec_field.schema):
            _prepare_graph(target, visiting)


def _finalize_graph(cls: type[object], recursive: bool | None = None) -> None:
    """Publish a prepared graph, using compiled indirection at back edges."""

    if "__talea_artifacts__" in vars(cls):
        return
    declaration = cast(_SpecDeclaration, vars(cls)["__talea_declaration__"])
    if declaration.finalizing:
        return
    declaration.finalizing = True
    try:
        assert declaration.prepared_fields is not None
        for field in declaration.prepared_fields:
            for target in _referenced_specs(field.schema):
                _finalize_graph(target)
        if recursive is None:
            recursive = declaration.is_recursive()
        _publish_declaration(cls, declaration, recursive)
    finally:
        declaration.finalizing = False


def _validate_static_defaults(
    schema: SpecSchema,
    validators: tuple[Validator, ...],
    affected_names: frozenset[str],
    title: str,
) -> None:
    """Reject invalid or transitively mutable static defaults."""

    for spec_field, validator in zip(schema.fields, validators, strict=True):
        if spec_field.name not in affected_names or not spec_field.has_static_default:
            continue
        try:
            validator(spec_field.default)
        except ValidationError as error:
            raise error.prefixed((spec_field.name,), title=title) from error.__cause__
        for hook in schema.hooks:
            if hook.kind != "check" or hook.fields != (spec_field.name,):
                continue
            try:
                result = hook.function(spec_field.default)
            except ValueError as error:
                failure = CustomValidationError(
                    "field_check",
                    hook.name,
                    spec_field.default,
                    ((spec_field.name,),),
                    title=title,
                    sensitive=bool(spec_field.metadata.sensitive),
                )
                raise failure from (None if spec_field.metadata.sensitive else error)
            if result is not None:
                raise TypeError(f"validation check {hook.name!r} must return None")
        if _contains_mutable_value(spec_field.default):
            raise TypeError(
                f"mutable static default for field {spec_field.name!r} is not supported; use field(default_factory=...)"
            )


def _slot_setters(
    cls: type[object],
    schema: SpecSchema,
) -> tuple[Callable[[object, object], None], ...]:
    """Bind the MRO-visible slot descriptor for every effective field."""

    setters = []
    for spec_field in schema.fields:
        descriptor = next(
            (vars(owner)[spec_field.name] for owner in cls.__mro__ if spec_field.name in vars(owner)),
            None,
        )
        if not isinstance(descriptor, MemberDescriptorType):
            raise TypeError(f"Spec field conflicts with an inherited attribute: {spec_field.name!r}")
        setters.append(descriptor.__set__)
    return tuple(setters)


def _publish_declaration(cls: type[object], declaration: _SpecDeclaration, recursive: bool) -> None:
    """Compile and publish one already-prepared declaration."""

    assert declaration.prepared_fields is not None
    schema = SpecSchema.compose(
        declaration.inherited_schemas,
        declaration.prepared_fields,
        declaration.declared_hooks,
        declaration.shadowed_hook_names,
        cast(tuple, declaration.declared_serializers),
        declaration.shadowed_serializer_names,
        declaration.declared_metadata,
    )
    validators = tuple(
        compile_validator(field.schema, sensitive=True) if field.metadata.sensitive else compile_validator(field.schema)
        for field in schema.fields
    )
    affected_defaults = set(declaration.annotations)
    affected_defaults.update(
        hook.fields[0] for hook in declaration.declared_hooks if hook.kind == "check" and len(hook.fields) == 1
    )
    if affected_defaults:
        _validate_static_defaults(schema, validators, frozenset(affected_defaults), cls.__name__)
    slot_setters = _slot_setters(cls, schema)
    initializer = _ConstructorCompiler(cls.__name__).compile(schema, slot_setters)
    initializer.__module__ = cls.__module__
    initializer.__qualname__ = f"{cls.__qualname__}.__init__"
    initializer.__doc__ = "Validate and retain every declared field."
    artifacts = _SpecArtifacts(
        schema,
        validators,
        compile_current_state_validator(schema) if recursive else None,
        _InputArtifacts(slot_setters, recursive),
        _OutputArtifacts(recursive),
        any(
            bool(field.metadata.sensitive) or schema_contains_sensitive_metadata(field.schema)
            for field in schema.fields
        ),
    )
    type.__setattr__(cls, "__init__", initializer)
    type.__setattr__(cls, "__talea_artifacts__", artifacts)
    declaration.local_namespace = None
    if not declaration.declares_to_dict:
        to_dict_owner = next(base for base in cls.__mro__[1:] if "to_dict" in vars(base))
        if getattr(to_dict_owner, "__talea_spec__", False):
            type.__setattr__(cls, "to_dict", _to_dict)


def _ensure_finalized(cls: type[object]) -> _SpecArtifacts:
    """Finalize one declaration graph once before its first concrete use."""

    artifacts = vars(cls).get("__talea_artifacts__")
    if artifacts is not None:
        return cast(_SpecArtifacts, artifacts)
    declaration = cast(_SpecDeclaration, vars(cls)["__talea_declaration__"])
    if declaration.type_params:
        raise TypeError(f"generic Spec {cls.__qualname__} requires concrete specialization")
    with _DECLARATION_LOCK:
        artifacts = vars(cls).get("__talea_artifacts__")
        if artifacts is None:
            _prepare_graph(cls, set())
            _finalize_graph(cls)
            artifacts = vars(cls).get("__talea_artifacts__")
    assert artifacts is not None
    return cast(_SpecArtifacts, artifacts)


def _deferred_init(instance: object, *args: object, **kwargs: object) -> None:
    """Finalize an unresolved declaration and call its concrete constructor."""

    cls = type(instance)
    _ensure_finalized(cls)
    initializer = vars(cls)["__init__"]
    assert initializer is not _deferred_init
    initializer(instance, *args, **kwargs)


def _contains_mutable_value(value: object) -> bool:
    """Return whether a validated default contains a mutable container."""

    if type(value) in (list, set, dict):
        return True
    if type(value) is tuple:
        return any(_contains_mutable_value(item) for item in value)
    if type(value) is frozenset:
        return any(_contains_mutable_value(item) for item in value)
    artifacts = getattr(type(value), "__talea_artifacts__", None)
    if artifacts is not None:
        schema = cast(SpecSchema, artifacts.schema)
        return not schema.instances_are_permanently_trusted
    return False
