"""Expose immutable public descriptions of canonical Talea contracts."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Annotated, Literal, cast, get_args, get_origin
from weakref import WeakKeyDictionary

from talea.callables.api import _callable_schema
from talea.callables.models import (
    MISSING_DEFAULT as CALLABLE_MISSING_DEFAULT,
    CallableKind,
    ParameterKind,
)
from talea.constraints import Constraint, Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.contract import Contract
from talea.declaration.metadata import Alias
from talea.declaration.models import (
    MISSING_DEFAULT,
    MISSING_SERIALIZER_OUTPUT,
    SerializationHook,
    SpecDerivation,
)
from talea.declaration.policies import (
    schema_contains_representation,
    schema_input_directions_are_available,
    schema_output_directions_are_available,
)
from talea.metadata import (
    EMPTY_METADATA,
    DeclarationMetadata,
    ExampleValue,
    annotation_metadata,
)
from talea.schema.nodes import (
    AliasSchema,
    ConstrainedSchema,
    DataclassSchema,
    FixedTupleSchema,
    MappingSchema,
    NamedReferenceSchema,
    NamedTupleSchema,
    RepresentationSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    TaggedUnionSchema,
    TypedDictSchema,
    UnionSchema,
    VariadicTupleSchema,
    _accepted_input_names,
)
from talea.spec.declaration import _SpecDeclaration
from talea.spec.fields import _FactoryDeclaration

__all__ = [
    "CallableInfo",
    "ContractInfo",
    "DerivationInfo",
    "FieldInfo",
    "ParameterInfo",
    "RepresentationInfo",
    "SerializerInfo",
    "SpecInfo",
    "inspect_contract",
    "inspect_callable",
    "inspect_spec",
]

type Operation = str

_OPERATIONS = (
    "strict_python",
    "external_python",
    "json_input",
    "python_output",
    "json_output",
)
_SPEC_INFO_CACHE: WeakKeyDictionary[type[object], SpecInfo] = WeakKeyDictionary()
_SPEC_INFO_LOCK = RLock()
_CONSTRAINT_TYPES = (Gt, Ge, Lt, Le, MultipleOf, MinLength, MaxLength, Pattern)


@dataclass(frozen=True, slots=True)
class FieldInfo:
    """Describe one effective Spec field without exposing mutable internals.

    ``name`` is the Python attribute. ``external_name`` is the current external
    input/output spelling, ``legacy_names`` is ordered historical input
    vocabulary, and ``accepted_input_names`` is their immutable normalized
    union in deterministic declaration order. Compiler lookup artifacts remain
    private.
    """

    name: str
    annotation: object
    schema: Schema | None
    required: bool
    has_static_default: bool
    default: object | None
    default_factory: Callable[[], object] | None
    alias: str | None
    external_name: str
    legacy_names: tuple[str, ...]
    accepted_input_names: tuple[str, ...]
    constraints: tuple[Constraint, ...]
    title: str | None
    description: str | None
    examples: tuple[ExampleValue, ...]
    deprecated: bool
    read_only: bool
    write_only: bool
    sensitive: bool
    omittable: bool = False


@dataclass(frozen=True, slots=True)
class ParameterInfo:
    """Describe one callable parameter without exposing defaults or execution."""

    name: str
    kind: ParameterKind
    schema: Schema | None
    required: bool
    has_default: bool
    receiver: bool = False
    variadic_semantics: Literal["items", "values", "unpack_typed_dict"] | None = None


@dataclass(frozen=True, slots=True)
class CallableInfo:
    """Describe one callable boundary as immutable canonical projection."""

    signature: inspect.Signature
    parameters: tuple[ParameterInfo, ...]
    return_schema: Schema
    is_async: bool
    callable_kind: CallableKind = "function"


@dataclass(frozen=True, slots=True)
class DerivationInfo:
    """Expose immutable source, selection, and direction truth for a derived Spec."""

    source: type[object]
    retained_fields: tuple[str, ...]
    omitted_fields: tuple[str, ...]
    selection: str
    partial: bool
    explicit_name: str | None
    mode: Literal["input", "output"] | None = None


@dataclass(frozen=True, slots=True)
class SpecInfo:
    """Describe one finalized Spec declaration as immutable public truth."""

    spec: type[object]
    fields: tuple[FieldInfo, ...]
    generic_parameters: tuple[object, ...]
    generic_origin: type[object] | None
    generic_arguments: tuple[object, ...]
    recursive: bool | None
    permanently_trusted: bool
    hook_names: tuple[str, ...]
    serializer_names: tuple[str, ...]
    title: str | None
    description: str | None
    examples: tuple[ExampleValue, ...]
    deprecated: bool
    presence_aware: bool = False
    derivation: DerivationInfo | None = None
    operations: tuple[Operation, ...] = _OPERATIONS
    representations: tuple[RepresentationInfo, ...] = ()
    serializers: tuple[SerializerInfo, ...] = ()


@dataclass(frozen=True, slots=True)
class ContractInfo:
    """Describe one arbitrary Contract without exposing compiler artifacts."""

    annotation: object
    schema: Schema
    title: str | None
    description: str | None
    examples: tuple[ExampleValue, ...]
    deprecated: bool
    read_only: bool
    write_only: bool
    sensitive: bool
    operations: tuple[Operation, ...] = _OPERATIONS
    representations: tuple[RepresentationInfo, ...] = ()


@dataclass(frozen=True, slots=True)
class RepresentationInfo:
    """Describe directional schema truth without exposing executable callbacks."""

    internal: Schema
    input: Schema | None
    output: Schema | None
    has_loader: bool
    has_dumper: bool


@dataclass(frozen=True, slots=True)
class SerializerInfo:
    """Describe one field serializer without exposing its callback."""

    name: str
    field: str
    has_declared_output: bool
    output_schema: Schema | None


def inspect_spec(spec: type[object]) -> SpecInfo:
    """Return one retained immutable description of a Spec declaration.

    Args:
        spec: A Talea Spec class, including inherited, generic, specialized, or
            recursive declarations.

    Returns:
        A cached read-only projection of canonical declaration truth.

    Raises:
        TypeError: If ``spec`` is not a Talea Spec class.
    """

    if not isinstance(spec, type) or not getattr(spec, "__talea_spec__", False):
        raise TypeError("inspect_spec requires a Spec class")
    cached = _SPEC_INFO_CACHE.get(spec)
    if cached is not None:
        return cached
    with _SPEC_INFO_LOCK:
        cached = _SPEC_INFO_CACHE.get(spec)
        if cached is None:
            declaration = cast(_SpecDeclaration, vars(spec)["__talea_declaration__"])
            if declaration.type_params:
                cached = _inspect_open_generic(spec, declaration)
                _SPEC_INFO_CACHE[spec] = cached
                return cached
            artifacts = declaration.artifacts()
            fields = tuple(
                _field_info(
                    field.name,
                    _field_annotation(spec, field.name),
                    field.schema,
                    field.required,
                    field.has_static_default,
                    None if field.default is MISSING_DEFAULT else field.default,
                    field.default_factory,
                    field.alias,
                    field.external_name,
                    field.legacy_names,
                    field.accepted_input_names,
                    field.metadata,
                    field.omittable,
                )
                for field in artifacts.schema.fields
            )
            cached = SpecInfo(
                spec,
                fields,
                declaration.type_params,
                declaration.generic_origin,
                declaration.generic_arguments,
                declaration.is_recursive(),
                artifacts.schema.instances_are_permanently_trusted,
                tuple(hook.name for hook in artifacts.schema.hooks),
                tuple(serializer.name for serializer in artifacts.schema.serializers),
                artifacts.schema.metadata.title,
                artifacts.schema.metadata.description,
                artifacts.schema.metadata.examples or (),
                bool(artifacts.schema.metadata.deprecated),
                artifacts.schema.presence_aware,
                _derivation_info(artifacts.schema.derivation),
                _schema_operations(tuple(field.schema for field in artifacts.schema.fields)),
                _representation_infos(tuple(field.schema for field in artifacts.schema.fields)),
                tuple(_serializer_info(serializer) for serializer in artifacts.schema.serializers),
            )
            _SPEC_INFO_CACHE[spec] = cached
    return cached


def inspect_contract[T](contract: Contract[T]) -> ContractInfo:
    """Return an immutable description of an arbitrary Talea Contract.

    The canonical Schema is itself frozen structural truth. Compiled functions,
    locks, lazy publication state, and codec choices remain private.

    Raises:
        TypeError: If ``contract`` is not a Talea Contract instance.
    """

    if not isinstance(contract, Contract):
        raise TypeError("inspect_contract requires a Contract instance")
    metadata = contract._artifacts.metadata
    return ContractInfo(
        contract.annotation,
        contract._artifacts.schema,
        metadata.title,
        metadata.description,
        metadata.examples or (),
        bool(metadata.deprecated),
        bool(metadata.read_only),
        bool(metadata.write_only),
        bool(metadata.sensitive),
        _schema_operations((contract._artifacts.schema,)),
        _representation_infos((contract._artifacts.schema,)),
    )


def inspect_callable(function: Callable[..., object]) -> CallableInfo:
    """Return an immutable description of a ``validate_call`` boundary.

    The description projects the retained callable contract. Generated source,
    validators, globals, caches, and the original callable remain private;
    ordinary Python tooling can obtain the original through ``__wrapped__``.

    Raises:
        TypeError: If ``function`` is not a Talea validated-call wrapper.
    """

    contract = _callable_schema(function)
    return CallableInfo(
        contract.signature,
        tuple(
            ParameterInfo(
                parameter.name,
                parameter.kind,
                parameter.schema,
                parameter.required,
                parameter.default is not CALLABLE_MISSING_DEFAULT,
                parameter.role == "receiver",
                (
                    "items"
                    if parameter.kind == "VAR_POSITIONAL"
                    else "unpack_typed_dict"
                    if parameter.unpack_typed_dict
                    else "values"
                    if parameter.kind == "VAR_KEYWORD"
                    else None
                ),
            )
            for parameter in contract.parameters
        ),
        contract.return_schema,
        contract.is_async,
        contract.callable_kind,
    )


def _schema_operations(schemas: tuple[Schema, ...]) -> tuple[Operation, ...]:
    """Project only operations implemented for reachable representation truth."""

    if not any(schema_contains_representation(schema) for schema in schemas):
        return _OPERATIONS
    operations: tuple[Operation, ...] = ("strict_python",)
    if all(schema_input_directions_are_available(schema) for schema in schemas):
        operations = (*operations, "external_python", "json_input")
    if all(schema_output_directions_are_available(schema) for schema in schemas):
        operations = (*operations, "python_output", "json_output")
    return operations


def _representation_infos(schemas: tuple[Schema, ...]) -> tuple[RepresentationInfo, ...]:
    """Project each reachable declaration once without retaining callback authority."""

    found: list[RepresentationInfo] = []
    seen_representations: set[int] = set()
    visited: set[object] = set()

    def visit(schema: Schema) -> None:
        while isinstance(schema, (AliasSchema, ConstrainedSchema)):
            schema = schema.schema
        if isinstance(schema, RepresentationSchema):
            identity = id(schema._declaration)
            if identity in seen_representations:
                return
            seen_representations.add(identity)
            found.append(
                RepresentationInfo(
                    schema.internal,
                    schema.input,
                    schema.output,
                    schema.input is not None,
                    schema.output is not None,
                )
            )
            visit(schema.internal)
            if schema.input is not None:
                visit(schema.input)
            if schema.output is not None:
                visit(schema.output)
            return
        if isinstance(schema, NamedReferenceSchema):
            if schema.identity in visited:
                return
            visited.add(schema.identity)
            visit(schema.target)
            return
        if isinstance(schema, SpecReferenceSchema):
            if schema.spec_type in visited:
                return
            visited.add(schema.spec_type)
            artifacts = vars(schema.spec_type)["__talea_artifacts__"]
            for field in artifacts.schema.fields:
                visit(field.schema)
            return
        if isinstance(schema, DataclassSchema):
            identity = schema.identity or schema.dataclass_type
            visited.add(identity)
            for field in schema.fields:
                visit(field.schema)
            return
        if isinstance(schema, NamedTupleSchema):
            identity = schema.identity or schema.named_tuple_type
            if identity in visited:
                return
            visited.add(identity)
            for field in schema.fields:
                visit(field.schema)
            return
        if isinstance(schema, SequenceSchema):
            visit(schema.item)
        elif isinstance(schema, MappingSchema):
            visit(schema.key)
            visit(schema.value)
        elif isinstance(schema, TypedDictSchema):
            for field in schema.fields:
                visit(field.schema)
        elif isinstance(schema, TaggedUnionSchema):
            for branch in schema.branches:
                visit(branch.schema)
        elif isinstance(schema, VariadicTupleSchema):
            visit(schema.item)
        elif isinstance(schema, FixedTupleSchema):
            for item in schema.items:
                visit(item)
        elif isinstance(schema, UnionSchema):
            for option in schema.options:
                visit(option)

    for schema in schemas:
        visit(schema)
    return tuple(found)


def _constraints(schema: Schema) -> tuple[Constraint, ...]:
    if isinstance(schema, AliasSchema):
        return _constraints(schema.schema)
    if isinstance(schema, ConstrainedSchema):
        return (*_constraints(schema.schema), *schema.constraints)
    return ()


def _inspect_open_generic(spec: type[object], declaration: _SpecDeclaration) -> SpecInfo:
    """Project declaration truth that cannot yet have a concrete Schema."""

    inherited_fields = tuple(field for schema in declaration.inherited_schemas for field in schema.fields)
    projected: dict[str, FieldInfo] = {
        field.name: _field_info(
            field.name,
            _field_annotation(spec, field.name),
            field.schema,
            field.required,
            field.has_static_default,
            None if field.default is MISSING_DEFAULT else field.default,
            field.default_factory,
            field.alias,
            field.external_name,
            field.legacy_names,
            field.accepted_input_names,
            field.metadata,
        )
        for field in inherited_fields
    }
    for name, annotation in declaration.annotations.items():
        field_default = declaration.declarations.get(name, MISSING_DEFAULT)
        default_factory = field_default.default_factory if isinstance(field_default, _FactoryDeclaration) else None
        default = MISSING_DEFAULT if default_factory is not None else field_default
        metadata = get_args(annotation)[1:] if get_origin(annotation) is Annotated else ()
        alias_marker = next((item for item in metadata if isinstance(item, Alias)), None)
        alias = None if alias_marker is None else alias_marker.name
        external_name = name if alias_marker is None else alias_marker.name
        legacy_names = () if alias_marker is None else alias_marker.legacy
        constraints = cast(
            tuple[Constraint, ...], tuple(item for item in metadata if isinstance(item, _CONSTRAINT_TYPES))
        )
        inherited_metadata = projected.get(name)
        local_metadata = annotation_metadata(annotation)
        effective_metadata = (
            EMPTY_METADATA
            if inherited_metadata is None
            else DeclarationMetadata(
                inherited_metadata.title,
                inherited_metadata.description,
                inherited_metadata.examples or None,
                inherited_metadata.deprecated,
                inherited_metadata.read_only,
                inherited_metadata.write_only,
                inherited_metadata.sensitive,
            )
        ).merged(local_metadata)
        projected[name] = _field_info(
            name,
            annotation,
            None,
            default is MISSING_DEFAULT and default_factory is None,
            default is not MISSING_DEFAULT,
            None if default is MISSING_DEFAULT else default,
            default_factory,
            alias,
            external_name,
            legacy_names,
            _accepted_input_names(external_name, legacy_names),
            effective_metadata,
            constraints=constraints,
        )
    hooks = tuple(hook for schema in declaration.inherited_schemas for hook in schema.hooks)
    serializers = tuple(serializer for schema in declaration.inherited_schemas for serializer in schema.serializers)
    effective_serializers = (*serializers, *cast(tuple[SerializationHook, ...], declaration.declared_serializers))
    spec_metadata = EMPTY_METADATA
    for schema in reversed(declaration.inherited_schemas):
        spec_metadata = spec_metadata.merged(schema.metadata)
    spec_metadata = spec_metadata.merged(declaration.declared_metadata)
    return SpecInfo(
        spec,
        tuple(projected.values()),
        declaration.type_params,
        declaration.generic_origin,
        declaration.generic_arguments,
        None,
        False,
        tuple(hook.name for hook in (*hooks, *declaration.declared_hooks)),
        tuple(serializer.name for serializer in effective_serializers),
        spec_metadata.title,
        spec_metadata.description,
        spec_metadata.examples or (),
        bool(spec_metadata.deprecated),
        serializers=tuple(_serializer_info(serializer) for serializer in effective_serializers),
    )


def _serializer_info(serializer: SerializationHook) -> SerializerInfo:
    """Project safe serializer declaration truth without retaining execution."""

    return SerializerInfo(
        serializer.name,
        serializer.field,
        serializer.output_schema is not None or serializer.output_annotation is not MISSING_SERIALIZER_OUTPUT,
        serializer.output_schema,
    )


def _field_info(
    name: str,
    annotation: object,
    schema: Schema | None,
    required: bool,
    has_static_default: bool,
    default: object | None,
    default_factory: Callable[[], object] | None,
    alias: str | None,
    external_name: str,
    legacy_names: tuple[str, ...],
    accepted_input_names: tuple[str, ...],
    metadata: DeclarationMetadata,
    omittable: bool = False,
    constraints: tuple[Constraint, ...] | None = None,
) -> FieldInfo:
    return FieldInfo(
        name,
        annotation,
        schema,
        required,
        has_static_default,
        default,
        default_factory,
        alias,
        external_name,
        legacy_names,
        accepted_input_names,
        _constraints(schema) if constraints is None and schema is not None else constraints or (),
        metadata.title,
        metadata.description,
        metadata.examples or (),
        bool(metadata.deprecated),
        bool(metadata.read_only),
        bool(metadata.write_only),
        bool(metadata.sensitive),
        omittable,
    )


def _derivation_info(derivation: SpecDerivation | None) -> DerivationInfo | None:
    if derivation is None:
        return None
    return DerivationInfo(
        derivation.source,
        derivation.retained_fields,
        derivation.omitted_fields,
        derivation.selection,
        derivation.partial,
        derivation.explicit_name,
        derivation.mode,
    )


def _field_annotation(spec: type[object], name: str) -> object:
    """Return the closest effective Python annotation for one field."""

    return next(
        declaration.annotations[name]
        for owner in spec.__mro__
        if (declaration := vars(owner).get("__talea_declaration__")) is not None
        if name in declaration.annotations
    )
