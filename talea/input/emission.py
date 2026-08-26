"""Emit schema-aware boundary conversion before canonical validation."""

from collections.abc import Mapping
from datetime import date, datetime, time
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
from typing import Literal, assert_never
from uuid import UUID

from talea.errors import ErrorCode
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
from talea.validation.emission import _GeneratedNames, _ValidationEmitter
from talea.validation.failure_contracts import describe_schema, schema_order_key

type InputMode = Literal["mapping", "json"]

_JSON_STRING_TYPES = frozenset(
    {
        UUID,
        date,
        datetime,
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
    }
)


def schema_needs_conversion(schema: Schema, mode: InputMode) -> bool:
    """Return whether one boundary must prepare values for ``schema``."""

    if isinstance(schema, ConstrainedSchema):
        return schema_needs_conversion(schema.schema, mode)
    if isinstance(schema, SpecReferenceSchema):
        return True
    if isinstance(schema, PrimitiveSchema):
        return mode == "json" and schema.kind == "float"
    if isinstance(schema, TypeSchema):
        return mode == "json" and (schema.python_type in _JSON_STRING_TYPES or schema.python_type is Decimal)
    if isinstance(schema, EnumSchema):
        return mode == "json" and bool(_json_enum_members(schema))
    if isinstance(schema, LiteralSchema):
        return mode == "json" and any(isinstance(item.value, Enum) for item in schema.values)
    if isinstance(schema, SequenceSchema):
        return (mode == "json" and schema.kind != "list") or schema_needs_conversion(schema.item, mode)
    if isinstance(schema, MappingSchema):
        return schema_needs_conversion(schema.value, mode)
    if isinstance(schema, VariadicTupleSchema):
        return mode == "json" or schema_needs_conversion(schema.item, mode)
    if isinstance(schema, FixedTupleSchema):
        return mode == "json" or any(schema_needs_conversion(item, mode) for item in schema.items)
    if isinstance(schema, UnionSchema):
        return any(schema_needs_conversion(option, mode) for option in schema.options)
    assert_never(schema)


def schema_may_construct_spec(schema: Schema) -> bool:
    """Return whether boundary conversion can create a nested Spec."""

    if isinstance(schema, ConstrainedSchema):
        return schema_may_construct_spec(schema.schema)
    if isinstance(schema, SpecReferenceSchema):
        artifacts = vars(schema.spec_type)["__talea_artifacts__"]
        return not artifacts.schema.instances_are_permanently_trusted
    if isinstance(schema, SequenceSchema):
        return schema_may_construct_spec(schema.item)
    if isinstance(schema, MappingSchema):
        return schema_may_construct_spec(schema.value)
    if isinstance(schema, VariadicTupleSchema):
        return schema_may_construct_spec(schema.item)
    if isinstance(schema, FixedTupleSchema):
        return any(schema_may_construct_spec(item) for item in schema.items)
    if isinstance(schema, UnionSchema):
        return any(schema_may_construct_spec(option) for option in schema.options)
    return False


def _json_enum_members(schema: EnumSchema | LiteralSchema) -> dict[tuple[type[object], object], Enum]:
    members: dict[tuple[type[object], object], Enum] = {}
    values = (
        (item.value for item in schema.members)
        if isinstance(schema, EnumSchema)
        else (item.value for item in schema.values)
    )
    for value in values:
        if not isinstance(value, Enum):
            continue
        representation = value.value
        if representation is None or type(representation) in (bool, int, str):
            members.setdefault((type(representation), representation), value)
        elif type(representation) is float and isfinite(representation):
            members.setdefault((float, representation), value)
    return members


