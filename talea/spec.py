"""Define Talea's compile-once ``Spec`` declaration lifecycle."""

import keyword
from annotationlib import Format
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import FunctionType, MemberDescriptorType
from typing import cast, dataclass_transform, get_type_hints
from unicodedata import normalize

from talea._declaration import MISSING_DEFAULT, SpecField, SpecSchema
from talea.annotations import resolve_annotation
from talea.validation import (
    ValidationError,
    Validator,
    _GeneratedNames,
    _ValidationEmitter,
    compile_validator,
)

__all__ = ["Spec", "field"]


@dataclass(frozen=True, slots=True)
class _FactoryDeclaration[T]:
    """Carry factory syntax from a class body into canonical declaration truth."""

    default_factory: Callable[[], T]


def field[T](*, default_factory: Callable[[], T]) -> T:
    """Declare a field whose omitted value is produced for each Spec instance.

    Args:
        default_factory: A zero-argument callable.  Talea calls it once for each
            construction that omits the field, then validates its result using
            the field's compiled validator.  Explicit values bypass the factory.

    Returns:
        A declaration marker consumed when the enclosing ``Spec`` class is
        created.  It is never stored on Spec instances or used on their hot
        paths.

    Raises:
        TypeError: If ``default_factory`` is not callable.

    This deliberately small API owns factory declaration only.  Static defaults
    use ordinary assignment, such as ``active: bool = True``.
    """

    if not callable(default_factory):
        raise TypeError("field default_factory must be callable")
    return cast(T, _FactoryDeclaration(default_factory))


class _FactorySentinel:
    """Provide a readable generated-signature marker for factory fields."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<factory>"


_FACTORY_SENTINEL = _FactorySentinel()


@dataclass(frozen=True, slots=True)
class _SpecArtifacts:
    """Retain one declaration's canonical schema and compiled validators."""

    schema: SpecSchema
    validators: tuple[Validator, ...]


