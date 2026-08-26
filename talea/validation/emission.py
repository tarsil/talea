"""Emit strict validation operations from canonical Talea schemas.

Compilation is the only point where this module traverses ``Schema`` values or
the fields of a referenced ``SpecSchema``.  One internal emitter supplies both
standalone validators and specialized Spec constructors.  Generated functions
retain neither the source schema nor the annotation that produced it, and
successful validation creates no error metadata.
"""

from contextvars import ContextVar
from decimal import Decimal
from math import isclose, isfinite, remainder
from typing import assert_never, cast

from talea.codegen import _GeneratedNames
from talea.constraints import Ge, Gt, Le, Lt, MaxLength, MinLength, MultipleOf, Pattern
from talea.declaration.models import SpecSchema, ValidationHook
from talea.errors import ErrorCode
from talea.errors.models import CustomValidationError, ValidationError
from talea.errors.safety import REDACTED
from talea.schema.nodes import (
    AliasSchema,
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
    TypedDictSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
)
from talea.validation.failure_contracts import (
    constraint_code,
    constraint_context,
    constraint_description,
    describe_schema,
    literal_key,
    schema_order_key,
)


def _identity_index(sequence: list[object] | tuple[object, ...], item: object) -> int:
    """Locate the first failing sequence member without success-path indexing."""

    for index, candidate in enumerate(sequence):
        if candidate is item:
            return index
    raise RuntimeError("validated sequence changed during validation")


_RECURSIVE_VALIDATION: ContextVar[set[int] | None] = ContextVar(
    "talea_recursive_validation",
    default=None,
)


class _RecursiveSpecValidator:
    """Call a finalized current-state artifact across one recursive graph edge."""

    __slots__ = ("spec_type",)

    def __init__(self, spec_type: type[object]) -> None:
        self.spec_type = spec_type

    def __call__(self, value: object) -> object:
        active = _RECURSIVE_VALIDATION.get()
        token = None
        if active is None:
            active = set()
            token = _RECURSIVE_VALIDATION.set(active)
        identity = id(value)
        if identity in active:
            return value
        active.add(identity)
        try:
            artifacts = vars(self.spec_type)["__talea_artifacts__"]
            validator = artifacts.current_validator
            assert validator is not None
            return validator(value)
        finally:
            active.remove(identity)
            if token is not None:
                _RECURSIVE_VALIDATION.reset(token)