class _BoundaryValidationEmitter(_ValidationEmitter):
    """Add boundary conversion while retaining canonical validation emission."""

    def __init__(
        self,
        lines: list[str],
        names: _GeneratedNames,
        namespace: dict[str, object],
        *,
        mode: InputMode,
        title: str | None = None,
        trusted_instances: str | None = None,
    ) -> None:
        super().__init__(
            lines,
            names,
            namespace,
            title=title,
            trusted_instances=trusted_instances,
        )
        self.mode = mode
        self._validating = False

    def emit_schema(
        self,
        schema: Schema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Prepare one external value, then emit its canonical validation once."""

        if self._validating:
            super().emit_schema(schema, value, location, indentation)
            return
        base = schema.schema if isinstance(schema, ConstrainedSchema) else schema
        if isinstance(base, UnionSchema):
            if schema_needs_conversion(base, self.mode):
                self.emit_boundary_union(base, value, location, indentation)
                return
            self._validating = True
            try:
                super().emit_schema(schema, value, location, indentation)
            finally:
                self._validating = False
            return
        self.emit_conversion(base, value, location, indentation)
        self._validating = True
        try:
            super().emit_schema(schema, value, location, indentation)
        finally:
            self._validating = False

    def emit_conversion(
        self,
        schema: Schema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit only representation conversion, never structural approval."""

        if isinstance(schema, ConstrainedSchema):
            self.emit_conversion(schema.schema, value, location, indentation)
        elif isinstance(schema, SpecReferenceSchema):
            self.emit_spec_conversion(schema, value, location, indentation)
        elif isinstance(schema, PrimitiveSchema):
            if self.mode == "json" and schema.kind == "float":
                self.emit_json_float_conversion(value, location, indentation)
        elif isinstance(schema, TypeSchema):
            if self.mode == "json":
                self.emit_json_type_conversion(schema, value, location, indentation)
        elif isinstance(schema, EnumSchema):
            if self.mode == "json":
                self.emit_enum_conversion(schema, value, indentation)
        elif isinstance(schema, LiteralSchema):
            if self.mode == "json":
                self.emit_enum_conversion(schema, value, indentation)
        elif isinstance(schema, SequenceSchema):
            self.emit_sequence_conversion(schema, value, location, indentation)
        elif isinstance(schema, MappingSchema):
            self.emit_mapping_conversion(schema, value, location, indentation)
        elif isinstance(schema, VariadicTupleSchema):
            self.emit_variadic_tuple_conversion(schema, value, location, indentation)
        elif isinstance(schema, FixedTupleSchema):
            self.emit_fixed_tuple_conversion(schema, value, location, indentation)
        elif isinstance(schema, UnionSchema):
            self.emit_boundary_union(schema, value, location, indentation)
        else:
            assert_never(schema)

    def emit_spec_conversion(
        self,
        schema: SpecReferenceSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Construct a nested Spec from its boundary object and mark same-operation trust."""

        instance_check = self.runtime("isinstance", isinstance)
        spec_type = self.bind("spec_type", schema.spec_type)
        if self.mode == "mapping":
            input_type = self.runtime("mapping", Mapping)
            shape = f"{instance_check}({value}, {input_type})"
            artifacts = vars(schema.spec_type)["__talea_artifacts__"]
            nested_input = artifacts.inputs.input_for(artifacts.schema, schema.spec_type, "mapping")
        else:
            input_type = self.runtime("dict", dict)
            shape = f"{self.runtime('type', type)}({value}) is {input_type}"
            artifacts = vars(schema.spec_type)["__talea_artifacts__"]
            nested_input = artifacts.inputs.input_for(artifacts.schema, schema.spec_type, "json")
        converter = self.bind("nested_input", nested_input)
        error = self.variable("nested_error")
        prefixed = self.variable("prefixed_error")
        self.emit(indentation, f"if not {instance_check}({value}, {spec_type}) and {shape}:")
        self.emit(indentation + 1, "try:")
        self.emit(indentation + 2, f"{value} = {converter}({value})")
        self.emit(indentation + 1, f"except {self.validation_error_name} as {error}:")
        self.emit(
            indentation + 2,
            f"{prefixed} = {error}.prefixed({self.location_expression(location)}{self.title_argument()})",
        )
        self.emit(indentation + 2, f"raise {prefixed} from {prefixed}.__cause__")
        if self.trusted_instances is not None:
            set_type = self.runtime("set", set)
            self.emit(indentation + 1, f"if {self.trusted_instances} is None:")
            self.emit(indentation + 2, f"{self.trusted_instances} = {set_type}()")
            self.emit(
                indentation + 1,
                f"{self.trusted_instances}.add({self.runtime('id', id)}({value}))",
            )

    def emit_sequence_conversion(
        self,
        schema: SequenceSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Convert sequence members and JSON's single array representation."""

        if not schema_needs_conversion(schema, self.mode):
            return
        source_type = list if self.mode == "json" else self._sequence_types[schema.kind]
        condition = self._exact_type_condition(value, source_type)
        converted = self.variable("converted_items")
        index = self.variable("index")
        item = self.variable("item")
        self.emit(indentation, f"if {condition}:")
        self.emit(indentation + 1, f"{converted} = []")
        self.emit(indentation + 1, f"for {index}, {item} in {self.runtime('enumerate', enumerate)}({value}):")
        self.emit_conversion(schema.item, item, (*location, index), indentation + 2)
        self.emit(indentation + 2, f"{converted}.append({item})")
        target = self._sequence_types[schema.kind]
        if target is list:
            expression = converted
            self.emit(indentation + 1, f"{value} = {expression}")
        else:
            expression = f"{self.runtime(schema.kind, target)}({converted})"
            self.emit(indentation + 1, "try:")
            self.emit(indentation + 2, f"{value} = {expression}")
            self.emit(
                indentation + 1,
                f"except {self.runtime('type_error', TypeError)}:",
            )
            self.emit(indentation + 2, "pass")

    def emit_mapping_conversion(
        self,
        schema: MappingSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Convert dictionary values while preserving JSON object keys as strings."""

        if not schema_needs_conversion(schema.value, self.mode):
            return
        condition = self._exact_type_condition(value, dict)
        converted = self.variable("converted_mapping")
        key = self.variable("key")
        item = self.variable("item")
        self.emit(indentation, f"if {condition}:")
        self.emit(indentation + 1, f"{converted} = {{}}")
        self.emit(indentation + 1, f"for {key}, {item} in {value}.items():")
        self.emit_conversion(schema.value, item, (*location, key), indentation + 2)
        self.emit(indentation + 2, f"{converted}[{key}] = {item}")
        self.emit(indentation + 1, f"{value} = {converted}")

    def emit_variadic_tuple_conversion(
        self,
        schema: VariadicTupleSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Convert a strict tuple or JSON array into a variadic tuple."""

        if not schema_needs_conversion(schema, self.mode):
            return
        source = list if self.mode == "json" else tuple
        condition = self._exact_type_condition(value, source)
        converted = self.variable("converted_items")
        index = self.variable("index")
        item = self.variable("item")
        self.emit(indentation, f"if {condition}:")
        self.emit(indentation + 1, f"{converted} = []")
        self.emit(indentation + 1, f"for {index}, {item} in {self.runtime('enumerate', enumerate)}({value}):")
        self.emit_conversion(schema.item, item, (*location, index), indentation + 2)
        self.emit(indentation + 2, f"{converted}.append({item})")
        self.emit(indentation + 1, f"{value} = {self.runtime('tuple', tuple)}({converted})")

    def emit_fixed_tuple_conversion(
        self,
        schema: FixedTupleSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Convert a correctly sized strict tuple or JSON array positionally."""

        if not schema_needs_conversion(schema, self.mode):
            return
        source = list if self.mode == "json" else tuple
        length = self.runtime("len", len)
        condition = f"{self._exact_type_condition(value, source)} and {length}({value}) == {len(schema.items)}"
        items = tuple(self.variable("tuple_item") for _ in schema.items)
        self.emit(indentation, f"if {condition}:")
        for index, (item_schema, item) in enumerate(zip(schema.items, items, strict=True)):
            self.emit(indentation + 1, f"{item} = {value}[{index}]")
            self.emit_conversion(item_schema, item, (*location, str(index)), indentation + 1)
        tuple_expression = ", ".join(items)
        if len(items) == 1:
            tuple_expression = f"{tuple_expression},"
        self.emit(indentation + 1, f"{value} = ({tuple_expression})")

    def emit_json_float_conversion(
        self,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Convert JSON integer/Decimal numbers to finite Python floats."""

        type_name = self.runtime("type", type)
        int_type = self.runtime("int", int)
        float_type = self.runtime("float", float)
        decimal_type = self.bind("decimal_type", Decimal)
        finite = self.runtime("isfinite", isfinite)
        self.emit(indentation, f"if {type_name}({value}) is {int_type}:")
        converted = self.variable("float_value")
        self.emit(indentation + 1, "try:")
        self.emit(indentation + 2, f"{converted} = {float_type}({value})")
        self.emit(
            indentation + 1,
            f"except {self.runtime('overflow_error', OverflowError)}:",
        )
        self.emit(indentation + 2, "pass")
        self.emit(indentation + 1, "else:")
        self.emit(indentation + 2, f"if {finite}({converted}):")
        self.emit(indentation + 3, f"{value} = {converted}")
        self.emit(indentation, f"elif {type_name}({value}) is {decimal_type}:")
        self.emit(indentation + 1, f"{converted} = {float_type}({value})")
        self.emit(indentation + 1, f"if {finite}({converted}):")
        self.emit(indentation + 2, f"{value} = {converted}")
        self.emit(indentation, f"elif {type_name}({value}) is {float_type} and not {finite}({value}):")
        self.emit_json_invalid(value, location, indentation + 1, "non_finite_number")

    def emit_json_type_conversion(
        self,
        schema: TypeSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Convert canonical JSON representations for supported standard types."""

        python_type = schema.python_type
        type_name = self.runtime("type", type)
        if python_type is Decimal:
            decimal_type = self.bind("decimal_type", Decimal)
            self.emit(indentation, f"if {type_name}({value}) is {self.runtime('int', int)}:")
            self.emit(indentation + 1, f"{value} = {decimal_type}({value})")
            self.emit(indentation, f"elif {type_name}({value}) is {decimal_type} and not {value}.is_finite():")
            self.emit_json_invalid(value, location, indentation + 1, "non_finite_number")
            return
        if python_type not in _JSON_STRING_TYPES:
            return
        if python_type is date:
            parser = date.fromisoformat
        elif python_type is datetime:
            parser = datetime.fromisoformat
        elif python_type is time:
            parser = time.fromisoformat
        else:
            parser = python_type
        parser_name = self.bind("json_parser", parser)
        error_types = self.bind(
            "json_conversion_errors",
            (ValueError, TypeError, OverflowError, OSError, NotImplementedError),
        )
        error = self.variable("conversion_error")
        self.emit(indentation, f"if {type_name}({value}) is {self.runtime('str', str)}:")
        self.emit(indentation + 1, "try:")
        self.emit(indentation + 2, f"{value} = {parser_name}({value})")
        self.emit(indentation + 1, f"except {error_types} as {error}:")
        self.emit(indentation + 2, "pass")

    def emit_enum_conversion(
        self,
        schema: EnumSchema | LiteralSchema,
        value: str,
        indentation: int,
    ) -> None:
        """Convert exact JSON-compatible Enum values without primitive widening."""

        members = _json_enum_members(schema)
        if not members:
            return
        mapping = self.bind("enum_members", members)
        sentinel = self.bind("enum_missing", object())
        member = self.variable("enum_member")
        lookup = self.variable("enum_value")
        converted = self.variable("enum_float")
        type_name = self.runtime("type", type)
        decimal_type = self.bind("decimal_type", Decimal)
        self.emit(indentation, f"{lookup} = {value}")
        self.emit(indentation, f"if {type_name}({lookup}) is {decimal_type}:")
        self.emit(indentation + 1, f"{converted} = {self.runtime('float', float)}({lookup})")
        self.emit(indentation + 1, f"if {self.runtime('isfinite', isfinite)}({converted}):")
        self.emit(indentation + 2, f"{lookup} = {converted}")
        self.emit(
            indentation,
            f"{member} = {mapping}.get(({type_name}({lookup}), {lookup}), {sentinel})",
        )
        self.emit(indentation, f"if {member} is not {sentinel}:")
        self.emit(indentation + 1, f"{value} = {member}")

    def emit_boundary_union(
        self,
        schema: UnionSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Select a converted union alternative through canonical validation emission."""

        options = sorted(schema.options, key=schema_order_key)
        matched = self.variable("matched")
        best = self.variable("best_error")
        best_label = self.variable("best_label")
        self.emit(indentation, f"{matched} = False")
        self.emit(indentation, f"{best} = None")
        for option in options:
            candidate = self.variable("candidate")
            error = self.variable("error")
            label = self.bind("union_label", describe_schema(option))
            self.emit(indentation, f"if not {matched} and ({self.boundary_condition(option, value)}):")
            self.emit(indentation + 1, f"{candidate} = {value}")
            self.emit(indentation + 1, "try:")
            self.emit_schema(option, candidate, location, indentation + 2)
            self.emit(indentation + 1, f"except {self.validation_error_name} as {error}:")
            self.emit(indentation + 2, f"if {best} is None:")
            self.emit(indentation + 3, f"{best} = {error}")
            self.emit(indentation + 3, f"{best_label} = {label}")
            self.emit(indentation + 2, f"elif {self.runtime('type', type)}({best}) is {self.runtime('list', list)}:")
            self.emit(indentation + 3, f"{best}.append(({label}, {error}))")
            self.emit(indentation + 2, "else:")
            self.emit(indentation + 3, f"{best} = [({best_label}, {best}), ({label}, {error})]")
            self.emit(indentation + 1, "else:")
            self.emit(indentation + 2, f"{value} = {candidate}")
            self.emit(indentation + 2, f"{matched} = True")
        self.emit(indentation, f"if not {matched}:")
        self.emit_union_failure(schema, options, value, location, (best, best_label), indentation + 1)

    def boundary_condition(self, schema: Schema, value: str) -> str:
        """Return a representation-aware union plausibility condition."""

        if isinstance(schema, ConstrainedSchema):
            return self.boundary_condition(schema.schema, value)
        if self.mode == "mapping":
            if isinstance(schema, SpecReferenceSchema):
                return (
                    f"{self.runtime('isinstance', isinstance)}({value}, {self.bind('spec_type', schema.spec_type)}) "
                    f"or {self.runtime('isinstance', isinstance)}({value}, {self.runtime('mapping', Mapping)})"
                )
            return self.top_level_condition(schema, value)
        type_name = self.runtime("type", type)
        if isinstance(schema, PrimitiveSchema):
            if schema.kind == "float":
                types = self.bind("json_number_types", (int, float, Decimal))
                return f"{type_name}({value}) in {types}"
            return self.top_level_condition(schema, value)
        if isinstance(schema, TypeSchema):
            existing = self.top_level_condition(schema, value)
            if schema.python_type is Decimal:
                return f"({existing}) or {type_name}({value}) is {self.runtime('int', int)}"
            if schema.python_type in _JSON_STRING_TYPES:
                return f"({existing}) or {type_name}({value}) is {self.runtime('str', str)}"
            return existing
        if isinstance(schema, (EnumSchema, LiteralSchema)):
            return "True"
        if isinstance(schema, SpecReferenceSchema):
            existing = self.top_level_condition(schema, value)
            return f"({existing}) or {type_name}({value}) is {self.runtime('dict', dict)}"
        if isinstance(schema, SequenceSchema):
            existing = self.top_level_condition(schema, value)
            return f"({existing}) or {type_name}({value}) is {self.runtime('list', list)}"
        if isinstance(schema, MappingSchema):
            return self.top_level_condition(schema, value)
        if isinstance(schema, (VariadicTupleSchema, FixedTupleSchema)):
            existing = self.top_level_condition(schema, value)
            return f"({existing}) or {type_name}({value}) is {self.runtime('list', list)}"
        if isinstance(schema, UnionSchema):
            return " or ".join(self.boundary_condition(option, value) for option in schema.options)
        assert_never(schema)

    def emit_json_invalid(
        self,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        reason: str,
    ) -> None:
        """Emit one field-located strict-JSON failure."""

        code = self.runtime("error_code_json_invalid", ErrorCode.JSON_INVALID)
        context = self.bind("json_error_context", (("reason", reason),))
        error = self.variable("json_error")
        self.emit(
            indentation,
            f"{error} = {self.validation_error_name}(None, {value}, {self.location_expression(location)}, {code}"
            f"{self.title_argument()}, context={context})",
        )
        self.emit(indentation, f"raise {error} from None")

    def _exact_type_condition(self, value: str, expected: type[object]) -> str:
        expected_name = self.runtime(expected.__name__, expected)
        return f"{self.runtime('type', type)}({value}) is {expected_name}"
