"""Measure canonical Representation input/output execution and permanent canaries."""

import gc
import json
import tracemalloc
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from dis import get_instructions
from statistics import median
from timeit import Timer
from types import FunctionType
from typing import Annotated, TypedDict, cast

from talea import Contract, Representation, ResourcePolicy, Sensitive, Spec, ValidationError
from talea.introspection import inspect_contract
from talea.resources.state import resource_state
from talea.serialization import SerializationError
from talea.serialization.selection import normalize_selection

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


class RetainedDumper:
    """Weak-referenceable output callback for bounded lifetime measurement."""

    def __call__(self, value: Money) -> str:
        return dump_money(value)


def load_money(value: str) -> Money:
    """Load one strict text representation."""

    return Money(int(value))


def exact_money(value: object) -> Money:
    """Perform the strict opaque contract by hand."""

    if type(value) is not Money:
        raise TypeError
    return value


def dump_money(value: Money) -> str:
    """Return the canonical scalar output."""

    return str(value.cents)


def manual_python_output(value: object) -> str:
    """Perform equivalent strict, dump, result-validation, and projection work."""

    internal = exact_money(value)
    return manual_dump_result(internal)


def manual_dump_result(value: Money) -> str:
    """Perform only the required scalar dump-result validation."""

    result = dump_money(value)
    if type(result) is not str:
        raise TypeError
    return result


def manual_json_output(value: object) -> str:
    """Perform equivalent strict scalar output and JSON encoding work."""

    return json.dumps(manual_python_output(value), allow_nan=False, separators=(",", ":"))


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
type FullMoneyValue = Annotated[
    Money,
    Representation(input=str, load=load_money, output=str, dump=dump_money),
]


class PairInput(TypedDict):
    """Structured external contract."""

    whole: int
    fraction: int


def load_pair(value: PairInput) -> Money:
    """Load one structured money representation."""

    return Money(value["whole"] * 100 + value["fraction"])


type StructuredMoney = Annotated[Money, Representation(input=PairInput, load=load_pair)]


class MoneyPayload(TypedDict):
    """Structured canonical output contract."""

    amount: int
    currency: str


def dump_payload(value: Money) -> MoneyPayload:
    """Return one structured output candidate."""

    return {"amount": value.cents, "currency": "CHF"}


def manual_selected_output(value: object) -> dict[str, dict[str, int]]:
    """Perform equivalent structured result validation and selected projection."""

    internal = exact_money(value)
    result = dump_payload(internal)
    if type(result) is not dict or type(result.get("amount")) is not int or type(result.get("currency")) is not str:
        raise TypeError
    return {"amount": {"amount": result["amount"]}}


type StructuredOutputMoney = Annotated[
    Money,
    Representation(output=MoneyPayload, dump=dump_payload),
]


class Payment(Spec):
    """Spec composition workload."""

    amount: MoneyValue


class OrdinarySpec(Spec):
    """Unrelated Spec construction canary."""

    value: int


class OutputPayment(Spec):
    """Spec output composition workload."""

    amount: FullMoneyValue


class StructuredPayment(Spec):
    """Nested represented-selection workload."""

    amount: StructuredOutputMoney


class Page[T](Spec):
    """Generic output containment workload."""

    item: T


class OutputNode(Spec):
    """Recursive containing output graph."""

    amount: FullMoneyValue
    children: list[OutputNode]


@dataclass
class Ledger:
    """Dataclass composition workload."""

    amount: MoneyValue


class Envelope(TypedDict):
    """TypedDict composition workload."""

    amount: MoneyValue


@dataclass
class OutputLedger:
    """Dataclass output composition workload."""

    amount: FullMoneyValue


