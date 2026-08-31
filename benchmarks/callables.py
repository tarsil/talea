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
from typing import Annotated, NotRequired, TypedDict, Unpack

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


def handwritten_positional(value: int, /) -> int:
    """Validate one positional-only integer and its return directly."""

    if type(value) is not int:
        raise TypeError
    result = value
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_positional(value: int, /) -> int:
    """Validate one positional-only integer through Talea."""

    return value


def handwritten_mixed(identifier: int, /, value: int) -> int:
    """Validate mixed fixed parameters and return directly."""

    if type(identifier) is not int or type(value) is not int:
        raise TypeError
    result = identifier + value
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_mixed(identifier: int, /, value: int) -> int:
    """Validate mixed fixed parameters through Talea."""

    return identifier + value


def handwritten_keyword(*, value: int) -> int:
    """Validate one keyword-only integer and return directly."""

    if type(value) is not int:
        raise TypeError
    result = value
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_keyword(*, value: int) -> int:
    """Validate one keyword-only integer through Talea."""

    return value


def handwritten_full(identifier: int, /, value: int, *, flag: bool, timeout: float = 1.0) -> int:
    """Validate a full fixed signature and return directly."""

    if type(identifier) is not int or type(value) is not int:
        raise TypeError
    if type(flag) is not bool or type(timeout) is not float:
        raise TypeError
    result = identifier + value + flag + int(timeout)
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_full(identifier: int, /, value: int, *, flag: bool, timeout: float = 1.0) -> int:
    """Validate a full fixed signature through Talea."""

    return identifier + value + flag + int(timeout)


def handwritten_args(*values: int) -> int:
    """Validate every variadic positional item with fail-fast semantics."""

    for value in values:
        if type(value) is not int:
            raise TypeError
    result = sum(values)
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_args(*values: int) -> int:
    """Validate every variadic positional item through Talea."""

    return sum(values)


def handwritten_kwargs(**values: int) -> int:
    """Validate every variadic keyword value with fail-fast semantics."""

    for value in values.values():
        if type(value) is not int:
            raise TypeError
    result = sum(values.values())
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_kwargs(**values: int) -> int:
    """Validate every variadic keyword value through Talea."""

    return sum(values.values())


class SmallOptions(TypedDict):
    """Small required-key unpack workload."""

    retries: int
    timeout: float


class LargeOptions(TypedDict):
    """Larger required/optional unpack workload."""

    retries: int
    timeout: float
    enabled: bool
    region: NotRequired[str]
    trace_id: NotRequired[str]


def handwritten_unpack_small(**values: Unpack[SmallOptions]) -> int:
    """Validate the complete small TypedDict keyword structure directly."""

    if tuple(values) != ("retries", "timeout"):
        raise TypeError
    if type(values["retries"]) is not int or type(values["timeout"]) is not float:
        raise TypeError
    result = values["retries"] + int(values["timeout"])
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_unpack_small(**values: Unpack[SmallOptions]) -> int:
    """Validate the small TypedDict keyword structure through Talea."""

    return values["retries"] + int(values["timeout"])


def handwritten_unpack_large(**values: Unpack[LargeOptions]) -> int:
    """Validate the complete larger TypedDict keyword structure directly."""

    if not {"retries", "timeout", "enabled"}.issubset(values):
        raise TypeError
    if not set(values).issubset({"retries", "timeout", "enabled", "region", "trace_id"}):
        raise TypeError
    if type(values["retries"]) is not int or type(values["timeout"]) is not float:
        raise TypeError
    if type(values["enabled"]) is not bool:
        raise TypeError
    if "region" in values and type(values["region"]) is not str:
        raise TypeError
    if "trace_id" in values and type(values["trace_id"]) is not str:
        raise TypeError
    result = values["retries"] + values["enabled"]
    if type(result) is not int:
        raise TypeError
    return result


@validate_call
def talea_unpack_large(**values: Unpack[LargeOptions]) -> int:
    """Validate the larger TypedDict keyword structure through Talea."""

    return values["retries"] + values["enabled"]


class DirectMethods:
    """Own direct descriptor-binding comparator methods."""

    def instance(self, value: int) -> int:
        return value

    @classmethod
    def class_method(cls, value: int) -> int:
        del cls
        return value

    @staticmethod
    def static_method(value: int) -> int:
        return value


