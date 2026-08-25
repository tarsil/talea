"""Compile canonical Talea schemas into strict Python validators.

Compilation is the only point where this module traverses ``Schema`` values.
The returned function contains inlined type checks and container loops; it
retains neither the source schema nor the annotation that produced it.  A
successful call returns its input unchanged and creates no error metadata.
"""

from collections.abc import Callable
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


class _ValidatorCompiler:
    """Emit one self-contained validator from canonical schema structure."""

    _primitive_types = {
        "int": "int",
        "float": "float",
        "str": "str",
        "bool": "bool",
        "bytes": "bytes",
    }
    _sequence_types = {"list": "list", "set": "set", "frozenset": "frozenset"}
    _primitive_order = {"int": 0, "float": 1, "str": 2, "bool": 3, "bytes": 4, "none": 5}

    def __init__(self) -> None:
        self.lines = ["def validate(value):"]
        self.counter = 0

    def compile(self, schema: Schema) -> Validator:
        """Compile ``schema`` and return its single-argument validation function."""

        self.emit_schema(schema, "value", (), 1)
        self.emit(1, "return value")
        source = "\n".join(self.lines)
        namespace: dict[str, object] = {
            "ValidationError": ValidationError,
            "_identity_index": _identity_index,
            "__name__": __name__,
        }
        exec(compile(source, "<talea validator>", "exec"), namespace)
        validator = cast(Validator, namespace["validate"])
        validator.__doc__ = "Validate one existing Python value strictly and return that same object on success."
        return validator

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
            condition = f"type({value}) is not {self._primitive_types[schema.kind]}"
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

        sequence_type = self._sequence_types[schema.kind]
        self.emit(indentation, f"if type({value}) is not {sequence_type}:")
        self.emit_failure(schema, value, location, indentation + 1)
        item = self.variable("item")
        self.emit(indentation, f"for {item} in {value}:")
        if schema.kind == "list":
            segment = f"_identity_index({value}, {item})"
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

        self.emit(indentation, f"if type({value}) is not dict:")
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

        self.emit(indentation, f"if type({value}) is not tuple:")
        self.emit_failure(schema, value, location, indentation + 1)
        item = self.variable("item")
        self.emit(indentation, f"for {item} in {value}:")
        segment = f"_identity_index({value}, {item})"
        self.emit_schema(schema.item, item, (*location, segment), indentation + 1)

    def emit_fixed_tuple(
        self,
        schema: FixedTupleSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit exact tuple type and length checks followed by positional checks."""

        self.emit(
            indentation,
            f"if type({value}) is not tuple or len({value}) != {len(schema.items)}:",
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
        self.emit(indentation, f"{matched} = False")
        self.emit(indentation, f"{best} = None")
        for option in options:
            error = self.variable("error")
            condition = self.top_level_condition(option, value)
            self.emit(indentation, f"if not {matched} and ({condition}):")
            self.emit(indentation + 1, "try:")
            self.emit_schema(option, value, location, indentation + 2)
            self.emit(indentation + 1, f"except ValidationError as {error}:")
            self.emit(
                indentation + 2,
                f"if {best} is None or len({error}.location) > len({best}.location):",
            )
            self.emit(indentation + 3, f"{best} = {error}")
            self.emit(indentation + 1, "else:")
            self.emit(indentation + 2, f"{matched} = True")
        self.emit(indentation, f"if not {matched}:")
        self.emit(
            indentation + 1,
            f"if {best} is None or len({best}.location) == {len(location)}:",
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
            f"raise ValidationError({expected!r}, {value}, {location_expression}) from None",
        )

    def primitive_failure_condition(self, schema: PrimitiveSchema, value: str) -> str:
        """Return source for one failed exact primitive alternative."""

        if schema.kind == "none":
            return f"{value} is not None"
        return f"type({value}) is not {self._primitive_types[schema.kind]}"

    def top_level_condition(self, schema: Schema, value: str) -> str:
        """Return source that selects structurally plausible union alternatives."""

        if isinstance(schema, PrimitiveSchema):
            if schema.kind == "none":
                return f"{value} is None"
            return f"type({value}) is {self._primitive_types[schema.kind]}"
        if isinstance(schema, SequenceSchema):
            return f"type({value}) is {self._sequence_types[schema.kind]}"
        if isinstance(schema, MappingSchema):
            return f"type({value}) is dict"
        if isinstance(schema, (VariadicTupleSchema, FixedTupleSchema)):
            return f"type({value}) is tuple"
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

        self.counter += 1
        return f"_{purpose}_{self.counter}"

    def emit(self, indentation: int, statement: str) -> None:
        """Append one generated statement at the requested indentation."""

        self.lines.append(f"{'    ' * indentation}{statement}")


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
