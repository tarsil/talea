"""Compile schema-aware value projection without runtime schema interpretation."""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from math import isfinite
from pathlib import Path, PosixPath, PurePath, PurePosixPath, PureWindowsPath, WindowsPath
from types import FunctionType
from typing import Literal, assert_never, cast
from uuid import UUID

from talea.codegen import _GeneratedNames
from talea.json.representations import encode_bytes, format_timedelta
from talea.schema.nodes import (
    ConstrainedSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    MappingSchema,
    PrimitiveSchema,
    Schema,
    SequenceSchema,
    SpecReferenceSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
)
from talea.serialization.errors import SerializationError
from talea.validation import ValidationError, compile_validator
from talea.validation.failure_contracts import schema_order_key

type OutputMode = Literal["python", "json"]
type ValueProjector = Callable[[object, tuple[object, ...]], object]

_JSON_STRING_TYPES = (
    UUID,
    datetime,
    date,
    time,
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
)


class _UnionProjector:
    """Select one already-compiled union branch for a current value."""

    __slots__ = ("branches",)

    def __init__(self, branches: tuple[tuple[Callable[[object], object], ValueProjector], ...]) -> None:
        self.branches = branches

    def __call__(self, value: object, location: tuple[object, ...]) -> object:
        for validator, projector in self.branches:
            try:
                validator(value)
            except ValidationError:
                continue
            return projector(value, location)
        raise SerializationError("current value no longer satisfies any declared union alternative", location)


def _finite_float(value: float, location: tuple[object, ...]) -> float:
    if not isfinite(value):
        raise SerializationError("non-finite floats are not valid JSON", location)
    return value


def _decimal_json(value: Decimal, location: tuple[object, ...]) -> str:
    if not value.is_finite():
        raise SerializationError("non-finite Decimal values are not valid JSON", location)
    return str(value)


def _enum_json(value: Enum, location: tuple[object, ...]) -> object:
    representation = value.value
    if representation is None or type(representation) in (bool, int, str):
        return representation
    if type(representation) is float and isfinite(representation):
        return representation
    raise SerializationError("Enum member value has no supported JSON representation", location)


def _literal_json(value: object, location: tuple[object, ...]) -> object:
    if isinstance(value, Enum):
        return _enum_json(value, location)
    if type(value) is bytes:
        return encode_bytes(value)
    if value is None or type(value) in (bool, int, str):
        return value
    raise SerializationError("Literal value has no supported JSON representation", location)


def _json_key(value: object, location: tuple[object, ...]) -> str:
    if type(value) is not str:
        raise SerializationError("JSON object keys must be exact strings", location)
    return value


def _unsupported_python_key(value: object, location: tuple[object, ...]) -> object:
    raise SerializationError("declared mapping key cannot be projected without losing hashability", location)


def _project_nested(
    value: object,
    serializer: Callable[[object], dict[str, object]],
    location: tuple[object, ...],
) -> dict[str, object]:
    try:
        return serializer(value)
    except SerializationError as error:
        raise error.prefixed(location) from error.__cause__