class HandwrittenMethods:
    """Own equivalent handwritten validated methods."""

    def instance(self, value: int) -> int:
        if type(value) is not int:
            raise TypeError
        result = value
        if type(result) is not int:
            raise TypeError
        return result

    @classmethod
    def class_method(cls, value: int) -> int:
        del cls
        if type(value) is not int:
            raise TypeError
        result = value
        if type(result) is not int:
            raise TypeError
        return result

    @staticmethod
    def static_method(value: int) -> int:
        if type(value) is not int:
            raise TypeError
        result = value
        if type(result) is not int:
            raise TypeError
        return result


class TaleaMethods:
    """Own Talea-validated descriptor workloads."""

    @validate_call
    def instance(self, value: int) -> int:
        return value

    @validate_call
    @classmethod
    def class_method(cls, value: int) -> int:
        del cls
        return value

    @validate_call
    @staticmethod
    def static_method(value: int) -> int:
        return value

    @validate_call
    def mixed(self, identifier: int, /, value: int, *items: int, flag: bool, **metadata: int) -> int:
        return identifier + value + sum(items) + flag + sum(metadata.values())

    @validate_call
    def invalid_return(self, value: int) -> int:
        del value
        return "bad"  # ty: ignore[invalid-return-type]


DIRECT_METHODS = DirectMethods()
HANDWRITTEN_METHODS = HandwrittenMethods()
TALEA_METHODS = TaleaMethods()


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


def declare_complex() -> Callable[..., int]:
    """Compile and retain one fresh complete-signature callable boundary."""

    def local(head: int, /, *items: int, flag: bool, **values: int) -> int:
        return head + sum(items) + flag + sum(values.values())

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


def benchmark_binding_forms() -> None:
    """Measure complete fixed, variadic, unpack, and descriptor binding forms."""

    fixed: tuple[tuple[str, Operation, Operation], ...] = (
        ("positional-only", lambda: handwritten_positional(1), lambda: talea_positional(1)),
        ("mixed positional", lambda: handwritten_mixed(1, 2), lambda: talea_mixed(1, 2)),
        ("keyword-only", lambda: handwritten_keyword(value=1), lambda: talea_keyword(value=1)),
        (
            "mixed full fixed",
            lambda: handwritten_full(1, 2, flag=True),
            lambda: talea_full(1, 2, flag=True),
        ),
        ("bound instance method", lambda: HANDWRITTEN_METHODS.instance(1), lambda: TALEA_METHODS.instance(1)),
        (
            "classmethod",
            lambda: HandwrittenMethods.class_method(1),
            lambda: TaleaMethods.class_method(1),
        ),
        (
            "staticmethod",
            lambda: HandwrittenMethods.static_method(1),
            lambda: TaleaMethods.static_method(1),
        ),
    )
    for name, handwritten, talea in fixed:
        report(name, "handwritten", measure(handwritten))
        report(name, "talea", measure(talea))
    report("bound instance method", "direct", measure(lambda: DIRECT_METHODS.instance(1)))
    report("classmethod", "direct", measure(lambda: DirectMethods.class_method(1)))
    report("staticmethod", "direct", measure(lambda: DirectMethods.static_method(1)))
    report(
        "mixed method signature",
        "talea",
        measure(lambda: TALEA_METHODS.mixed(1, 2, 3, 4, flag=True, extra=5)),
    )

    for size in (0, 1, 5, 20):
        args = tuple(range(size))
        kwargs = {f"k{index}": index for index in range(size)}
        report(f"*args {size}", "handwritten", measure(lambda args=args: handwritten_args(*args)))
        report(f"*args {size}", "talea", measure(lambda args=args: talea_args(*args)))
        report(f"**kwargs {size}", "handwritten", measure(lambda kwargs=kwargs: handwritten_kwargs(**kwargs)))
        report(f"**kwargs {size}", "talea", measure(lambda kwargs=kwargs: talea_kwargs(**kwargs)))

    small: SmallOptions = {"retries": 1, "timeout": 1.0}
    large: LargeOptions = {"retries": 1, "timeout": 1.0, "enabled": True, "region": "eu", "trace_id": "t"}
    report("Unpack small", "handwritten", measure(lambda: handwritten_unpack_small(**small)))
    report("Unpack small", "talea", measure(lambda: talea_unpack_small(**small)))
    report("Unpack larger mix", "handwritten", measure(lambda: handwritten_unpack_large(**large)))
    report("Unpack larger mix", "talea", measure(lambda: talea_unpack_large(**large)))


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
        ("positional-only as keyword", lambda: talea_positional(value=1), TypeError),  # ty: ignore[positional-only-parameter-as-kwarg]
        ("missing keyword-only", lambda: talea_keyword(), TypeError),  # ty: ignore[missing-argument]
        ("invalid *args first", lambda: talea_args("bad", 1), ValidationError),  # ty: ignore[invalid-argument-type]
        ("invalid *args late", lambda: talea_args(1, 2, "bad"), ValidationError),  # ty: ignore[invalid-argument-type]
        ("invalid **kwargs first", lambda: talea_kwargs(bad="x", later=1), ValidationError),  # ty: ignore[invalid-argument-type]
        ("invalid **kwargs late", lambda: talea_kwargs(first=1, bad="x"), ValidationError),  # ty: ignore[invalid-argument-type]
        ("Unpack missing key", lambda: talea_unpack_small(timeout=1.0), ValidationError),  # ty: ignore[missing-argument]
        (
            "Unpack unexpected key",
            lambda: talea_unpack_small(retries=1, timeout=1.0, extra=1),
            ValidationError,
        ),
        (
            "Unpack wrong value",
            lambda: talea_unpack_small(retries="bad", timeout=1.0),  # ty: ignore[invalid-argument-type]
            ValidationError,
        ),
        ("method invalid argument", lambda: TALEA_METHODS.instance("bad"), ValidationError),  # ty: ignore[invalid-argument-type]
        ("method invalid return", lambda: TALEA_METHODS.invalid_return(1), ValidationError),
    )
    for name, operation, error_type in failures:
        report(name, "talea", measure(partial(capture, operation, error_type), _FAILURE_ITERATIONS))


