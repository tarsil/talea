"""Measure Campaign 18 input limits, adversarial scaling, and retention."""

import gc
import sys
import weakref
from collections.abc import Callable
from decimal import Decimal
from statistics import median
from timeit import Timer
from typing import TypedDict

from json_schema import TAGGED, HundredDefinitions
from recursive_generics import cold_specialization, retained_specialization_bytes
from utilities import released_contract_bytes

from talea import Contract, ResourceLimitError, ResourcePolicy, Spec, ValidationError

_REPEATS = 5
_HOT_ITERATIONS = 10_000
_LARGE_ITERATIONS = 100

type Operation = Callable[[], object]
type RecursiveValue = int | list[RecursiveValue]

UNLIMITED = ResourcePolicy(
    max_input_bytes=None,
    max_depth=None,
    max_nodes=None,
    max_errors=None,
)


class Measurement:
    """Retain minimum and median nanoseconds for one benchmark row."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


class Five(Spec):
    """Representative small external object."""

    field_0: int
    field_1: int
    field_2: int
    field_3: int
    field_4: int


class Node(TypedDict):
    """Recursive input graph for deliberate depth measurements."""

    value: int
    children: list[Node]


def measure(operation: Operation, iterations: int = _HOT_ITERATIONS) -> Measurement:
    """Measure one warmed operation across independent samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    values = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(values), median(values))


def report(case: str, result: Measurement) -> None:
    """Print one deterministic benchmark row."""

    print(f"{case:42} min={result.minimum:12.1f} ns/op median={result.median:12.1f} ns/op")


def node_data(depth: int) -> dict[str, object]:
    """Return one acyclic single-child graph at ``depth``."""

    value: dict[str, object] = {"value": 0, "children": []}
    for index in range(depth):
        value = {"value": index, "children": [value]}
    return value


def capture_resource(operation: Operation) -> None:
    """Consume one expected resource-policy failure."""

    try:
        operation()
    except ResourceLimitError:
        return
    raise AssertionError("resource failure benchmark succeeded")


def capture_validation(operation: Operation) -> None:
    """Consume one expected validation failure."""

    try:
        operation()
    except ValidationError:
        return
    raise AssertionError("validation failure benchmark succeeded")


def benchmark_boundaries() -> None:
    """Measure small input, depth, breadth, scalar, and failure workloads."""

    mapping = {f"field_{index}": index for index in range(5)}
    encoded = "{" + ",".join(f'"field_{index}":{index}' for index in range(5)) + "}"
    report("small Mapping default policy", measure(lambda: Five.from_mapping(mapping)))
    report("small Mapping unlimited policy", measure(lambda: Five.from_mapping(mapping, policy=UNLIMITED)))
    report("small JSON default policy", measure(lambda: Five.from_json(encoded)))
    report("small JSON unlimited policy", measure(lambda: Five.from_json(encoded, policy=UNLIMITED)))

    json_megabyte = '"' + "x" * 1_000_000 + '"'
    json_oversize = '"' + "x" * (8 * 1024 * 1024) + '"'
    text_contract = Contract(str)
    report("one-megabyte JSON string", measure(lambda: text_contract.from_json(json_megabyte), 10))
    report(
        "oversized JSON pre-parse failure",
        measure(lambda: capture_resource(lambda: text_contract.from_json(json_oversize)), 1_000),
    )

    nodes = Contract(Node)
    for depth in (10, 100):
        value = node_data(depth)
        policy = ResourcePolicy(max_depth=2 * depth + 2, max_nodes=10 * depth + 10)
        report(
            f"recursive Mapping depth {depth}",
            measure(lambda value=value, policy=policy: nodes.from_python(value, policy=policy), _LARGE_ITERATIONS),
        )

    integers = Contract(list[int])
    for count in (1_000, 10_000):
        values = list(range(count))
        policy = ResourcePolicy(max_nodes=count + 1)
        report(
            f"integer list {count} items",
            measure(
                lambda values=values, policy=policy: integers.from_python(values, policy=policy),
                _LARGE_ITERATIONS,
            ),
        )

    invalid = {f"field_{index}": "bad" for index in range(5)}
    report(
        "capped five-error failure",
        measure(
            lambda: capture_validation(lambda: Five.from_mapping(invalid, policy=ResourcePolicy(max_errors=3))),
            1_000,
        ),
    )
    cyclic: list[object] = []
    cyclic.append(cyclic)
    recursive = Contract(RecursiveValue)
    report(
        "recursive cycle failure",
        measure(lambda: capture_validation(lambda: recursive.from_python(cyclic)), 1_000),
    )
    over_depth = node_data(10)
    depth_policy = ResourcePolicy(max_depth=10)
    report(
        "depth-limit early failure",
        measure(
            lambda: capture_resource(lambda: nodes.from_python(over_depth, policy=depth_policy)),
            1_000,
        ),
    )

    late_union = Contract(list[int] | list[str])
    late_values = ["value"] * 1_000
    report(
        "untagged union late branch 1k",
        measure(lambda: late_union.from_python(late_values), _LARGE_ITERATIONS),
    )
    report(
        "tagged union selected of 32",
        measure(lambda: TAGGED[32].from_python({"kind": "tag-31", "value": 1})),
    )

    large_text = "x" * 1_000_000
    report("one-million-character str", measure(lambda: text_contract.from_python(large_text), 1_000))
    decimal_text = '"' + "9" * 100_000 + '"'
    decimal_contract = Contract(Decimal)
    report(
        "100k-digit Decimal JSON string",
        measure(lambda: decimal_contract.from_json(decimal_text), 10),
    )


def benchmark_tooling_and_retention() -> None:
    """Record trusted-tooling scaling and weak-retention evidence."""

    report("100-definition schema projection", measure(HundredDefinitions.json_schema, 20))
    report("32-branch tagged OpenAPI", measure(TAGGED[32].openapi_schema, 20))
    print(f"released warmed Contracts (1000) retained={released_contract_bytes()} bytes")
    references = [weakref.ref(cold_specialization()) for _ in range(1_000)]
    gc.collect()
    live = sum(reference() is not None for reference in references)
    print(
        "released cold specializations (1000) "
        f"live={live} traced_allocator_delta={retained_specialization_bytes()} bytes"
    )
    print(
        "Python protections "
        f"recursion_limit={sys.getrecursionlimit()} integer_string_digits={sys.get_int_max_str_digits()}"
    )


def main() -> None:
    """Print the Campaign 18 reproducible resource/security evidence."""

    print("Boundary resource policy")
    benchmark_boundaries()
    print("\nTrusted tooling and retention")
    benchmark_tooling_and_retention()


if __name__ == "__main__":
    main()
