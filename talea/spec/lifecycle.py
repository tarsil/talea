"""Define Talea's compile-once ``Spec`` declaration lifecycle."""

import keyword
from annotationlib import Format, call_annotate_function
from collections import ChainMap
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from inspect import (
    Parameter,
    isasyncgenfunction,
    iscoroutinefunction,
    isgeneratorfunction,
    signature,
)
from sys import _getframe
from threading import RLock
from types import FunctionType, MemberDescriptorType
from typing import (
    Annotated,
    ClassVar,
    ParamSpec,
    Protocol,
    Self,
    SupportsIndex,
    TypeVar,
    TypeVarTuple,
    cast,
    dataclass_transform,
    get_args,
    get_origin,
    get_type_hints,
)
from unicodedata import normalize
from weakref import WeakValueDictionary

from talea.declaration.metadata import Alias
from talea.declaration.models import (
    MISSING_DEFAULT,
    SpecField,
    SpecSchema,
    ValidationHook,
)
from talea.input.artifacts import _InputArtifacts
from talea.input.json import JsonInput, JsonLoads, decode_json
from talea.schema.nodes import (
    ConstrainedSchema,
    FixedTupleSchema,
    MappingSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    UnionSchema,
    VariadicTupleSchema,
)
from talea.schema.resolution import AnnotationResolutionError, resolve_annotation
from talea.serialization.api import to_dict as _to_dict, to_json as _to_json
from talea.serialization.artifacts import _OutputArtifacts
from talea.serialization.declaration import (
    inspect_serializers,
    mro_shadowed_serializers,
    validate_callback_markers,
)
from talea.spec.construction import _ConstructorCompiler
from talea.spec.fields import _FactoryDeclaration, field
from talea.spec.generics import (
    needs_local_namespace,
    normalize_specialization,
    retain_referenced_namespace,
    substitute_annotation,
    type_argument_name,
    validate_annotation_strings,
)
from talea.spec.hooks import _HOOK_MARKER, _HookMarker
from talea.validation.compilation import (
    Validator,
    compile_current_state_validator,
    compile_validator,
)
from talea.validation.errors import CustomValidationError, ValidationError

__all__ = ["Spec", "field"]


class _Subscriptable(Protocol):
    """Describe runtime annotation and Spec objects that accept specialization."""

    def __getitem__(self, argument: object, /) -> object: ...


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
    """Retain one declaration's canonical schema, validators, and input owner."""

    schema: SpecSchema
    validators: tuple[Validator, ...]
    current_validator: Validator | None
    inputs: _InputArtifacts
    outputs: _OutputArtifacts


@dataclass(slots=True)
class _SpecDeclaration:
    """Own one class's resolvable declaration identity before and after finalization."""

    owner: type[object]
    annotations: Mapping[str, object]
    declarations: dict[str, object]
    inherited_schemas: tuple[SpecSchema, ...]
    declared_hooks: tuple[ValidationHook, ...]
    declared_serializers: tuple[object, ...]
    shadowed_hook_names: frozenset[str]
    shadowed_serializer_names: frozenset[str]
    declares_to_dict: bool
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
        """Return graph-aware trust without requiring an already-published artifact."""

        artifacts = vars(self.owner).get("__talea_artifacts__")
        if artifacts is not None:
            return cast(_SpecArtifacts, artifacts).schema.instances_are_permanently_trusted
        fields = self.prepared_fields
        assert fields is not None
        from talea.declaration.policies import schema_values_are_immutable

        return all(schema_values_are_immutable(field.schema, visiting) for field in fields)

    def is_recursive(self) -> bool:
        """Return whether this declaration participates in its reachable type graph."""

        return _reaches_spec(self.owner, self.owner, set(), root=True)


