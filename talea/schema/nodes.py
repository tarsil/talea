"""Immutable structural values that own Talea's resolved schema truth.

The objects in this module describe supported annotations without retaining or
re-inspecting their ``typing`` representation.  They contain structure only;
validation, serialization, field metadata, and runtime execution do not belong
to this domain.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from talea.constraints import Constraint
from talea.metadata import EMPTY_METADATA, DeclarationMetadata
from talea.schema.references import NamedReferenceSchema, NamedSchemaIdentity

__all__ = [
    "AliasSchema",
    "DataclassField",
    "DataclassSchema",
    "FixedTupleSchema",
    "ConstrainedSchema",
    "EnumSchema",
    "LiteralSchema",
    "LiteralValue",
    "MappingSchema",
    "NamedReferenceSchema",
    "NamedSchemaIdentity",
    "PrimitiveKind",
    "PrimitiveSchema",
    "Schema",
    "SequenceKind",
    "SequenceSchema",
    "SpecReferenceSchema",
    "TaggedUnionBranch",
    "TaggedUnionSchema",
    "TypeCheckMode",
    "TypeSchema",
    "TypedDictField",
    "TypedDictSchema",
    "UnionSchema",
    "VariadicTupleSchema",
]

type PrimitiveKind = Literal["int", "float", "str", "bool", "bytes", "none"]
type SequenceKind = Literal["list", "set", "frozenset"]
type TypeCheckMode = Literal["exact", "nominal"]


class _DataclassMissing:
    """Represent the absence of a retained dataclass static default."""

    __slots__ = ()


DATACLASS_MISSING = _DataclassMissing()


@dataclass(frozen=True, slots=True)
class AliasSchema:
    """Retain a named Python alias while sharing its structural semantics.

    Validation and projection compilers unwrap ``schema`` at compile time. The
    stable name and module remain available for errors, introspection, recursive
    design, and schema component naming without adding runtime dispatch.
    """

    name: str
    module: str
    schema: "Schema"
    metadata: DeclarationMetadata = EMPTY_METADATA
    identity: NamedSchemaIdentity | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PrimitiveSchema:
    """Canonical schema for one supported scalar annotation.

    ``kind`` is a stable Talea-owned structural tag rather than a reference to
    the original Python annotation.  The frozen, slotted representation keeps
    primitive structure immutable and free of per-instance dictionaries.
    """

    kind: PrimitiveKind


@dataclass(frozen=True, slots=True)
class SpecReferenceSchema:
    """Canonical schema for a nominal reference to one Talea Spec class.

    ``spec_type`` is the runtime class identity required for Python subclass
    checks. Its class-owned declaration identity resolves to the target's one
    canonical ``SpecSchema``; this node neither copies fields nor retains the
    annotation that named the class. The identity may exist before a recursive
    declaration graph has finalized its schema artifacts.

    Raises:
        TypeError: If ``spec_type`` is not a Talea Spec declaration.
    """

    spec_type: type[object]

    def __post_init__(self) -> None:
        if getattr(self.spec_type, "__talea_spec__", False) is not True or "__talea_declaration__" not in vars(
            self.spec_type
        ):
            raise TypeError("a Spec reference schema requires a declared Spec class")


@dataclass(frozen=True, slots=True)
class TypeSchema:
    """Canonical schema for one strict standard-library runtime type.

    ``mode`` records the deliberate exact-versus-nominal contract selected by
    Talea for the family. The original annotation is the runtime type itself,
    so retaining it supplies compact validation and projection truth
    without keeping a ``typing`` wrapper or registry key.
    """

    python_type: type[object]
    mode: TypeCheckMode

    def __post_init__(self) -> None:
        if not isinstance(self.python_type, type):
            raise TypeError("a type schema requires a runtime type")
        if self.mode not in ("exact", "nominal"):
            raise ValueError("a type schema requires exact or nominal checking")


@dataclass(frozen=True, slots=True)
class LiteralValue:
    """Retain one Literal value together with its strict runtime type identity."""

    python_type: type[object]
    value: object


@dataclass(frozen=True, slots=True)
class LiteralSchema:
    """Canonical type-sensitive alternatives for one ``typing.Literal`` annotation."""

    values: frozenset[LiteralValue]

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("a literal schema requires at least one value")


@dataclass(frozen=True, slots=True)
class EnumSchema:
    """Canonical exact-member contract for a declared Enum class.

    Members are retained as Literal-like canonical values for schema
    projection; validation binds only ``enum_type`` and never reconstructs
    members from their primitive values.
    """

    enum_type: type[Enum]
    members: tuple[LiteralValue, ...]

    def __post_init__(self) -> None:
        if not issubclass(self.enum_type, Enum):
            raise TypeError("an enum schema requires an Enum type")


@dataclass(frozen=True, slots=True)
class SequenceSchema:
    """Schema for a homogeneous built-in container.

    ``kind`` preserves the exact container required by the annotation, while
    ``item`` is the already-resolved schema for every member.  No original
    annotation is retained.
    """

    kind: SequenceKind
    item: "Schema"


@dataclass(frozen=True, slots=True)
class MappingSchema:
    """Schema for a built-in dictionary.

    ``key`` and ``value`` independently preserve the resolved structure of the
    annotation's two type arguments.
    """

    key: "Schema"
    value: "Schema"


@dataclass(frozen=True, slots=True)
class TypedDictField:
    """Canonical key contract for one TypedDict declaration."""

    name: str
    schema: "Schema"
    required: bool
    read_only: bool = False
    metadata: DeclarationMetadata = EMPTY_METADATA

    @property
    def external_name(self) -> str:
        """Return the TypedDict key used by every boundary."""

        return self.name


@dataclass(frozen=True, slots=True)
class TypedDictSchema:
    """Canonical closed structural mapping declared by ``typing.TypedDict``.

    Values are exact dictionaries during strict validation. Boundary conversion
    may accept a general Mapping and always produces a detached dictionary.
    Unknown keys are rejected consistently across Python and JSON input.
    """

    name: str
    module: str
    fields: tuple[TypedDictField, ...]
    identity: NamedSchemaIdentity | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("a TypedDict schema requires unique field names")


@dataclass(frozen=True, slots=True)
class DataclassField:
    """Canonical stored-field and constructor-participation truth.

    ``init`` controls whether external Mapping and JSON boundaries may supply
    the field. Every field remains part of strict current-state validation and
    output. Defaults and factories are retained only so the original dataclass
    constructor can remain their sole lifecycle owner.
    """

    name: str
    schema: "Schema"
    init: bool
    kw_only: bool
    default: object = DATACLASS_MISSING
    default_factory: object = DATACLASS_MISSING
    alias: str | None = None
    metadata: DeclarationMetadata = EMPTY_METADATA

    def __post_init__(self) -> None:
        if self.default is not DATACLASS_MISSING and self.default_factory is not DATACLASS_MISSING:
            raise ValueError("a dataclass field cannot have both a static default and a default factory")
        if self.alias is not None and (not isinstance(self.alias, str) or not self.alias):
            raise TypeError("a dataclass field alias must be a non-empty string")

    @property
    def required(self) -> bool:
        """Return whether external construction requires this field."""

        return self.init and self.default is DATACLASS_MISSING and self.default_factory is DATACLASS_MISSING

    @property
    def has_static_default(self) -> bool:
        """Return whether stdlib construction owns a static default."""

        return self.default is not DATACLASS_MISSING

    @property
    def has_default_factory(self) -> bool:
        """Return whether stdlib construction owns a default factory."""

        return self.default_factory is not DATACLASS_MISSING

    @property
    def external_name(self) -> str:
        """Return the one canonical external boundary name."""

        return self.name if self.alias is None else self.alias


@dataclass(frozen=True, slots=True)
class DataclassSchema:
    """Canonical structural and lifecycle truth for one stdlib dataclass.

    The original class remains the runtime representation. This immutable node
    records only the effective stored fields, constructor participation,
    frozen binding policy, lifecycle classification, and recursive declaration
    identity needed by Talea consumers; no dataclass class or instance is
    mutated. ``construction_preserves_validated_fields`` is true only when
    resolution proved that ordinary construction directly stores every
    already-validated required field without another lifecycle mutation route.
    """

    dataclass_type: type[object]
    fields: tuple[DataclassField, ...]
    frozen: bool
    identity: NamedSchemaIdentity | None = field(default=None, repr=False, compare=False)
    construction_preserves_validated_fields: bool = False

    def __post_init__(self) -> None:
        names = tuple(item.name for item in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("a dataclass schema requires unique field names")
        external_names = tuple(item.external_name for item in self.fields)
        canonical_names = frozenset(names)
        for item in self.fields:
            if item.alias is not None and item.alias in canonical_names:
                raise ValueError(f"field alias {item.alias!r} conflicts with a canonical field name")
        if len(external_names) != len(set(external_names)):
            raise ValueError("a dataclass schema requires unique external field names")

    @property
    def instances_are_permanently_trusted(self) -> bool:
        """Return whether validated instances cannot later leave the contract."""

        from talea.declaration.policies import schema_values_are_immutable

        return schema_values_are_immutable(self)


@dataclass(frozen=True, slots=True)
class TaggedUnionBranch:
    """Bind one exact Python and JSON tag representation to one branch."""

    tag: LiteralValue
    json_tag: LiteralValue
    schema: SpecReferenceSchema | TypedDictSchema


@dataclass(frozen=True, slots=True)
class TaggedUnionSchema:
    """Canonical finite dispatch truth for an explicitly tagged union.

    ``discriminator`` is the common Python field name and ``external_name`` is
    its Alias-derived boundary key. Branch order is deterministic and no
    mutable dispatch mapping is exposed to introspection consumers.
    """

    discriminator: str
    external_name: str
    branches: tuple[TaggedUnionBranch, ...]
    sensitive: bool = False

    def __post_init__(self) -> None:
        if len(self.branches) < 2:
            raise ValueError("a tagged union requires at least two branches")
        tags = tuple(branch.tag for branch in self.branches)
        if len(tags) != len(set(tags)):
            raise ValueError("a tagged union requires unique Python tags")
        json_tags = tuple(branch.json_tag for branch in self.branches)
        if len(json_tags) != len(set(json_tags)):
            raise ValueError("a tagged union requires unique JSON tags")


@dataclass(frozen=True, slots=True)
class VariadicTupleSchema:
    """Schema for ``tuple[T, ...]``.

    Every position has the resolved ``item`` structure, with no fixed length.
    """

    item: "Schema"


@dataclass(frozen=True, slots=True)
class FixedTupleSchema:
    """Schema for a non-empty fixed tuple.

    ``items`` contains one resolved schema per position and therefore preserves
    order as structural truth.  Empty tuples are not part of the supported
    annotation subset.
    """

    items: tuple["Schema", ...]

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("a fixed tuple schema requires at least one item")


@dataclass(frozen=True, slots=True)
class UnionSchema:
    """Schema for an unordered choice between two or more resolved structures.

    A union's alternatives are a ``frozenset`` because Python union equality is
    order-independent and duplicate alternatives have no semantic effect.  The
    representation requires at least two distinct members because a single
    alternative is not a union.
    """

    options: frozenset["Schema"]

    def __post_init__(self) -> None:
        if len(self.options) < 2:
            raise ValueError("a union schema requires at least two options")


@dataclass(frozen=True, slots=True)
class ConstrainedSchema:
    """Canonical normalized constraints applied to one structural schema.

    The wrapper owns only validation-relevant Talea metadata. Unknown
    ``Annotated`` metadata is deliberately ignored rather than retained on hot
    schema objects, and is never executed.
    """

    schema: "Schema"
    constraints: tuple[Constraint, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema, ConstrainedSchema):
            raise ValueError("constrained schemas must be flattened")
        if not self.constraints:
            raise ValueError("a constrained schema requires at least one constraint")


type Schema = (
    AliasSchema
    | PrimitiveSchema
    | SpecReferenceSchema
    | DataclassSchema
    | NamedReferenceSchema
    | TypeSchema
    | LiteralSchema
    | EnumSchema
    | SequenceSchema
    | MappingSchema
    | TypedDictSchema
    | TaggedUnionSchema
    | VariadicTupleSchema
    | FixedTupleSchema
    | UnionSchema
    | ConstrainedSchema
)
