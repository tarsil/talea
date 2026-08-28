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
from pathlib import PurePath
from types import FunctionType
from typing import Protocol, assert_never, cast
from uuid import UUID

from talea.codegen import _GeneratedNames
from talea.errors.safety import REDACTED
from talea.json.representations import encode_bytes, format_timedelta, standard_json_representation
from talea.schema.nodes import (
    AliasSchema,
    ConstrainedSchema,
    DataclassSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    MappingSchema,
    NamedReferenceSchema,
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
from talea.serialization.errors import SerializationError
from talea.serialization.references import (
    OutputMode,
    ValueProjector,
    _NamedOutputReference,
    _NamedOutputRoot,
)
from talea.serialization.selection import _Selection
from talea.tagged.dispatch import nominal_dispatch
from talea.validation import ValidationError, compile_validator
from talea.validation.failure_contracts import schema_order_key


class _UnionProjector:
    """Select one already-compiled union branch for a current value."""

    __slots__ = ("branches", "sensitive")

    def __init__(
        self,
        branches: tuple[tuple[Callable[[object], object], ValueProjector], ...],
        sensitive: bool = False,
    ) -> None:
        self.branches = branches
        self.sensitive = sensitive

    def __call__(self, value: object, location: tuple[object, ...]) -> object:
        for validator, projector in self.branches:
            try:
                validator(value)
            except ValidationError:
                continue
            return projector(value, location)
        raise SerializationError(
            "current value no longer satisfies any declared union alternative",
            location,
            sensitive=self.sensitive,
        )


class _TaggedUnionProjector:
    """Select a validated branch by nominal identity or its canonical tag."""

    __slots__ = ("discriminator", "projectors", "spec_dispatch", "spec_types", "tags", "sensitive")

    def __init__(
        self,
        discriminator: str,
        projectors: tuple[ValueProjector, ...],
        *,
        spec_types: tuple[type[object], ...] = (),
        tags: dict[tuple[type[object], object], int] | None = None,
        sensitive: bool = False,
    ) -> None:
        self.discriminator = discriminator
        self.projectors = projectors
        self.spec_types = spec_types
        self.spec_dispatch = {branch_type: index for index, branch_type in enumerate(spec_types)}
        self.tags = tags
        self.sensitive = sensitive

    def __call__(self, value: object, location: tuple[object, ...]) -> object:
        if self.spec_types:
            direct = nominal_dispatch(value, self.spec_dispatch)
            if direct is not None:
                return self.projectors[direct](value, location)
        else:
            assert self.tags is not None and type(value) is dict
            dictionary = cast(dict[object, object], value)
            tag = dictionary[self.discriminator]
            index = self.tags.get((type(tag), tag))
            if index is not None:
                return self.projectors[index](value, location)
        raise SerializationError(
            "current value no longer identifies a declared tagged-union branch",
            location,
            sensitive=self.sensitive,
        )


def _finite_float(value: float, location: tuple[object, ...], sensitive: bool = False) -> float:
    if not isfinite(value):
        raise SerializationError("non-finite floats are not valid JSON", location, sensitive=sensitive)
    return value


def _decimal_json(value: Decimal, location: tuple[object, ...], sensitive: bool = False) -> str:
    if not value.is_finite():
        raise SerializationError("non-finite Decimal values are not valid JSON", location, sensitive=sensitive)
    return str(value)


def _enum_json(value: Enum, location: tuple[object, ...], sensitive: bool = False) -> object:
    representation = value.value
    if representation is None or type(representation) in (bool, int, str):
        return representation
    if type(representation) is float and isfinite(representation):
        return representation
    raise SerializationError(
        "Enum member value has no supported JSON representation",
        location,
        sensitive=sensitive,
    )


def _literal_json(value: object, location: tuple[object, ...], sensitive: bool = False) -> object:
    if isinstance(value, Enum):
        return _enum_json(value, location, sensitive)
    if type(value) is bytes:
        return encode_bytes(value)
    if value is None or type(value) in (bool, int, str):
        return value
    raise SerializationError("Literal value has no supported JSON representation", location, sensitive=sensitive)


def _json_key(value: object, location: tuple[object, ...], sensitive: bool = False) -> str:
    if type(value) is not str:
        raise SerializationError("JSON object keys must be exact strings", location, sensitive=sensitive)
    return value


def _unsupported_python_key(
    value: object,
    location: tuple[object, ...],
    sensitive: bool = False,
) -> object:
    raise SerializationError(
        "declared mapping key cannot be projected without losing hashability",
        location,
        sensitive=sensitive,
    )


def _project_nested(
    value: object,
    serializer: Callable[[object], dict[str, object]],
    location: tuple[object, ...],
    sensitive: bool = False,
) -> dict[str, object]:
    try:
        return serializer(value)
    except SerializationError as error:
        prefixed = error.prefixed(location, sensitive=sensitive)
        raise prefixed from prefixed.__cause__


def _project_representation(
    dump: Callable[[object], object],
    validator: Callable[[object], object],
    projector: ValueProjector,
    value: object,
    location: tuple[object, ...],
    sensitive: bool = False,
) -> object:
    """Dump one internal value and enforce its declared output contract."""

    try:
        candidate = dump(value)
    except Exception as error:
        failure = SerializationError(
            "Representation dumper failed",
            location,
            sensitive=sensitive,
        )
        raise failure from (None if sensitive else error)
    try:
        validator(candidate)
    except ValidationError as error:
        failure = SerializationError(
            "Representation dumper returned a value outside its declared output contract",
            location,
            sensitive=sensitive,
        )
        raise failure from (None if sensitive else error)
    return projector(candidate, location)


class _ValueProjectionCompiler:
    """Emit one direct value projector for a canonical Schema."""

    __slots__ = ("by_alias", "mode", "sensitive")

    def __init__(self, mode: OutputMode, by_alias: bool) -> None:
        self.mode = mode
        self.by_alias = by_alias
        self.sensitive = False

    def compile(
        self,
        schema: Schema,
        *,
        sensitive: bool = False,
        include: _Selection | None = None,
        exclude: _Selection | None = None,
        exclude_none: bool = False,
    ) -> FunctionType:
        """Compile one schema-specialized ``(value, location)`` projector."""

        names = _GeneratedNames(("value", "location"))
        namespace: dict[str, object] = {"__name__": __name__}
        expression = self.expression(
            schema,
            "value",
            "location",
            names,
            namespace,
            sensitive=sensitive,
            include=include,
            exclude=exclude,
            exclude_none=exclude_none,
        )
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
        *,
        sensitive: bool | None = None,
        include: _Selection | None = None,
        exclude: _Selection | None = None,
        exclude_none: bool = False,
    ) -> str:
        """Return specialized source for one value and its dynamic location."""

        if not sensitive or self.sensitive:
            return self._expression(
                schema,
                value,
                location,
                names,
                namespace,
                include,
                exclude,
                exclude_none,
            )
        self.sensitive = True
        try:
            return self._expression(
                schema,
                value,
                location,
                names,
                namespace,
                include,
                exclude,
                exclude_none,
            )
        finally:
            self.sensitive = False

    def _expression(
        self,
        schema: Schema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
        include: _Selection | None,
        exclude: _Selection | None,
        exclude_none: bool,
    ) -> str:
        """Project one schema while inheriting compile-time sensitivity."""

        if isinstance(schema, ConstrainedSchema):
            return self.expression(
                schema.schema,
                value,
                location,
                names,
                namespace,
                include=include,
                exclude=exclude,
                exclude_none=exclude_none,
            )
        if isinstance(schema, AliasSchema):
            return self.expression(
                schema.schema,
                value,
                location,
                names,
                namespace,
                sensitive=bool(schema.metadata.sensitive),
                include=include,
                exclude=exclude,
                exclude_none=exclude_none,
            )
        if isinstance(schema, NamedReferenceSchema):
            if include is not None or exclude is not None or exclude_none:
                return self.expression(
                    schema.target,
                    value,
                    location,
                    names,
                    namespace,
                    include=include,
                    exclude=exclude,
                    exclude_none=exclude_none,
                )
            projector = self._bind(
                names,
                namespace,
                "named_projector",
                _NamedOutputReference(schema, self.mode, self.by_alias, self.sensitive),
            )
            return f"{projector}({value}, {location})"
        if isinstance(schema, RepresentationSchema):
            output = schema.output
            dump = schema._declaration.dump
            if output is None or dump is None:
                raise SerializationError("Representation has no output direction")
            callback = self._bind(names, namespace, "representation_dump", dump)
            validator = self._bind(
                names,
                namespace,
                "representation_output_validator",
                compile_validator(output, sensitive=self.sensitive),
            )
            projector = self._bind(
                names,
                namespace,
                "representation_output_projector",
                _ValueProjectionCompiler(self.mode, self.by_alias).compile(
                    output,
                    sensitive=self.sensitive,
                    include=include,
                    exclude=exclude,
                    exclude_none=exclude_none,
                ),
            )
            project = self._bind(names, namespace, "project_representation", _project_representation)
            return f"{project}({callback}, {validator}, {projector}, {value}, {location}{self._sensitive_argument()})"
        if isinstance(schema, PrimitiveSchema):
            if self.mode == "json" and schema.kind == "bytes":
                encoder = self._bind(names, namespace, "encode_bytes", encode_bytes)
                return f"{encoder}({value})"
            if self.mode == "json" and schema.kind == "float":
                finite = self._bind(names, namespace, "finite_float", _finite_float)
                return f"{finite}({value}, {location}{self._sensitive_argument()})"
            return value
        if isinstance(schema, TypeSchema):
            if self.mode == "python":
                return value
            representation = standard_json_representation(schema.python_type)
            if representation == "decimal":
                decimal = self._bind(names, namespace, "decimal_json", _decimal_json)
                return f"{decimal}({value}, {location}{self._sensitive_argument()})"
            if representation == "duration":
                formatter = self._bind(names, namespace, "format_timedelta", format_timedelta)
                return f"{formatter}({value})"
            if representation is not None:
                if representation == "iso":
                    return f"{value}.isoformat()"
                string = self._bind(names, namespace, "str", str)
                return f"{string}({value})"
            return value
        if isinstance(schema, EnumSchema):
            if self.mode == "python":
                return value
            converter = self._bind(names, namespace, "enum_json", _enum_json)
            return f"{converter}({value}, {location}{self._sensitive_argument()})"
        if isinstance(schema, LiteralSchema):
            if self.mode == "python":
                return value
            converter = self._bind(names, namespace, "literal_json", _literal_json)
            return f"{converter}({value}, {location}{self._sensitive_argument()})"
        if isinstance(schema, SpecReferenceSchema):
            artifacts = vars(schema.spec_type)["__talea_artifacts__"]
            if include is None and exclude is None and not exclude_none:
                serializer = artifacts.outputs.reference_for(
                    artifacts.schema,
                    self.mode,
                    self.by_alias,
                )
            else:
                from talea.serialization.compilation import compile_selected_serialization

                serializer = compile_selected_serialization(
                    artifacts.schema,
                    self.mode,
                    self.by_alias,
                    include,
                    exclude,
                    exclude_none,
                )
            nested = self._bind(names, namespace, "nested_serializer", serializer)
            project = self._bind(names, namespace, "project_nested", _project_nested)
            return f"{project}({value}, {nested}, {location}{self._sensitive_argument()})"
        if isinstance(schema, DataclassSchema):
            return self._dataclass_expression(schema, value, location, names, namespace, include, exclude, exclude_none)
        if isinstance(schema, SequenceSchema):
            return self._sequence_expression(schema, value, location, names, namespace, include, exclude, exclude_none)
        if isinstance(schema, MappingSchema):
            return self._mapping_expression(schema, value, location, names, namespace, include, exclude, exclude_none)
        if isinstance(schema, TypedDictSchema):
            return self._typed_dict_expression(
                schema, value, location, names, namespace, include, exclude, exclude_none
            )
        if isinstance(schema, TaggedUnionSchema):
            projectors = tuple(
                _ValueProjectionCompiler(self.mode, self.by_alias).compile(
                    branch.schema,
                    sensitive=self.sensitive or schema.sensitive,
                    include=include,
                    exclude=exclude,
                    exclude_none=exclude_none,
                )
                for branch in schema.branches
            )
            first = schema.branches[0].schema
            if isinstance(first, SpecReferenceSchema):
                spec_types = tuple(cast(SpecReferenceSchema, branch.schema).spec_type for branch in schema.branches)
                tags = None
            else:
                spec_types = ()
                tags = {
                    (branch.tag.python_type, branch.tag.value): index for index, branch in enumerate(schema.branches)
                }
            projector = self._bind(
                names,
                namespace,
                "tagged_union_projector",
                _TaggedUnionProjector(
                    schema.discriminator,
                    projectors,
                    spec_types=spec_types,
                    tags=tags,
                    sensitive=self.sensitive or schema.sensitive,
                ),
            )
            return f"{projector}({value}, {location})"
        if isinstance(schema, VariadicTupleSchema):
            return self._variadic_tuple_expression(
                schema, value, location, names, namespace, include, exclude, exclude_none
            )
        if isinstance(schema, FixedTupleSchema):
            return self._fixed_tuple_expression(
                schema, value, location, names, namespace, include, exclude, exclude_none
            )
        if isinstance(schema, UnionSchema):
            options = sorted(schema.options, key=schema_order_key)
            selected = include is not None or exclude is not None or exclude_none
            branches = tuple(
                (
                    compile_validator(option, sensitive=True) if self.sensitive else compile_validator(option),
                    _ValueProjectionCompiler(self.mode, self.by_alias).compile(
                        option,
                        sensitive=self.sensitive,
                        include=include if selected and _schema_accepts_selection(option) else None,
                        exclude=exclude if selected and _schema_accepts_selection(option) else None,
                        exclude_none=exclude_none if selected and _schema_accepts_selection(option) else False,
                    ),
                )
                for option in options
            )
            projector = self._bind(
                names,
                namespace,
                "union_projector",
                _UnionProjector(branches, self.sensitive),
            )
            return f"{projector}({value}, {location})"
        assert_never(schema)

    def _sequence_expression(
        self,
        schema: SequenceSchema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
        include: _Selection | None,
        exclude: _Selection | None,
        exclude_none: bool,
    ) -> str:
        if (
            self.mode == "python"
            and schema.kind != "list"
            and (include is not None or exclude is not None)
            and _schema_accepts_selection(schema.item)
        ):
            raise ValueError(f"nested selection for {schema.kind} output cannot preserve hashable structural members")
        index = names.allocate("index")
        item = names.allocate("item")
        enumerate_name = self._bind(names, namespace, "enumerate", enumerate)
        projected = self.expression(
            schema.item,
            item,
            f"(*{location}, {index})",
            names,
            namespace,
            include=include,
            exclude=exclude,
            exclude_none=exclude_none,
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
        include: _Selection | None,
        exclude: _Selection | None,
        exclude_none: bool,
    ) -> str:
        key = names.allocate("key")
        item = names.allocate("item")
        segment = self._bind(names, namespace, "redacted_location", REDACTED) if self.sensitive else key
        member_location = f"(*{location}, {segment})"
        if self.mode == "json":
            key_converter = self._bind(names, namespace, "json_key", _json_key)
            key_expression = f"{key_converter}({key}, {member_location}{self._sensitive_argument()})"
        elif self._python_key_supported(schema.key):
            key_expression = self.expression(schema.key, key, member_location, names, namespace)
        else:
            unsupported = self._bind(names, namespace, "unsupported_key", _unsupported_python_key)
            key_expression = f"{unsupported}({key}, {member_location}{self._sensitive_argument()})"
        item_expression = self.expression(
            schema.value,
            item,
            member_location,
            names,
            namespace,
            include=include,
            exclude=exclude,
            exclude_none=exclude_none,
        )
        return f"{{{key_expression}: {item_expression} for {key}, {item} in {value}.items()}}"

    def _typed_dict_expression(
        self,
        schema: TypedDictSchema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
        include: _Selection | None,
        exclude: _Selection | None,
        exclude_none: bool,
    ) -> str:
        """Project present declared keys into a detached dictionary."""

        fields = []
        for field, child_include, child_exclude in _selected_output_fields(schema.fields, include, exclude):
            key = self._bind(names, namespace, "typed_dict_key", field.name)
            projected = self.expression(
                field.schema,
                f"{value}[{key}]",
                f"(*{location}, {key})",
                names,
                namespace,
                sensitive=bool(field.metadata.sensitive),
                include=child_include,
                exclude=child_exclude,
                exclude_none=exclude_none and (child_include is not None or child_exclude is not None),
            )
            condition = f"{key} in {value}"
            if exclude_none:
                condition += f" and {value}[{key}] is not None"
            fields.append(f"**({{{key}: {projected}}} if {condition} else {{}})")
        return f"{{{', '.join(fields)}}}"

    def _dataclass_expression(
        self,
        schema: DataclassSchema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
        include: _Selection | None,
        exclude: _Selection | None,
        exclude_none: bool,
    ) -> str:
        """Project declared stored fields directly into a detached dictionary."""

        entries = []
        for field, child_include, child_exclude in _selected_output_fields(schema.fields, include, exclude):
            key = field.external_name if self.by_alias else field.name
            key_name = self._bind(names, namespace, "dataclass_key", key)
            projected = self.expression(
                field.schema,
                f"{value}.{field.name}",
                f"(*{location}, {key_name})",
                names,
                namespace,
                sensitive=bool(field.metadata.sensitive),
                include=child_include,
                exclude=child_exclude,
                exclude_none=exclude_none and (child_include is not None or child_exclude is not None),
            )
            if exclude_none:
                entries.append(f"**({{{key_name}: {projected}}} if {value}.{field.name} is not None else {{}})")
            else:
                entries.append(f"{key_name}: {projected}")
        return f"{{{', '.join(entries)}}}"

    def _variadic_tuple_expression(
        self,
        schema: VariadicTupleSchema,
        value: str,
        location: str,
        names: _GeneratedNames,
        namespace: dict[str, object],
        include: _Selection | None,
        exclude: _Selection | None,
        exclude_none: bool,
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
            include=include,
            exclude=exclude,
            exclude_none=exclude_none,
        )
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
        include: _Selection | None,
        exclude: _Selection | None,
        exclude_none: bool,
    ) -> str:
        items = tuple(
            self.expression(
                item,
                f"{value}[{index}]",
                f"(*{location}, {index})",
                names,
                namespace,
                include=include,
                exclude=exclude,
                exclude_none=exclude_none,
            )
            for index, item in enumerate(schema.items)
        )
        if self.mode == "json":
            return f"[{', '.join(items)}]"
        suffix = "," if len(items) == 1 else ""
        return f"({', '.join(items)}{suffix})"

    def _python_key_supported(self, schema: Schema) -> bool:
        if isinstance(schema, ConstrainedSchema):
            return self._python_key_supported(schema.schema)
        if isinstance(schema, AliasSchema):
            return self._python_key_supported(schema.schema)
        if isinstance(schema, NamedReferenceSchema):
            return False
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
        if isinstance(schema, TaggedUnionSchema):
            return False
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

    def _sensitive_argument(self) -> str:
        return ", True" if self.sensitive else ""


