"""Compare Campaign 6 strict type and constraint paths with equivalent Python."""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from functools import partial
from ipaddress import IPv4Address
from re import compile as compile_pattern
from statistics import median
from timeit import Timer
from typing import Annotated, Literal, cast
from uuid import UUID

from talea import Ge, Le, MaxLength, MinLength, Pattern, Spec
from talea.schema import resolve_annotation
from talea.validation import ValidationError, compile_validator

_REPEATS = 7
_EXECUTION_ITERATIONS = 100_000
_FAILURE_ITERATIONS = 20_000
_COMPILATION_ITERATIONS = 2_000

type Operation = Callable[[], object]


class Status(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class Measurement:
    """Retain minimum and median nanoseconds for one benchmark operation."""

    minimum: float
    median: float


def measure(operation: Operation, iterations: int) -> Measurement:
    """Measure one warmed operation over repeated fixed-size samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def print_measurement(case: str, implementation: str, result: Measurement) -> None:
    """Print one stable benchmark row."""

    print(f"{case:42} {implementation:12} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def swallowed_failure(operation: Operation, error_type: type[BaseException]) -> Operation:
    """Return an operation that measures failure without escaping the timer."""

    def timed() -> None:
        try:
            operation()
        except error_type:
            pass

    return timed


def compile_hand_validator(condition: str, namespace: dict[str, object]) -> Callable[[object], object]:
    """Compile one direct handwritten strict check for comparison."""

    source = f"def validate(value):\n    if {condition}:\n        raise TypeError\n    return value"
    exec(compile(source, "<handwritten Campaign 6 validator>", "exec"), namespace)
    return cast(Callable[[object], object], namespace["validate"])


def make_hand_constructor(name: str, condition: str, namespace: dict[str, object]) -> type:
    """Create an immutable slotted class with one equivalent inline check."""

    def immutable(instance: object, attribute: str, value: object) -> None:
        raise AttributeError("instances are immutable")

    cls = type(name, (), {"__slots__": ("value",), "__setattr__": immutable})
    namespace["slot"] = vars(cls)["value"].__set__
    source = f"def __init__(self, *, value):\n    if {condition}:\n        raise TypeError\n    slot(self, value)"
    exec(compile(source, "<handwritten Campaign 6 constructor>", "exec"), namespace)
    type.__setattr__(cls, "__init__", namespace["__init__"])
    return cls


def benchmark_types() -> None:
    """Measure strict validators and Spec construction for required type families."""

    type_cases = [
        ("UUID", UUID, UUID(int=0), "not isinstance(value, expected)", {"expected": UUID}),
        ("date", date, date.min, "type(value) is not expected", {"expected": date}),
        ("datetime", datetime, datetime.min, "not isinstance(value, expected)", {"expected": datetime}),
        ("Decimal", Decimal, Decimal("1"), "not isinstance(value, expected)", {"expected": Decimal}),
        ("Enum", Status, Status.ACTIVE, "type(value) is not expected", {"expected": Status}),
        (
            "Literal[str]",
            Literal["a", "b"],
            "a",
            "type(value) is not str or value not in literals",
            {"literals": ("a", "b")},
        ),
        (
            "IPv4Address",
            IPv4Address,
            IPv4Address("127.0.0.1"),
            "type(value) is not expected",
            {"expected": IPv4Address},
        ),
    ]
    for case, annotation, value, condition, namespace in type_cases:
        talea_validator = compile_validator(resolve_annotation(annotation))
        hand_validator = compile_hand_validator(condition, dict(namespace))
        talea_type = type(f"Talea{case}", (Spec,), {"__annotations__": {"value": annotation}})
        hand_type = make_hand_constructor(f"Hand{case}", condition, dict(namespace))
        for label, operation in (
            ("talea", partial(talea_validator, value)),
            ("handwritten", partial(hand_validator, value)),
        ):
            print_measurement(f"validate {case}", label, measure(operation, _EXECUTION_ITERATIONS))
        for label, operation in (
            ("talea", partial(talea_type, value=value)),
            ("handwritten", partial(hand_type, value=value)),
        ):
            print_measurement(f"construct {case}", label, measure(operation, _EXECUTION_ITERATIONS))

    invalid = "00000000-0000-0000-0000-000000000000"
    talea_failure = compile_validator(resolve_annotation(UUID))
    hand_failure = compile_hand_validator("not isinstance(value, expected)", {"expected": UUID})
    print_measurement(
        "validate UUID failure",
        "talea",
        measure(swallowed_failure(partial(talea_failure, invalid), ValidationError), _FAILURE_ITERATIONS),
    )
    print_measurement(
        "validate UUID failure",
        "handwritten",
        measure(swallowed_failure(partial(hand_failure, invalid), TypeError), _FAILURE_ITERATIONS),
    )


def benchmark_constraints() -> None:
    """Measure normalized constraint checks against equivalent direct operations."""

    pattern = compile_pattern(r"^[a-z]+$")

    def handwritten_list(value: object) -> object:
        if type(value) is not list or len(value) < 1:
            raise TypeError
        for item in value:
            if type(item) is not int:
                raise TypeError
        return value

    cases = [
        (
            "Annotated[int, Ge(0)]",
            Annotated[int, Ge(0)],
            50,
            compile_hand_validator("type(value) is not int or value < 0", {}),
        ),
        (
            "Annotated[int, Ge(0), Le(100)]",
            Annotated[int, Ge(0), Le(100)],
            50,
            compile_hand_validator("type(value) is not int or value < 0 or value > 100", {}),
        ),
        (
            "str length",
            Annotated[str, MinLength(2), MaxLength(20)],
            "talea",
            compile_hand_validator("type(value) is not str or len(value) < 2 or len(value) > 20", {}),
        ),
        (
            "Pattern",
            Annotated[str, Pattern(pattern)],
            "talea",
            compile_hand_validator(
                "type(value) is not str or pattern.search(value) is None",
                {"pattern": pattern},
            ),
        ),
        (
            "list length",
            Annotated[list[int], MinLength(1)],
            [1, 2],
            handwritten_list,
        ),
    ]
    for case, annotation, value, hand_validator in cases:
        talea_validator = compile_validator(resolve_annotation(annotation))
        for label, operation in (
            ("talea", partial(talea_validator, value)),
            ("handwritten", partial(hand_validator, value)),
        ):
            print_measurement(case, label, measure(operation, _EXECUTION_ITERATIONS))


def benchmark_literals() -> None:
    """Measure representative type-sensitive Literal contracts."""

    cases = [
        ("Literal['a', 'b']", Literal["a", "b"], "a", "type(value) is not str or value not in values", ("a", "b")),
        ("Literal[1, 2, 3]", Literal[1, 2, 3], 2, "type(value) is not int or value not in values", (1, 2, 3)),
        ("Literal[True]", Literal[True], True, "type(value) is not bool or value is not literal", True),
        (
            "Literal[Status]",
            Literal[Status.ACTIVE, Status.DISABLED],
            Status.ACTIVE,
            "type(value) is not expected or value not in values",
            (Status.ACTIVE, Status.DISABLED),
        ),
    ]
    for case, annotation, value, condition, values in cases:
        namespace: dict[str, object] = {
            "values": values,
            "literal": values,
            "expected": Status,
        }
        talea_validator = compile_validator(resolve_annotation(annotation))
        hand_validator = compile_hand_validator(condition, namespace)
        print_measurement(case, "talea", measure(partial(talea_validator, value), _EXECUTION_ITERATIONS))
        print_measurement(case, "handwritten", measure(partial(hand_validator, value), _EXECUTION_ITERATIONS))


def benchmark_compilation_and_memory() -> None:
    """Measure representative resolution/compilation costs and instance size."""

    cases = [
        ("UUID", UUID),
        ("Literal", Literal["a", "b"]),
        ("numeric range", Annotated[int, Ge(0), Le(100)]),
        ("Pattern", Annotated[str, Pattern(r"^[a-z]+$")]),
    ]
    for case, annotation in cases:
        schema = resolve_annotation(annotation)
        print_measurement(
            f"resolve {case}",
            "talea",
            measure(partial(resolve_annotation, annotation), _COMPILATION_ITERATIONS),
        )
        print_measurement(
            f"compile {case}",
            "talea",
            measure(partial(compile_validator, schema), _COMPILATION_ITERATIONS),
        )

    class Plain(Spec):
        value: int

    class Constrained(Spec):
        value: Annotated[int, Ge(0), Le(100)]

    plain = Plain(value=1)
    constrained = Constrained(value=1)
    print(f"instance size plain={sys.getsizeof(plain)} B constrained={sys.getsizeof(constrained)} B")
    names = vars(Plain)["__init__"].__code__.co_names
    print(f"zero-feature names={names!r}")


if __name__ == "__main__":
    print(f"Campaign 6 execution ({_REPEATS} samples x {_EXECUTION_ITERATIONS:,} operations)")
    benchmark_types()
    benchmark_constraints()
    benchmark_literals()
    print(f"Campaign 6 compilation ({_REPEATS} samples x {_COMPILATION_ITERATIONS:,} operations)")
    benchmark_compilation_and_memory()
