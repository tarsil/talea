"""Decode settings-owned textual leaves from canonical Talea schema truth."""

import json
import math
import re
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from pathlib import Path, PosixPath, PurePath, PurePosixPath, PureWindowsPath, WindowsPath
from types import NoneType
from typing import cast
from uuid import UUID

from talea.introspection import inspect_spec
from talea.json.representations import decode_bytes, parse_timedelta
from talea.schema.nodes import (
    AliasSchema,
    ConstrainedSchema,
    DataclassSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    MappingSchema,
    NamedReferenceSchema,
    NamedTupleSchema,
    PrimitiveSchema,
    RepresentationSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    TaggedUnionSchema,
    TypedDictSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
)

_INTEGER = re.compile(r"-?(?:0|[1-9]\d*)\Z")
_FLOAT = re.compile(r"-?(?:(?:0|[1-9]\d*)(?:\.\d+)?)(?:[eE][+-]?\d+)?\Z")
_STANDARD_TEXT_TYPES = frozenset(
    {
        Decimal,
        UUID,
        date,
        datetime,
        time,
        timedelta,
        PurePath,
        Path,
        PurePosixPath,
        PureWindowsPath,
        PosixPath,
        WindowsPath,
        IPv4Address,
        IPv6Address,
        IPv4Network,
        IPv6Network,
        IPv4Interface,
        IPv6Interface,
    }
)


def decode_text(schema: Schema, text: str) -> object:
    """Return one source-specific external value, leaving failures for Spec input."""

    base = _unwrap(schema)
    try:
        if isinstance(base, PrimitiveSchema):
            return _decode_primitive(base, text)
        if isinstance(base, TypeSchema):
            return _decode_standard_type(base.python_type, text)
        if isinstance(base, EnumSchema):
            return _decode_enum(base, text)
        if isinstance(base, LiteralSchema):
            return _decode_literal(base, text)
        if isinstance(base, RepresentationSchema):
            if base.input is None:
                return text
            return decode_text(base.input, text)
        if isinstance(base, UnionSchema):
            return _decode_union(base, text)
        decoded = _json_load(text)
        return _convert_json_value(base, decoded)
    except (ValueError, TypeError, OverflowError, OSError, InvalidOperation, json.JSONDecodeError):
        # The ordinary Mapping boundary creates the canonical, Sensitive-aware
        # ValidationError. Retaining the original text here would create a
        # second failure vocabulary and bypass existing field locations.
        return text


def _unwrap(schema: Schema) -> Schema:
    while isinstance(schema, (AliasSchema, ConstrainedSchema, NamedReferenceSchema)):
        if isinstance(schema, NamedReferenceSchema):
            schema = schema.target
        else:
            schema = schema.schema
    return schema


def _decode_primitive(schema: PrimitiveSchema, text: str) -> object:
    if schema.kind == "str":
        return text
    if schema.kind == "none":
        return None if text == "null" else text
    if schema.kind == "bool":
        if text == "true":
            return True
        if text == "false":
            return False
        return text
    if schema.kind == "int":
        return int(text) if _INTEGER.fullmatch(text) else text
    if schema.kind == "float":
        if _FLOAT.fullmatch(text) is None:
            return text
        value = float(text)
        return value if math.isfinite(value) else text
    return decode_bytes(text)


def _decode_standard_type(python_type: type[object], text: str) -> object:
    if python_type is Decimal:
        value = Decimal(text)
        return value if value.is_finite() else text
    if python_type is timedelta:
        return parse_timedelta(text)
    if python_type is date:
        return date.fromisoformat(text)
    if python_type is datetime:
        return datetime.fromisoformat(text)
    if python_type is time:
        return time.fromisoformat(text)
    return python_type(text)


def _json_load(text: str) -> object:
    return json.loads(
        text,
        parse_float=Decimal,
        parse_constant=lambda token: _reject_constant(token),
        object_pairs_hook=_object_from_pairs,
    )


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(token: str) -> object:
    raise ValueError(f"non-finite JSON number {token}")


def _decode_enum(schema: EnumSchema, text: str) -> object:
    parsed: object = text
    try:
        parsed = _json_load(text)
    except json.JSONDecodeError:
        pass
    for member in schema.enum_type:
        value = member.value
        if type(value) is type(parsed) and value == parsed:
            return member
    return text


def _decode_literal(schema: LiteralSchema, text: str) -> object:
    parsed: object = text
    try:
        parsed = _json_load(text)
    except json.JSONDecodeError:
        pass
    for literal in schema.values:
        if literal.python_type is type(parsed) and literal.value == parsed:
            return literal.value
    return text