class _SelectableField(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def schema(self) -> Schema: ...


def _selected_output_fields[F: _SelectableField](
    fields: tuple[F, ...],
    include: _Selection | None,
    exclude: _Selection | None,
) -> tuple[tuple[F, _Selection | None, _Selection | None], ...]:
    """Resolve nested compile-time field selection for one structural object."""

    include_by_name = None if include is None else dict(include.entries)
    exclude_by_name = None if exclude is None else dict(exclude.entries)
    selected = []
    for field in fields:
        if include_by_name is not None and field.name not in include_by_name:
            continue
        if exclude_by_name is not None and field.name in exclude_by_name and exclude_by_name[field.name] is None:
            continue
        child_include = None if include_by_name is None else include_by_name.get(field.name)
        child_exclude = None if exclude_by_name is None else exclude_by_name.get(field.name)
        selected.append((field, child_include, child_exclude))
    return tuple(selected)


def _schema_accepts_selection(schema: Schema, active: set[int] | None = None) -> bool:
    """Return whether one branch has structural descendants for a validated plan."""

    if active is None:
        active = set()
    while isinstance(schema, (AliasSchema, ConstrainedSchema)):
        schema = schema.schema
    identity = id(schema)
    if identity in active:
        return False
    active.add(identity)
    try:
        if isinstance(schema, NamedReferenceSchema):
            return _schema_accepts_selection(schema.target, active)
        if isinstance(schema, RepresentationSchema):
            return schema.output is not None and _schema_accepts_selection(schema.output, active)
        if isinstance(schema, (SpecReferenceSchema, DataclassSchema, TypedDictSchema, TaggedUnionSchema)):
            return True
        if isinstance(schema, (SequenceSchema, VariadicTupleSchema)):
            return _schema_accepts_selection(schema.item, active)
        if isinstance(schema, MappingSchema):
            return _schema_accepts_selection(schema.value, active)
        if isinstance(schema, FixedTupleSchema):
            return all(_schema_accepts_selection(item, active) for item in schema.items)
        if isinstance(schema, UnionSchema):
            return any(_schema_accepts_selection(option, active) for option in schema.options)
        return False
    finally:
        active.remove(identity)


def compile_value_projector(
    schema: Schema,
    mode: OutputMode,
    by_alias: bool,
    *,
    sensitive: bool = False,
) -> ValueProjector:
    """Compile one direct projector for a canonical field schema."""

    compiled = _ValueProjectionCompiler(mode, by_alias).compile(schema, sensitive=sensitive)
    from talea.declaration.policies import schema_contains_named_reference

    if schema_contains_named_reference(schema):
        return _NamedOutputRoot(compiled, sensitive)
    return compiled


def project_hook_value(
    function: FunctionType,
    value: object,
    mode: OutputMode,
    by_alias: bool,
    location: tuple[object, ...],
    sensitive: bool = False,
) -> object:
    """Run one user serializer once, then safely project its replacement."""

    try:
        replacement = function(value)
    except Exception as error:
        failure = SerializationError(
            f"serialization hook {function.__name__!r} failed",
            location,
            sensitive=sensitive,
        )
        raise failure from (None if sensitive else error)
    if mode == "python":
        return _copy_hook_python(replacement, by_alias, location, sensitive, None)
    return _project_hook_json(replacement, by_alias, location, sensitive, None)


def project_declared_hook_value(
    function: FunctionType,
    hook_name: str,
    validator: Callable[[object], object],
    projector: ValueProjector,
    value: object,
    location: tuple[object, ...],
    sensitive: bool = False,
) -> object:
    """Run one field serializer once and enforce its declared result schema."""

    try:
        replacement = function(value)
    except Exception as error:
        failure = SerializationError(
            f"serialization hook {hook_name!r} failed",
            location,
            sensitive=sensitive,
        )
        raise failure from (None if sensitive else error)
    try:
        validator(replacement)
    except ValidationError as error:
        failure = SerializationError(
            f"serialization hook {hook_name!r} returned a value outside its declared output contract",
            location,
            sensitive=sensitive,
        )
        raise failure from (None if sensitive else error)
    return projector(replacement, location)


def _copy_hook_python(
    value: object,
    by_alias: bool,
    location: tuple[object, ...],
    sensitive: bool,
    active: set[int] | None,
) -> object:
    artifacts = getattr(type(value), "__talea_artifacts__", None)
    if artifacts is not None:
        serializer = artifacts.outputs.output_for(artifacts.schema, "python", by_alias, False)
        return _project_nested(value, serializer, location, sensitive)
    container_type = type(value)
    if container_type not in (list, tuple, set, frozenset, dict):
        return value
    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        raise SerializationError(
            "cyclic object graphs cannot be serialized",
            location,
            sensitive=sensitive,
        ) from None
    active.add(identity)
    try:
        container = cast(list[object] | tuple[object, ...] | set[object] | frozenset[object], value)
        if container_type is list:
            return [
                _copy_hook_python(item, by_alias, (*location, index), sensitive, active)
                for index, item in enumerate(container)
            ]
        if container_type is tuple:
            return tuple(
                _copy_hook_python(item, by_alias, (*location, index), sensitive, active)
                for index, item in enumerate(container)
            )
        if container_type is set:
            return {
                _copy_hook_python(item, by_alias, (*location, index), sensitive, active)
                for index, item in enumerate(container)
            }
        if container_type is frozenset:
            return frozenset(
                _copy_hook_python(item, by_alias, (*location, index), sensitive, active)
                for index, item in enumerate(container)
            )
        return {
            _copy_hook_python(
                key,
                by_alias,
                (*location, REDACTED if sensitive else key),
                sensitive,
                active,
            ): _copy_hook_python(
                item,
                by_alias,
                (*location, REDACTED if sensitive else key),
                sensitive,
                active,
            )
            for key, item in cast(dict[object, object], value).items()
        }
    finally:
        active.remove(identity)


def _project_hook_json(
    value: object,
    by_alias: bool,
    location: tuple[object, ...],
    sensitive: bool,
    active: set[int] | None,
) -> object:
    artifacts = getattr(type(value), "__talea_artifacts__", None)
    if artifacts is not None:
        serializer = artifacts.outputs.output_for(artifacts.schema, "json", by_alias, False)
        return _project_nested(value, serializer, location, sensitive)
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return _finite_float(value, location, sensitive)
    if type(value) is bytes:
        return encode_bytes(value)
    if isinstance(value, Decimal):
        return _decimal_json(value, location, sensitive)
    if isinstance(value, timedelta):
        return format_timedelta(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(
        value, (UUID, PurePath, IPv4Address, IPv6Address, IPv4Network, IPv6Network, IPv4Interface, IPv6Interface)
    ):
        return str(value)
    if isinstance(value, Enum):
        return _enum_json(value, location, sensitive)
    container_type = type(value)
    if container_type in (list, tuple, set, frozenset, dict):
        if active is None:
            active = set()
        identity = id(value)
        if identity in active:
            raise SerializationError(
                "cyclic object graphs cannot be serialized",
                location,
                sensitive=sensitive,
            ) from None
        active.add(identity)
        try:
            if container_type is not dict:
                container = cast(list[object] | tuple[object, ...] | set[object] | frozenset[object], value)
                return [
                    _project_hook_json(item, by_alias, (*location, index), sensitive, active)
                    for index, item in enumerate(container)
                ]
            return {
                _json_key(key, (*location, REDACTED if sensitive else key), sensitive): _project_hook_json(
                    item,
                    by_alias,
                    (*location, REDACTED if sensitive else key),
                    sensitive,
                    active,
                )
                for key, item in cast(dict[object, object], value).items()
            }
        finally:
            active.remove(identity)
    raise SerializationError(
        f"hook returned unsupported JSON value {type(value).__qualname__}",
        location,
        sensitive=sensitive,
    )
