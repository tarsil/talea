"""Measure validator compilation and strict Python-value validation.

Execution samples reuse precompiled Talea and Pydantic validators.  Msgspec's
public Python-value API accepts the target type on each call and is therefore
reported as its closest available strict operation.  Pydantic and msgspec
reconstruct container values, unlike Talea and the hand-written baselines;
their container rows are context rather than equivalent identity-preserving
operations.
"""

import gc
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from statistics import median
from timeit import Timer

import msgspec
from pydantic import TypeAdapter, ValidationError as PydanticValidationError

from talea.schema import Schema, resolve_annotation
from talea.validation import ValidationError, compile_validator

_REPEATS = 7
_EXECUTION_ITERATIONS = 100_000
_FAILURE_ITERATIONS = 20_000
_COMPILATION_ITERATIONS = 2_000
_ALLOCATION_SAMPLES = 1_000

type Operation = Callable[[], object]


@dataclass(frozen=True, slots=True)
class Measurement:
    """Minimum and median nanoseconds for one measured operation."""

    minimum: float
    median: float


@dataclass(frozen=True, slots=True)
class AllocationMeasurement:
    """Minimum retained and peak traced bytes across warmed calls."""

    retained: int
    peak: int


def measure(operation: Operation, iterations: int) -> Measurement:
    """Measure one zero-argument operation across seven independent samples."""

    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def measure_allocations(operation: Operation) -> AllocationMeasurement:
    """Measure steady-state traced memory deltas for one successful call."""

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
        retained=min(retained for retained, _ in samples),
        peak=min(peak for _, peak in samples),
    )


def handwritten_int(value: object) -> object:
    """Validate an exact integer with direct Python."""

    if type(value) is not int:
        raise TypeError
    return value


def handwritten_list(value: object) -> object:
    """Validate ``list[int]`` with direct Python."""

    if type(value) is not list:
        raise TypeError
    for item in value:
        if type(item) is not int:
            raise TypeError
    return value


def handwritten_dict(value: object) -> object:
    """Validate ``dict[str, int]`` with direct Python."""

    if type(value) is not dict:
        raise TypeError
    for key, item in value.items():
        if type(key) is not str or type(item) is not int:
            raise TypeError
    return value


def handwritten_nested(value: object) -> object:
    """Validate ``list[dict[str, int | None]]`` with direct Python."""

    if type(value) is not list:
        raise TypeError
    for mapping in value:
        if type(mapping) is not dict:
            raise TypeError
        for key, item in mapping.items():
            if type(key) is not str or (type(item) is not int and item is not None):
                raise TypeError
    return value


def handwritten_fixed_tuple(value: object) -> object:
    """Validate ``tuple[int, str]`` with direct Python."""

    if type(value) is not tuple or len(value) != 2 or type(value[0]) is not int or type(value[1]) is not str:
        raise TypeError
    return value


def handwritten_union(value: object) -> object:
    """Validate ``int | str`` with direct Python."""

    if type(value) is not int and type(value) is not str:
        raise TypeError
    return value


def swallowed_failure(operation: Operation, error_type: type[BaseException]) -> Operation:
    """Return a timer-safe operation that consumes one expected validation failure."""

    def fail() -> object:
        try:
            return operation()
        except error_type:
            return None

    return fail


def execution_operations(
    annotation: object, value: object, handwritten: Callable[[object], object]
) -> dict[str, Operation]:
    """Build preconfigured execution operations for all comparison implementations."""

    schema = resolve_annotation(annotation)
    talea_validator = compile_validator(schema)
    pydantic_validator = TypeAdapter(annotation)
    return {
        "talea": lambda: talea_validator(value),
        "handwritten": lambda: handwritten(value),
        "pydantic": lambda: pydantic_validator.validate_python(value, strict=True),
        "msgspec": lambda: msgspec.convert(value, type=annotation, strict=True),
    }


def print_measurement(case: str, implementation: str, result: Measurement) -> None:
    """Print one stable, machine-readable benchmark row."""

    print(f"{case:43} {implementation:12} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def benchmark_execution() -> None:
    """Measure the required successful validation workloads."""

    cases = [
        ("primitive int success", int, 1, handwritten_int),
        ("list[int] success", list[int], [1, 2, 3, 4, 5], handwritten_list),
        ("dict[str, int] success", dict[str, int], {"one": 1, "two": 2}, handwritten_dict),
        (
            "nested list[dict[str, int | None]] success",
            list[dict[str, int | None]],
            [{"one": 1, "none": None}, {"two": 2}],
            handwritten_nested,
        ),
        ("fixed tuple success", tuple[int, str], (1, "two"), handwritten_fixed_tuple),
        ("union second-member success", int | str, "value", handwritten_union),
    ]
    for case, annotation, value, handwritten in cases:
        for implementation, operation in execution_operations(annotation, value, handwritten).items():
            print_measurement(case, implementation, measure(operation, _EXECUTION_ITERATIONS))


def benchmark_failure() -> None:
    """Measure exact-integer failure including each implementation's exception."""

    value = "1"
    operations = execution_operations(int, value, handwritten_int)
    error_types = {
        "talea": ValidationError,
        "handwritten": TypeError,
        "pydantic": PydanticValidationError,
        "msgspec": msgspec.ValidationError,
    }
    for implementation, operation in operations.items():
        timed = swallowed_failure(operation, error_types[implementation])
        print_measurement(
            "primitive int failure",
            implementation,
            measure(timed, _FAILURE_ITERATIONS),
        )


def benchmark_compilation() -> None:
    """Measure Talea compilation separately from annotation resolution and execution."""

    cases: dict[str, Schema] = {
        "compile primitive int": resolve_annotation(int),
        "compile nested list[dict[str, int | None]]": resolve_annotation(list[dict[str, int | None]]),
    }
    for case, schema in cases.items():
        print_measurement(
            case,
            "talea",
            measure(partial(compile_validator, schema), _COMPILATION_ITERATIONS),
        )


def benchmark_allocations() -> None:
    """Report successful-path allocations relative to an empty call baseline."""

    primitive = compile_validator(resolve_annotation(int))
    sequence = compile_validator(resolve_annotation(list[int]))
    nested = compile_validator(resolve_annotation(list[dict[str, int | None]]))
    sequence_value = [1, 2, 3, 4, 5]
    nested_value = [{"one": 1, "none": None}, {"two": 2}]
    operations: dict[str, Operation] = {
        "empty call baseline": lambda: None,
        "primitive int": lambda: primitive(1),
        "list[int]": lambda: sequence(sequence_value),
        "nested list[dict[str, int | None]]": lambda: nested(nested_value),
    }
    print(f"Successful-path traced memory ({_ALLOCATION_SAMPLES:,} warmed calls)")
    for name, operation in operations.items():
        result = measure_allocations(operation)
        print(f"{name:43} retained={result.retained:4} B peak={result.peak:4} B")


def main() -> None:
    """Print reproducible compilation, success, and failure measurements."""

    print(
        f"Validator execution ({_REPEATS} samples x {_EXECUTION_ITERATIONS:,} operations; "
        "failure uses 20,000 operations)"
    )
    print("Container references reconstruct values; Talea and handwritten validators preserve identity.")
    benchmark_execution()
    benchmark_failure()
    print(f"Validator compilation ({_REPEATS} samples x {_COMPILATION_ITERATIONS:,} compilations)")
    benchmark_compilation()
    benchmark_allocations()


if __name__ == "__main__":
    main()
