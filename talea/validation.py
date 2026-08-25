"""Emit strict validation operations from canonical Talea schemas.

Compilation is the only point where this module traverses ``Schema`` values.
One internal emitter supplies both standalone validators and specialized Spec
constructors.  Generated functions retain neither the source schema nor the
annotation that produced it, and successful validation creates no error
metadata.
"""

from collections.abc import Callable, Iterable
from typing import assert_never, cast

from talea.schema import (
    FixedTupleSchema,
    MappingSchema,
    PrimitiveSchema,
    Schema,
    SequenceSchema,
    UnionSchema,
    VariadicTupleSchema,
)

__all__ = ["ValidationError", "Validator", "compile_validator"]

type Location = tuple[object, ...]
type Validator = Callable[[object], object]


class ValidationError(TypeError):
    """Describe one strict validation failure.

    Attributes:
        expected: Deterministic Python-like text for the required structure.
        value: The exact rejected object.  It is retained only on failure.
        location: A root-relative path.  List and tuple positions are integers,
            mapping positions are original keys, and set positions are the
            rejected members themselves.

    The exception is constructed only after a check fails.  Its string form
    includes the path, expected structure, received type, and received value.
    """

    def __init__(self, expected: str, value: object, location: Location) -> None:
        self.expected = expected
        self.value = value
        self.location = location
        super().__init__()

    @property
    def received_type(self) -> type[object]:
        """Return the concrete type of the rejected value."""

        return type(self.value)

    def __str__(self) -> str:
        """Render a stable description without precomputing it on failure."""

        location = "".join(f"[{segment!r}]" for segment in self.location) or "<root>"
        return (
            f"Validation failed at {location}: expected {self.expected}, "
            f"received {self.received_type.__name__} ({self.value!r})"
        )


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

        if isinstance(schema, PrimitiveSchema):
            self.emit_primitive(schema, value, location, indentation)
            return
        if isinstance(schema, SequenceSchema):
            self.emit_sequence(schema, value, location, indentation)
            return
        if isinstance(schema, MappingSchema):
            self.emit_mapping(schema, value, location, indentation)
            return
        if isinstance(schema, VariadicTupleSchema):
            self.emit_variadic_tuple(schema, value, location, indentation)
            return
        if isinstance(schema, FixedTupleSchema):
            self.emit_fixed_tuple(schema, value, location, indentation)
            return
        if isinstance(schema, UnionSchema):
            self.emit_union(schema, value, location, indentation)
            return
        assert_never(schema)

    def emit_primitive(
        self,
        schema: PrimitiveSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
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

    def emit_sequence(
        self,
        schema: SequenceSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit an exact list, set, or frozenset check and its member loop."""

        type_name = self.runtime("type", type)
        sequence_type = self.runtime(schema.kind, self._sequence_types[schema.kind])
        self.emit(indentation, f"if {type_name}({value}) is not {sequence_type}:")
        self.emit_failure(schema, value, location, indentation + 1)
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
    ) -> None:
        """Emit an exact dictionary check with independently compiled keys and values."""

        type_name = self.runtime("type", type)
        dictionary_type = self.runtime("dict", dict)
        self.emit(indentation, f"if {type_name}({value}) is not {dictionary_type}:")
        self.emit_failure(schema, value, location, indentation + 1)
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
    ) -> None:
        """Emit an exact tuple check and homogeneous member loop."""

        type_name = self.runtime("type", type)
        tuple_type = self.runtime("tuple", tuple)
        self.emit(indentation, f"if {type_name}({value}) is not {tuple_type}:")
        self.emit_failure(schema, value, location, indentation + 1)
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
    ) -> None:
        """Emit failure construction, including lazy location expressions."""

        expected = self.describe(schema)
        location_expression = f"({', '.join(location)},)" if location else "()"
        self.emit(
            indentation,
            f"raise {self.validation_error_name}({expected!r}, {value}, {location_expression}) from None",
        )

    def primitive_failure_condition(self, schema: PrimitiveSchema, value: str) -> str:
        """Return source for one failed exact primitive alternative."""

        if schema.kind == "none":
            return f"{value} is not None"
        type_name = self.runtime("type", type)
        expected_type = self.runtime(schema.kind, self._primitive_types[schema.kind])
        return f"{type_name}({value}) is not {expected_type}"

    def top_level_condition(self, schema: Schema, value: str) -> str:
        """Return source that selects structurally plausible union alternatives."""

        if isinstance(schema, PrimitiveSchema):
            if schema.kind == "none":
                return f"{value} is None"
            type_name = self.runtime("type", type)
            expected_type = self.runtime(schema.kind, self._primitive_types[schema.kind])
            return f"{type_name}({value}) is {expected_type}"
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

    def describe(self, schema: Schema) -> str:
        """Project canonical structure into deterministic expected-type text."""

        if isinstance(schema, PrimitiveSchema):
            return "None" if schema.kind == "none" else schema.kind
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

    def order_key(self, schema: Schema) -> tuple[int, str]:
        """Define compiler-owned union execution order independently of schema equality."""

        if isinstance(schema, PrimitiveSchema):
            return self._primitive_order[schema.kind], schema.kind
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


class _ValidatorCompiler:
    """Wrap shared validation emission in a standalone one-argument function."""

    def compile(self, schema: Schema) -> Validator:
        """Compile ``schema`` and return its single-argument validation function."""

        lines = ["def validate(value):"]
        namespace: dict[str, object] = {"__name__": __name__}
        emitter = _ValidationEmitter(lines, _GeneratedNames(("value",)), namespace)
        emitter.emit_schema(schema, "value", (), 1)
        emitter.emit(1, "return value")
        source = "\n".join(lines)
        exec(compile(source, "<talea validator>", "exec"), namespace)
        validator = cast(Validator, namespace["validate"])
        validator.__doc__ = "Validate one existing Python value strictly and return that same object on success."
        return validator


def compile_validator(schema: Schema) -> Validator:
    """Compile canonical schema into a strict, reusable validator.

    Args:
        schema: A supported immutable Talea schema.  Original annotations are
            neither accepted nor retained.

    Returns:
        A one-argument callable.  It returns the identical input object after
        successful strict validation and raises ``ValidationError`` otherwise.

    Runtime validation performs exact built-in type checks, executes inlined
    child validation, and creates paths and errors only after a failure.  Each
    call to this function compiles independently; lifecycle-level caching
    belongs to the future declaration owner rather than this compiler.
    """

    return _ValidatorCompiler().compile(schema)
