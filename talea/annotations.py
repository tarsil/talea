"""Resolve supported Python annotations into Talea's canonical schema values."""

from types import GenericAlias, NoneType, UnionType
from typing import get_args, get_origin

from talea.schema import (
    FixedTupleSchema,
    MappingSchema,
    PrimitiveSchema,
    Schema,
    SequenceSchema,
    UnionSchema,
    VariadicTupleSchema,
)

__all__ = ["AnnotationResolutionError", "resolve_annotation"]


class AnnotationResolutionError(TypeError):
    """Report an annotation that has no canonical Talea schema representation.

    The ``annotation`` attribute contains the exact unresolved annotation.  For
    nested structures this is the unsupported leaf, which identifies the point
    where resolution stopped without constructing a broader error hierarchy.
    """

    def __init__(self, annotation: object) -> None:
        self.annotation = annotation
        super().__init__(f"Unsupported annotation: {annotation!r}")


def resolve_annotation(annotation: object) -> Schema:
    """Normalize a supported Python annotation into immutable structural truth.

    Resolution recursively removes Python ``typing`` structure before any
    runtime validation exists.  Supported inputs are the built-in primitive
    types, ``None``, PEP 585 list/set/frozenset/dict/tuple aliases, and PEP 604
    unions composed from those forms.  The function is intentionally uncached:
    schema compilation owns when resolution occurs, while this transformation
    remains stateless and does not retain annotation graphs globally.

    Args:
        annotation: A Python annotation to normalize.

    Returns:
        The immutable Talea schema value that completely describes the
        supported annotation's structure.

    Raises:
        AnnotationResolutionError: If ``annotation`` or any nested component
            is outside the currently supported forms.
    """

    if annotation is int:
        return PrimitiveSchema("int")
    if annotation is float:
        return PrimitiveSchema("float")
    if annotation is str:
        return PrimitiveSchema("str")
    if annotation is bool:
        return PrimitiveSchema("bool")
    if annotation is bytes:
        return PrimitiveSchema("bytes")
    if annotation is None or annotation is NoneType:
        return PrimitiveSchema("none")

    origin = get_origin(annotation)
    arguments = get_args(annotation)

    if origin is UnionType:
        return UnionSchema(frozenset(resolve_annotation(argument) for argument in arguments))

    if not isinstance(annotation, GenericAlias):
        raise AnnotationResolutionError(annotation)

    if origin is list and len(arguments) == 1:
        return SequenceSchema("list", resolve_annotation(arguments[0]))
    if origin is set and len(arguments) == 1:
        return SequenceSchema("set", resolve_annotation(arguments[0]))
    if origin is frozenset and len(arguments) == 1:
        return SequenceSchema("frozenset", resolve_annotation(arguments[0]))
    if origin is dict and len(arguments) == 2:
        return MappingSchema(resolve_annotation(arguments[0]), resolve_annotation(arguments[1]))
    if origin is tuple:
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return VariadicTupleSchema(resolve_annotation(arguments[0]))
        if arguments and Ellipsis not in arguments:
            return FixedTupleSchema(tuple(resolve_annotation(item) for item in arguments))

    raise AnnotationResolutionError(annotation)
