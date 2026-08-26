"""Measure Campaign 13 declaration metadata and sensitive failure paths.

Metadata-free rows remain permanent hot-path canaries. Sensitive rows measure
the deliberate failure-only cost of safe snapshots, raw-value discard,
rendering, projection, and nested/callback redaction.
"""

import gc
import sys
import tracemalloc
from collections.abc import Callable
from functools import partial
from statistics import median
from timeit import Timer
from typing import Annotated

from talea import Description, Sensitive, Spec, ValidationError, check, transform

_REPEATS = 7
_DECLARATION_ITERATIONS = 1_000
_SUCCESS_ITERATIONS = 100_000
_FAILURE_ITERATIONS = 20_000
_PRESENTATION_ITERATIONS = 20_000
_ALLOCATION_SAMPLES = 1_000

type Operation = Callable[[], object]


class Plain(Spec):
    """Metadata-free success canary."""

    identifier: int
    active: bool


class Documented(Spec):
    """Documentation-bearing success canary."""

    identifier: Annotated[int, Description("Stable identifier.")]
    active: bool


class OrdinaryFailure(Spec):
    """Ordinary structural failure canary."""

    value: int


class SensitiveFailure(Spec):
    """Sensitive structural failure canary."""

    value: Annotated[int, Sensitive()]


class SensitiveNestedFailure(Spec):
    """Nested sensitive path canary."""

    value: Annotated[list[dict[str, int]], Sensitive()]


class SensitiveTransformFailure(Spec):
    """Sensitive callback failure canary."""

    value: Annotated[int, Sensitive()]

    @transform("value")
    def reject(value: object) -> object:
        raise ValueError("secret callback detail")


class SensitiveCheckFailure(Spec):
    """Sensitive post-validation callback failure canary."""

    value: Annotated[int, Sensitive()]

    @check("value")
    def reject(value: int) -> None:
        raise ValueError("secret callback detail")


def measure(operation: Operation, iterations: int) -> tuple[float, float]:
    """Return minimum and median nanoseconds across independent samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return min(nanoseconds), median(nanoseconds)


def report(case: str, operation: Operation, iterations: int) -> None:
    """Print one stable timing row."""

    minimum, median_time = measure(operation, iterations)
    print(f"{case:38} min={minimum:10.1f} ns/op median={median_time:10.1f} ns/op")


def capture(operation: Operation) -> ValidationError:
    """Return one expected validation failure."""

    try:
        operation()
    except ValidationError as error:
        return error
    raise AssertionError("metadata benchmark operation succeeded")


def make_spec(field_count: int, metadata: bool = False) -> type[Spec]:
    """Declare one dynamic class with or without field metadata."""

    annotation = Annotated[int, Description("Documented field.")] if metadata else int
    return type(
        f"Metadata{field_count}",
        (Spec,),
        {"__annotations__": {f"field_{index}": annotation for index in range(field_count)}},
    )


FAILURES: dict[str, Operation] = {
    "ordinary structural failure": lambda: OrdinaryFailure(value="secret"),  # ty: ignore[invalid-argument-type]
    "sensitive structural failure": lambda: SensitiveFailure(value="secret"),  # ty: ignore[invalid-argument-type]
    "sensitive nested failure": lambda: SensitiveNestedFailure(value=[{"token": "secret"}]),  # ty: ignore[invalid-argument-type]
    "sensitive transform failure": lambda: SensitiveTransformFailure(value="secret"),  # ty: ignore[invalid-argument-type]
    "sensitive check failure": lambda: SensitiveCheckFailure(value=1),
}


def allocation(operation: Operation) -> tuple[int, int]:
    """Return minimum retained and peak traced bytes for warmed operations."""

    operation()
    gc.collect()
    tracemalloc.start()
    samples = []
    for _ in range(_ALLOCATION_SAMPLES):
        before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        operation()
        current, peak = tracemalloc.get_traced_memory()
        samples.append((current - before, peak - before))
    tracemalloc.stop()
    return min(item[0] for item in samples), min(item[1] for item in samples)


def main() -> None:
    """Print Campaign 13 declaration, execution, failure, and memory evidence."""

    print(f"Metadata declaration ({_REPEATS} samples x {_DECLARATION_ITERATIONS:,} declarations)")
    for count in (1, 5, 10):
        report(f"metadata-free {count} fields", partial(make_spec, count), _DECLARATION_ITERATIONS)
    report("documented 1 field", partial(make_spec, 1, True), _DECLARATION_ITERATIONS)

    print(f"Successful construction ({_REPEATS} samples x {_SUCCESS_ITERATIONS:,} operations)")
    report("metadata-free", partial(Plain, identifier=1, active=True), _SUCCESS_ITERATIONS)
    report("documentation-bearing", partial(Documented, identifier=1, active=True), _SUCCESS_ITERATIONS)

    print(f"Failure creation ({_REPEATS} samples x {_FAILURE_ITERATIONS:,} failures)")
    for name, operation in FAILURES.items():
        report(name, partial(capture, operation), _FAILURE_ITERATIONS)

    ordinary = capture(FAILURES["ordinary structural failure"])
    sensitive = capture(FAILURES["sensitive nested failure"])
    print(f"Presentation ({_REPEATS} samples x {_PRESENTATION_ITERATIONS:,} operations)")
    report("ordinary str(error)", partial(str, ordinary), _PRESENTATION_ITERATIONS)
    report("sensitive str(error)", partial(str, sensitive), _PRESENTATION_ITERATIONS)
    report("ordinary errors()", ordinary.errors, _PRESENTATION_ITERATIONS)
    report("sensitive errors()", sensitive.errors, _PRESENTATION_ITERATIONS)

    ordinary_retained, ordinary_peak = allocation(partial(capture, FAILURES["ordinary structural failure"]))
    sensitive_retained, sensitive_peak = allocation(partial(capture, FAILURES["sensitive nested failure"]))
    print(
        "Failure allocations "
        f"ordinary={ordinary_retained}/{ordinary_peak} B sensitive={sensitive_retained}/{sensitive_peak} B"
    )
    plain = Plain(identifier=1, active=True)
    documented = Documented(identifier=1, active=True)
    print(
        "Shallow memory "
        f"class={sys.getsizeof(Plain)}/{sys.getsizeof(Documented)} B "
        f"instance={sys.getsizeof(plain)}/{sys.getsizeof(documented)} B "
        f"error={sys.getsizeof(ordinary)}/{sys.getsizeof(sensitive)} B"
    )


if __name__ == "__main__":
    main()