def _referenced_specs(schema: Schema) -> tuple[type[object], ...]:
    """Return canonical Spec targets reachable from one field schema."""

    if isinstance(schema, ConstrainedSchema):
        return _referenced_specs(schema.schema)
    if isinstance(schema, SpecReferenceSchema):
        return (schema.spec_type,)
    if isinstance(schema, SequenceSchema):
        return _referenced_specs(schema.item)
    if isinstance(schema, MappingSchema):
        return (*_referenced_specs(schema.key), *_referenced_specs(schema.value))
    if isinstance(schema, VariadicTupleSchema):
        return _referenced_specs(schema.item)
    if isinstance(schema, FixedTupleSchema):
        return tuple(target for item in schema.items for target in _referenced_specs(item))
    if isinstance(schema, UnionSchema):
        return tuple(target for option in schema.options for target in _referenced_specs(option))
    return ()


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
        alias = _SpecMeta._field_alias(annotation)
        if isinstance(field_declaration, _FactoryDeclaration):
            fields.append(
                SpecField(
                    field_name,
                    resolved,
                    default_factory=field_declaration.default_factory,
                    alias=alias,
                )
            )
        else:
            fields.append(SpecField(field_name, resolved, default=field_declaration, alias=alias))
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
    """Publish a reachable prepared graph, using compiled indirection at back edges."""

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
    )
    validators = tuple(compile_validator(field.schema) for field in schema.fields)
    affected_defaults = set(declaration.annotations)
    affected_defaults.update(
        hook.fields[0] for hook in declaration.declared_hooks if hook.kind == "check" and len(hook.fields) == 1
    )
    if affected_defaults:
        _SpecMeta._validate_static_defaults(
            schema,
            validators,
            frozenset(affected_defaults),
            cls.__name__,
        )
    slot_setters = _SpecMeta._slot_setters(cls, schema)
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
    )
    type.__setattr__(cls, "__init__", initializer)
    type.__setattr__(cls, "__talea_artifacts__", artifacts)
    declaration.local_namespace = None
    if not declaration.declares_to_dict:
        to_dict_owner = next(base for base in cls.__mro__[1:] if "to_dict" in vars(base))
        if isinstance(to_dict_owner, _SpecMeta):
            type.__setattr__(cls, "to_dict", _to_dict)


def _ensure_finalized(cls: type[object]) -> _SpecArtifacts:
    """Finalize one declaration graph exactly once before its first concrete use."""

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
    """Finalize an unresolved declaration and tail-call its concrete constructor."""

    cls = type(instance)
    _ensure_finalized(cls)
    initializer = vars(cls)["__init__"]
    assert initializer is not _deferred_init
    initializer(instance, *args, **kwargs)


def _restore_spec_instance(
    origin: type[object],
    generic_arguments: tuple[object, ...],
    values: tuple[object, ...],
) -> object:
    """Restore one trusted pickle payload through canonical class artifacts."""

    if generic_arguments:
        argument = generic_arguments[0] if len(generic_arguments) == 1 else generic_arguments
        spec_type = cast(type[object], cast(_Subscriptable, origin)[argument])
    else:
        spec_type = origin
    artifacts = _ensure_finalized(spec_type)
    restored = object.__new__(spec_type)
    for value, setter in zip(values, artifacts.inputs.slot_setters, strict=True):
        setter(restored, value)
    return restored


