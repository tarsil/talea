"""Define Talea's compile-once ``Spec`` declaration lifecycle."""

import keyword
from annotationlib import Format
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from inspect import (
    Parameter,
    isasyncgenfunction,
    iscoroutinefunction,
    isgeneratorfunction,
    signature,
)
from threading import RLock
from types import FunctionType, MemberDescriptorType
from typing import ClassVar, Self, cast, dataclass_transform, get_type_hints
from unicodedata import normalize

from talea.declaration.models import MISSING_DEFAULT, SpecField, SpecSchema, ValidationHook
from talea.input.compilation import InputCallable, compile_input
from talea.input.emission import InputMode
from talea.input.json import JsonInput, JsonLoads, decode_json
from talea.schema.resolution import resolve_annotation
from talea.spec.construction import _ConstructorCompiler
from talea.spec.fields import _FactoryDeclaration, field
from talea.spec.hooks import _HOOK_MARKER, _HookMarker
from talea.validation.compilation import Validator, compile_validator
from talea.validation.errors import CustomValidationError, ValidationError

__all__ = ["Spec", "field"]


_INPUT_COMPILATION_LOCK = RLock()


@dataclass(slots=True)
class _InputArtifacts:
    """Own lazily compiled input functions for one Spec declaration."""

    slot_setters: tuple[Callable[[object, object], None], ...]
    mapping_input: InputCallable | None = None
    json_input: InputCallable | None = None

    def input_for(
        self,
        schema: SpecSchema,
        spec_type: type[object],
        mode: InputMode,
    ) -> InputCallable:
        """Return one boundary, compiling and publishing it atomically on first use."""

        compiled = self.mapping_input if mode == "mapping" else self.json_input
        if compiled is not None:
            return compiled
        with _INPUT_COMPILATION_LOCK:
            compiled = self.mapping_input if mode == "mapping" else self.json_input
            if compiled is None:
                compiled = compile_input(schema, spec_type, self.slot_setters, mode)
                if mode == "mapping":
                    self.mapping_input = compiled
                else:
                    self.json_input = compiled
        return compiled


@dataclass(frozen=True, slots=True)
class _SpecArtifacts:
    """Retain one declaration's canonical schema, validators, and input owner."""

    schema: SpecSchema
    validators: tuple[Validator, ...]
    inputs: _InputArtifacts


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
        if not bases:
            namespace["__slots__"] = ()
            namespace["__talea_spec__"] = True
            cls = super().__new__(metaclass, name, bases, namespace, **kwargs)
            schema = SpecSchema(())
            type.__setattr__(
                cls,
                "__talea_artifacts__",
                _SpecArtifacts(
                    schema,
                    (),
                    _InputArtifacts(()),
                ),
            )
            return cls

        spec_bases = tuple(base for base in bases if isinstance(base, _SpecMeta))
        if not spec_bases:
            raise TypeError("a Spec declaration requires at least one Spec base")
        metaclass._validate_bases(bases, spec_bases)
        annotations = metaclass._inspect_annotations(namespace)
        field_names = tuple(annotations)
        local_attribute_names = frozenset(namespace)
        declared_hooks = metaclass._inspect_hooks(namespace, field_names)
        declared_hook_names = frozenset(hook.name for hook in declared_hooks)
        inherited_schemas = tuple(cast(_SpecArtifacts, vars(base)["__talea_artifacts__"]).schema for base in spec_bases)
        inherited_names = frozenset(field.name for schema in inherited_schemas for field in schema.fields)
        metaclass._validate_declaration(namespace, bases, field_names, inherited_names)

        declarations: dict[str, object] = {}
        for field_name in field_names:
            if field_name in namespace:
                declarations[field_name] = namespace.pop(field_name)

        namespace["__slots__"] = tuple(name for name in field_names if name not in inherited_names)
        cls = super().__new__(metaclass, name, bases, namespace, **kwargs)

        resolved_annotations = (
            get_type_hints(cls, include_extras=True)
            if any(isinstance(annotation, str) for annotation in annotations.values())
            else annotations
        )
        fields = []
        for field_name in field_names:
            declaration = declarations.get(field_name, MISSING_DEFAULT)
            resolved = resolve_annotation(resolved_annotations[field_name])
            if isinstance(declaration, _FactoryDeclaration):
                fields.append(SpecField(field_name, resolved, default_factory=declaration.default_factory))
            else:
                fields.append(SpecField(field_name, resolved, default=declaration))
        declared_fields = tuple(fields)
        mro_shadowed_hooks = metaclass._mro_shadowed_hooks(cls, inherited_schemas)
        schema = SpecSchema.compose(
            inherited_schemas,
            declared_fields,
            declared_hooks,
            (local_attribute_names - declared_hook_names) | mro_shadowed_hooks,
        )
        validators = tuple(compile_validator(field.schema) for field in schema.fields)
        affected_defaults = set(field_names)
        affected_defaults.update(
            hook.fields[0] for hook in declared_hooks if hook.kind == "check" and len(hook.fields) == 1
        )
        if affected_defaults:
            metaclass._validate_static_defaults(
                schema,
                validators,
                frozenset(affected_defaults),
                cls.__name__,
            )
        slot_setters = metaclass._slot_setters(cls, schema)
        initializer = _ConstructorCompiler(cls.__name__).compile(schema, slot_setters)
        initializer.__module__ = cls.__module__
        initializer.__qualname__ = f"{cls.__qualname__}.__init__"
        initializer.__doc__ = "Validate and retain every declared field."
        type.__setattr__(cls, "__init__", initializer)
        type.__setattr__(
            cls,
            "__talea_artifacts__",
            _SpecArtifacts(schema, validators, _InputArtifacts(slot_setters)),
        )
        return cls

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
            schema = cast(_SpecArtifacts, vars(base)["__talea_artifacts__"]).schema
            for spec_field in schema.fields:
                for owner in base.__mro__:
                    if isinstance(vars(owner).get(spec_field.name), MemberDescriptorType):
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
        evaluated = cast(Callable[[Format], object], annotate)(Format.VALUE)
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

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> Self:
        """Construct ``cls`` from an untrusted Python mapping.

        The mapping boundary accepts any :class:`collections.abc.Mapping` with
        exact declared field names. Python values remain strict: primitives and
        containers are not coerced, while a nested Mapping may construct a
        nested Spec. Field transforms run once before boundary conversion and
        canonical validation. Independent declared-field failures are reported
        in declaration order, followed by unexpected keys in mapping encounter
        order. Missing defaulted fields use their retained value; factories run
        only after supplied fields and the external key set are valid.

        Args:
            data: Untrusted field values keyed by exact Spec field names.

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

        artifacts = cls.__talea_artifacts__
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
        float on the default path. Timedelta has no JSON representation in this
        release. Transforms receive the decoded JSON value before Talea's
        schema-specific conversion and run exactly once.

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
        JSON use. Outbound encoding is intentionally outside this API.
        """

        decoded = decode_json(data, loads, title=cls.__name__)
        artifacts = cls.__talea_artifacts__
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

        artifacts = cast(_SpecArtifacts, vars(type(self))["__talea_artifacts__"])
        values = ", ".join(f"{field.name}={getattr(self, field.name)!r}" for field in artifacts.schema.fields)
        return f"{type(self).__name__}({values})"
