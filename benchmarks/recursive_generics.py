"""Measure generic specialization and recursive Spec execution separately."""

import gc
import tracemalloc
from collections.abc import Callable
from statistics import median
from timeit import Timer
from typing import TypeVar

from talea import SerializationError, Spec

_REPEATS = 7
_HOT_ITERATIONS = 100_000
_COLD_ITERATIONS = 1_000
_RECURSIVE_ITERATIONS = 10_000

type Operation = Callable[[], object]


class Measurement:
    """Minimum and median nanoseconds for one operation."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


def measure(operation: Operation, iterations: int) -> Measurement:
    """Measure one operation across independent timer samples."""

    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def print_measurement(case: str, implementation: str, result: Measurement) -> None:
    """Print one stable timing row."""

    print(f"{case:30} {implementation:24} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


class Box[T](Spec):
    """Generic single-field construction canary."""

    value: T


class IntegerBox(Spec):
    """Concrete equivalent to ``Box[int]``."""

    value: int


class Point(Spec):
    """Ordinary non-generic, non-recursive zero-tax canary."""

    x: int
    y: int


class Node(Spec):
    """Recursive traversal canary."""

    value: int
    children: list[Node]


def cold_specialization() -> type[object]:
    """Create and specialize a fresh generic declaration."""

    Value = TypeVar("Value")
    origin = type(
        "ColdBox",
        (Spec,),
        {"__type_params__": (Value,), "__annotations__": {"value": Value}},
    )
    return origin[int]


def recursive_data(depth: int) -> dict[str, object]:
    """Build one acyclic single-branch mapping of ``depth`` nodes."""

    node: dict[str, object] = {"value": depth, "children": []}
    for value in reversed(range(depth)):
        node = {"value": value, "children": [node]}
    return node


def recursive_instance(depth: int) -> Node:
    """Build one validated single-branch graph of ``depth`` edges."""

    node = Node(value=depth, children=[])
    for value in reversed(range(depth)):
        node = Node(value=value, children=[node])
    return node


def swallowed_cycle(node: Node) -> None:
    """Consume the selected serialization-cycle failure for timing."""

    try:
        node.to_dict()
    except SerializationError:
        pass


def retained_specialization_bytes(count: int = 1_000) -> int:
    """Measure net memory while short-lived generic specializations are released."""

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for _ in range(count):
        cold_specialization()
    gc.collect()
    after = tracemalloc.take_snapshot()
    retained = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    tracemalloc.stop()
    return retained


def main() -> None:
    """Run Campaign 11 hot, cold, recursive, cycle, and retention canaries."""

    integer_box = Box[int]
    string_box = Box[str]
    shallow_data = recursive_data(3)
    deep_data = recursive_data(15)
    shallow_node = Node.from_mapping(shallow_data)
    deep_node = Node.from_mapping(deep_data)
    cyclic = Node(value=1, children=[])
    cyclic.children.append(cyclic)

    print("Generic construction")
    print_measurement(
        "Box[int] construction", "generic concrete", measure(lambda: integer_box(value=1), _HOT_ITERATIONS)
    )
    print_measurement(
        "Box[str] construction", "generic concrete", measure(lambda: string_box(value="x"), _HOT_ITERATIONS)
    )
    print_measurement(
        "int box construction", "equivalent concrete", measure(lambda: IntegerBox(value=1), _HOT_ITERATIONS)
    )
    print_measurement("Point construction", "ordinary canary", measure(lambda: Point(x=1, y=2), _HOT_ITERATIONS))

    print("\nSpecialization")
    print_measurement("cold specialization", "fresh origin", measure(cold_specialization, _COLD_ITERATIONS))
    print_measurement("cached specialization", "Box[int]", measure(lambda: Box[int], _HOT_ITERATIONS))

    print("\nRecursive construction and boundaries")
    print_measurement(
        "recursive construct depth 3", "python values", measure(lambda: recursive_instance(3), _RECURSIVE_ITERATIONS)
    )
    print_measurement(
        "recursive construct depth 15", "python values", measure(lambda: recursive_instance(15), _RECURSIVE_ITERATIONS)
    )
    print_measurement(
        "recursive mapping depth 3",
        "from_mapping",
        measure(lambda: Node.from_mapping(shallow_data), _RECURSIVE_ITERATIONS),
    )
    print_measurement(
        "recursive mapping depth 15",
        "from_mapping",
        measure(lambda: Node.from_mapping(deep_data), _RECURSIVE_ITERATIONS),
    )
    print_measurement(
        "recursive to_dict depth 3", "serialization", measure(shallow_node.to_dict, _RECURSIVE_ITERATIONS)
    )
    print_measurement("recursive to_dict depth 15", "serialization", measure(deep_node.to_dict, _RECURSIVE_ITERATIONS))
    print_measurement(
        "recursive to_json depth 3", "serialization", measure(shallow_node.to_json, _RECURSIVE_ITERATIONS)
    )
    print_measurement(
        "cycle detection", "to_dict failure", measure(lambda: swallowed_cycle(cyclic), _RECURSIVE_ITERATIONS)
    )

    print("\nSpecialization retention")
    print(f"released cold specializations (1000) retained={retained_specialization_bytes()} bytes")


if __name__ == "__main__":
    main()
