"""Immutable structural values that own Talea's resolved schema truth.

The objects in this module describe supported annotations without retaining or
re-inspecting their ``typing`` representation.  They contain structure only;
validation, serialization, field metadata, and runtime execution do not belong
to this domain.
"""

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "FixedTupleSchema",
    "MappingSchema",
    "PrimitiveKind",
    "PrimitiveSchema",
    "Schema",
    "SequenceKind",
    "SequenceSchema",
    "UnionSchema",
    "VariadicTupleSchema",
]

type PrimitiveKind = Literal["int", "float", "str", "bool", "bytes", "none"]
type SequenceKind = Literal["list", "set", "frozenset"]


@dataclass(frozen=True, slots=True)
class PrimitiveSchema:
    """Canonical schema for one supported scalar annotation.

    ``kind`` is a stable Talea-owned structural tag rather than a reference to
    the original Python annotation.  The frozen, slotted representation keeps
    primitive structure immutable and free of per-instance dictionaries.
    """

    kind: PrimitiveKind


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


type Schema = PrimitiveSchema | SequenceSchema | MappingSchema | VariadicTupleSchema | FixedTupleSchema | UnionSchema
