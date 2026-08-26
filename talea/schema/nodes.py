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


@dataclass(frozen=True, slots=True)
class AliasSchema:
    """Retain a named Python alias while sharing its structural semantics.

    Validation and projection compilers unwrap ``schema`` at compile time. The
    stable name and module remain available for errors, introspection, recursive
    design, and future schema component naming without adding runtime dispatch.
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
    so retaining it supplies compact validation and future projection truth
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

    Members are retained as Literal-like canonical values for future schema
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