@dataclass_transform(kw_only_default=True, frozen_default=True, field_specifiers=(field,))
class _SpecMeta(type):
    """Build a complete Spec declaration before its first instance exists."""

    def __new__(
        metaclass,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, object],
        **kwargs: object,
    ) -> "_SpecMeta":
        specialization = namespace.pop("__talea_specialization__", None)
        if specialization is not None:
            origin, arguments, substitutions, free_parameters = cast(
                tuple[type[object], tuple[object, ...], Mapping[TypeVar, object], tuple[TypeVar, ...]],
                specialization,
            )
            origin_declaration = cast(_SpecDeclaration, vars(origin)["__talea_declaration__"])
            namespace["__slots__"] = ()
            namespace["__type_params__"] = free_parameters
            cls = super().__new__(metaclass, name, bases, namespace, **kwargs)
            if origin_declaration.specializations is not None:
                origin_declaration.specializations[arguments] = cls
            specialized_annotations = (
                origin_declaration.annotations
                if free_parameters
                else {
                    field_name: substitute_annotation(annotation, substitutions)
                    for field_name, annotation in origin_declaration.annotations.items()
                }
            )
            type.__setattr__(cls, "__annotations__", dict(specialized_annotations))
            specialized_inherited = list(origin_declaration.inherited_schemas)
            for generic_base in origin_declaration.generic_bases:
                base_namespace = vars(generic_base)
                base_origin = cast(type[object], base_namespace["__talea_generic_origin__"])
                base_arguments = cast(tuple[object, ...], base_namespace["__talea_generic_arguments__"])
                concrete_arguments = tuple(
                    substitute_annotation(argument, substitutions) for argument in base_arguments
                )
                concrete_base = cast(_Subscriptable, base_origin)[
                    concrete_arguments[0] if len(concrete_arguments) == 1 else concrete_arguments
                ]
                specialized_inherited.append(_ensure_finalized(cast(type[object], concrete_base)).schema)
            declaration = _SpecDeclaration(
                cls,
                specialized_annotations,
                origin_declaration.declarations.copy(),
                tuple(specialized_inherited),
                origin_declaration.declared_hooks,
                origin_declaration.declared_serializers,
                origin_declaration.shadowed_hook_names,
                origin_declaration.shadowed_serializer_names,
                False,
                type_params=free_parameters,
                generic_origin=origin,
                generic_arguments=arguments,
                local_namespace=origin_declaration.local_namespace,
                requires_local_namespace=needs_local_namespace(specialized_annotations),
            )
            type.__setattr__(cls, "__talea_declaration__", declaration)
            type.__setattr__(cls, "__talea_generic_origin__", origin)
            type.__setattr__(cls, "__talea_generic_arguments__", arguments)
            type.__setattr__(cls, "__init__", _deferred_init)
            if not free_parameters:
                _prepare_declaration(cls)
                _finalize_graph(cls)
                origin_declaration.local_namespace = retain_referenced_namespace(
                    origin_declaration.annotations, origin_declaration.local_namespace
                )
            return cls
        if not bases:
            namespace["__slots__"] = ()
            namespace["__talea_spec__"] = True
            cls = super().__new__(metaclass, name, bases, namespace, **kwargs)
            schema = SpecSchema(())
            declaration = _SpecDeclaration(cls, {}, {}, (), (), (), frozenset(), frozenset(), False, ())
            type.__setattr__(cls, "__talea_declaration__", declaration)
            type.__setattr__(
                cls,
                "__talea_artifacts__",
                _SpecArtifacts(
                    schema,
                    (),
                    lambda value: value,
                    _InputArtifacts(()),
                    _OutputArtifacts(),
                ),
            )
            return cls

        spec_bases = tuple(base for base in bases if isinstance(base, _SpecMeta))
        if not spec_bases:
            raise TypeError("a Spec declaration requires at least one Spec base")
        declared_type_params = tuple(cast(tuple[object, ...], namespace.get("__type_params__", ())))
        generic_bases = tuple(
            base
            for base in spec_bases
            if "__talea_artifacts__" not in vars(base)
            and cast(_SpecDeclaration, vars(base)["__talea_declaration__"]).type_params
        )
        for base in spec_bases:
            if "__talea_artifacts__" not in vars(base) and base not in generic_bases:
                _ensure_finalized(base)
        if generic_bases and not declared_type_params:
            raise TypeError("a concrete Spec cannot inherit an unspecialized generic base")
        metaclass._validate_bases(bases, spec_bases)
        annotations = metaclass._inspect_annotations(namespace)
        field_names = tuple(annotations)
        local_attribute_names = frozenset(namespace)
        validate_callback_markers(namespace, _HOOK_MARKER)
        declared_hooks = metaclass._inspect_hooks(namespace, field_names)
        declared_serializers = inspect_serializers(namespace, field_names)
        declares_to_dict = "to_dict" in namespace
        declared_hook_names = frozenset(hook.name for hook in declared_hooks)
        declared_serializer_names = frozenset(serializer.name for serializer in declared_serializers)
        inherited_schemas = tuple(
            cast(_SpecArtifacts, vars(base)["__talea_artifacts__"]).schema
            for base in spec_bases
            if "__talea_artifacts__" in vars(base)
        )
        inherited_names = {field.name for schema in inherited_schemas for field in schema.fields}
        for base in generic_bases:
            base_origin = cast(type[object], vars(base)["__talea_generic_origin__"])
            base_declaration = cast(_SpecDeclaration, vars(base_origin)["__talea_declaration__"])
            inherited_names.update(base_declaration.annotations)
            inherited_names.update(
                field.name for schema in base_declaration.inherited_schemas for field in schema.fields
            )
        inherited_names = frozenset(inherited_names)
        metaclass._validate_declaration(namespace, bases, field_names, inherited_names)

        declarations: dict[str, object] = {}
        for field_name in field_names:
            if field_name in namespace:
                declarations[field_name] = namespace.pop(field_name)

        namespace["__slots__"] = tuple(name for name in field_names if name not in inherited_names)
        cls = super().__new__(metaclass, name, bases, namespace, **kwargs)
        mro_shadowed_hooks = metaclass._mro_shadowed_hooks(cls, inherited_schemas)
        mro_shadowed = mro_shadowed_serializers(cls, inherited_schemas, _SpecMeta)
        requires_local_namespace = needs_local_namespace(annotations)
        if requires_local_namespace:
            local_namespace = _getframe(1).f_locals
            if declared_type_params:
                local_namespace = ChainMap(local_namespace, _getframe(2).f_locals)
        else:
            local_namespace = next(
                (
                    cast(_SpecDeclaration, vars(base)["__talea_declaration__"]).local_namespace
                    for base in spec_bases
                    if cast(_SpecDeclaration, vars(base)["__talea_declaration__"]).local_namespace is not None
                ),
                None,
            )
        declaration = _SpecDeclaration(
            cls,
            annotations,
            declarations,
            inherited_schemas,
            declared_hooks,
            declared_serializers,
            (local_attribute_names - declared_hook_names) | mro_shadowed_hooks,
            (local_attribute_names - declared_serializer_names) | mro_shadowed,
            declares_to_dict,
            type_params=tuple(getattr(cls, "__type_params__", ())),
            generic_bases=generic_bases,
            local_namespace=local_namespace,
            requires_local_namespace=requires_local_namespace,
        )
        type.__setattr__(cls, "__talea_declaration__", declaration)
        type.__setattr__(cls, "__init__", _deferred_init)
        if declaration.type_params:
            if any(isinstance(parameter, (ParamSpec, TypeVarTuple)) for parameter in declaration.type_params):
                raise TypeError("Spec generics support TypeVar parameters only")
            declaration.specializations = WeakValueDictionary()
            return cls
        try:
            _prepare_declaration(cls)
        except _UnresolvedReference:
            return cls
        assert declaration.prepared_fields is not None
        targets = {target for field in declaration.prepared_fields for target in _referenced_specs(field.schema)}
        if all("__talea_artifacts__" in vars(target) for target in targets):
            if targets:
                _finalize_graph(cls)
            else:
                _publish_declaration(cls, declaration, False)
        return cls

    def __getitem__(cls, supplied: object) -> type[object]:
        """Return one class-owned concrete or partially bound Spec specialization."""

        declaration = cast(_SpecDeclaration, vars(cls)["__talea_declaration__"])
        origin = declaration.generic_origin or cls
        origin_declaration = cast(_SpecDeclaration, vars(origin)["__talea_declaration__"])
        if not origin_declaration.type_params or (
            declaration.generic_origin is not None and not declaration.type_params
        ):
            raise TypeError(f"{cls.__qualname__} is not a generic Spec")
        cache = origin_declaration.specializations
        assert cache is not None
        if declaration.generic_origin is None:
            direct_arguments = supplied if isinstance(supplied, tuple) else (supplied,)
            if len(direct_arguments) == len(origin_declaration.type_params):
                with _DECLARATION_LOCK:
                    specialized = cache.get(direct_arguments)
                    if specialized is not None:
                        return specialized
        if declaration.generic_origin is not None:
            supplied_arguments = supplied if isinstance(supplied, tuple) else (supplied,)
            if len(supplied_arguments) != len(declaration.type_params):
                raise TypeError(f"{cls.__qualname__} expects {len(declaration.type_params)} type arguments")
            partial_substitutions = dict(zip(declaration.type_params, supplied_arguments, strict=True))
            expanded = tuple(
                substitute_annotation(argument, partial_substitutions) for argument in declaration.generic_arguments
            )
            arguments, free_parameters = normalize_specialization(origin, expanded)
        else:
            arguments, free_parameters = normalize_specialization(origin, supplied)
        with _DECLARATION_LOCK:
            specialized = cache.get(arguments)
            if specialized is not None:
                return specialized
            substitutions = dict(zip(origin_declaration.type_params, arguments, strict=True))
            label = ", ".join(type_argument_name(argument) for argument in arguments)
            specialized_name = f"{origin.__name__}[{label}]"
            specialized_qualname = f"{origin.__qualname__}[{label}]"
            namespace: dict[str, object] = {
                "__module__": origin.__module__,
                "__qualname__": specialized_qualname,
                "__talea_specialization__": (origin, arguments, substitutions, free_parameters),
            }
            specialized = _SpecMeta(specialized_name, (origin,), namespace)
            cache[arguments] = specialized
            return specialized

    @staticmethod
    def _validate_bases(bases: tuple[type, ...], spec_bases: tuple[type, ...]) -> None:
        """Enforce the broadest compact layout supported by CPython slots."""

        for base in bases:
            if base in spec_bases:
                continue
            if base.__basicsize__ != object.__basicsize__ or base.__dictoffset__ != 0 or base.__weakrefoffset__ != 0:
                raise TypeError("Spec mixins must define empty slots and carry no instance state")

        storage_owners: set[type] = set()
        for base in spec_bases:
            artifacts = vars(base).get("__talea_artifacts__")
            if artifacts is not None:
                field_names = tuple(field.name for field in cast(_SpecArtifacts, artifacts).schema.fields)
            else:
                base_origin = cast(type[object], vars(base)["__talea_generic_origin__"])
                base_declaration = cast(_SpecDeclaration, vars(base_origin)["__talea_declaration__"])
                field_names = tuple(base_declaration.annotations)
            for field_name in field_names:
                for owner in base.__mro__:
                    if isinstance(vars(owner).get(field_name), MemberDescriptorType):
                        storage_owners.add(owner)
                        break
        maximal_owners = {
            owner
            for owner in storage_owners
            if not any(owner is not other and issubclass(other, owner) for other in storage_owners)
        }
        if len(maximal_owners) > 1:
            raise TypeError("Spec multiple inheritance requires one state-bearing slot lineage")

    @staticmethod
    def _inspect_annotations(namespace: dict[str, object]) -> Mapping[str, object]:
        """Evaluate one Python 3.14 annotation source before slots are fixed."""

        annotations = namespace.get("__annotations__")
        if annotations is not None:
            if not isinstance(annotations, Mapping):
                raise TypeError("a Spec declaration requires an annotations mapping")
            return annotations

        annotate = namespace.get("__annotate_func__")
        if annotate is None:
            return {}
        if not callable(annotate):
            raise TypeError("a Spec declaration requires a callable annotation function")
        evaluated = call_annotate_function(cast(Callable[[Format], dict[str, object]], annotate), Format.FORWARDREF)
        if not isinstance(evaluated, Mapping):
            raise TypeError("a Spec annotation function must return a mapping")
        return evaluated

    @staticmethod
    def _inspect_hooks(
        namespace: dict[str, object],
        field_names: tuple[object, ...],
    ) -> tuple[ValidationHook, ...]:
        """Consume marked plain functions into ordered immutable hook truth."""

        hooks = []
        for name, value in tuple(namespace.items()):
            descriptor_function = value.__func__ if isinstance(value, (staticmethod, classmethod)) else None
            if descriptor_function is not None and hasattr(descriptor_function, _HOOK_MARKER):
                raise TypeError("Talea validation hooks cannot combine with staticmethod or classmethod")
            marker = getattr(value, _HOOK_MARKER, None)
            if marker is None:
                continue
            if not isinstance(value, FunctionType) or not isinstance(marker, _HookMarker):
                raise TypeError("Talea validation hook metadata requires a plain function")
            if name in field_names:
                raise TypeError(f"validation hook conflicts with Spec field {name!r}")
            if iscoroutinefunction(value) or isasyncgenfunction(value):
                raise TypeError(f"validation hook {name!r} must be synchronous")
            if isgeneratorfunction(value):
                raise TypeError(f"validation hook {name!r} cannot be a generator")
            parameters = tuple(signature(value).parameters.values())
            expected_count = 1 if marker.kind == "transform" else len(marker.fields)
            if len(parameters) != expected_count or any(
                parameter.kind not in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
                or parameter.default is not Parameter.empty
                for parameter in parameters
            ):
                raise TypeError(
                    f"validation hook {name!r} requires exactly {expected_count} positional parameter"
                    f"{'s' if expected_count != 1 else ''}"
                )
            if marker.kind == "check" and tuple(parameter.name for parameter in parameters) != marker.fields:
                raise TypeError(f"validation check {name!r} parameters must match its field targets")
            hooks.append(ValidationHook(name, marker.kind, marker.fields, value))
            delattr(value, _HOOK_MARKER)
            namespace[name] = staticmethod(value)
        return tuple(hooks)

    @staticmethod
    def _field_alias(annotation: object) -> str | None:
        """Extract one top-level Alias while leaving type resolution canonical."""

        if get_origin(annotation) is not Annotated:
            return None
        aliases = tuple(item for item in get_args(annotation)[1:] if isinstance(item, Alias))
        if len(aliases) > 1:
            raise TypeError("a Spec field can declare only one Alias")
        return aliases[0].name if aliases else None

    @staticmethod
    def _mro_shadowed_hooks(
        cls: type,
        inherited_schemas: tuple[SpecSchema, ...],
    ) -> frozenset[str]:
        """Find inherited hooks hidden by ordinary attributes earlier in the MRO."""

        inherited_names = frozenset(hook.name for schema in inherited_schemas for hook in schema.hooks)
        shadowed = set()
        for name in inherited_names:
            owner = next((base for base in cls.__mro__[1:] if name in vars(base)), None)
            if owner is None or not isinstance(owner, _SpecMeta):
                shadowed.add(name)
                continue
            owner_schema = cast(_SpecArtifacts, vars(owner)["__talea_artifacts__"]).schema
            if all(hook.name != name for hook in owner_schema.hooks):
                shadowed.add(name)
        return frozenset(shadowed)

    @staticmethod
    def _validate_declaration(
        namespace: dict[str, object],
        bases: tuple[type, ...],
        field_names: tuple[object, ...],
        inherited_names: frozenset[str],
    ) -> None:
        """Reject declaration forms whose lifecycle is outside this campaign."""

        if "__slots__" in namespace:
            raise TypeError("Spec manages instance slots from declared fields")
        if "__init__" in namespace or "__new__" in namespace:
            raise TypeError("Spec manages construction from declared fields")
        if "__setattr__" in namespace or "__delattr__" in namespace:
            raise TypeError("Spec manages immutable field bindings")
        if "__talea_artifacts__" in namespace or "__talea_spec__" in namespace:
            raise TypeError("Spec manages internal declaration state")
        for field_name in field_names:
            if (
                not isinstance(field_name, str)
                or not field_name.isidentifier()
                or keyword.iskeyword(field_name)
                or normalize("NFKC", field_name) != field_name
                or (field_name.startswith("__") and not field_name.endswith("__"))
            ):
                raise TypeError(f"invalid Spec field name: {field_name!r}")
            if field_name not in inherited_names and any(hasattr(base, field_name) for base in bases):
                raise TypeError(f"Spec field conflicts with an inherited attribute: {field_name!r}")
        for inherited_name in inherited_names:
            if inherited_name in namespace and inherited_name not in field_names:
                raise TypeError(f"inherited Spec field cannot be replaced by a non-field attribute: {inherited_name!r}")
        undeclared_factories = tuple(
            name
            for name, value in namespace.items()
            if isinstance(value, _FactoryDeclaration) and name not in field_names
        )
        if undeclared_factories:
            raise TypeError(f"field() requires an annotation: {undeclared_factories[0]!r}")

    @staticmethod
    def _validate_static_defaults(
        schema: SpecSchema,
        validators: tuple[Validator, ...],
        affected_names: frozenset[str],
        title: str,
    ) -> None:
        """Reject invalid or transitively mutable static defaults at declaration time."""

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
                    raise CustomValidationError(
                        "field_check",
                        hook.name,
                        spec_field.default,
                        ((spec_field.name,),),
                        title=title,
                    ) from error
                if result is not None:
                    raise TypeError(f"validation check {hook.name!r} must return None")
            if _contains_mutable_value(spec_field.default):
                raise TypeError(
                    f"mutable static default for field {spec_field.name!r} is not supported; "
                    "use field(default_factory=...)"
                )

    @staticmethod
    def _slot_setters(
        cls: type,
        schema: SpecSchema,
    ) -> tuple[Callable[[object, object], None], ...]:
        """Bind the one MRO-visible slot descriptor for every effective field."""

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


