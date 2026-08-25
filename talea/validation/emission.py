"""Emit strict validation operations from canonical Talea schemas.

Compilation is the only point where this module traverses ``Schema`` values or
the fields of a referenced ``SpecSchema``.  One internal emitter supplies both
standalone validators and specialized Spec constructors.  Generated functions
retain neither the source schema nor the annotation that produced it, and
successful validation creates no error metadata.
"""

from collections.abc import Iterable
from decimal import Decimal
from enum import Enum
from math import isclose, isfinite, remainder
from typing import assert_never, cast

from talea.constraints import Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.declaration.models import SpecSchema, ValidationHook
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
from talea.validation.errors import CustomValidationError, ValidationError


def _identity_index(sequence: list[object] | tuple[object, ...], item: object) -> int:
    """Locate the first failing sequence member without success-path indexing."""

    for index, candidate in enumerate(sequence):
        if candidate is item:
            return index
    raise RuntimeError("validated sequence changed during validation")


class _GeneratedNames:
    """Allocate deterministic compiler-owned names within one generated unit."""

    __slots__ = ("counters", "reserved")

    def __init__(self, reserved: Iterable[str] = ()) -> None:
        self.counters: dict[str, int] = {}
        self.reserved = set(reserved)

    def allocate(self, purpose: str) -> str:
        """Return a unique identifier disjoint from user and compiler names."""

        index = self.counters.get(purpose, 0)
        while True:
            index += 1
            candidate = f"_talea_{purpose}_{index}"
            if candidate not in self.reserved:
                self.counters[purpose] = index
                self.reserved.add(candidate)
                return candidate


