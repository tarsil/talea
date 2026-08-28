"""Canonical declaration-level structure for Talea Specs.

This module owns the ordered relationship between declared fields, canonical
type ``Schema`` values, default-producing lifecycles, and effective custom
validation hooks. It deliberately does not own annotation resolution,
validation execution, or instance state.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Final, Literal

from talea.declaration.policies import (
    schema_contains_tagged_union,
    schema_is_covariant_override,
    schema_values_are_immutable,
)
from talea.metadata import EMPTY_METADATA, DeclarationMetadata
from talea.schema.nodes import Schema


class _MissingDefault:
    """Represent required-field state without conflating it with ``None``."""

    __slots__ = ()


MISSING_DEFAULT: Final = _MissingDefault()

type HookKind = Literal["transform", "check"]
type DerivationSelection = Literal["all", "include", "exclude"]
type DerivationMode = Literal["input", "output"]


@dataclass(frozen=True, slots=True)
class SpecDerivation:
    """Describe the immutable source and policy of one derived Spec.

    ``retained_fields`` and ``omitted_fields`` use the source's effective
    canonical order.  ``partial`` records whether instances retain supplied-key
    presence; requiredness itself remains owned by each resulting ``SpecField``.
    ``mode`` records declaration-time input/output selection without imposing
    per-instance or operation-specific runtime policy.
    """

    source: type[object]
    retained_fields: tuple[str, ...]
    omitted_fields: tuple[str, ...]
    selection: DerivationSelection
    partial: bool
    explicit_name: str | None = None
    mode: DerivationMode | None = None


@dataclass(frozen=True, slots=True)
class ValidationHook:
    """Describe one effective custom validation callback.

    Args:
        name: The class attribute name that provides Python override identity.
        kind: Whether the callback transforms inbound data or checks validated
            values.
        fields: Ordered field targets supplied to the callback.
        function: The synchronous plain function bound at class declaration.

    Transform hooks target exactly one field. A one-field check belongs to that
    field's local lifecycle; checks with two or more targets run after every
    field has completed structural validation. The containing ``SpecSchema``
    owns effective ordering and inheritance.
    """

    name: str
    kind: HookKind
    fields: tuple[str, ...]
    function: Callable[..., object]

    def __post_init__(self) -> None:
        if self.kind not in ("transform", "check"):
            raise ValueError("a validation hook requires transform or check kind")
        if not self.fields or len(self.fields) != len(set(self.fields)):
            raise ValueError("a validation hook requires unique field targets")
        if self.kind == "transform" and len(self.fields) != 1:
            raise ValueError("a transform hook requires exactly one field target")


@dataclass(frozen=True, slots=True)
class SerializationHook:
    """Describe one effective outbound field serializer.

    The callback receives the validated Python field value and returns its
    replacement output representation. Method names provide normal Python
    inheritance and override identity; exactly one effective serializer may
    target a field.
    """

    name: str
    field: str
    function: Callable[[object], object]


@dataclass(frozen=True, slots=True)
class SpecField:
    """Describe one named field in a Spec declaration.

    Args:
        name: The Python attribute name declared by the Spec body.
        schema: The already-resolved canonical type structure for the field.
        default: The validated static default, or ``MISSING_DEFAULT``.
        default_factory: The zero-argument producer used when the field is
            omitted, or ``None``.
        alias: The optional canonical external field name consumed by input,
            output, and standards projection.
        metadata: Normalized documentation, boundary, and security truth.

    The value is immutable and contains no validator or original annotation.
    Exactly one instance owns the complete declaration truth for a field;
    declaration order belongs to the containing ``SpecSchema``.

    Raises:
        ValueError: If both a static default and factory are provided.
    """

    name: str
    schema: Schema
    default: object = MISSING_DEFAULT
    default_factory: Callable[[], object] | None = None
    alias: str | None = None
    metadata: DeclarationMetadata = EMPTY_METADATA
    omittable: bool = False

    def __post_init__(self) -> None:
        if self.default is not MISSING_DEFAULT and self.default_factory is not None:
            raise ValueError("a Spec field cannot have both a static default and a default factory")
        if self.alias is not None and (not isinstance(self.alias, str) or not self.alias):
            raise TypeError("a Spec field alias must be a non-empty string")

    @property
    def required(self) -> bool:
        """Return whether construction requires an explicit field value."""

        return not self.omittable and self.default is MISSING_DEFAULT and self.default_factory is None

    @property
    def has_static_default(self) -> bool:
        """Return whether this field owns a retained static default."""

        return self.default is not MISSING_DEFAULT

    @property
    def external_name(self) -> str:
        """Return the one canonical name used by external data boundaries."""

        return self.name if self.alias is None else self.alias


@dataclass(frozen=True, slots=True)
class SpecSchema:
    """Own the ordered canonical field structure of one Spec declaration.

    Args:
        fields: Immutable field definitions in class-body declaration order.
        hooks: Effective custom validation callbacks in deterministic
            inheritance and declaration order.
        instances_are_permanently_trusted: Whether immutable field bindings
            are sufficient to keep every value graph schema-valid.  This is
            derived once from ``fields`` and cannot be supplied independently.

    Field names must be unique.  The schema is reusable by validation,
    introspection, serialization, and standards projections without any of
    those consumers reconstructing declaration truth from annotations.

    Raises:
        ValueError: If more than one field has the same name.
    """

    fields: tuple[SpecField, ...]
    hooks: tuple[ValidationHook, ...] = ()
    serializers: tuple[SerializationHook, ...] = ()
    metadata: DeclarationMetadata = EMPTY_METADATA
    derivation: SpecDerivation | None = None
    instances_are_permanently_trusted: bool = field(init=False)

    def __post_init__(self) -> None:
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("a Spec schema requires unique field names")
        hook_names = tuple(hook.name for hook in self.hooks)
        if len(hook_names) != len(set(hook_names)):
            raise ValueError("a Spec schema requires unique hook names")
        known_fields = frozenset(names)
        for hook in self.hooks:
            unknown = tuple(name for name in hook.fields if name not in known_fields)
            if unknown:
                raise TypeError(f"validation hook {hook.name!r} targets unknown field {unknown[0]!r}")
        external_names = tuple(field.external_name for field in self.fields)
        canonical_names = frozenset(names)
        for spec_field in self.fields:
            if spec_field.alias is not None and spec_field.alias in canonical_names:
                raise ValueError(f"field alias {spec_field.alias!r} conflicts with a canonical field name")
        if len(external_names) != len(set(external_names)):
            raise ValueError("a Spec schema requires unique external field names")
        serializer_names = tuple(serializer.name for serializer in self.serializers)
        if len(serializer_names) != len(set(serializer_names)):
            raise ValueError("a Spec schema requires unique serializer names")
        serializer_fields = tuple(serializer.field for serializer in self.serializers)
        if len(serializer_fields) != len(set(serializer_fields)):
            raise ValueError("a Spec field can have only one serialization hook")
        for serializer in self.serializers:
            if serializer.field not in known_fields:
                raise TypeError(f"serialization hook {serializer.name!r} targets unknown field {serializer.field!r}")
            target = next(field for field in self.fields if field.name == serializer.field)
            if schema_contains_tagged_union(target.schema):
                raise TypeError(
                    f"serialization hook {serializer.name!r} cannot replace tagged-union field {serializer.field!r}"
                )
        object.__setattr__(
            self,
            "instances_are_permanently_trusted",
            all(schema_values_are_immutable(field.schema) for field in self.fields),
        )

    @classmethod
    def compose(
        cls,
        inherited: tuple["SpecSchema", ...],
        declared: tuple[SpecField, ...],
        declared_hooks: tuple[ValidationHook, ...] = (),
        shadowed_hook_names: frozenset[str] = frozenset(),
        declared_serializers: tuple[SerializationHook, ...] = (),
        shadowed_serializer_names: frozenset[str] = frozenset(),
        declared_metadata: DeclarationMetadata = EMPTY_METADATA,
        derivation: SpecDerivation | None = None,
    ) -> "SpecSchema":
        """Create one effective declaration from bases and local contributions.

        Base schemas are consumed in declared-base order.  The first occurrence
        of an inherited name owns its MRO-selected semantics.  A local override
        replaces that field at its established position; a new local field is
        appended in class-body order. Hook method names use the same first-base
        and in-place override rules. A locally shadowed ordinary attribute
        removes its same-named inherited hook.

        Raises:
            TypeError: If an override widens or otherwise conflicts with the
                immutable inherited field type.
        """

        fields: dict[str, SpecField] = {}
        hooks: dict[str, ValidationHook] = {}
        serializers: dict[str, SerializationHook] = {}
        metadata = EMPTY_METADATA
        for schema in reversed(inherited):
            metadata = metadata.merged(schema.metadata)
        for schema in inherited:
            for inherited_field in schema.fields:
                fields.setdefault(inherited_field.name, inherited_field)
            for inherited_hook in schema.hooks:
                hooks.setdefault(inherited_hook.name, inherited_hook)
            for inherited_serializer in schema.serializers:
                serializers.setdefault(inherited_serializer.name, inherited_serializer)
        for declared_field in declared:
            inherited_field = fields.get(declared_field.name)
            if inherited_field is not None and not schema_is_covariant_override(
                declared_field.schema, inherited_field.schema
            ):
                raise TypeError(f"Spec field {declared_field.name!r} override is not type-compatible")
            if inherited_field is not None:
                declared_field = replace(
                    declared_field,
                    metadata=inherited_field.metadata.merged(declared_field.metadata),
                )
            fields[declared_field.name] = declared_field
        for hook_name in shadowed_hook_names:
            hooks.pop(hook_name, None)
        for declared_hook in declared_hooks:
            hooks[declared_hook.name] = declared_hook
        for serializer_name in shadowed_serializer_names:
            serializers.pop(serializer_name, None)
        for declared_serializer in declared_serializers:
            serializers[declared_serializer.name] = declared_serializer
        return cls(
            tuple(fields.values()),
            tuple(hooks.values()),
            tuple(serializers.values()),
            metadata.merged(declared_metadata),
            derivation,
        )

    @property
    def presence_aware(self) -> bool:
        """Return whether instances must retain supplied-field presence."""

        return bool(self.derivation is not None and self.derivation.partial) or any(
            field.omittable for field in self.fields
        )
