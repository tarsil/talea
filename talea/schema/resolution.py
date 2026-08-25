"""Resolve Python annotations into compact canonical Talea schema values."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from pathlib import Path, PosixPath, PurePath, PurePosixPath, PureWindowsPath, WindowsPath
from types import GenericAlias, NoneType, UnionType
from typing import Annotated, Literal, Union, cast, get_args, get_origin
from uuid import UUID

from talea.constraints import Constraint, Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.schema.nodes import (
    ConstrainedSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    LiteralValue,
    MappingSchema,
    PrimitiveSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
)

__all__ = ["AnnotationResolutionError", "ConstraintDeclarationError", "resolve_annotation"]

_CONSTRAINT_TYPES = (Gt, Ge, Lt, Le, MultipleOf, MinLength, MaxLength, Pattern)
_EXACT_STANDARD_TYPES = frozenset(
    {
        date,
        IPv4Address,
        IPv6Address,
        IPv4Network,
        IPv6Network,
        IPv4Interface,
        IPv6Interface,
    }
)
_NOMINAL_STANDARD_TYPES = frozenset(
    {
        UUID,
        time,
        datetime,
        timedelta,
        Decimal,
        PurePath,
        Path,
        PurePosixPath,
        PureWindowsPath,
        PosixPath,
        WindowsPath,
    }
)


class AnnotationResolutionError(TypeError):
    """Report an annotation that has no canonical Talea schema representation.

    ``annotation`` is the exact unsupported leaf, allowing nested failures to
    identify where resolution stopped without retaining the broader typing
    graph.
    """

    def __init__(self, annotation: object) -> None:
        self.annotation = annotation
        super().__init__(f"Unsupported annotation: {annotation!r}")


class ConstraintDeclarationError(TypeError):
    """Report incompatible or contradictory Talea constraint metadata."""


def resolve_annotation(annotation: object) -> Schema:
    """Normalize one supported Python annotation into immutable schema truth.

    Resolution removes ``typing`` wrappers once, canonicalizes Talea-owned
    ``Annotated`` constraints, and ignores unknown metadata without executing
    or retaining it. Strict primitive, standard-library, Enum, Literal,
    container, union, and Spec-reference semantics are represented entirely by
    Talea schema nodes before runtime validators are compiled.

    Args:
        annotation: A Python annotation to normalize.

    Returns:
        A compact immutable schema containing all validation-relevant truth.

    Raises:
        AnnotationResolutionError: If the annotation or a nested leaf is not
            supported.
        ConstraintDeclarationError: If Talea constraints are inapplicable or
            form an obvious contradiction.
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
    if isinstance(annotation, type):
        if issubclass(annotation, Enum) and annotation not in (Enum, IntEnum, StrEnum):
            return EnumSchema(
                annotation,
                tuple(LiteralValue(type(member), member) for member in annotation),
            )
        if annotation in _EXACT_STANDARD_TYPES:
            return TypeSchema(cast(type[object], annotation), "exact")
        if annotation in _NOMINAL_STANDARD_TYPES:
            return TypeSchema(cast(type[object], annotation), "nominal")
        if getattr(annotation, "__talea_spec__", False) is True and "__talea_artifacts__" in vars(annotation):
            return SpecReferenceSchema(annotation)

    origin = get_origin(annotation)
    arguments = get_args(annotation)

    if origin is Annotated:
        schema = resolve_annotation(arguments[0])
        constraints = tuple(item for item in arguments[1:] if isinstance(item, _CONSTRAINT_TYPES))
        return _apply_constraints(schema, constraints)
    if origin is Literal:
        if not arguments:
            raise AnnotationResolutionError(annotation)
        return _resolve_literal(arguments)

    if origin in (UnionType, Union):
        options = frozenset(resolve_annotation(argument) for argument in arguments)
        if len(options) == 1:
            return next(iter(options))
        return UnionSchema(options)

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


def _resolve_literal(arguments: tuple[object, ...]) -> LiteralSchema:
    values: set[LiteralValue] = set()
    for value in arguments:
        if value is None or type(value) in (str, bytes, int, bool) or isinstance(value, Enum):
            values.add(LiteralValue(type(value), value))
            continue
        raise AnnotationResolutionError(value)
    return LiteralSchema(frozenset(values))


def _apply_constraints(schema: Schema, declared: tuple[Constraint, ...]) -> Schema:
    if not declared:
        return schema
    if isinstance(schema, ConstrainedSchema):
        base = schema.schema
        declared = (*schema.constraints, *declared)
    else:
        base = schema
    constraints = _normalize_constraints(base, declared)
    if not constraints:
        return base
    return ConstrainedSchema(base, constraints)


