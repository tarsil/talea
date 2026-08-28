"""Measure canonical Representation input execution and permanent canaries."""

import gc
import tracemalloc
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from timeit import Timer
from typing import Annotated, TypedDict

from talea import Contract, ResourcePolicy, Spec, ValidationError
from talea.representation import Representation
from talea.resources.state import resource_state

_REPEATS = 5
_HOT_ITERATIONS = 20_000
_BOUNDARY_ITERATIONS = 5_000
_COLD_ITERATIONS = 300

type Operation = Callable[[], object]


@dataclass(frozen=True, slots=True)
class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    minimum: float
    median: float


def measure(operation: Operation, iterations: int = _HOT_ITERATIONS) -> Measurement:
    """Measure one warmed operation across independent samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    values = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(values), median(values))


def report(case: str, owner: str, result: Measurement) -> None:
    """Print one stable benchmark row."""

    print(f"{case:34} {owner:24} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def allocated_bytes(operation: Operation, iterations: int = 1_000) -> int:
    """Return retained traced bytes across repeated execution."""

    operation()
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for _ in range(iterations):
        operation()
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    return sum(stat.size_diff for stat in after.compare_to(before, "lineno"))


class Money:
    """Opaque exact-type benchmark value."""

    __slots__ = ("cents",)

    def __init__(self, cents: int) -> None:
        self.cents = cents


class RetainedLoader:
    """Weak-referenceable callback for bounded lifetime measurement."""

    def __call__(self, value: str) -> Money:
        return load_money(value)


def load_money(value: str) -> Money:
    """Load one strict text representation."""

    return Money(int(value))


def exact_money(value: object) -> Money:
    """Perform the strict opaque contract by hand."""

    if type(value) is not Money:
        raise TypeError
    return value


def manual_input(value: object, policy: ResourcePolicy) -> Money:
    """Perform equivalent resource-aware input, callback, and result work."""

    state = resource_state(policy)
    state.consume_node(0)
    if type(value) is not str:
        raise TypeError
    result = load_money(value)
    state.consume_node(0)
    return exact_money(result)


type MoneyValue = Annotated[Money, Representation(input=str, load=load_money)]


class PairInput(TypedDict):
    """Structured external contract."""

    whole: int
    fraction: int


def load_pair(value: PairInput) -> Money:
    """Load one structured money representation."""

    return Money(value["whole"] * 100 + value["fraction"])


type StructuredMoney = Annotated[Money, Representation(input=PairInput, load=load_pair)]


class Payment(Spec):
    """Spec composition workload."""

    amount: MoneyValue


class OrdinarySpec(Spec):
    """Unrelated Spec construction canary."""

    value: int


@dataclass
class Ledger:
    """Dataclass composition workload."""

    amount: MoneyValue


class Envelope(TypedDict):
    """TypedDict composition workload."""

    amount: MoneyValue


def main() -> None:
    """Run representation work and unrelated zero-tax canaries."""

    policy = ResourcePolicy()
    scalar = Contract[Money](MoneyValue)
    structured = Contract[Money](StructuredMoney)
    nested = Contract[list[Money]](list[MoneyValue])
    dataclass_contract = Contract(Ledger)
    typed_dict_contract = Contract[Envelope](Envelope)
    integer = Contract(int)
    money = Money(1)
    structured_input: PairInput = {"whole": 1, "fraction": 25}

    scalar.from_python("1")
    scalar.from_json('"1"')
    structured.from_python(structured_input)
    structured.from_json('{"whole":1,"fraction":25}')
    nested.from_python(["1", "2"])
    Payment.from_mapping({"amount": "1"})
    dataclass_contract.from_python({"amount": "1"})
    typed_dict_contract.from_python({"amount": "1"})

    report("strict opaque", "Talea", measure(lambda: scalar.validate(money)))
    report("strict opaque", "manual exact", measure(lambda: exact_money(money)))
    report("loader lower bound", "callback", measure(lambda: load_money("1")))
    report("loader + result check", "manual", measure(lambda: exact_money(load_money("1"))))
    report(
        "scalar from_python",
        "Talea public boundary",
        measure(lambda: scalar.from_python("1"), _BOUNDARY_ITERATIONS),
    )
    report(
        "scalar from_python",
        "manual equivalent",
        measure(lambda: manual_input("1", policy), _BOUNDARY_ITERATIONS),
    )
    report(
        "structured from_python",
        "Talea public boundary",
        measure(lambda: structured.from_python(structured_input), _BOUNDARY_ITERATIONS),
    )
    report(
        "scalar from_json",
        "Talea public boundary",
        measure(lambda: scalar.from_json('"1"'), _BOUNDARY_ITERATIONS),
    )
    report(
        "structured from_json",
        "Talea public boundary",
        measure(lambda: structured.from_json('{"whole":1,"fraction":25}'), _BOUNDARY_ITERATIONS),
    )
    report(
        "nested list input",
        "Talea public boundary",
        measure(lambda: nested.from_python(["1", "2"]), _BOUNDARY_ITERATIONS),
    )
    report(
        "Spec field input",
        "Talea public boundary",
        measure(lambda: Payment.from_mapping({"amount": "1"}), _BOUNDARY_ITERATIONS),
    )
    report(
        "dataclass field input",
        "Talea public boundary",
        measure(lambda: dataclass_contract.from_python({"amount": "1"}), _BOUNDARY_ITERATIONS),
    )
    report(
        "TypedDict field input",
        "Talea public boundary",
        measure(lambda: typed_dict_contract.from_python({"amount": "1"}), _BOUNDARY_ITERATIONS),
    )
    report("cold Contract", "Talea", measure(lambda: Contract[Money](MoneyValue), _COLD_ITERATIONS))
    report(
        "cold input artifact",
        "Talea",
        measure(lambda: Contract[Money](MoneyValue).from_python("1"), _COLD_ITERATIONS),
    )

    def failure() -> object:
        try:
            return scalar.from_python(1)
        except ValidationError as error:
            return error

    report("input failure", "Talea", measure(failure, _BOUNDARY_ITERATIONS))

    type Expanded = Annotated[list[int], Representation(input=int, load=lambda size: list(range(size)))]
    expanded = Contract[list[int]](Expanded)
    report(
        "resource structural result",
        "Talea public boundary",
        measure(lambda: expanded.from_python(8), _BOUNDARY_ITERATIONS),
    )

    retained = allocated_bytes(lambda: scalar.from_python("1"))
    print(f"{'scalar input retained bytes':34} {'Talea':24} {retained:10d}")
    marker_size = Representation(input=str, load=load_money).__sizeof__()
    print(f"{'marker shallow bytes':34} {'Python':24} {marker_size:10d}")
    print(f"{'Contract shallow bytes':34} {'Python':24} {scalar.__sizeof__():10d}")

    references = []
    for _ in range(128):
        loader = RetainedLoader()
        references.append(weakref.ref(loader))
        Contract[Money](Annotated[Money, Representation(input=str, load=loader)]).from_python("1")
    for _ in range(256):
        loader = RetainedLoader()
        Contract[Money](Annotated[Money, Representation(input=str, load=loader)]).from_python("1")
    del loader
    gc.collect()
    retained_callbacks = sum(reference() is not None for reference in references)
    print(f"{'callbacks after bounded churn':34} {'Talea/Python':24} {retained_callbacks:10d}")

    report("Contract(int) canary", "Talea", measure(lambda: integer.from_python(1), _BOUNDARY_ITERATIONS))
    report("ordinary Spec canary", "Talea", measure(lambda: OrdinarySpec(value=1)))


if __name__ == "__main__":
    main()
