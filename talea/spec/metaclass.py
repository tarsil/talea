"""Build Talea Spec classes from ordinary Python class namespaces."""

import keyword
from annotationlib import Format, call_annotate_function
from collections import ChainMap
from collections.abc import Callable, Iterable, Mapping
from inspect import (
    Parameter,
    cleandoc,
    isasyncgenfunction,
    iscoroutinefunction,
    isgeneratorfunction,
    signature,
)
from sys import _getframe
from types import FunctionType, MemberDescriptorType
from typing import (
    ParamSpec,
    Protocol,
    TypeVar,
    TypeVarTuple,
    cast,
    dataclass_transform,
)
from unicodedata import normalize
from weakref import WeakValueDictionary

from talea.declaration.models import SpecSchema, ValidationHook
from talea.input.artifacts import _InputArtifacts
from talea.metadata import EMPTY_METADATA, normalize_metadata
from talea.serialization.artifacts import _OutputArtifacts
from talea.serialization.declaration import (
    inspect_serializers,
    mro_shadowed_serializers,
    validate_callback_markers,
)
from talea.spec.declaration import (
    _deferred_init,
    _DerivedSpecPlan,
    _ensure_finalized,
    _finalize_graph,
    _prepare_declaration,
    _publish_declaration,
    _referenced_specs,
    _SpecArtifacts,
    _SpecDeclaration,
    _UnresolvedReference,
)
from talea.spec.fields import _FactoryDeclaration, field
from talea.spec.generics import (
    needs_local_namespace,
    retain_referenced_namespace,
    substitute_annotation,
)
from talea.spec.hooks import _HOOK_MARKER, _HookMarker
from talea.spec.specialization import specialize_spec


class _Subscriptable(Protocol):
    """Describe a generic Spec base used during specialization."""

    def __getitem__(self, argument: object, /) -> object: ...


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
        metadata_items = kwargs.pop("metadata", ())
        derived_plan = kwargs.pop("_talea_derived_plan", None)
        if derived_plan is not None:
            derived_plan = cast(_DerivedSpecPlan, derived_plan)
            namespace["__slots__"] = (
                *(field.name for field in derived_plan.fields),
                *(("__talea_presence__",) if derived_plan.derivation.partial else ()),
            )
            cls = super().__new__(metaclass, name, bases, namespace, **kwargs)
            declaration = _SpecDeclaration(
                cls,
                derived_plan.annotations,
                {},
                (),
                derived_plan.hooks,
                derived_plan.serializers,
                frozenset(),
                frozenset(),
                False,
                declared_metadata=derived_plan.metadata,
                prepared_fields=derived_plan.fields,
                prepared_serializers=derived_plan.serializers,
                derivation=derived_plan.derivation,
            )
            type.__setattr__(cls, "__talea_declaration__", declaration)
            _publish_declaration(cls, declaration, False)
            return cls
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
                declared_metadata=origin_declaration.declared_metadata,
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
            declaration = _SpecDeclaration(
                cls,
                {},
                {},
                (),
                (),
                (),
                frozenset(),
                frozenset(),
                False,
                prepared_fields=(),
            )
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
        raw_doc = namespace.get("__doc__")
        doc = cleandoc(raw_doc) if isinstance(raw_doc, str) and raw_doc.strip() else None
        if not isinstance(metadata_items, Iterable) or isinstance(metadata_items, (str, bytes)):
            raise TypeError("Spec metadata must be an iterable of Talea marker values")
        declared_metadata = (
            EMPTY_METADATA
            if metadata_items == () and doc is None
            else normalize_metadata(metadata_items, spec=True, doc=doc)
        )
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
            declared_metadata=declared_metadata,
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
        assert declaration.prepared_serializers is not None
        targets = {
            target
            for schema in (
                *(field.schema for field in declaration.prepared_fields),
                *(
                    serializer.output_schema
                    for serializer in declaration.prepared_serializers
                    if serializer.output_schema is not None
                ),
            )
            for target in _referenced_specs(schema)
        }
        if all("__talea_artifacts__" in vars(target) for target in targets):
            if targets:
                _finalize_graph(cls)
            else:
                _publish_declaration(cls, declaration, False)
        return cls

    def __getitem__(cls, supplied: object) -> type[object]:
        """Return one class-owned concrete or partially bound Spec specialization."""

        return specialize_spec(cls, supplied)

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
        """Reject declaration forms whose lifecycle is owned by Spec."""

        if "__slots__" in namespace:
            raise TypeError("Spec manages instance slots from declared fields")
        if "__init__" in namespace or "__new__" in namespace:
            raise TypeError("Spec manages construction from declared fields")
        if "__setattr__" in namespace or "__delattr__" in namespace:
            raise TypeError("Spec manages immutable field bindings")
        if any(name.startswith("__talea_") for name in namespace):
            raise TypeError("Spec manages internal declaration state")
        for field_name in field_names:
            if (
                not isinstance(field_name, str)
                or not field_name.isidentifier()
                or keyword.iskeyword(field_name)
                or normalize("NFKC", field_name) != field_name
                or (field_name.startswith("__") and not field_name.endswith("__"))
                or field_name.startswith("__talea_")
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
