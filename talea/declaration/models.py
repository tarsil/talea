"""Canonical declaration-level structure for Talea Specs.

This module owns the ordered relationship between declared fields, canonical
type ``Schema`` values, default-producing lifecycles, and effective custom
validation hooks. It deliberately does not own annotation resolution,
validation execution, or instance state.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Literal

from talea.declaration.policies import schema_is_covariant_override, schema_values_are_immutable
from talea.schema.nodes import Schema


class _MissingDefault:
    """Represent required-field state without conflating it with ``None``."""

    __slots__ = ()


MISSING_DEFAULT: Final = _MissingDefault()

type HookKind = Literal["transform", "check"]


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
class SpecField:
    """Describe one named field in a Spec declaration.

    Args:
        name: The Python attribute name declared by the Spec body.
        schema: The already-resolved canonical type structure for the field.
        default: The validated static default, or ``MISSING_DEFAULT``.
        default_factory: The zero-argument producer used when the field is
            omitted, or ``None``.

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

    def __post_init__(self) -> None:
        if self.default is not MISSING_DEFAULT and self.default_factory is not None:
            raise ValueError("a Spec field cannot have both a static default and a default factory")

    @property
    def required(self) -> bool:
        """Return whether construction requires an explicit field value."""

        return self.default is MISSING_DEFAULT and self.default_factory is None

    @property
    def has_static_default(self) -> bool:
        """Return whether this field owns a retained static default."""

        return self.default is not MISSING_DEFAULT


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
    introspection, serialization, and other future projections without any of
    those consumers reconstructing declaration truth from annotations.

    Raises:
        ValueError: If more than one field has the same name.
    """

    fields: tuple[SpecField, ...]
    hooks: tuple[ValidationHook, ...] = ()
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
        for schema in inherited:
            for inherited_field in schema.fields:
                fields.setdefault(inherited_field.name, inherited_field)
            for inherited_hook in schema.hooks:
                hooks.setdefault(inherited_hook.name, inherited_hook)
        for declared_field in declared:
            inherited_field = fields.get(declared_field.name)
            if inherited_field is not None and not schema_is_covariant_override(
                declared_field.schema, inherited_field.schema
            ):
                raise TypeError(f"Spec field {declared_field.name!r} override is not type-compatible")
            fields[declared_field.name] = declared_field
        for hook_name in shadowed_hook_names:
            hooks.pop(hook_name, None)
        for declared_hook in declared_hooks:
            hooks[declared_hook.name] = declared_hook
        return cls(tuple(fields.values()), tuple(hooks.values()))