class _ValueProjectionCompiler:
    """Emit one direct value projector for a canonical Schema."""

    __slots__ = ("by_alias", "mode")

    def __init__(self, mode: OutputMode, by_alias: bool) -> None:
        self.mode = mode
        self.by_alias = by_alias

    def compile(self, schema: Schema) -> FunctionType:
        """Compile one schema-specialized ``(value, location)`` projector."""

        names = _GeneratedNames(("value", "location"))
        namespace: dict[str, object] = {"__name__": __name__}
        expression = self.expression(schema, "value", "location", names, namespace)
        source = f"def project(value, location):\n    return {expression}"
        exec(compile(source, f"<talea {self.mode} value serialization>", "exec"), namespace)
        return cast(FunctionType, namespace["project"])

    def expression(
        self,
        schema: Schema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
    ) -> str:
        """Return specialized source for one value and its dynamic location."""

        if isinstance(schema, ConstrainedSchema):
            return self.expression(schema.schema, value, location, names, namespace)
        if isinstance(schema, PrimitiveSchema):
            if self.mode == "json" and schema.kind == "bytes":
                encoder = self._bind(names, namespace, "encode_bytes", encode_bytes)
                return f"{encoder}({value})"
            if self.mode == "json" and schema.kind == "float":
                finite = self._bind(names, namespace, "finite_float", _finite_float)
                return f"{finite}({value}, {location})"
            return value
        if isinstance(schema, TypeSchema):
            if self.mode == "python":
                return value
            if schema.python_type is Decimal:
                decimal = self._bind(names, namespace, "decimal_json", _decimal_json)
                return f"{decimal}({value}, {location})"
            if schema.python_type is timedelta:
                formatter = self._bind(names, namespace, "format_timedelta", format_timedelta)
                return f"{formatter}({value})"
            if schema.python_type in _JSON_STRING_TYPES:
                if schema.python_type in (datetime, date, time):
                    return f"{value}.isoformat()"
                string = self._bind(names, namespace, "str", str)
                return f"{string}({value})"
            return value
        if isinstance(schema, EnumSchema):
            if self.mode == "python":
                return value
            converter = self._bind(names, namespace, "enum_json", _enum_json)
            return f"{converter}({value}, {location})"
        if isinstance(schema, LiteralSchema):
            if self.mode == "python":
                return value
            converter = self._bind(names, namespace, "literal_json", _literal_json)
            return f"{converter}({value}, {location})"
        if isinstance(schema, SpecReferenceSchema):
            artifacts = vars(schema.spec_type)["__talea_artifacts__"]
            serializer = artifacts.outputs.reference_for(
                artifacts.schema,
                self.mode,
                self.by_alias,
            )
            nested = self._bind(names, namespace, "nested_serializer", serializer)
            project = self._bind(names, namespace, "project_nested", _project_nested)
            return f"{project}({value}, {nested}, {location})"
        if isinstance(schema, SequenceSchema):
            return self._sequence_expression(schema, value, location, names, namespace)
        if isinstance(schema, MappingSchema):
            return self._mapping_expression(schema, value, location, names, namespace)
        if isinstance(schema, VariadicTupleSchema):
            return self._variadic_tuple_expression(schema, value, location, names, namespace)
        if isinstance(schema, FixedTupleSchema):
            return self._fixed_tuple_expression(schema, value, location, names, namespace)
        if isinstance(schema, UnionSchema):
            options = sorted(schema.options, key=schema_order_key)
            branches = tuple(
                (compile_validator(option), _ValueProjectionCompiler(self.mode, self.by_alias).compile(option))
                for option in options
            )
            projector = self._bind(names, namespace, "union_projector", _UnionProjector(branches))
            return f"{projector}({value}, {location})"
        assert_never(schema)

    def _sequence_expression(
        self,
        schema: SequenceSchema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
    ) -> str:
        index = names.allocate("index")
        item = names.allocate("item")
        enumerate_name = self._bind(names, namespace, "enumerate", enumerate)
        projected = self.expression(
            schema.item,
            item,
            f"(*{location}, {index})",
            names,
            namespace,
        )
        generator = f"({projected} for {index}, {item} in {enumerate_name}({value}))"
        if self.mode == "json" or schema.kind == "list":
            constructor = self._bind(names, namespace, "list", list)
        elif schema.kind == "set":
            constructor = self._bind(names, namespace, "set", set)
        else:
            constructor = self._bind(names, namespace, "frozenset", frozenset)
        return f"{constructor}{generator}"

    def _mapping_expression(
        self,
        schema: MappingSchema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
    ) -> str:
        key = names.allocate("key")
        item = names.allocate("item")
        member_location = f"(*{location}, {key})"
        if self.mode == "json":
            key_converter = self._bind(names, namespace, "json_key", _json_key)
            key_expression = f"{key_converter}({key}, {member_location})"
        elif self._python_key_supported(schema.key):
            key_expression = self.expression(schema.key, key, member_location, names, namespace)
        else:
            unsupported = self._bind(names, namespace, "unsupported_key", _unsupported_python_key)
            key_expression = f"{unsupported}({key}, {member_location})"
        item_expression = self.expression(schema.value, item, member_location, names, namespace)
        return f"{{{key_expression}: {item_expression} for {key}, {item} in {value}.items()}}"

    def _variadic_tuple_expression(
        self,
        schema: VariadicTupleSchema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
    ) -> str:
        index = names.allocate("index")
        item = names.allocate("item")
        enumerate_name = self._bind(names, namespace, "enumerate", enumerate)
        projected = self.expression(schema.item, item, f"(*{location}, {index})", names, namespace)
        generator = f"({projected} for {index}, {item} in {enumerate_name}({value}))"
        constructor = self._bind(
            names, namespace, "list" if self.mode == "json" else "tuple", list if self.mode == "json" else tuple
        )
        return f"{constructor}{generator}"

    def _fixed_tuple_expression(
        self,
        schema: FixedTupleSchema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
    ) -> str:
        items = tuple(
            self.expression(item, f"{value}[{index}]", f"(*{location}, {index})", names, namespace)
            for index, item in enumerate(schema.items)
        )
        if self.mode == "json":
            return f"[{', '.join(items)}]"
        suffix = "," if len(items) == 1 else ""
        return f"({', '.join(items)}{suffix})"

    def _python_key_supported(self, schema: Schema) -> bool:
        if isinstance(schema, ConstrainedSchema):
            return self._python_key_supported(schema.schema)
        if isinstance(schema, (PrimitiveSchema, TypeSchema, EnumSchema, LiteralSchema)):
            return True
        if isinstance(schema, SequenceSchema):
            return schema.kind == "frozenset" and self._python_key_supported(schema.item)
        if isinstance(schema, VariadicTupleSchema):
            return self._python_key_supported(schema.item)
        if isinstance(schema, FixedTupleSchema):
            return all(self._python_key_supported(item) for item in schema.items)
        if isinstance(schema, UnionSchema):
            return all(self._python_key_supported(option) for option in schema.options)
        return False

    @staticmethod
    def _bind(
        names: _GeneratedNames,
        namespace: dict[str, object],
        purpose: str,
        value: object,
    ) -> str:
        name = names.allocate(purpose)
        namespace[name] = value
        return name