def _decode_union(schema: UnionSchema, text: str) -> object:
    none_options = []
    for option in schema.options:
        unwrapped = _unwrap(option)
        if isinstance(unwrapped, PrimitiveSchema) and unwrapped.kind == "none":
            none_options.append(option)
    none_options = tuple(none_options)
    others = tuple(option for option in schema.options if option not in none_options)
    if len(none_options) == 1 and len(others) == 1:
        return None if text == "null" else decode_text(others[0], text)
    decoded = _json_load(text)
    compatible = tuple(option for option in schema.options if _json_shape_matches(_unwrap(option), decoded))
    if len(compatible) == 1:
        return _convert_json_value(_unwrap(compatible[0]), decoded)
    return decoded


def _json_shape_matches(schema: Schema, value: object) -> bool:
    if isinstance(schema, PrimitiveSchema):
        expected = {
            "str": str,
            "int": int,
            "float": (int, Decimal),
            "bool": bool,
            "bytes": str,
            "none": NoneType,
        }[schema.kind]
        if isinstance(expected, tuple):
            return type(value) in expected
        return type(value) is expected
    if isinstance(schema, TypeSchema):
        return type(value) is str if schema.python_type in _STANDARD_TEXT_TYPES else type(value) is schema.python_type
    if isinstance(schema, (SpecReferenceSchema, DataclassSchema, TypedDictSchema, TaggedUnionSchema, MappingSchema)):
        return type(value) is dict
    if isinstance(schema, (SequenceSchema, VariadicTupleSchema, FixedTupleSchema, NamedTupleSchema)):
        return type(value) is list
    if isinstance(schema, (EnumSchema, LiteralSchema)):
        return True
    assert isinstance(schema, RepresentationSchema)
    return schema.input is not None and _json_shape_matches(_unwrap(schema.input), value)


def _convert_json_value(schema: Schema, value: object) -> object:
    schema = _unwrap(schema)
    if isinstance(schema, PrimitiveSchema):
        if schema.kind == "bytes" and type(value) is str:
            return decode_bytes(value)
        if schema.kind == "float" and type(value) in (int, Decimal):
            converted = float(cast(int | Decimal, value))
            return converted if math.isfinite(converted) else value
        return value
    if isinstance(schema, TypeSchema):
        if schema.python_type is Decimal and type(value) is int:
            return Decimal(value)
        if type(value) is str:
            return _decode_standard_type(schema.python_type, value)
        return value
    if isinstance(schema, EnumSchema):
        return _enum_json_value(schema, value)
    if isinstance(schema, LiteralSchema):
        return _literal_json_value(schema, value)
    if isinstance(schema, RepresentationSchema):
        return value if schema.input is None else _convert_json_value(schema.input, value)
    if isinstance(schema, SequenceSchema) and type(value) is list:
        items = [_convert_json_value(schema.item, item) for item in value]
        return items if schema.kind == "list" else set(items) if schema.kind == "set" else frozenset(items)
    if isinstance(schema, VariadicTupleSchema) and type(value) is list:
        return tuple(_convert_json_value(schema.item, item) for item in value)
    if isinstance(schema, FixedTupleSchema) and type(value) is list and len(value) == len(schema.items):
        return tuple(
            _convert_json_value(item_schema, item) for item_schema, item in zip(schema.items, value, strict=True)
        )
    if isinstance(schema, NamedTupleSchema) and type(value) is list:
        converted = list(value)
        for index, field in enumerate(schema.fields[: len(converted)]):
            converted[index] = _convert_json_value(field.schema, converted[index])
        return converted
    if isinstance(schema, MappingSchema) and type(value) is dict:
        return {
            _convert_json_value(schema.key, key): _convert_json_value(schema.value, item) for key, item in value.items()
        }
    if isinstance(schema, SpecReferenceSchema) and isinstance(value, Mapping):
        fields = []
        for field in inspect_spec(schema.spec_type).fields:
            assert field.schema is not None
            fields.append((field.accepted_input_names, field.schema))
        return _convert_named_mapping(
            tuple(fields),
            value,
        )
    if isinstance(schema, DataclassSchema) and isinstance(value, Mapping):
        return _convert_named_mapping(
            tuple((field.accepted_input_names, field.schema) for field in schema.fields if field.init),
            value,
        )
    if isinstance(schema, TypedDictSchema) and isinstance(value, Mapping):
        return _convert_named_mapping(tuple(((field.name,), field.schema) for field in schema.fields), value)
    return value


def _convert_named_mapping(
    fields: tuple[tuple[tuple[str, ...], Schema], ...],
    value: Mapping[object, object],
) -> dict[object, object]:
    converted = dict(value)
    for accepted, schema in fields:
        for name in accepted:
            if name in converted:
                converted[name] = _convert_json_value(schema, converted[name])
    return converted


def _enum_json_value(schema: EnumSchema, value: object) -> object:
    for member in schema.enum_type:
        if type(member.value) is type(value) and member.value == value:
            return member
    return value


def _literal_json_value(schema: LiteralSchema, value: object) -> object:
    for literal in schema.values:
        if literal.python_type is type(value) and literal.value == value:
            return literal.value
    return value
