"""Measure compiled strict callable execution, failures, and retained artifacts."""

import dis
import gc
import inspect
import sys
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from statistics import median
from timeit import Timer
from typing import Annotated, TypedDict

from talea import Ge, Representation, Spec, ValidationError, validate_call

_REPEATS = 5
_HOT_ITERATIONS = 100_000
_FAILURE_ITERATIONS = 10_000
_COLD_ITERATIONS = 500
_ALLOCATION_SAMPLES = 1_000
_RETAINED_WRAPPERS = 200

type Operation = Callable[[], object]


@dataclass(frozen=True, slots=True)
class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    minimum: float
    median: float


@dataclass(frozen=True, slots=True)
class AllocationMeasurement:
    """Retain minimum steady-state and peak traced bytes for one operation."""

    retained: int
    peak: int


def measure(operation: Operation, iterations: int = _HOT_ITERATIONS) -> Measurement:
    """Measure one warmed operation over independent timer samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    values = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(values), median(values))


def measure_allocations(operation: Operation) -> AllocationMeasurement:
    """Measure minimum traced deltas across warmed operation samples."""

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


def report(case: str, owner: str, result: Measurement) -> None:
    """Print one stable benchmark row."""

    print(f"{case:36} {owner:20} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def capture(operation: Operation, error_type: type[BaseException]) -> BaseException:
    """Run one expected failure without letting it escape the timer."""

    try:
        operation()
    except error_type as error:
        return error
    raise AssertionError("callable failure benchmark succeeded")


def direct_one(value: int) -> int:
    """Return one integer without boundary validation."""

    return value


def handwritten_one(value: int) -> int:
    """Apply the exact accepted one-argument callable semantics directly."""

    if type(value) is not int:
        raise TypeError
    result = direct_one(value)
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_one(value: int) -> int:
    """Return one integer through Talea's compiled boundary."""

    return value


def direct_two(left: int, right: int) -> int:
    """Add two integers without validation."""

    return left + right


def handwritten_two(left: int, right: int) -> int:
    """Apply equivalent two-argument strict validation directly."""

    if type(left) is not int or type(right) is not int:
        raise TypeError
    result = direct_two(left, right)
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_two(left: int, right: int) -> int:
    """Add two integers through Talea's compiled boundary."""

    return left + right


def direct_five(a: int, b: int, c: int, d: int, e: int) -> int:
    """Sum five integers without validation."""

    return a + b + c + d + e


def handwritten_five(a: int, b: int, c: int, d: int, e: int) -> int:
    """Apply equivalent five-argument strict validation directly."""

    if type(a) is not int or type(b) is not int or type(c) is not int or type(d) is not int or type(e) is not int:
        raise TypeError
    result = direct_five(a, b, c, d, e)
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_five(a: int, b: int, c: int, d: int, e: int) -> int:
    """Sum five integers through Talea's compiled boundary."""

    return a + b + c + d + e


def direct_return() -> int:
    """Return one integer for return-only comparison."""

    return 1


def handwritten_return() -> int:
    """Validate only a returned integer directly."""

    result = direct_return()
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_return() -> int:
    """Validate only a returned integer through Talea."""

    return 1


@validate_call
def talea_nested(values: list[int]) -> None:
    """Validate one nested list argument."""


def handwritten_nested(values: list[int]) -> None:
    """Validate the same nested list and return contract directly."""

    if type(values) is not list or any(type(value) is not int for value in values):
        raise TypeError


class Item(Spec):
    """Immutable nominal callable workload."""

    value: int


@dataclass
class MutableItem:
    """Mutable dataclass callable workload."""

    value: int


class Payload(TypedDict):
    """TypedDict callable workload."""

    value: int


class Money:
    """Internal represented callable workload."""


def load_money(value: str) -> Money:
    """Construct benchmark Money at an external boundary."""

    del value
    return Money()


def dump_money(value: Money) -> str:
    """Project benchmark Money at an output boundary."""

    del value
    return "money"


