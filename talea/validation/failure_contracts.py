"""Project canonical schemas into compile-time validation failure contracts.

This module owns expected-contract text, constraint error-code selection, and
structured constraint context. It performs no runtime lookup: the validation
emitter consumes these values once and binds them directly into generated
failure paths.
"""

from enum import Enum
from typing import assert_never

from talea.constraints import Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.errors import ErrorCode
from talea.schema.nodes import (
    AliasSchema,
    ConstrainedSchema,
    DataclassSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    LiteralValue,
    MappingSchema,
    NamedReferenceSchema,
    PrimitiveSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    TaggedUnionSchema,
    TypedDictSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
)

_PRIMITIVE_ORDER = {"int": 0, "float": 1, "str": 2, "bool": 3, "bytes": 4, "none": 5}


def describe_schema(schema: Schema) -> str:
    """Return deterministic expected-contract text from canonical truth."""

    if isinstance(schema, PrimitiveSchema):
        return "None" if schema.kind == "none" else schema.kind
    if isinstance(schema, TypeSchema):
        return schema.python_type.__name__
    if isinstance(schema, EnumSchema):
        return schema.enum_type.__name__
    if isinstance(schema, LiteralSchema):
        values = sorted(schema.values, key=literal_key)
        return f"Literal[{', '.join(literal_description(item) for item in values)}]"
    if isinstance(schema, AliasSchema):
        return schema.name
    if isinstance(schema, NamedReferenceSchema):
        return schema.identity.name
    if isinstance(schema, ConstrainedSchema):
        descriptions = ", ".join(constraint_label(item) for item in schema.constraints)
        return f"Annotated[{describe_schema(schema.schema)}, {descriptions}]"
    if isinstance(schema, SpecReferenceSchema):
        return schema.spec_type.__name__
    if isinstance(schema, DataclassSchema):
        return schema.dataclass_type.__name__
    if isinstance(schema, SequenceSchema):
        return f"{schema.kind}[{describe_schema(schema.item)}]"
    if isinstance(schema, MappingSchema):
        return f"dict[{describe_schema(schema.key)}, {describe_schema(schema.value)}]"
    if isinstance(schema, TypedDictSchema):
        return schema.name
    if isinstance(schema, TaggedUnionSchema):
        return " | ".join(describe_schema(branch.schema) for branch in schema.branches)
    if isinstance(schema, VariadicTupleSchema):
        return f"tuple[{describe_schema(schema.item)}, ...]"
    if isinstance(schema, FixedTupleSchema):
        return f"tuple[{', '.join(describe_schema(item) for item in schema.items)}]"
    if isinstance(schema, UnionSchema):
        options = sorted(schema.options, key=schema_order_key)
        return " | ".join(describe_schema(option) for option in options)
    assert_never(schema)


def literal_description(item: LiteralValue) -> str:
    """Render one canonical Literal alternative for expected-contract text."""

    if isinstance(item.value, Enum):
        return f"{item.python_type.__name__}.{item.value.name}"
    return repr(item.value)


def literal_key(item: LiteralValue) -> tuple[str, str, str]:
    """Define deterministic Literal order without arbitrary metadata access."""

    value_name = item.value.name if isinstance(item.value, Enum) else repr(item.value)
    return item.python_type.__module__, item.python_type.__qualname__, value_name


def constraint_description(schema: Schema, constraint: object) -> str:
    """Describe one failed constraint without changing base-type truth."""

    return f"{describe_schema(schema)} satisfying {constraint_label(constraint)}"


def constraint_label(constraint: object) -> str:
    """Return stable declaration-like text for one canonical constraint."""

    if isinstance(constraint, Pattern):
        return f"Pattern({constraint.pattern!r})"
    if isinstance(constraint, (Gt, Ge, Lt, Le, MultipleOf, MinLength, MaxLength)):
        return f"{type(constraint).__name__}({constraint.value!r})"
    raise AssertionError("unsupported canonical constraint")


def constraint_code(constraint: object) -> ErrorCode:
    """Return the public machine code owned by one constraint type."""

    if isinstance(constraint, Gt):
        return ErrorCode.GREATER_THAN
    if isinstance(constraint, Ge):
        return ErrorCode.GREATER_THAN_OR_EQUAL
    if isinstance(constraint, Lt):
        return ErrorCode.LESS_THAN
    if isinstance(constraint, Le):
        return ErrorCode.LESS_THAN_OR_EQUAL
    if isinstance(constraint, MultipleOf):
        return ErrorCode.MULTIPLE_OF
    if isinstance(constraint, MinLength):
        return ErrorCode.MIN_LENGTH
    if isinstance(constraint, MaxLength):
        return ErrorCode.MAX_LENGTH
    if isinstance(constraint, Pattern):
        return ErrorCode.PATTERN
    raise AssertionError("unsupported canonical constraint")


def constraint_context(constraint: object) -> tuple[tuple[str, object], ...]:
    """Return stable structured context for one canonical constraint."""

    if isinstance(constraint, (Gt, Ge, Lt, Le)):
        return (("limit", constraint.value),)
    if isinstance(constraint, MultipleOf):
        return (("multiple_of", constraint.value),)
    if isinstance(constraint, MinLength):
        return (("minimum", constraint.value),)
    if isinstance(constraint, MaxLength):
        return (("maximum", constraint.value),)
    if isinstance(constraint, Pattern):
        return (("pattern", constraint.pattern),)
    raise AssertionError("unsupported canonical constraint")


def schema_order_key(schema: Schema) -> tuple[int, str]:
    """Define shared deterministic union execution and presentation order."""

    if isinstance(schema, PrimitiveSchema):
        return _PRIMITIVE_ORDER[schema.kind], schema.kind
    if isinstance(schema, ConstrainedSchema):
        base_order, _ = schema_order_key(schema.schema)
        return base_order, describe_schema(schema)
    if isinstance(schema, AliasSchema):
        base_order, _ = schema_order_key(schema.schema)
        return base_order, f"{schema.module}.{schema.name}"
    if isinstance(schema, NamedReferenceSchema):
        return 7, f"{schema.identity.module}.{schema.identity.name}"
    if isinstance(schema, (TypeSchema, EnumSchema, LiteralSchema)):
        return 6, describe_schema(schema)
    if isinstance(schema, SpecReferenceSchema):
        return 6, f"{schema.spec_type.__module__}.{schema.spec_type.__qualname__}"
    if isinstance(schema, DataclassSchema):
        return 6, f"{schema.dataclass_type.__module__}.{schema.dataclass_type.__qualname__}"
    if isinstance(schema, TypedDictSchema):
        return 7, f"{schema.module}.{schema.name}"
    if isinstance(schema, TaggedUnionSchema):
        return 8, describe_schema(schema)
    return 10, describe_schema(schema)