class _ValidationEmitter:
    """Own Schema-to-validation operation emission for every compilation target."""

    _primitive_types = {"int": int, "float": float, "str": str, "bool": bool, "bytes": bytes}
    _sequence_types = {"list": list, "set": set, "frozenset": frozenset}

    def __init__(
        self,
        lines: list[str],
        names: _GeneratedNames,
        namespace: dict[str, object],
        *,
        title: str | None = None,
        trusted_instances: str | None = None,
    ) -> None:
        self.lines = lines
        self.names = names
        self.namespace = namespace
        self.runtime_names: dict[str, str] = {}
        self.validation_error_name = self.runtime("validation_error", ValidationError)
        self.title_name = self.bind("error_title", title) if title is not None else None
        self.trusted_instances = trusted_instances
        self.sensitive = False

    def emit_schema(
        self,
        schema: Schema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        *,
        sensitive: bool | None = None,
    ) -> None:
        """Emit the specialized statements for one schema node."""

        if not sensitive or self.sensitive:
            self._emit_schema(schema, value, location, indentation)
            return
        self.sensitive = True
        try:
            self._emit_schema(schema, value, location, indentation)
        finally:
            self.sensitive = False

    def _emit_schema(
        self,
        schema: Schema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Dispatch one schema while inheriting compile-time sensitivity."""

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
        if isinstance(base, AliasSchema):
            self.emit_schema(
                base.schema,
                value,
                location,
                indentation,
                sensitive=bool(base.metadata.sensitive),
            )
            self.emit_constraints(base, constraints, value, location, indentation)
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
        if isinstance(base, TypedDictSchema):
            self.emit_typed_dict(base, value, location, indentation)
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
        target_namespace = vars(schema.spec_type)
        artifacts = target_namespace.get("__talea_artifacts__")
        target_identity = target_namespace["__talea_declaration__"]
        if artifacts is None and target_identity.prepared_fields is None and not target_identity.finalizing:
            artifacts = target_identity.artifacts()
        if artifacts is None or target_identity.is_recursive():
            if target_identity.values_are_immutable(frozenset({schema.spec_type})):
                return
            validator = self.bind("recursive_validator", _RecursiveSpecValidator(schema.spec_type))
            error = self.variable("recursive_error")
            prefixed = self.variable("prefixed_error")
            self.emit(indentation, "try:")
            self.emit(indentation + 1, f"{validator}({value})")
            self.emit(indentation, f"except {self.validation_error_name} as {error}:")
            self.emit(
                indentation + 1,
                f"{prefixed} = {error}.prefixed({self.location_expression(location)}"
                f"{self.title_argument()}{self.sensitive_argument()})",
            )
            self.emit(indentation + 1, f"raise {prefixed} from {prefixed}.__cause__")
            return
        declaration = cast(SpecSchema, artifacts.schema)
        if declaration.instances_are_permanently_trusted:
            return
        if self.trusted_instances is not None:
            identity = self.runtime("id", id)
            self.emit(
                indentation,
                f"if {self.trusted_instances} is None or {identity}({value}) not in {self.trusted_instances}:",
            )
            indentation += 1
        field_names = self.bind("spec_field_names", tuple(field.name for field in declaration.fields))
        for index, field in enumerate(declaration.fields):
            nested_value = f"{value}.{field.name}"
            self.emit_schema(
                field.schema,
                nested_value,
                (*location, f"{field_names}[{index}]"),
                indentation,
                sensitive=bool(field.metadata.sensitive),
            )
            for hook in declaration.hooks:
                if hook.kind == "check" and hook.fields == (field.name,):
                    self.emit_check(
                        hook,
                        (nested_value,),
                        ((*location, f"{field_names}[{index}]"),),
                        indentation,
                        sensitive=bool(field.metadata.sensitive),
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
                    sensitive=any(bool(declaration.fields[index].metadata.sensitive) for index in indices),
                )

    def emit_transform(
        self,
        hook: ValidationHook,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        *,
        sensitive: bool = False,
    ) -> None:
        """Emit one direct inbound callback and narrow failure translation."""

        callback = self.bind("transform", hook.function)
        error = self.variable("hook_error")
        value_error = self.runtime("value_error", ValueError)
        self.emit(indentation, "try:")
        self.emit(indentation + 1, f"{value} = {callback}({value})")
        self.emit(indentation, f"except {value_error} as {error}:")
        self.emit_hook_failure("transform", hook, value, (location,), error, indentation + 1, sensitive)

    def emit_check(
        self,
        hook: ValidationHook,
        values: tuple[str, ...],
        locations: tuple[tuple[str, ...], ...],
        indentation: int,
        *,
        sensitive: bool = False,
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
        self.emit_hook_failure(stage, hook, rejected, locations, error, indentation + 1, sensitive)
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
        sensitive: bool,
    ) -> None:
        """Emit custom failure transport only inside a callback failure path."""

        custom_error = self.runtime("custom_validation_error", CustomValidationError)
        hook_name = self.bind("hook_name", hook.name)
        location_expressions = ", ".join(self.location_expression(location) for location in locations)
        locations_expression = f"({location_expressions},)"
        self.emit(
            indentation,
            f"raise {custom_error}({stage!r}, {hook_name}, {value}, {locations_expression}"
            f"{self.title_argument()}{', sensitive=True' if sensitive else ''}) "
            f"from {'None' if sensitive else error}",
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

        conditions = " or ".join(self.literal_condition(item, value) for item in sorted(schema.values, key=literal_key))
        self.emit(indentation, f"if not ({conditions}):")
        self.emit_failure(schema, value, location, indentation + 1, code=ErrorCode.LITERAL)

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
            segment = self.sensitive_location_segment(item)
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
        member_location = (*location, self.sensitive_location_segment(key))
        self.emit_schema(schema.key, key, member_location, indentation + 1)
        self.emit_schema(schema.value, item, member_location, indentation + 1)

    def emit_typed_dict(
        self,
        schema: TypedDictSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit one closed exact-dict contract with required-key semantics."""

        type_name = self.runtime("type", type)
        dictionary_type = self.runtime("dict", dict)
        self.emit(indentation, f"if {type_name}({value}) is not {dictionary_type}:")
        self.emit_failure(schema, value, location, indentation + 1)
        names = self.bind("typed_dict_names", tuple(field.name for field in schema.fields))
        known = self.bind("typed_dict_known", frozenset(field.name for field in schema.fields))
        title = self.title_name or self.bind("typed_dict_title", schema.name)
        for index, field in enumerate(schema.fields):
            member_location = (*location, f"{names}[{index}]")
            if field.required:
                self.emit(indentation, f"if {names}[{index}] not in {value}:")
                missing = self.variable("missing_error")
                self.emit(
                    indentation + 1,
                    f"{missing} = {self.validation_error_name}._missing("
                    f"{self.location_expression(member_location)}, title={title})",
                )
                self.emit(indentation + 1, f"raise {missing} from None")
            self.emit(indentation, f"if {names}[{index}] in {value}:")
            self.emit_schema(
                field.schema,
                f"{value}[{names}[{index}]]",
                member_location,
                indentation + 1,
                sensitive=bool(field.metadata.sensitive),
            )
        key = self.variable("typed_dict_key")
        item = self.variable("typed_dict_item")
        code = self.runtime("error_code_unexpected", ErrorCode.UNEXPECTED)
        self.emit(indentation, f"for {key}, {item} in {value}.items():")
        self.emit(indentation + 1, f"if {key} not in {known}:")
        unexpected_segment = self.sensitive_location_segment(key)
        self.emit(
            indentation + 2,
            f"raise {self.validation_error_name}(None, {item}, "
            f"{self.location_expression((*location, unexpected_segment))}, {code}, title={title}"
            f"{self.sensitive_argument()}) from None",
        )

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

        options = sorted(schema.options, key=schema_order_key)
        primitive_options = [option for option in options if isinstance(option, PrimitiveSchema)]
        if len(primitive_options) == len(options):
            conditions = " and ".join(self.primitive_failure_condition(option, value) for option in primitive_options)
            self.emit(indentation, f"if {conditions}:")
            self.emit_union_failure(schema, options, value, location, None, indentation + 1)
            return

        matched = self.variable("matched")
        best = self.variable("best_error")
        best_label = self.variable("best_label")
        list_type = self.runtime("list", list)
        self.emit(indentation, f"{matched} = False")
        self.emit(indentation, f"{best} = None")
        for option in options:
            error = self.variable("error")
            label = self.bind("union_label", describe_schema(option))
            condition = self.top_level_condition(option, value)
            self.emit(indentation, f"if not {matched} and ({condition}):")
            self.emit(indentation + 1, "try:")
            self.emit_schema(option, value, location, indentation + 2)
            self.emit(indentation + 1, f"except {self.validation_error_name} as {error}:")
            self.emit(indentation + 2, f"if {best} is None:")
            self.emit(indentation + 3, f"{best} = {error}")
            self.emit(indentation + 3, f"{best_label} = {label}")
            self.emit(indentation + 2, f"elif {self.runtime('type', type)}({best}) is {list_type}:")
            self.emit(indentation + 3, f"{best}.append(({label}, {error}))")
            self.emit(indentation + 2, "else:")
            self.emit(indentation + 3, f"{best} = [({best_label}, {best}), ({label}, {error})]")
            self.emit(indentation + 1, "else:")
            self.emit(indentation + 2, f"{matched} = True")
        self.emit(indentation, f"if not {matched}:")
        self.emit_union_failure(schema, options, value, location, (best, best_label), indentation + 1)

    def emit_union_failure(
        self,
        schema: UnionSchema,
        options: list[Schema],
        value: str,
        location: tuple[str, ...],
        captured: tuple[str, str] | None,
        indentation: int,
    ) -> None:
        """Emit outer union detail and branch diagnostics only after all options fail."""

        alternatives = self.bind("union_alternatives", tuple(describe_schema(option) for option in options))
        expected = self.bind("union_expected", describe_schema(schema))
        location_expression = self.location_expression(location)
        union_error = self.variable("union_error")
        if captured is None:
            failures = "()"
        else:
            best, best_label = captured
            failures_name = self.variable("union_failures")
            list_type = self.runtime("list", list)
            tuple_type = self.runtime("tuple", tuple)
            self.emit(indentation, f"if {self.runtime('type', type)}({best}) is {list_type}:")
            self.emit(indentation + 1, f"{failures_name} = {tuple_type}({best})")
            self.emit(indentation, "else:")
            self.emit(indentation + 1, f"{failures_name} = (({best_label}, {best}),) if {best} is not None else ()")
            failures = failures_name
        self.emit(
            indentation,
            f"{union_error} = {self.validation_error_name}.union("
            f"{expected}, {value}, {location_expression}, {alternatives}, {failures}{self.title_argument()}"
            f"{self.sensitive_argument()})",
        )
        self.emit(indentation, f"raise {union_error} from {union_error}.__cause__")

    def emit_failure(
        self,
        schema: Schema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        *,
        expected: str | None = None,
        code: ErrorCode = ErrorCode.TYPE,
        context: tuple[tuple[str, object], ...] = (),
    ) -> None:
        """Emit failure construction, including lazy location expressions."""

        expected = describe_schema(schema) if expected is None else expected
        location_expression = self.location_expression(location)
        code_name = self.runtime(f"error_code_{code.value}", code)
        context_argument = ""
        if context:
            context_name = self.bind("error_context", context)
            context_argument = f", context={context_name}"
        self.emit(
            indentation,
            f"raise {self.validation_error_name}({expected!r}, {value}, {location_expression}, {code_name}"
            f"{self.title_argument()}{context_argument}{self.sensitive_argument()}) from None",
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
                expected=constraint_description(schema, first),
                code=constraint_code(first),
                context=constraint_context(first),
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
                expected=constraint_description(schema, constraint),
                code=constraint_code(constraint),
                context=constraint_context(constraint),
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

    def top_level_condition(self, schema: Schema, value: str) -> str:
        """Return source that selects structurally plausible union alternatives."""

        if isinstance(schema, ConstrainedSchema):
            return self.top_level_condition(schema.schema, value)
        if isinstance(schema, AliasSchema):
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
            return " or ".join(self.literal_condition(item, value) for item in sorted(schema.values, key=literal_key))
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
        if isinstance(schema, TypedDictSchema):
            type_name = self.runtime("type", type)
            dictionary_type = self.runtime("dict", dict)
            return f"{type_name}({value}) is {dictionary_type}"
        if isinstance(schema, (VariadicTupleSchema, FixedTupleSchema)):
            type_name = self.runtime("type", type)
            tuple_type = self.runtime("tuple", tuple)
            return f"{type_name}({value}) is {tuple_type}"
        if isinstance(schema, UnionSchema):
            conditions = (
                self.top_level_condition(option, value) for option in sorted(schema.options, key=schema_order_key)
            )
            return " or ".join(conditions)
        assert_never(schema)

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

    def title_argument(self) -> str:
        """Return a generated keyword argument only for Spec-bound errors."""

        return f", title={self.title_name}" if self.title_name is not None else ""

    def sensitive_argument(self) -> str:
        """Return a failure-only keyword for sensitive compiled paths."""

        return ", sensitive=True" if self.sensitive else ""

    def sensitive_location_segment(self, expression: str) -> str:
        """Replace value-derived location members on sensitive failure paths."""

        return self.bind("redacted_location", REDACTED) if self.sensitive else expression

    def emit(self, indentation: int, statement: str) -> None:
        """Append one generated statement at the requested indentation."""

        self.lines.append(f"{'    ' * indentation}{statement}")