class _ConstructorCompiler:
    """Compile a keyword-only initializer specialized for one Spec schema."""

    def compile(
        self,
        schema: SpecSchema,
        slot_setters: tuple[Callable[[object, object], None], ...],
    ) -> FunctionType:
        """Return an initializer containing inline validation and slot writes.

        ``slot_setters`` are bound C-level member-descriptor operations from
        the class being initialized.  Calling them after every validation has
        succeeded bypasses the public immutable-assignment hook without making
        the class or instance temporarily writable.
        """

        fields = schema.fields
        field_names = tuple(field.name for field in fields)
        names = _GeneratedNames(field_names)
        instance_name = names.allocate("instance")
        if all(field.required for field in fields):
            default_names: dict[int, str] = {}
            factory_names: dict[int, str] = {}
        else:
            default_names = {
                index: names.allocate(f"default_{index}")
                for index, field in enumerate(fields)
                if field.has_static_default
            }
            factory_names = {
                index: names.allocate(f"factory_{index}")
                for index, field in enumerate(fields)
                if field.default_factory is not None
            }
        factory_sentinel_name = names.allocate("factory_sentinel") if factory_names else ""
        exception_type_name = names.allocate("exception_type") if factory_names else ""
        type_error_name = names.allocate("type_error") if factory_names else ""
        slot_setter_names: tuple[str, ...] = ()
        namespace: dict[str, object]
        if not field_names:
            source = f"def __init__({instance_name}):\n    pass"
            namespace = {"__name__": __name__}
        else:
            field_names_name = names.allocate("field_names")
            factory_error_names = {index: names.allocate(f"factory_error_{index}") for index in factory_names}
            slot_setter_names = tuple(names.allocate(f"slot_{index}") for index in range(len(field_names)))
            parameters = []
            for index, field in enumerate(fields):
                if field.required:
                    parameters.append(field.name)
                elif field.has_static_default:
                    parameters.append(f"{field.name}={default_names[index]}")
                else:
                    parameters.append(f"{field.name}={factory_sentinel_name}")
            lines = [f"def __init__({instance_name}, *, {', '.join(parameters)}):"]
            namespace = {field_names_name: field_names, "__name__": __name__}
            emitter = _ValidationEmitter(lines, names, namespace)
            for index, field in enumerate(fields):
                field_name = field.name
                if field.default_factory is not None:
                    factory_name = factory_names[index]
                    error_name = factory_error_names[index]
                    lines.extend(
                        (
                            f"    if {field_name} is {factory_sentinel_name}:",
                            "        try:",
                            f"            {field_name} = {factory_name}()",
                            f"        except {exception_type_name} as {error_name}:",
                            f'            raise {type_error_name}("default factory for field '
                            f"'{field_name}' failed\") from {error_name}",
                        )
                    )
                elif field.has_static_default:
                    lines.append(f"    if {field_name} is not {default_names[index]}:")
                emitter.emit_schema(
                    field.schema,
                    field_name,
                    (f"{field_names_name}[{index}]",),
                    2 if field.has_static_default else 1,
                )
            for field_name, slot_setter_name in zip(field_names, slot_setter_names, strict=True):
                lines.append(f"    {slot_setter_name}({instance_name}, {field_name})")
            source = "\n".join(lines)

        if factory_names:
            namespace[factory_sentinel_name] = _FACTORY_SENTINEL
            namespace[exception_type_name] = Exception
            namespace[type_error_name] = TypeError
        for index, field in enumerate(fields):
            if field.has_static_default:
                namespace[default_names[index]] = field.default
            if field.default_factory is not None:
                namespace[factory_names[index]] = field.default_factory
        for slot_setter_name, slot_setter in zip(slot_setter_names, slot_setters, strict=True):
            namespace[slot_setter_name] = slot_setter
        exec(compile(source, "<talea Spec constructor>", "exec"), namespace)
        return cast(FunctionType, namespace["__init__"])


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
            type.__setattr__(cls, "__talea_artifacts__", _SpecArtifacts(SpecSchema(()), ()))
            return cls

        spec_bases = tuple(base for base in bases if isinstance(base, _SpecMeta))
        if not spec_bases:
            raise TypeError("a Spec declaration requires at least one Spec base")
        metaclass._validate_bases(bases, spec_bases)
        annotations = metaclass._inspect_annotations(namespace)
        field_names = tuple(annotations)
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
        schema = SpecSchema.compose(inherited_schemas, declared_fields)
        validators = tuple(compile_validator(field.schema) for field in schema.fields)
        if declarations:
            validators_by_name = dict(zip((field.name for field in schema.fields), validators, strict=True))
            metaclass._validate_static_defaults(
                declared_fields,
                tuple(validators_by_name[field.name] for field in declared_fields),
            )
        slot_setters = metaclass._slot_setters(cls, schema)
        initializer = _ConstructorCompiler().compile(schema, slot_setters)
        initializer.__module__ = cls.__module__
        initializer.__qualname__ = f"{cls.__qualname__}.__init__"
        initializer.__doc__ = "Validate and retain every declared field."
        type.__setattr__(cls, "__init__", initializer)
        type.__setattr__(cls, "__talea_artifacts__", _SpecArtifacts(schema, validators))
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
            for field in schema.fields:
                for owner in base.__mro__:
                    if isinstance(vars(owner).get(field.name), MemberDescriptorType):
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
    def _validate_declaration(
        namespace: dict[str, object],
        bases: tuple[type, ...],
        field_names: tuple[object, ...],
        inherited_names: frozenset[str],
    ) -> None:
        """Reject declaration forms whose lifecycle is outside this campaign."""

        if "__slots__" in namespace:
            raise TypeError("Spec manages instance slots from declared fields")
        if "__init__" in namespace:
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
    def _validate_static_defaults(fields: tuple[SpecField, ...], validators: tuple[Validator, ...]) -> None:
        """Reject invalid or transitively mutable static defaults at declaration time."""

        for field, validator in zip(fields, validators, strict=True):
            if not field.has_static_default:
                continue
            try:
                validator(field.default)
            except ValidationError as error:
                raise ValidationError(error.expected, error.value, (field.name, *error.location)) from None
            if _contains_mutable_value(field.default):
                raise TypeError(
                    f"mutable static default for field {field.name!r} is not supported; use field(default_factory=...)"
                )

    @staticmethod
    def _slot_setters(
        cls: type,
        schema: SpecSchema,
    ) -> tuple[Callable[[object, object], None], ...]:
        """Bind the one MRO-visible slot descriptor for every effective field."""

        setters = []
        for field in schema.fields:
            descriptor = next(
                (vars(owner)[field.name] for owner in cls.__mro__ if field.name in vars(owner)),
                None,
            )
            if not isinstance(descriptor, MemberDescriptorType):
                raise TypeError(f"Spec field conflicts with an inherited attribute: {field.name!r}")
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
    """

    def __setattr__(self, name: str, value: object) -> None:
        """Reject mutation so a validated Spec cannot silently become invalid."""

        raise AttributeError(f"{type(self).__name__} instances are immutable")

    def __delattr__(self, name: str) -> None:
        """Reject deletion so a validated Spec cannot lose a required value."""

        raise AttributeError(f"{type(self).__name__} instances are immutable")

    def __repr__(self) -> str:
        """Return the declaration name and current field values in order."""

        artifacts = cast(_SpecArtifacts, vars(type(self))["__talea_artifacts__"])
        values = ", ".join(f"{field.name}={getattr(self, field.name)!r}" for field in artifacts.schema.fields)
        return f"{type(self).__name__}({values})"