def benchmark_compilation_memory_and_bytecode() -> None:
    """Measure cold compilation, allocations, retention, and warm instructions."""

    report("cold decoration/compilation", "talea", measure(declare_one, _COLD_ITERATIONS))
    report("cold complex compilation", "talea", measure(declare_complex, _COLD_ITERATIONS))
    operations: tuple[tuple[str, Operation], ...] = (
        ("empty call baseline", lambda: None),
        ("successful one int", lambda: talea_one(1)),
        ("failed one int", partial(capture, lambda: talea_one("bad"), ValidationError)),  # ty: ignore[invalid-argument-type]
        ("successful *args 20", lambda: talea_args(*range(20))),
        ("successful **kwargs 20", lambda: talea_kwargs(**{f"k{index}": index for index in range(20)})),
        (
            "failed *args late",
            partial(capture, lambda: talea_args(1, 2, "bad"), ValidationError),  # ty: ignore[invalid-argument-type]
        ),
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

    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    complex_wrappers = [declare_complex() for _ in range(_RETAINED_WRAPPERS)]
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    complex_retained = (current - before) / len(complex_wrappers)
    complex_peak = (peak - before) / len(complex_wrappers)
    print(f"retained complex callable              retained={complex_retained:8.1f} B peak={complex_peak:8.1f} B")
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
    for name, function in (
        ("positional-only", talea_positional),
        ("keyword-only", talea_keyword),
        ("mixed", talea_full),
        ("*args", talea_args),
        ("**kwargs", talea_kwargs),
        ("Unpack", talea_unpack_small),
        ("method", TaleaMethods.instance),
    ):
        instructions = tuple(dis.get_instructions(function))
        calls = sum(instruction.opname == "CALL" for instruction in instructions)
        loops = sum(instruction.opname == "FOR_ITER" for instruction in instructions)
        print(f"bytecode {name:20} instructions={len(instructions):3} calls={calls:2} loops={loops}")


def main() -> None:
    """Print the permanent callable timing and memory scorecard."""

    print(f"Callable warm execution ({_REPEATS} samples x {_HOT_ITERATIONS:,} operations)")
    benchmark_success()
    print("\nComplete binding forms")
    benchmark_binding_forms()
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