class _ValidationEmitter:
    """Own Schema-to-validation operation emission for every compilation target."""

    _primitive_types = {"int": int, "float": float, "str": str, "bool": bool, "bytes": bytes}
    _sequence_types = {"list": list, "set": set, "frozenset": frozenset}
    _primitive_order = {"int": 0, "float": 1, "str": 2, "bool": 3, "bytes": 4, "none": 5}

    def __init__(self, lines: list[str], names: _GeneratedNames, namespace: dict[str, object]) -> None:
        self.lines = lines
        self.names = names
        self.namespace = namespace
        self.runtime_names: dict[str, str] = {}
        self.validation_error_name = self.runtime("validation_error", ValidationError)

    def emit_schema(
        self,
        schema: Schema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit the specialized statements for one schema node."""

        if isinstance(schema, ConstrainedSchema):
            base = schema.schema
            constraints = schema.constraints
        else:
            base = schema
            constraints = ()
        if isinstance(base, PrimitiveSchema):
            self.emit_primitive(base, value, location, indentation, constraints)
            return
        if isinstance(base, TypeSchema):
            self.emit_type(base, value, location, indentation, constraints)
            return
        if isinstance(base, EnumSchema):
            self.emit_enum(base, value, location, indentation)
            return
        if isinstance(base, LiteralSchema):
            self.emit_literal(base, value, location, indentation)
            return
        if isinstance(base, SpecReferenceSchema):
            self.emit_spec_reference(base, value, location, indentation)
            return
        if isinstance(base, SequenceSchema):
            self.emit_sequence(base, value, location, indentation, constraints)
            return
        if isinstance(base, MappingSchema):
            self.emit_mapping(base, value, location, indentation, constraints)
            return
        if isinstance(base, VariadicTupleSchema):
            self.emit_variadic_tuple(base, value, location, indentation, constraints)
            return
        if isinstance(base, FixedTupleSchema):
            self.emit_fixed_tuple(base, value, location, indentation, constraints)
            return
        if isinstance(base, UnionSchema):
            self.emit_union(base, value, location, indentation)
            return
        raise AssertionError("nested constrained schema reached validation emission")

    def emit_spec_reference(
        self,
        schema: SpecReferenceSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit one nominal Spec compatibility check without walking its fields."""

        instance_check = self.runtime("isinstance", isinstance)
        expected_type = self.bind("spec_type", schema.spec_type)
        self.emit(indentation, f"if not {instance_check}({value}, {expected_type}):")
        self.emit_failure(schema, value, location, indentation + 1)
        artifacts = vars(schema.spec_type)["__talea_artifacts__"]
        declaration = cast(SpecSchema, artifacts.schema)
        if declaration.instances_are_permanently_trusted:
            return
        field_names = self.bind("spec_field_names", tuple(field.name for field in declaration.fields))
        for index, field in enumerate(declaration.fields):
            nested_value = f"{value}.{field.name}"
            self.emit_schema(
                field.schema,
                nested_value,
                (*location, f"{field_names}[{index}]"),
                indentation,
            )
            for hook in declaration.hooks:
                if hook.kind == "check" and hook.fields == (field.name,):
                    self.emit_check(
                        hook,
                        (nested_value,),
                        ((*location, f"{field_names}[{index}]"),),
                        indentation,
                    )
        for hook in declaration.hooks:
            if hook.kind == "check" and len(hook.fields) > 1:
                indices = tuple(
                    next(index for index, field in enumerate(declaration.fields) if field.name == name)
                    for name in hook.fields
                )
                self.emit_check(
                    hook,
                    tuple(f"{value}.{name}" for name in hook.fields),
                    tuple((*location, f"{field_names}[{index}]") for index in indices),
                    indentation,
                )

    def emit_transform(
        self,
        hook: ValidationHook,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit one direct inbound callback and narrow failure translation."""

        callback = self.bind("transform", hook.function)
        error = self.variable("hook_error")
        value_error = self.runtime("value_error", ValueError)
        self.emit(indentation, "try:")
        self.emit(indentation + 1, f"{value} = {callback}({value})")
        self.emit(indentation, f"except {value_error} as {error}:")
        self.emit_hook_failure("transform", hook, value, (location,), error, indentation + 1)

    def emit_check(
        self,
        hook: ValidationHook,
        values: tuple[str, ...],
        locations: tuple[tuple[str, ...], ...],
        indentation: int,
    ) -> None:
        """Emit one direct assertion callback without retaining its result."""

        callback = self.bind("check", hook.function)
        error = self.variable("hook_error")
        result = self.variable("check_result")
        value_error = self.runtime("value_error", ValueError)
        type_error = self.runtime("type_error", TypeError)
        hook_name = self.bind("hook_name", hook.name)
        arguments = ", ".join(values)
        rejected = values[0] if len(values) == 1 else f"({arguments},)"
        stage = "field_check" if len(values) == 1 else "spec_check"
        self.emit(indentation, "try:")
        self.emit(indentation + 1, f"{result} = {callback}({arguments})")
        self.emit(indentation, f"except {value_error} as {error}:")
        self.emit_hook_failure(stage, hook, rejected, locations, error, indentation + 1)
        self.emit(indentation, f"if {result} is not None:")
        self.emit(indentation + 1, f'raise {type_error}(f"validation check {{{hook_name}!r}} must return None")')

    def emit_hook_failure(
        self,
        stage: str,
        hook: ValidationHook,
        value: str,
        locations: tuple[tuple[str, ...], ...],
        error: str,
        indentation: int,
    ) -> None:
        """Emit custom failure transport only inside a callback failure path."""

        custom_error = self.runtime("custom_validation_error", CustomValidationError)
        hook_name = self.bind("hook_name", hook.name)
        location_expressions = ", ".join(self.location_expression(location) for location in locations)
        locations_expression = f"({location_expressions},)"
        self.emit(
            indentation,
            f"raise {custom_error}({stage!r}, {hook_name}, {value}, {locations_expression}) from {error}",
        )

    def emit_primitive(
        self,
        schema: PrimitiveSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        constraints: tuple[object, ...] = (),
    ) -> None:
        """Emit an exact primitive check, keeping ``bool`` distinct from ``int``."""

        if schema.kind == "none":
            condition = f"{value} is not None"
        else:
            type_name = self.runtime("type", type)
            expected_type = self.runtime(schema.kind, self._primitive_types[schema.kind])
            condition = f"{type_name}({value}) is not {expected_type}"
        self.emit(indentation, f"if {condition}:")
        self.emit_failure(schema, value, location, indentation + 1)
        self.emit_constraints(schema, constraints, value, location, indentation)

    def emit_type(
        self,
        schema: TypeSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        constraints: tuple[object, ...] = (),
    ) -> None:
        """Emit one deliberate exact or nominal standard-library check."""

        expected_type = self.bind("standard_type", schema.python_type)
        if schema.mode == "exact":
            type_name = self.runtime("type", type)
            condition = f"{type_name}({value}) is not {expected_type}"
        else:
            instance_check = self.runtime("isinstance", isinstance)
            condition = f"not {instance_check}({value}, {expected_type})"
        self.emit(indentation, f"if {condition}:")
        self.emit_failure(schema, value, location, indentation + 1)
        self.emit_constraints(schema, constraints, value, location, indentation)

    def emit_enum(
        self,
        schema: EnumSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit an exact Enum-class check, excluding underlying primitives."""

        type_name = self.runtime("type", type)
        enum_type = self.bind("enum_type", schema.enum_type)
        self.emit(indentation, f"if {type_name}({value}) is not {enum_type}:")
        self.emit_failure(schema, value, location, indentation + 1)

    def emit_literal(
        self,
        schema: LiteralSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit type-sensitive direct alternatives for one Literal contract."""

        conditions = " or ".join(
            self.literal_condition(item, value) for item in sorted(schema.values, key=self.literal_key)
        )
        self.emit(indentation, f"if not ({conditions}):")
        self.emit_failure(schema, value, location, indentation + 1, code="literal")

    def emit_sequence(
        self,
        schema: SequenceSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        constraints: tuple[object, ...] = (),
    ) -> None:
        """Emit an exact list, set, or frozenset check and its member loop."""

        type_name = self.runtime("type", type)
        sequence_type = self.runtime(schema.kind, self._sequence_types[schema.kind])
        self.emit(indentation, f"if {type_name}({value}) is not {sequence_type}:")
        self.emit_failure(schema, value, location, indentation + 1)
        self.emit_constraints(schema, constraints, value, location, indentation)
        item = self.variable("item")
        self.emit(indentation, f"for {item} in {value}:")
        if schema.kind == "list":
            segment = f"{self.identity_index()}({value}, {item})"
        else:
            segment = item
        self.emit_schema(schema.item, item, (*location, segment), indentation + 1)

    def emit_mapping(
        self,
        schema: MappingSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        constraints: tuple[object, ...] = (),
    ) -> None:
        """Emit an exact dictionary check with independently compiled keys and values."""

        type_name = self.runtime("type", type)
        dictionary_type = self.runtime("dict", dict)
        self.emit(indentation, f"if {type_name}({value}) is not {dictionary_type}:")
        self.emit_failure(schema, value, location, indentation + 1)
        self.emit_constraints(schema, constraints, value, location, indentation)
        key = self.variable("key")
        item = self.variable("item")
        self.emit(indentation, f"for {key}, {item} in {value}.items():")
        member_location = (*location, key)
        self.emit_schema(schema.key, key, member_location, indentation + 1)
        self.emit_schema(schema.value, item, member_location, indentation + 1)

    def emit_variadic_tuple(
        self,
        schema: VariadicTupleSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        constraints: tuple[object, ...] = (),
    ) -> None:
        """Emit an exact tuple check and homogeneous member loop."""

        type_name = self.runtime("type", type)
        tuple_type = self.runtime("tuple", tuple)
        self.emit(indentation, f"if {type_name}({value}) is not {tuple_type}:")
        self.emit_failure(schema, value, location, indentation + 1)
        self.emit_constraints(schema, constraints, value, location, indentation)
        item = self.variable("item")
        self.emit(indentation, f"for {item} in {value}:")
        segment = f"{self.identity_index()}({value}, {item})"
        self.emit_schema(schema.item, item, (*location, segment), indentation + 1)

    def emit_fixed_tuple(
        self,
        schema: FixedTupleSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        constraints: tuple[object, ...] = (),
    ) -> None:
        """Emit exact tuple type and length checks followed by positional checks."""

        type_name = self.runtime("type", type)
        tuple_type = self.runtime("tuple", tuple)
        length = self.runtime("len", len)
        self.emit(
            indentation,
            f"if {type_name}({value}) is not {tuple_type} or {length}({value}) != {len(schema.items)}:",
        )
        self.emit_failure(schema, value, location, indentation + 1)
        self.emit_constraints(schema, constraints, value, location, indentation)
        for index, item_schema in enumerate(schema.items):
            self.emit_schema(item_schema, f"{value}[{index}]", (*location, str(index)), indentation)

    def emit_union(
        self,
        schema: UnionSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit deterministic alternatives without treating frozenset order as priority."""

        options = sorted(schema.options, key=self.order_key)
        primitive_options = [option for option in options if isinstance(option, PrimitiveSchema)]
        if len(primitive_options) == len(options):
            conditions = " and ".join(self.primitive_failure_condition(option, value) for option in primitive_options)
            self.emit(indentation, f"if {conditions}:")
            self.emit_failure(schema, value, location, indentation + 1)
            return

        matched = self.variable("matched")
        best = self.variable("best_error")
        length = self.runtime("len", len)
        self.emit(indentation, f"{matched} = False")
        self.emit(indentation, f"{best} = None")
        for option in options:
            error = self.variable("error")
            condition = self.top_level_condition(option, value)
            self.emit(indentation, f"if not {matched} and ({condition}):")
            self.emit(indentation + 1, "try:")
            self.emit_schema(option, value, location, indentation + 2)
            self.emit(indentation + 1, f"except {self.validation_error_name} as {error}:")
            self.emit(
                indentation + 2,
                f"if {best} is None or {length}({error}.location) > {length}({best}.location):",
            )
            self.emit(indentation + 3, f"{best} = {error}")
            self.emit(indentation + 1, "else:")
            self.emit(indentation + 2, f"{matched} = True")
        self.emit(indentation, f"if not {matched}:")
        self.emit(
            indentation + 1,
            f"if {best} is None or {length}({best}.location) == {len(location)}:",
        )
        self.emit_failure(schema, value, location, indentation + 2)
        self.emit(indentation + 1, f"raise {best} from None")

    def emit_failure(
        self,
        schema: Schema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        *,
        expected: str | None = None,
        code: str = "type",
    ) -> None:
        """Emit failure construction, including lazy location expressions."""

        expected = self.describe(schema) if expected is None else expected
        location_expression = self.location_expression(location)
        self.emit(
            indentation,
            f"raise {self.validation_error_name}({expected!r}, {value}, {location_expression}, {code!r}) from None",
        )

    @staticmethod
    def location_expression(location: tuple[str, ...]) -> str:
        """Return generated source for one lazy root-relative location."""

        return f"({', '.join(location)},)" if location else "()"

    def emit_constraints(
        self,
        schema: Schema,
        constraints: tuple[object, ...],
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit normalized built-in checks with no runtime metadata loop."""

        if (
            isinstance(schema, TypeSchema)
            and schema.python_type is Decimal
            and any(isinstance(item, (Gt, Ge, Lt, Le, MultipleOf)) for item in constraints)
        ):
            first = constraints[0]
            self.emit(indentation, f"if not {value}.is_finite():")
            self.emit_failure(
                schema,
                value,
                location,
                indentation + 1,
                expected=self.constraint_description(schema, first),
                code=self.constraint_code(first),
            )
        for constraint in constraints:
            if isinstance(constraint, Gt):
                condition = f"not {value} > {self.bind('bound', constraint.value)}"
            elif isinstance(constraint, Ge):
                condition = f"not {value} >= {self.bind('bound', constraint.value)}"
            elif isinstance(constraint, Lt):
                condition = f"not {value} < {self.bind('bound', constraint.value)}"
            elif isinstance(constraint, Le):
                condition = f"not {value} <= {self.bind('bound', constraint.value)}"
            elif isinstance(constraint, MultipleOf):
                condition = self.multiple_of_failure(schema, constraint, value, indentation)
            elif isinstance(constraint, MinLength):
                length = self.runtime("len", len)
                condition = f"{length}({value}) < {constraint.value}"
            elif isinstance(constraint, MaxLength):
                length = self.runtime("len", len)
                condition = f"{length}({value}) > {constraint.value}"
            elif isinstance(constraint, Pattern):
                pattern = self.bind("pattern", constraint.compiled)
                condition = f"{pattern}.search({value}) is None"
            else:
                raise AssertionError("unsupported canonical constraint")
            self.emit(indentation, f"if {condition}:")
            self.emit_failure(
                schema,
                value,
                location,
                indentation + 1,
                expected=self.constraint_description(schema, constraint),
                code=self.constraint_code(constraint),
            )

    def multiple_of_failure(
        self,
        schema: Schema,
        constraint: MultipleOf,
        value: str,
        indentation: int,
    ) -> str:
        """Return family-specific direct source for a failed multiple constraint."""

        divisor = constraint.value
        if isinstance(schema, PrimitiveSchema) and schema.kind == "int":
            return f"{value} % {self.bind('multiple', divisor)} != 0"
        if isinstance(schema, PrimitiveSchema) and schema.kind == "float":
            finite = self.runtime("isfinite", isfinite)
            close = self.runtime("isclose", isclose)
            floating_remainder = self.runtime("remainder", remainder)
            absolute = self.runtime("abs", abs)
            bound = self.bind("multiple", divisor)
            return (
                f"not {finite}({value}) or not {close}({floating_remainder}({value}, {bound}), 0.0, "
                f"rel_tol=0.0, abs_tol={absolute}({bound}) * 1e-12)"
            )
        if isinstance(schema, TypeSchema) and schema.python_type is Decimal:
            numerator, denominator = divisor.as_integer_ratio()
            ratio = self.variable("decimal_ratio")
            self.emit(indentation, f"{ratio} = {value}.as_integer_ratio()")
            return f"({ratio}[0] * {denominator}) % ({ratio}[1] * {numerator}) != 0"
        raise AssertionError("unsupported canonical MultipleOf schema")

    def primitive_failure_condition(self, schema: PrimitiveSchema, value: str) -> str:
        """Return source for one failed exact primitive alternative."""

        if schema.kind == "none":
            return f"{value} is not None"
        type_name = self.runtime("type", type)
        expected_type = self.runtime(schema.kind, self._primitive_types[schema.kind])
        return f"{type_name}({value}) is not {expected_type}"

    def literal_condition(self, item: LiteralValue, value: str) -> str:
        """Return a safe type-sensitive expression for one bound Literal value."""

        if item.value is None:
            return f"{value} is None"
        type_name = self.runtime("type", type)
        literal_type = self.bind("literal_type", item.python_type)
        literal_value = self.bind("literal_value", item.value)
        return f"{type_name}({value}) is {literal_type} and {value} == {literal_value}"

    @staticmethod
    def literal_key(item: LiteralValue) -> tuple[str, str, str]:
        """Define deterministic Literal order without consulting arbitrary metadata."""

        value_name = item.value.name if isinstance(item.value, Enum) else repr(item.value)
        return item.python_type.__module__, item.python_type.__qualname__, value_name

    def top_level_condition(self, schema: Schema, value: str) -> str:
        """Return source that selects structurally plausible union alternatives."""

        if isinstance(schema, ConstrainedSchema):
            return self.top_level_condition(schema.schema, value)
        if isinstance(schema, PrimitiveSchema):
            if schema.kind == "none":
                return f"{value} is None"
            type_name = self.runtime("type", type)
            expected_type = self.runtime(schema.kind, self._primitive_types[schema.kind])
            return f"{type_name}({value}) is {expected_type}"
        if isinstance(schema, TypeSchema):
            expected_type = self.bind("standard_type", schema.python_type)
            if schema.mode == "exact":
                type_name = self.runtime("type", type)
                return f"{type_name}({value}) is {expected_type}"
            instance_check = self.runtime("isinstance", isinstance)
            return f"{instance_check}({value}, {expected_type})"
        if isinstance(schema, EnumSchema):
            type_name = self.runtime("type", type)
            enum_type = self.bind("enum_type", schema.enum_type)
            return f"{type_name}({value}) is {enum_type}"
        if isinstance(schema, LiteralSchema):
            return " or ".join(
                self.literal_condition(item, value) for item in sorted(schema.values, key=self.literal_key)
            )
        if isinstance(schema, SpecReferenceSchema):
            instance_check = self.runtime("isinstance", isinstance)
            expected_type = self.bind("spec_type", schema.spec_type)
            return f"{instance_check}({value}, {expected_type})"
        if isinstance(schema, SequenceSchema):
            type_name = self.runtime("type", type)
            sequence_type = self.runtime(schema.kind, self._sequence_types[schema.kind])
            return f"{type_name}({value}) is {sequence_type}"
        if isinstance(schema, MappingSchema):
            type_name = self.runtime("type", type)
            dictionary_type = self.runtime("dict", dict)
            return f"{type_name}({value}) is {dictionary_type}"
        if isinstance(schema, (VariadicTupleSchema, FixedTupleSchema)):
            type_name = self.runtime("type", type)
            tuple_type = self.runtime("tuple", tuple)
            return f"{type_name}({value}) is {tuple_type}"
        if isinstance(schema, UnionSchema):
            conditions = (
                self.top_level_condition(option, value) for option in sorted(schema.options, key=self.order_key)
            )
            return " or ".join(conditions)
        assert_never(schema)

    def describe(self, schema: Schema) -> str:
        """Project canonical structure into deterministic expected-type text."""

        if isinstance(schema, PrimitiveSchema):
            return "None" if schema.kind == "none" else schema.kind
        if isinstance(schema, TypeSchema):
            return schema.python_type.__qualname__
        if isinstance(schema, EnumSchema):
            return schema.enum_type.__qualname__
        if isinstance(schema, LiteralSchema):
            values = sorted(schema.values, key=self.literal_key)
            return f"Literal[{', '.join(self.literal_description(item) for item in values)}]"
        if isinstance(schema, ConstrainedSchema):
            descriptions = ", ".join(self.constraint_label(item) for item in schema.constraints)
            return f"Annotated[{self.describe(schema.schema)}, {descriptions}]"
        if isinstance(schema, SpecReferenceSchema):
            return schema.spec_type.__qualname__
        if isinstance(schema, SequenceSchema):
            return f"{schema.kind}[{self.describe(schema.item)}]"
        if isinstance(schema, MappingSchema):
            return f"dict[{self.describe(schema.key)}, {self.describe(schema.value)}]"
        if isinstance(schema, VariadicTupleSchema):
            return f"tuple[{self.describe(schema.item)}, ...]"
        if isinstance(schema, FixedTupleSchema):
            return f"tuple[{', '.join(self.describe(item) for item in schema.items)}]"
        if isinstance(schema, UnionSchema):
            options = sorted(schema.options, key=self.order_key)
            return " | ".join(self.describe(option) for option in options)
        assert_never(schema)

    @staticmethod
    def literal_description(item: LiteralValue) -> str:
        """Render one canonical Literal alternative for error descriptions."""

        if isinstance(item.value, Enum):
            return f"{item.python_type.__qualname__}.{item.value.name}"
        return repr(item.value)

    def constraint_description(self, schema: Schema, constraint: object) -> str:
        """Describe the single failed constraint without changing base type truth."""

        return f"{self.describe(schema)} satisfying {self.constraint_label(constraint)}"

    @staticmethod
    def constraint_label(constraint: object) -> str:
        """Return stable declaration-like text for one canonical constraint."""

        if isinstance(constraint, Pattern):
            return f"Pattern({constraint.pattern!r})"
        if isinstance(constraint, (Gt, Ge, Lt, Le, MultipleOf, MinLength, MaxLength)):
            return f"{type(constraint).__name__}({constraint.value!r})"
        raise AssertionError("unsupported canonical constraint")

    @staticmethod
    def constraint_code(constraint: object) -> str:
        """Return the stable failure category owned by one constraint type."""

        if isinstance(constraint, Gt):
            return "greater_than"
        if isinstance(constraint, Ge):
            return "greater_than_or_equal"
        if isinstance(constraint, Lt):
            return "less_than"
        if isinstance(constraint, Le):
            return "less_than_or_equal"
        if isinstance(constraint, MultipleOf):
            return "multiple_of"
        if isinstance(constraint, MinLength):
            return "min_length"
        if isinstance(constraint, MaxLength):
            return "max_length"
        if isinstance(constraint, Pattern):
            return "pattern"
        raise AssertionError("unsupported canonical constraint")

    def order_key(self, schema: Schema) -> tuple[int, str]:
        """Define compiler-owned union execution order independently of schema equality."""

        if isinstance(schema, PrimitiveSchema):
            return self._primitive_order[schema.kind], schema.kind
        if isinstance(schema, ConstrainedSchema):
            base_order, _ = self.order_key(schema.schema)
            return base_order, self.describe(schema)
        if isinstance(schema, (TypeSchema, EnumSchema, LiteralSchema)):
            return 6, self.describe(schema)
        if isinstance(schema, SpecReferenceSchema):
            return 6, f"{schema.spec_type.__module__}.{schema.spec_type.__qualname__}"
        return 10, self.describe(schema)

    def variable(self, purpose: str) -> str:
        """Return a unique generated local name."""

        return self.names.allocate(purpose)

    def identity_index(self) -> str:
        """Bind the sequence failure locator only for schemas that need it."""

        return self.runtime("identity_index", _identity_index)

    def runtime(self, purpose: str, value: object) -> str:
        """Return one stable compiler-owned binding for a runtime operation."""

        name = self.runtime_names.get(purpose)
        if name is None:
            name = self.bind(purpose, value)
            self.runtime_names[purpose] = name
        return name

    def bind(self, purpose: str, value: object) -> str:
        """Retain one runtime object behind a compiler-controlled global name."""

        name = self.names.allocate(purpose)
        self.namespace[name] = value
        return name

    def emit(self, indentation: int, statement: str) -> None:
        """Append one generated statement at the requested indentation."""

        self.lines.append(f"{'    ' * indentation}{statement}")
