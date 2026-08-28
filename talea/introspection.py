"""Expose immutable public descriptions of canonical Talea contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Annotated, Literal, cast, get_args, get_origin
from weakref import WeakKeyDictionary

from talea.constraints import Constraint, Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.contract import Contract
from talea.declaration.metadata import Alias
from talea.declaration.models import MISSING_DEFAULT, SerializationHook, SpecDerivation
from talea.metadata import EMPTY_METADATA, DeclarationMetadata, ExampleValue, annotation_metadata
from talea.schema.nodes import AliasSchema, ConstrainedSchema, Schema
from talea.spec.declaration import _SpecDeclaration
from talea.spec.fields import _FactoryDeclaration

__all__ = [
    "ContractInfo",
    "DerivationInfo",
    "FieldInfo",
    "SpecInfo",
    "inspect_contract",
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
    """Describe one effective Spec field without exposing mutable internals."""

    name: str
    annotation: object
    schema: Schema | None
    required: bool
    has_static_default: bool
    default: object | None
    default_factory: Callable[[], object] | None
    alias: str | None
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
    )


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
            field.metadata,
        )
        for field in inherited_fields
    }
    for name, annotation in declaration.annotations.items():
        field_default = declaration.declarations.get(name, MISSING_DEFAULT)
        default_factory = field_default.default_factory if isinstance(field_default, _FactoryDeclaration) else None
        default = MISSING_DEFAULT if default_factory is not None else field_default
        metadata = get_args(annotation)[1:] if get_origin(annotation) is Annotated else ()
        alias = next((item.name for item in metadata if isinstance(item, Alias)), None)
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
            effective_metadata,
            constraints=constraints,
        )
    hooks = tuple(hook for schema in declaration.inherited_schemas for hook in schema.hooks)
    serializers = tuple(serializer for schema in declaration.inherited_schemas for serializer in schema.serializers)
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
        tuple(
            serializer.name
            for serializer in (*serializers, *cast(tuple[SerializationHook, ...], declaration.declared_serializers))
        ),
        spec_metadata.title,
        spec_metadata.description,
        spec_metadata.examples or (),
        bool(spec_metadata.deprecated),
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