def _normalize_constraints(schema: Schema, declared: tuple[Constraint, ...]) -> tuple[Constraint, ...]:
    numeric_type = _numeric_type(schema)
    numeric = tuple(item for item in declared if isinstance(item, (Gt, Ge, Lt, Le, MultipleOf)))
    lengths = tuple(item for item in declared if isinstance(item, (MinLength, MaxLength)))
    patterns = tuple(item for item in declared if isinstance(item, Pattern))

    if numeric:
        if numeric_type is None:
            raise ConstraintDeclarationError(f"numeric constraints do not apply to {_schema_label(schema)}")
        for constraint in numeric:
            if type(constraint.value) is not numeric_type:
                raise ConstraintDeclarationError(
                    f"{type(constraint).__name__} boundary must be an exact {numeric_type.__name__}"
                )
    if lengths and not _is_sized_schema(schema):
        raise ConstraintDeclarationError(f"length constraints do not apply to {_schema_label(schema)}")
    if patterns and schema != PrimitiveSchema("str"):
        raise ConstraintDeclarationError(f"Pattern does not apply to {_schema_label(schema)}")

    normalized_numeric = _normalize_numeric(numeric_type, numeric)
    normalized_lengths = _normalize_lengths(schema, lengths)
    normalized_patterns = tuple(sorted(set(patterns), key=lambda item: (item.pattern, item.flags)))
    return (*normalized_numeric, *normalized_lengths, *normalized_patterns)


def _numeric_type(schema: Schema) -> type[object] | None:
    if schema == PrimitiveSchema("int"):
        return int
    if schema == PrimitiveSchema("float"):
        return float
    if isinstance(schema, TypeSchema) and schema.python_type is Decimal:
        return Decimal
    return None


def _is_sized_schema(schema: Schema) -> bool:
    return schema in (PrimitiveSchema("str"), PrimitiveSchema("bytes")) or isinstance(
        schema, (SequenceSchema, MappingSchema, VariadicTupleSchema, FixedTupleSchema)
    )


def _normalize_numeric(
    numeric_type: type[object] | None,
    constraints: tuple[Constraint, ...],
) -> tuple[Constraint, ...]:
    lowers = [item for item in constraints if isinstance(item, (Gt, Ge))]
    uppers = [item for item in constraints if isinstance(item, (Lt, Le))]
    multiples = [item for item in constraints if isinstance(item, MultipleOf)]
    lower = max(lowers, key=lambda item: (item.value, isinstance(item, Gt)), default=None)
    upper = min(uppers, key=lambda item: (item.value, not isinstance(item, Lt)), default=None)

    if lower is not None and upper is not None:
        if numeric_type is int:
            minimum = lower.value + int(isinstance(lower, Gt))
            maximum = upper.value - int(isinstance(upper, Lt))
            contradictory = minimum > maximum
        else:
            contradictory = lower.value > upper.value or (
                lower.value == upper.value and (isinstance(lower, Gt) or isinstance(upper, Lt))
            )
        if contradictory:
            raise ConstraintDeclarationError(f"contradictory numeric bounds: {lower!r} and {upper!r}")

    normalized_multiples = {MultipleOf(abs(item.value)) for item in multiples}
    ordered_multiples = tuple(sorted(normalized_multiples, key=lambda item: item.value))
    return tuple(item for item in (lower, upper, *ordered_multiples) if item is not None)


def _normalize_lengths(schema: Schema, constraints: tuple[Constraint, ...]) -> tuple[Constraint, ...]:
    minimums = [item for item in constraints if isinstance(item, MinLength)]
    maximums = [item for item in constraints if isinstance(item, MaxLength)]
    minimum = max(minimums, key=lambda item: item.value, default=None)
    maximum = min(maximums, key=lambda item: item.value, default=None)
    if isinstance(schema, FixedTupleSchema):
        size = len(schema.items)
        if (minimum is not None and size < minimum.value) or (maximum is not None and size > maximum.value):
            raise ConstraintDeclarationError(f"fixed tuple length {size} contradicts declared length constraints")
        return ()
    if minimum is not None and maximum is not None and minimum.value > maximum.value:
        raise ConstraintDeclarationError(f"contradictory length bounds: {minimum!r} and {maximum!r}")
    return tuple(item for item in (minimum, maximum) if item is not None)


def _schema_label(schema: Schema) -> str:
    if isinstance(schema, PrimitiveSchema):
        return "None" if schema.kind == "none" else schema.kind
    if isinstance(schema, TypeSchema):
        return schema.python_type.__qualname__
    return type(schema).__name__
