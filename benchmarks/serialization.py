"""Measure Campaign 10 compiled Python projection and JSON encoding.

The primary comparison is a direct hand-written dictionary literal reading the
same immutable slotted values. Container rows necessarily include the fresh
copying required by Talea's no-alias output contract. JSON work is split into
schema projection, syntax encoding, and the public full operation.
"""

import gc
import importlib
import sys
import tracemalloc
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
from statistics import median
from time import perf_counter_ns
from timeit import Timer
from typing import Annotated, cast
from uuid import UUID

from talea import Alias, Spec, serialize
from talea.serialization.json import _default_dumps

_REPEATS = 7
_SUCCESS_ITERATIONS = 50_000
_DECLARATION_ITERATIONS = 500
_FIRST_USE_SAMPLES = 100
_ALLOCATION_SAMPLES = 500

type Operation = Callable[[], object]


class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


class AllocationMeasurement:
    """Retain minimum steady-state traced-memory deltas."""

    __slots__ = ("peak", "retained")

    def __init__(self, retained: int, peak: int) -> None:
        self.retained = retained
        self.peak = peak


def measure(operation: Operation, iterations: int = _SUCCESS_ITERATIONS) -> Measurement:
    """Measure one warmed operation across independent timer samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def measure_allocations(operation: Operation) -> AllocationMeasurement:
    """Measure minimum retained and peak traced bytes for one warmed operation."""

    operation()
    gc.collect()
    tracemalloc.start()
    samples: list[tuple[int, int]] = []
    for _ in range(_ALLOCATION_SAMPLES):
        before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        operation()
        current, peak = tracemalloc.get_traced_memory()
        samples.append((current - before, peak - before))
    tracemalloc.stop()
    return AllocationMeasurement(
        min(retained for retained, _ in samples),
        min(peak for _, peak in samples),
    )


def print_measurement(case: str, implementation: str, result: Measurement) -> None:
    """Print one stable timing row."""

    print(f"{case:34} {implementation:18} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def names(count: int) -> tuple[str, ...]:
    """Return deterministic field names for scaling measurements."""

    return tuple(f"field_{index}" for index in range(count))


def values(count: int) -> dict[str, int]:
    """Return one valid scaling payload."""

    return {name: index for index, name in enumerate(names(count))}


def make_spec(count: int) -> type[Spec]:
    """Declare one strict integer Spec for scaling measurements."""

    return type(f"Output{count}", (Spec,), {"__annotations__": dict.fromkeys(names(count), int)})


def make_hand_serializer(count: int) -> Callable[[object], dict[str, object]]:
    """Compile the equivalent direct hand-written dictionary literal."""

    entries = ", ".join(f"{name!r}: instance.{name}" for name in names(count))
    namespace: dict[str, object] = {}
    exec(compile(f"def serialize(instance):\n    return {{{entries}}}", "<hand serializer>", "exec"), namespace)
    return cast(Callable[[object], dict[str, object]], namespace["serialize"])


class Address(Spec):
    city: str
    postcode: str


class Nested(Spec):
    identifier: int
    address: Address


class Container(Spec):
    values: list[int]
    pair: tuple[int, ...]


class Standard(Spec):
    identifier: UUID
    created_at: datetime
    amount: Decimal


class Parent(Spec):
    identifier: int
    name: str


class Inherited(Parent):
    active: bool


class Hooked(Spec):
    value: int

    @serialize("value")
    def output(value: int) -> str:
        return str(value)


class Aliased(Spec):
    identifier: Annotated[int, Alias("id")]
    name: str


NESTED = Nested(identifier=1, address=Address(city="Zurich", postcode="8001"))
CONTAINER = Container(values=[1, 2, 3], pair=(1, 2, 3))
STANDARD = Standard(
    identifier=UUID(int=0),
    created_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    amount=Decimal("1234567890.123456789"),
)
INHERITED = Inherited(identifier=1, name="Ada", active=True)
HOOKED = Hooked(value=1)
ALIASED = Aliased(identifier=1, name="Ada")


def measure_first_use(mode: str) -> Measurement:
    """Measure compilation plus execution for fresh five-field declarations."""

    samples: list[int] = []
    for _ in range(_FIRST_USE_SAMPLES):
        spec = make_spec(5)
        instance = spec(**values(5))
        started = perf_counter_ns()
        instance.to_dict() if mode == "python" else instance.to_json()
        samples.append(perf_counter_ns() - started)
    return Measurement(min(samples), median(samples))


def benchmark_python_projection() -> None:
    """Measure plain and feature-bearing Python mapping projection."""

    print(f"Python projection ({_REPEATS} samples x {_SUCCESS_ITERATIONS:,} operations)")
    for count in (1, 5, 10):
        spec = make_spec(count)
        instance = spec(**values(count))
        hand = make_hand_serializer(count)
        print_measurement(f"to_dict {count} fields", "talea", measure(instance.to_dict))
        print_measurement(f"to_dict {count} fields", "handwritten", measure(partial(hand, instance)))

    cases: tuple[tuple[str, Operation], ...] = (
        ("nested Spec", NESTED.to_dict),
        ("list/container", CONTAINER.to_dict),
        ("standard-library", STANDARD.to_dict),
        ("inherited Spec", INHERITED.to_dict),
        ("serialization hook", HOOKED.to_dict),
        ("alias", ALIASED.to_dict),
        ("include/exclude", partial(INHERITED.to_dict, include={"identifier", "active"})),
        ("exclude_none", partial(INHERITED.to_dict, exclude_none=True)),
    )
    for name, operation in cases:
        print_measurement(name, "talea", measure(operation))


def benchmark_json_projection() -> None:
    """Measure projection, dumps-only, full encoding, and optional codec context."""

    five_type = make_spec(5)
    five = five_type(**values(5))
    artifacts = vars(five_type)["__talea_artifacts__"]
    projection = cast(
        Callable[[object], dict[str, object]],
        artifacts.outputs.output_for(artifacts.schema, "json", True, False),
    )
    tree = projection(five)
    print(f"JSON output ({_REPEATS} samples x {_SUCCESS_ITERATIONS:,} operations)")
    print_measurement("five-field projection", "talea", measure(partial(projection, five)))
    print_measurement("five-field dumps only", "stdlib", measure(partial(_default_dumps, tree)))
    print_measurement("five-field full to_json", "talea + stdlib", measure(five.to_json))
    for name, instance in (
        ("nested full to_json", NESTED),
        ("container full to_json", CONTAINER),
        ("UUID/datetime/Decimal", STANDARD),
    ):
        print_measurement(name, "talea + stdlib", measure(instance.to_json))

    try:
        orjson = importlib.import_module("orjson")
    except ModuleNotFoundError:
        print("optional orjson: unavailable")
    else:
        dumps = cast(Callable[[object], bytes], orjson.dumps)
        print_measurement("five-field dumps only", "orjson", measure(partial(dumps, tree)))
        print_measurement("five-field full to_json", "talea + orjson", measure(partial(five.to_json, dumps=dumps)))


def benchmark_costs() -> None:
    """Measure declaration, first use, allocations, and retained memory."""

    print(f"Output declaration ({_REPEATS} samples x {_DECLARATION_ITERATIONS:,} declarations)")
    for count in (1, 5, 10):
        print_measurement(
            f"declare {count} fields",
            "serialization unused",
            measure(partial(make_spec, count), _DECLARATION_ITERATIONS),
        )
    print(f"Output first use ({_FIRST_USE_SAMPLES:,} fresh five-field declarations)")
    print_measurement("first to_dict", "compile + execute", measure_first_use("python"))
    print_measurement("first to_json", "compile + execute", measure_first_use("json"))

    print(f"Output allocations ({_ALLOCATION_SAMPLES:,} warmed operations)")
    for name, operation in (
        ("five-field to_dict", make_spec(5)(**values(5)).to_dict),
        ("nested to_dict", NESTED.to_dict),
        ("five-field to_json", make_spec(5)(**values(5)).to_json),
    ):
        result = measure_allocations(operation)
        print(f"{name:34} retained={result.retained:5} B peak={result.peak:5} B")

    cold_type = make_spec(5)
    artifacts = vars(cold_type)["__talea_artifacts__"]
    instance = cold_type(**values(5))
    cold_bytes = sys.getsizeof(artifacts.outputs)
    instance_bytes = sys.getsizeof(instance)
    instance.to_dict()
    serializer = artifacts.outputs.python_alias
    assert serializer is not None
    warm_bytes = cold_bytes + sys.getsizeof(serializer) + sys.getsizeof(serializer.__globals__)
    print(
        "Output retained shallow memory "
        f"cold={cold_bytes} B python-warm={warm_bytes} B instance={instance_bytes} B "
        f"json_compiled={artifacts.outputs.json_alias is not None}"
    )


def main() -> None:
    """Run all Campaign 10 serialization benchmark families."""

    benchmark_python_projection()
    benchmark_json_projection()
    benchmark_costs()


if __name__ == "__main__":
    main()