class OutputEnvelope(TypedDict):
    """TypedDict output composition workload."""

    amount: FullMoneyValue


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

    scalar_output = Contract[Money](FullMoneyValue)
    structured_output = Contract[Money](StructuredOutputMoney)
    list_output = Contract[list[Money]](list[FullMoneyValue])
    mapping_output = Contract[dict[str, Money]](dict[str, FullMoneyValue])
    dataclass_output = Contract(OutputLedger)
    typed_dict_output = Contract[OutputEnvelope](OutputEnvelope)
    output_payment = OutputPayment(amount=money)
    structured_payment = StructuredPayment(amount=money)
    page = Page[FullMoneyValue](item=money)
    recursive = OutputNode(amount=money, children=[OutputNode(amount=money, children=[])])
    structured_artifacts = vars(StructuredPayment)["__talea_artifacts__"]
    selection = normalize_selection({"amount": {"amount": True}}, structured_artifacts.schema, "include")
    selected_output = structured_artifacts.outputs.selected_for(
        structured_artifacts.schema,
        "python",
        True,
        selection,
        None,
        False,
    )
    scalar_output.to_python(money)
    scalar_output.to_json(money)
    structured_output.to_python(money)
    structured_output.to_json(money)

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
    report("dump lower bound", "callback", measure(lambda: dump_money(money)))
    report("dump + result check", "manual", measure(lambda: manual_dump_result(money)))
    report("scalar to_python", "Talea", measure(lambda: scalar_output.to_python(money), _BOUNDARY_ITERATIONS))
    report("scalar to_python", "manual equivalent", measure(lambda: manual_python_output(money), _BOUNDARY_ITERATIONS))
    report("scalar to_json", "Talea", measure(lambda: scalar_output.to_json(money), _BOUNDARY_ITERATIONS))
    report("scalar to_json", "manual equivalent", measure(lambda: manual_json_output(money), _BOUNDARY_ITERATIONS))
    report(
        "structured to_python",
        "Talea",
        measure(lambda: structured_output.to_python(money), _BOUNDARY_ITERATIONS),
    )
    report(
        "structured to_json",
        "Talea",
        measure(lambda: structured_output.to_json(money), _BOUNDARY_ITERATIONS),
    )
    report("list output", "Talea", measure(lambda: list_output.to_python([money, money]), _BOUNDARY_ITERATIONS))
    report(
        "mapping value output",
        "Talea",
        measure(lambda: mapping_output.to_python({"amount": money}), _BOUNDARY_ITERATIONS),
    )
    report("Spec field output", "Talea", measure(output_payment.to_dict, _BOUNDARY_ITERATIONS))
    report(
        "dataclass field output",
        "Talea",
        measure(lambda: dataclass_output.to_python(OutputLedger(money)), _BOUNDARY_ITERATIONS),
    )
    report(
        "TypedDict field output",
        "Talea",
        measure(lambda: typed_dict_output.to_python({"amount": money}), _BOUNDARY_ITERATIONS),
    )
    report("generic containment", "Talea", measure(page.to_dict, _BOUNDARY_ITERATIONS))
    report("recursive containment", "Talea", measure(recursive.to_dict, _BOUNDARY_ITERATIONS))
    report(
        "selection public boundary",
        "Talea",
        measure(lambda: structured_payment.to_dict(include={"amount": {"amount": True}}), _BOUNDARY_ITERATIONS),
    )
    report(
        "selection direct compiled",
        "Talea",
        measure(lambda: selected_output(structured_payment), _BOUNDARY_ITERATIONS),
    )
    report(
        "selection direct compiled",
        "manual equivalent",
        measure(lambda: manual_selected_output(money), _BOUNDARY_ITERATIONS),
    )
    report(
        "cold output artifact",
        "Talea",
        measure(lambda: Contract[Money](FullMoneyValue).to_python(money), _COLD_ITERATIONS),
    )

    def failure() -> object:
        try:
            return scalar.from_python(1)
        except ValidationError as error:
            return error

    report("input failure", "Talea", measure(failure, _BOUNDARY_ITERATIONS))

    invalid_output = Contract[Money](Annotated[Money, Representation(output=str, dump=lambda value: value.cents)])

    def output_failure() -> object:
        try:
            return invalid_output.to_python(money)
        except SerializationError as error:
            return error

    report("invalid output failure", "Talea", measure(output_failure, _BOUNDARY_ITERATIONS))
    sensitive_output = Contract[Money](
        Annotated[Money, Representation(output=str, dump=lambda value: value.cents), Sensitive()]
    )

    def sensitive_failure() -> object:
        try:
            return sensitive_output.to_python(money)
        except SerializationError as error:
            return error

    report("sensitive output failure", "Talea", measure(sensitive_failure, _BOUNDARY_ITERATIONS))

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
    output_retained = allocated_bytes(lambda: scalar_output.to_python(money))
    print(f"{'scalar output retained bytes':34} {'Talea':24} {output_retained:10d}")
    representation_info = inspect_contract(scalar_output).representations[0]
    print(f"{'RepresentationInfo shallow bytes':34} {'Python':24} {representation_info.__sizeof__():10d}")
    print(f"{'warmed output artifact bytes':34} {'Python':24} {scalar_output._artifacts.__sizeof__():10d}")
    selection_variants = structured_artifacts.outputs.variants or {}
    selected_count = sum(bool(key) and key[0] == "selected" for key in selection_variants)
    print(f"{'retained selected output plans':34} {'Talea':24} {selected_count:10d}")
    python_output = scalar_output._artifacts.python_output
    json_output = scalar_output._artifacts.json_output
    assert python_output is not None and json_output is not None
    python_function = cast(FunctionType, python_output)
    public_calls = sum(instruction.opname == "CALL" for instruction in get_instructions(Contract.to_python))
    retained_calls = sum(instruction.opname == "CALL" for instruction in get_instructions(python_function))
    print(f"{'public to_python CALL opcodes':34} {'dis':24} {public_calls:10d}")
    print(f"{'retained output CALL opcodes':34} {'dis':24} {retained_calls:10d}")
    generated_names = tuple(name for name in python_function.__code__.co_names if "representation" in name)
    print(f"{'generated representation names':34} {'dis':24} {generated_names!r}")
    print(f"{'direct dump bound':34} {'Talea':24} {str(dump_money in python_function.__globals__.values()):>10}")
    print(
        f"{'registry names on warm output':34} {'Talea':24} "
        f"{sum('registry' in name for name in python_function.__globals__):10d}"
    )

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

    output_references = []
    for _ in range(128):
        dumper = RetainedDumper()
        output_references.append(weakref.ref(dumper))
        Contract[Money](Annotated[Money, Representation(output=str, dump=dumper)]).to_python(money)
    for _ in range(256):
        dumper = RetainedDumper()
        Contract[Money](Annotated[Money, Representation(output=str, dump=dumper)]).to_python(money)
    del dumper
    gc.collect()
    retained_dumpers = sum(reference() is not None for reference in output_references)
    print(f"{'dumpers after bounded churn':34} {'Talea/Python':24} {retained_dumpers:10d}")

    report("Contract(int) canary", "Talea", measure(lambda: integer.from_python(1), _BOUNDARY_ITERATIONS))
    report("ordinary Spec canary", "Talea", measure(lambda: OrdinarySpec(value=1)))


if __name__ == "__main__":
    main()
