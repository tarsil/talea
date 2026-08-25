"""Measure Spec declaration, construction, field lifecycles, failure, and memory.

The hand-written rows use slotted classes with the same exact integer checks
as Talea and are the primary equivalent comparison.  Strict Pydantic also
validates.  Slotted dataclasses and direct msgspec Struct construction do not
validate their annotated values, so those rows provide construction context
rather than equivalent semantics.  Msgspec failure uses strict mapping
conversion because direct Struct construction has no validation failure path.

Spec performs no lazy instance initialization.  Its first construction uses
the same retained constructor and validators as every repeated construction,
so there is no distinct first-construction workload to report.
"""

import gc
import tracemalloc
from collections.abc import Callable
from dataclasses import make_dataclass
from functools import partial
from statistics import median
from timeit import Timer
from typing import Any, cast

import msgspec
from pydantic import ConfigDict, ValidationError as PydanticValidationError, create_model

from talea import Spec, field
from talea.validation import ValidationError

_REPEATS = 7
_DECLARATION_ITERATIONS = 1_000
_CONSTRUCTION_ITERATIONS = 100_000
_FAILURE_ITERATIONS = 20_000
_MEMORY_INSTANCES = 20_000

type Operation = Callable[[], object]
type Constructor = Callable[..., object]


class Measurement:
    """Minimum and median nanoseconds for one operation."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


def measure(operation: Operation, iterations: int) -> Measurement:
    """Measure an operation across independent timer samples."""

    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def field_names(field_count: int) -> tuple[str, ...]:
    """Return deterministic identifier names for one scaling canary."""

    return tuple(f"field_{index}" for index in range(field_count))


def field_values(field_count: int) -> dict[str, object]:
    """Return valid integer values for one scaling canary."""

    return {name: index for index, name in enumerate(field_names(field_count))}


def make_spec(field_count: int) -> Constructor:
    """Declare one Spec through the same metaclass path as class syntax."""

    annotations = dict.fromkeys(field_names(field_count), int)
    return type(f"Talea{field_count}", (Spec,), {"__annotations__": annotations})


def make_static_default_spec() -> Constructor:
    """Declare one Spec with a validated immutable static default."""

    return type("StaticDefault", (Spec,), {"__annotations__": {"value": int}, "value": 1})


def make_factory_default_spec() -> Constructor:
    """Declare one Spec with a per-instance mutable default factory."""

    return type(
        "FactoryDefault",
        (Spec,),
        {"__annotations__": {"values": list[int]}, "values": field(default_factory=list)},
    )


def make_handwritten(field_count: int) -> Constructor:
    """Create an equivalent direct-checking slotted Python class."""

    names = field_names(field_count)
    parameters = ", ".join(names)
    lines = [f"def __init__(self, *, {parameters}):"]
    for name in names:
        lines.extend((f"    if type({name}) is not int:", "        raise TypeError"))
    for name in names:
        lines.append(f"    self.{name} = {name}")
    namespace: dict[str, object] = {}
    exec(compile("\n".join(lines), "<hand-written benchmark>", "exec"), namespace)
    return type(
        f"Handwritten{field_count}",
        (),
        {"__slots__": names, "__init__": namespace["__init__"]},
    )


def make_dataclass_type(field_count: int) -> Constructor:
    """Create a non-validating slotted dataclass reference."""

    return make_dataclass(
        f"Dataclass{field_count}",
        [(name, int) for name in field_names(field_count)],
        slots=True,
    )


def make_pydantic(field_count: int) -> Constructor:
    """Create a strict validating Pydantic model reference."""

    fields = dict.fromkeys(field_names(field_count), (int, ...))
    return create_model(
        f"Pydantic{field_count}",
        __config__=ConfigDict(strict=True),
        **cast(dict[str, Any], fields),
    )


def make_msgspec(field_count: int) -> Constructor:
    """Create a non-validating msgspec Struct constructor reference."""

    return msgspec.defstruct(
        f"Msgspec{field_count}",
        [(name, int) for name in field_names(field_count)],
    )


def implementations(field_count: int) -> dict[str, Constructor]:
    """Build each construction implementation once outside timed execution."""

    return {
        "talea validating": make_spec(field_count),
        "handwritten validating": make_handwritten(field_count),
        "dataclass non-validating": make_dataclass_type(field_count),
        "pydantic validating": make_pydantic(field_count),
        "msgspec non-validating": make_msgspec(field_count),
    }


def print_measurement(case: str, implementation: str, result: Measurement) -> None:
    """Print one stable timing row."""

    print(f"{case:26} {implementation:25} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def swallowed_failure(operation: Operation, error_type: type[BaseException]) -> Operation:
    """Return a timer-safe operation that consumes one expected failure."""

    def fail() -> object:
        try:
            return operation()
        except error_type:
            return None

    return fail


def benchmark_declaration() -> None:
    """Measure complete Spec declaration cost independently of construction."""

    for count in (1, 5, 10):
        result = measure(partial(make_spec, count), _DECLARATION_ITERATIONS)
        print_measurement(f"declare {count} fields", "talea", result)
    print_measurement(
        "declare static default",
        "talea",
        measure(make_static_default_spec, _DECLARATION_ITERATIONS),
    )
    print_measurement(
        "declare factory default",
        "talea",
        measure(make_factory_default_spec, _DECLARATION_ITERATIONS),
    )


def benchmark_construction() -> None:
    """Measure successful repeated keyword construction at scaling canaries."""

    for count in (1, 5, 10):
        values = field_values(count)
        for name, constructor in implementations(count).items():
            print_measurement(
                f"construct {count} fields",
                name,
                measure(partial(constructor, **values), _CONSTRUCTION_ITERATIONS),
            )


def benchmark_defaults() -> None:
    """Measure static omitted/explicit and factory-omitted construction."""

    static_default = make_static_default_spec()
    factory_default = make_factory_default_spec()
    cases: dict[str, Operation] = {
        "static default omitted": static_default,
        "static default explicit": partial(static_default, value=2),
        "factory default omitted": factory_default,
        "factory default explicit": partial(factory_default, values=[]),
    }
    for name, operation in cases.items():
        print_measurement(name, "talea validating", measure(operation, _CONSTRUCTION_ITERATIONS))


def benchmark_failure() -> None:
    """Measure a last-field strict failure for validating implementations."""

    count = 5
    values = field_values(count)
    values[field_names(count)[-1]] = "wrong"
    candidates = implementations(count)
    operations: dict[str, tuple[Operation, type[BaseException]]] = {
        "talea validating": (partial(candidates["talea validating"], **values), ValidationError),
        "handwritten validating": (
            partial(candidates["handwritten validating"], **values),
            TypeError,
        ),
        "pydantic validating": (
            partial(candidates["pydantic validating"], **values),
            PydanticValidationError,
        ),
        "msgspec convert mapping": (
            partial(msgspec.convert, values, type=candidates["msgspec non-validating"], strict=True),
            msgspec.ValidationError,
        ),
    }
    for name, (operation, error_type) in operations.items():
        print_measurement(
            "failure 5 fields",
            name,
            measure(swallowed_failure(operation, error_type), _FAILURE_ITERATIONS),
        )


def retained_bytes_per_instance(operation: Operation) -> float:
    """Approximate retained traced memory while keeping instances alive."""

    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    instances = [operation() for _ in range(_MEMORY_INSTANCES)]
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if not instances:
        raise RuntimeError("memory benchmark did not retain instances")
    return (current - before) / _MEMORY_INSTANCES


def benchmark_memory() -> None:
    """Measure retained per-instance memory for the five-field canary."""

    count = 5
    values = field_values(count)
    for name, constructor in implementations(count).items():
        retained = retained_bytes_per_instance(partial(constructor, **values))
        print(f"memory {count} fields          {name:25} retained={retained:8.1f} B/instance")
    static_retained = retained_bytes_per_instance(make_static_default_spec())
    factory_retained = retained_bytes_per_instance(make_factory_default_spec())
    print(f"memory static default omitted talea validating          retained={static_retained:8.1f} B/instance")
    print(f"memory factory default omitted talea validating          retained={factory_retained:8.1f} B/instance")


def main() -> None:
    """Print reproducible Spec lifecycle and scaling measurements."""

    print(f"Spec declaration ({_REPEATS} samples x {_DECLARATION_ITERATIONS:,} declarations)")
    benchmark_declaration()
    print("First construction is not distinct: Spec has no lazy instance work.")
    print(f"Repeated construction ({_REPEATS} samples x {_CONSTRUCTION_ITERATIONS:,} operations)")
    benchmark_construction()
    print(f"Default construction ({_REPEATS} samples x {_CONSTRUCTION_ITERATIONS:,} operations)")
    benchmark_defaults()
    print(f"Construction failure ({_REPEATS} samples x {_FAILURE_ITERATIONS:,} operations)")
    benchmark_failure()
    print(f"Retained memory ({_MEMORY_INSTANCES:,} live instances)")
    benchmark_memory()


if __name__ == "__main__":
    main()