def compile_value_projector(schema: Schema, mode: OutputMode, by_alias: bool) -> ValueProjector:
    """Compile one direct projector for a canonical field schema."""

    return _ValueProjectionCompiler(mode, by_alias).compile(schema)


def project_hook_value(
    function: FunctionType,
    value: object,
    mode: OutputMode,
    by_alias: bool,
    location: tuple[object, ...],
) -> object:
    """Run one user serializer once, then safely project its replacement."""

    try:
        replacement = function(value)
    except Exception as error:
        raise SerializationError(f"serialization hook {function.__name__!r} failed", location) from error
    if mode == "python":
        return _copy_hook_python(replacement, by_alias, location)
    return _project_hook_json(replacement, by_alias, location)


def _copy_hook_python(value: object, by_alias: bool, location: tuple[object, ...]) -> object:
    artifacts = getattr(type(value), "__talea_artifacts__", None)
    if artifacts is not None:
        serializer = artifacts.outputs.output_for(artifacts.schema, "python", by_alias, False)
        return _project_nested(value, serializer, location)
    if type(value) is list:
        return [_copy_hook_python(item, by_alias, (*location, index)) for index, item in enumerate(value)]
    if type(value) is tuple:
        return tuple(_copy_hook_python(item, by_alias, (*location, index)) for index, item in enumerate(value))
    if type(value) is set:
        return {_copy_hook_python(item, by_alias, (*location, index)) for index, item in enumerate(value)}
    if type(value) is frozenset:
        return frozenset(_copy_hook_python(item, by_alias, (*location, index)) for index, item in enumerate(value))
    if type(value) is dict:
        return {
            _copy_hook_python(key, by_alias, (*location, key)): _copy_hook_python(item, by_alias, (*location, key))
            for key, item in value.items()
        }
    return value


def _project_hook_json(value: object, by_alias: bool, location: tuple[object, ...]) -> object:
    artifacts = getattr(type(value), "__talea_artifacts__", None)
    if artifacts is not None:
        serializer = artifacts.outputs.output_for(artifacts.schema, "json", by_alias, False)
        return _project_nested(value, serializer, location)
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return _finite_float(value, location)
    if type(value) is bytes:
        return encode_bytes(value)
    if isinstance(value, Decimal):
        return _decimal_json(value, location)
    if isinstance(value, timedelta):
        return format_timedelta(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(
        value, (UUID, PurePath, IPv4Address, IPv6Address, IPv4Network, IPv6Network, IPv4Interface, IPv6Interface)
    ):
        return str(value)
    if isinstance(value, Enum):
        return _enum_json(value, location)
    if type(value) in (list, tuple, set, frozenset):
        container = cast(list[object] | tuple[object, ...] | set[object] | frozenset[object], value)
        return [_project_hook_json(item, by_alias, (*location, index)) for index, item in enumerate(container)]
    if type(value) is dict:
        return {
            _json_key(key, (*location, key)): _project_hook_json(item, by_alias, (*location, key))
            for key, item in value.items()
        }
    raise SerializationError(f"hook returned unsupported JSON value {type(value).__qualname__}", location)