type MoneyValue = Annotated[
    Money,
    Representation(input=str, load=load_money, output=str, dump=dump_money),
]
type Positive = Annotated[int, Ge(0)]


@validate_call
def talea_spec(value: Item) -> None:
    """Validate a Spec argument."""


@validate_call
def talea_dataclass(value: MutableItem) -> None:
    """Validate a dataclass argument and current state."""


@validate_call
def talea_typed_dict(value: Payload) -> None:
    """Validate a TypedDict argument."""


@validate_call
def talea_representation(value: MoneyValue) -> None:
    """Validate represented internal state without conversion."""


@validate_call
def talea_constraint(value: Positive) -> None:
    """Validate a constrained argument."""


@validate_call
def talea_default(value: int = 1) -> int:
    """Validate an immutable default policy workload."""

    return value


@validate_call
def talea_invalid_return(value: int) -> int:
    """Return an invalid value after one valid argument."""

    del value
    return "invalid"  # ty: ignore[invalid-return-type]


class ApplicationFailure(RuntimeError):
    """Identify application-owned failure timing."""


@validate_call
def talea_application_failure(value: int) -> int:
    """Raise one application exception after valid arguments."""

    del value
    raise ApplicationFailure


def handwritten_spec(value: Item) -> None:
    """Apply Talea's permanently trusted nominal Spec semantics directly."""

    if not isinstance(value, Item):
        raise TypeError


def handwritten_dataclass(value: MutableItem) -> None:
    """Apply exact dataclass current-state semantics directly."""

    if type(value) is not MutableItem or type(value.value) is not int:
        raise TypeError


def handwritten_typed_dict(value: Payload) -> None:
    """Apply exact TypedDict shape and value semantics directly."""

    if type(value) is not dict or tuple(value) != ("value",) or type(value["value"]) is not int:
        raise TypeError


def handwritten_representation(value: Money) -> None:
    """Apply represented internal nominal semantics directly."""

    if not isinstance(value, Money):
        raise TypeError


def handwritten_constraint(value: int) -> None:
    """Apply the equivalent strict non-negative integer contract directly."""

    if type(value) is not int or value < 0:
        raise TypeError


def declare_one() -> Callable[[int], int]:
    """Compile and retain one fresh callable boundary."""

    def local(value: int) -> int:
        return value

    return validate_call(local)


def benchmark_success() -> None:
    """Measure direct, handwritten-equivalent, and Talea warm execution."""

    cases: tuple[tuple[str, Operation, Operation, Operation], ...] = (
        ("one int", lambda: direct_one(1), lambda: handwritten_one(1), lambda: talea_one(1)),
        ("two int", lambda: direct_two(1, 2), lambda: handwritten_two(1, 2), lambda: talea_two(1, 2)),
        (
            "five primitive",
            lambda: direct_five(1, 2, 3, 4, 5),
            lambda: handwritten_five(1, 2, 3, 4, 5),
            lambda: talea_five(1, 2, 3, 4, 5),
        ),
        ("return validation only", direct_return, handwritten_return, talea_return),
    )
    for name, direct, handwritten, talea in cases:
        report(name, "direct", measure(direct))
        report(name, "handwritten", measure(handwritten))
        report(name, "talea", measure(talea))


def benchmark_structures() -> None:
    """Measure representative structured strict argument contracts."""

    values = [1, 2, 3, 4, 5]
    item = Item(value=1)
    mutable = MutableItem(1)
    payload = Payload(value=1)
    money = Money()
    cases: tuple[tuple[str, Operation, Operation], ...] = (
        ("nested list argument", lambda: handwritten_nested(values), lambda: talea_nested(values)),
        ("Spec argument", lambda: handwritten_spec(item), lambda: talea_spec(item)),
        ("dataclass argument", lambda: handwritten_dataclass(mutable), lambda: talea_dataclass(mutable)),
        ("TypedDict argument", lambda: handwritten_typed_dict(payload), lambda: talea_typed_dict(payload)),
        (
            "Representation internal argument",
            lambda: handwritten_representation(money),
            lambda: talea_representation(money),
        ),
        ("constraint argument", lambda: handwritten_constraint(1), lambda: talea_constraint(1)),
    )
    for name, handwritten, talea in cases:
        report(name, "handwritten", measure(handwritten))
        report(name, "talea", measure(talea))


