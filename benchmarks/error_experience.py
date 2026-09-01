"""Measure Campaign 8 success canaries and distinct rich-error operations.

Failure rows create Talea's structured detail and therefore are not equivalent
to a hand-written bare ``TypeError``. The direct Python comparison is limited
to successful construction, where both classes perform the same exact checks,
atomic bound-slot writes, and immutable field binding.
"""

import gc
import tracemalloc
from collections.abc import Callable
from functools import partial
from statistics import median
from timeit import Timer
from typing import Annotated, Literal
from uuid import UUID

from talea import Ge, Spec, ValidationError, check, transform

_REPEATS = 7
_SUCCESS_ITERATIONS = 100_000
_FAILURE_ITERATIONS = 20_000
_PROJECTION_ITERATIONS = 20_000
_ALLOCATION_SAMPLES = 1_000

type Operation = Callable[[], object]


class Measurement:
    """Minimum and median nanoseconds for one operation."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


class AllocationMeasurement:
    """Minimum retained and peak traced bytes across warmed operations."""

    __slots__ = ("peak", "retained")

    def __init__(self, retained: int, peak: int) -> None:
        self.retained = retained
        self.peak = peak


def measure(operation: Operation, iterations: int) -> Measurement:
    """Measure one warmed operation across fixed independent samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def measure_allocations(operation: Operation) -> AllocationMeasurement:
    """Return minimum traced memory deltas for one warmed operation."""

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


def print_measurement(case: str, result: Measurement) -> None:
    """Print one stable timing row."""

    print(f"{case:38} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def capture(operation: Operation) -> ValidationError:
    """Run one operation and return its expected Talea failure."""

    try:
        operation()
    except ValidationError as error:
        return error
    raise AssertionError("error benchmark operation succeeded")


def immutable(instance: object, name: str, value: object) -> None:
    """Reject assignment in the equivalent hand-written canary."""

    raise AttributeError("instances are immutable")


class Simple(Spec):
    """Required-only successful construction canary."""

    identifier: int
    active: bool


class HandSimple:
    """Equivalent direct strict and immutable Python construction canary."""

    __slots__ = ("active", "identifier")
    __setattr__ = immutable

    def __init__(self, *, identifier: int, active: bool) -> None:
        if type(identifier) is not int or type(active) is not bool:
            raise TypeError
        _HAND_IDENTIFIER(self, identifier)
        _HAND_ACTIVE(self, active)


_HAND_IDENTIFIER = vars(HandSimple)["identifier"].__set__
_HAND_ACTIVE = vars(HandSimple)["active"].__set__


class Deep(Spec):
    """Nested location failure canary."""

    payload: list[dict[str, int]]


class Constrained(Spec):
    """Built-in constraint failure canary."""

    value: Annotated[int, Ge(0)]


class LiteralValue(Spec):
    """Literal failure canary."""

    value: Literal["open", "closed"]


class UnionValue(Spec):
    """Union branch-detail failure canary."""

    value: int | UUID


class Transformed(Spec):
    """Transform callback failure canary."""

    value: int

    @transform("value")
    def reject(value: object) -> object:
        raise ValueError("rejected")


class FieldChecked(Spec):
    """Field-check failure canary."""

    value: int

    @check("value")
    def reject(value: int) -> None:
        raise ValueError("rejected")


class SpecChecked(Spec):
    """Whole-Spec check failure canary."""

    start: int
    end: int

    @check("start", "end")
    def ordered(start: int, end: int) -> None:
        if end < start:
            raise ValueError("unordered")


FAILURES: dict[str, Operation] = {
    "one structural field failure": lambda: Simple(identifier="1", active=True),  # ty: ignore[invalid-argument-type]
    "deep nested failure": lambda: Deep(payload=[{"value": "1"}]),  # ty: ignore[invalid-argument-type]
    "constraint failure": lambda: Constrained(value=-1),
    "Literal failure": lambda: LiteralValue(value="other"),  # ty: ignore[invalid-argument-type]
    "union failure": lambda: UnionValue(value="hello"),  # ty: ignore[invalid-argument-type]
    "transform failure": lambda: Transformed(value=1),
    "field-check failure": lambda: FieldChecked(value=1),
    "whole-Spec-check failure": lambda: SpecChecked(start=2, end=1),
}


def benchmark_success() -> None:
    """Measure the permanent required-only construction canary."""

    print_measurement(
        "successful simple Spec",
        measure(partial(Simple, identifier=1, active=True), _SUCCESS_ITERATIONS),
    )
    print_measurement(
        "successful equivalent Python",
        measure(partial(HandSimple, identifier=1, active=True), _SUCCESS_ITERATIONS),
    )


def benchmark_failure_creation() -> None:
    """Measure validation and canonical detail creation without projection."""

    for name, operation in FAILURES.items():
        print_measurement(name, measure(partial(capture, operation), _FAILURE_ITERATIONS))


def benchmark_presentation() -> None:
    """Measure rendering and structured projection on retained errors."""

    for name in ("one structural field failure", "deep nested failure", "union failure"):
        error = capture(FAILURES[name])
        print_measurement(f"str: {name}", measure(partial(str, error), _PROJECTION_ITERATIONS))
        print_measurement(f"errors(): {name}", measure(error.errors, _PROJECTION_ITERATIONS))
        print_measurement(f"error_tree(): {name}", measure(error.error_tree, _PROJECTION_ITERATIONS))
        tree = error.error_tree()
        print_measurement(f"tree.to_dict(): {name}", measure(tree.to_dict, _PROJECTION_ITERATIONS))


def benchmark_allocations() -> None:
    """Measure success and representative failure allocation peaks separately."""

    primitive = vars(Simple)["__talea_artifacts__"].validators[0]
    deep_error = capture(FAILURES["deep nested failure"])
    deep_tree = deep_error.error_tree()
    cases: dict[str, Operation] = {
        "successful primitive": lambda: primitive(1),
        "successful simple Spec": partial(Simple, identifier=1, active=True),
        "failed primitive": partial(capture, lambda: primitive("1")),
        "failed nested field": partial(capture, FAILURES["deep nested failure"]),
        "failed union": partial(capture, FAILURES["union failure"]),
        "failed custom check": partial(capture, FAILURES["field-check failure"]),
        "nested error tree": deep_error.error_tree,
        "nested tree JSON data": deep_tree.to_dict,
    }
    print(f"Traced allocations ({_ALLOCATION_SAMPLES:,} warmed operations)")
    for name, operation in cases.items():
        result = measure_allocations(operation)
        print(f"{name:38} retained={result.retained:5} B peak={result.peak:5} B")


def main() -> None:
    """Print Campaign 8 timing and allocation evidence by operation."""

    print(f"Error success canary ({_REPEATS} samples x {_SUCCESS_ITERATIONS:,} constructions)")
    benchmark_success()
    print(f"Failure creation ({_REPEATS} samples x {_FAILURE_ITERATIONS:,} failures)")
    print("Failure rows create structured diagnostics; a bare TypeError is not semantically equivalent.")
    benchmark_failure_creation()
    print(f"Presentation ({_REPEATS} samples x {_PROJECTION_ITERATIONS:,} operations)")
    benchmark_presentation()
    print("Spec fields remain fail-fast; union alternatives are the only aggregated failure dimension.")
    benchmark_allocations()


if __name__ == "__main__":
    main()