def _contains_mutable_value(value: object) -> bool:
    """Return whether a validated default contains a supported mutable container."""

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


class Spec(metaclass=_SpecMeta):
    """Declare a compact object whose annotated fields validate strictly.

    Subclasses declare required fields with supported Python annotations.  At
    class creation Talea resolves those annotations into canonical schemas,
    compiles one standalone validator per field, and emits those same validation
    operations directly into a keyword-only constructor.  Repeated construction
    performs no annotation reflection, schema traversal, validator calls, or
    compilation.

    Construction accepts every declared field exactly once by keyword.  A
    direct assignment provides a validated immutable static default;
    ``field(default_factory=...)`` produces and validates an omitted value per
    instance.  Optional annotations remain required without one of those
    declarations.  Values use Talea's exact-type semantics: there is no
    coercion, supplied mutable containers retain their identity, missing fields
    and unknown keywords are rejected, and validation errors begin with the
    failing field name.

    Instances use slots derived from declaration order and retain only field
    values.  They have no instance dictionary or per-instance schema metadata.
    Field bindings are immutable after successful construction.  Declarations
    containing only transitively immutable schemas are permanently trusted;
    declarations containing list, set, or dictionary values remain validated
    but are not eligible for Talea's no-revalidation trust path.  Equality and
    hashing keep ordinary Python identity semantics.  Subclasses inherit and
    may override fields while compiling one flat effective constructor.
    Multiple inheritance is supported when CPython can preserve one
    state-bearing slot lineage; additional mixins must use empty slots.

    ``transform`` callbacks explicitly prepare inbound field values before the
    emitted structural checks. ``check`` callbacks assert field or cross-field
    invariants after structure and before slot commitment. Declarations without
    hooks retain the same generated construction path.
    """

    __talea_artifacts__: ClassVar[_SpecArtifacts]

    def __setattr__(self, name: str, value: object) -> None:
        """Reject mutation so a validated Spec cannot silently become invalid."""

        raise AttributeError(f"{type(self).__name__} instances are immutable")

    def __copy__(self) -> Self:
        """Return a shallow copy without repeating validation or lifecycle hooks."""

        spec_type = type(self)
        artifacts = _ensure_finalized(spec_type)
        copied = object.__new__(spec_type)
        for spec_field, setter in zip(artifacts.schema.fields, artifacts.inputs.slot_setters, strict=True):
            setter(copied, getattr(self, spec_field.name))
        return copied

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        """Return a graph-preserving deep copy of this validated instance."""

        from copy import deepcopy

        spec_type = type(self)
        artifacts = _ensure_finalized(spec_type)
        copied = object.__new__(spec_type)
        memo[id(self)] = copied
        for spec_field, setter in zip(artifacts.schema.fields, artifacts.inputs.slot_setters, strict=True):
            setter(copied, deepcopy(getattr(self, spec_field.name), memo))
        return copied

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> tuple[object, tuple[object, ...]]:
        """Describe an acyclic instance for trusted Python pickle reconstruction."""

        del protocol
        spec_type = type(self)
        declaration = vars(spec_type)["__talea_declaration__"]
        origin = declaration.generic_origin or spec_type
        artifacts = _ensure_finalized(spec_type)
        values = tuple(getattr(self, spec_field.name) for spec_field in artifacts.schema.fields)
        return _restore_spec_instance, (origin, declaration.generic_arguments, values)

    to_dict = _to_dict
    to_json = _to_json

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> Self:
        """Construct ``cls`` from an untrusted Python mapping.

        The mapping boundary accepts any :class:`collections.abc.Mapping` with
        each field's canonical external name (its Alias when declared, otherwise
        its Python name). Python values remain strict: primitives and
        containers are not coerced, while a nested Mapping may construct a
        nested Spec. Field transforms run once before boundary conversion and
        canonical validation. Independent declared-field failures are reported
        in declaration order, followed by unexpected keys in mapping encounter
        order. Missing defaulted fields use their retained value; factories run
        only after supplied fields and the external key set are valid.

        Args:
            data: Untrusted field values keyed by canonical external names.

        Returns:
            A fully validated immutable instance of the invoked Spec subclass.

        Raises:
            ValidationError: If the input is not a Mapping, a field is missing
                or unexpected, nested conversion fails, or Talea validation
                rejects one or more independent fields.
            Exception: Unexpected exceptions from Mapping operations, transforms,
                checks, or factories retain their documented lifecycle behavior.

        The callable is compiled once on first Mapping use and cached on the
        declaration. Repeated calls perform no annotation reflection or runtime
        schema interpretation.
        """

        artifacts = _ensure_finalized(cls)
        construct = artifacts.inputs.mapping_input
        if construct is None:
            construct = artifacts.inputs.input_for(artifacts.schema, cls, "mapping")
        # The internal callable erases the dynamic class that it was compiled to allocate.
        return construct(data)  # ty: ignore[invalid-return-type]

    @classmethod
    def from_json(
        cls,
        data: JsonInput,
        *,
        loads: JsonLoads | None = None,
    ) -> Self:
        """Decode JSON and construct ``cls`` through Talea's input contract.

        ``str``, ``bytes``, and ``bytearray`` are accepted. The default standard
        library decoder rejects duplicate keys, NaN, and Infinity and preserves
        fractional tokens for precision-safe Decimal fields. ``loads`` may
        select an external decoder per call; it must accept the supplied input
        and return a JSON-native Python tree. Decoder syntax choices never
        replace Talea's compiled schema conversion, transforms, constraints, or
        checks.

        JSON arrays convert to the declared list, tuple, set, or frozenset
        representation. Strings convert to supported UUID, temporal, path, IP,
        and JSON-compatible Enum contracts. Decimal numbers never pass through
        float on the default path; exact Decimal strings are also accepted.
        Timedelta uses ISO 8601 duration strings and bytes use strict base64.
        Transforms receive the decoded JSON value before Talea's schema-specific
        conversion and run exactly once.

        Args:
            data: Serialized JSON text or bytes accepted by the selected decoder.
            loads: Optional one-argument decoder callable for this operation.

        Returns:
            A fully validated immutable instance of the invoked Spec subclass.

        Raises:
            ValidationError: If decoding fails, default decoding finds a
                duplicate/non-standard token, the top level is not an object,
                or converted field data violates the Spec contract.
            Exception: Non-``ValueError`` exceptions from a custom decoder and
                unexpected application callback exceptions propagate unchanged.

        Codec state is per call and is never retained by a Spec class or
        instance. The decoded-value boundary is compiled and cached on first
        JSON use. :meth:`to_json` owns the symmetric outbound operation.
        """

        decoded = decode_json(data, loads, title=cls.__name__)
        artifacts = _ensure_finalized(cls)
        construct = artifacts.inputs.json_input
        if construct is None:
            construct = artifacts.inputs.input_for(artifacts.schema, cls, "json")
        # The internal callable erases the dynamic class that it was compiled to allocate.
        return construct(decoded)  # ty: ignore[invalid-return-type]

    def __delattr__(self, name: str) -> None:
        """Reject deletion so a validated Spec cannot lose a required value."""

        raise AttributeError(f"{type(self).__name__} instances are immutable")

    def __repr__(self) -> str:
        """Return the declaration name and current field values in order."""

        artifacts = _ensure_finalized(type(self))
        values = ", ".join(f"{field.name}={getattr(self, field.name)!r}" for field in artifacts.schema.fields)
        return f"{type(self).__name__}({values})"