def benchmark_defaults_and_binding() -> None:
    """Measure default policy and the rejected generic binder comparator."""

    signature = inspect.signature(direct_two)
    cases: tuple[tuple[str, Operation], ...] = (
        ("default omitted", talea_default),
        ("default explicitly supplied", lambda: talea_default(1)),
        ("Signature.bind comparator", lambda: signature.bind(1, 2)),
    )
    for name, operation in cases:
        report(name, "talea" if "bind" not in name else "inspect", measure(operation))


def benchmark_failures() -> None:
    """Measure boundary and application failure costs separately."""

    failures: tuple[tuple[str, Operation, type[BaseException]], ...] = (
        ("invalid first argument", lambda: talea_five("bad", 2, 3, 4, 5), ValidationError),  # ty: ignore[invalid-argument-type]
        ("invalid late argument", lambda: talea_five(1, 2, 3, 4, "bad"), ValidationError),  # ty: ignore[invalid-argument-type]
        ("invalid return", lambda: talea_invalid_return(1), ValidationError),
        ("application exception", lambda: talea_application_failure(1), ApplicationFailure),
    )
    for name, operation, error_type in failures:
        report(name, "talea", measure(partial(capture, operation, error_type), _FAILURE_ITERATIONS))


def benchmark_compilation_memory_and_bytecode() -> None:
    """Measure cold compilation, allocations, retention, and warm instructions."""

    report("cold decoration/compilation", "talea", measure(declare_one, _COLD_ITERATIONS))
    operations: tuple[tuple[str, Operation], ...] = (
        ("empty call baseline", lambda: None),
        ("successful one int", lambda: talea_one(1)),
        ("failed one int", partial(capture, lambda: talea_one("bad"), ValidationError)),  # ty: ignore[invalid-argument-type]
    )
    print(f"Traced allocations ({_ALLOCATION_SAMPLES:,} warmed operations)")
    for name, operation in operations:
        result = measure_allocations(operation)
        print(f"{name:36} retained={result.retained:6} B peak={result.peak:6} B")

    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    wrappers = [declare_one() for _ in range(_RETAINED_WRAPPERS)]
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    retained = (current - before) / len(wrappers)
    peak_per_wrapper = (peak - before) / len(wrappers)
    print(f"retained callable artifact             retained={retained:8.1f} B peak={peak_per_wrapper:8.1f} B")
    print(f"wrapper object shallow size            bytes={sys.getsizeof(talea_one):8}")

    for function in (talea_one, talea_two, talea_five):
        instructions = tuple(dis.get_instructions(function))
        calls = sum(instruction.opname == "CALL" for instruction in instructions)
        globals_loaded = tuple(
            instruction.argval for instruction in instructions if instruction.opname == "LOAD_GLOBAL"
        )
        print(
            f"bytecode {len(inspect.signature(function).parameters)} argument(s): "
            f"instructions={len(instructions)} calls={calls} globals={globals_loaded}"
        )


def main() -> None:
    """Print the permanent callable timing and memory scorecard."""

    print(f"Callable warm execution ({_REPEATS} samples x {_HOT_ITERATIONS:,} operations)")
    benchmark_success()
    print("\nStructured arguments")
    benchmark_structures()
    print("\nDefaults and binding")
    benchmark_defaults_and_binding()
    print("\nFailures")
    benchmark_failures()
    print("\nCompilation, memory, and bytecode")
    benchmark_compilation_memory_and_bytecode()


if __name__ == "__main__":
    main()
