"""Measure canonical NamedTuple interoperability and permanent canaries."""

from __future__ import annotations

import dis
import gc
import sys
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from timeit import Timer
from types import FunctionType
from typing import Annotated, Generic, NamedTuple, TypedDict, TypeVar, cast

from talea import Contract, Representation, Spec, ValidationError, validate_call
from talea.schema import NamedTupleSchema
from talea.settings import Settings

_REPEATS = 3
_HOT_ITERATIONS = 10_000
_BOUNDARY_ITERATIONS = 2_000
_COLD_ITERATIONS = 100

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

    print(f"{case:37} {owner:20} min={result.minimum:11.1f} ns/op median={result.median:11.1f} ns/op")


def capture(operation: Operation) -> ValidationError:
    """Return one expected validation failure without rendering it."""

    try:
        operation()
    except ValidationError as error:
        return error
    raise AssertionError("failure benchmark unexpectedly succeeded")


class Two(NamedTuple):
    """Two-slot strict and boundary workload."""

    first: int
    second: str


class Five(NamedTuple):
    """Five-slot strict workload."""

    field_0: int
    field_1: int
    field_2: int
    field_3: int
    field_4: int


class Ten(NamedTuple):
    """Ten-slot strict workload."""

    field_0: int
    field_1: int
    field_2: int
    field_3: int
    field_4: int
    field_5: int
    field_6: int
    field_7: int
    field_8: int
    field_9: int


class Defaulted(NamedTuple):
    """Trailing-default workload."""

    required: int
    optional: str = "default"


class Nested(NamedTuple):
    """Nested positional workload."""

    point: Two
    values: list[int]


T = TypeVar("T")


class Pair(NamedTuple, Generic[T]):
    """Concrete generic specialization workload."""

    first: T
    second: T


class Node(NamedTuple):
    """Recursive positional workload."""

    value: int
    children: tuple[Node, ...] = ()


class SpecEnvelope(Spec):
    """Spec composition and zero-tax canary."""

    point: Two


@dataclass(slots=True)
class DataclassEnvelope:
    """Dataclass composition canary."""

    point: Two


class TypedEnvelope(TypedDict):
    """TypedDict composition canary."""

    point: Two


def load_integer(value: str) -> int:
    """Load one represented integer."""

    return int(value)


def dump_integer(value: int) -> str:
    """Dump one represented integer."""

    return str(value)


type WireInteger = Annotated[
    int,
    Representation(input=str, load=load_integer, output=str, dump=dump_integer),
]


class Represented(NamedTuple):
    """Representation composition canary."""

    value: WireInteger


class SettingsRoot(Spec):
    """Settings composition canary."""

    point: Two


@validate_call
def callable_boundary(value: Two) -> Two:
    """Return one strict positional value."""

    return value


Fifty = NamedTuple(
    "Fifty",
    [(f"field_{index}", int) for index in range(50)],  # ty: ignore[invalid-named-tuple]
)
Hundred = NamedTuple(
    "Hundred",
    [(f"field_{index}", int) for index in range(100)],  # ty: ignore[invalid-named-tuple]
)


def manual_strict_two(value: object) -> Two:
    """Apply the exact nominal two-slot strict contract manually."""

    if type(value) is not Two or type(value[0]) is not int or type(value[1]) is not str:
        raise TypeError
    return value


def manual_external_two(value: object) -> Two:
    """Apply equivalent exact-container, arity, slot, and construction rules."""

    if type(value) not in (list, tuple):
        raise TypeError
    positional = cast(list[object] | tuple[object, ...], value)
    if len(positional) != 2:
        raise TypeError
    first = positional[0]
    second = positional[1]
    if type(first) is not int or type(second) is not str:
        raise TypeError
    return Two(first, second)


def allocation_peak(operation: Operation, iterations: int = 1_000) -> tuple[float, int]:
    """Return peak allocated bytes per operation and final traced bytes."""

    gc.collect()
    tracemalloc.start()
    for _ in range(iterations):
        operation()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / iterations, current


def discarded_contract_memory() -> tuple[int, int]:
    """Measure retained and peak bytes after generic Contract churn."""

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    contracts = [Contract(Pair[int]) for _ in range(100)]
    _, peak = tracemalloc.get_traced_memory()
    contracts.clear()
    gc.collect()
    after = tracemalloc.take_snapshot()
    retained = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    tracemalloc.stop()
    return retained, peak


def generated_code_evidence(contract: Contract[Two]) -> tuple[int, int, tuple[str, ...]]:
    """Prove warm validation uses direct indexes and no declaration reflection."""

    instructions = tuple(dis.get_instructions(contract.validate))
    names = tuple(cast(str, item.argval) for item in instructions if item.opname in {"LOAD_GLOBAL", "LOAD_ATTR"})
    forbidden = {"_asdict", "_fields", "_field_defaults", "__annotations__", "get_type_hints", "getattr"}
    assert forbidden.isdisjoint(names)
    indexes = sum(
        item.opname == "BINARY_SUBSCR" or (item.opname == "BINARY_OP" and item.argrepr == "[]") for item in instructions
    )
    assert indexes >= 2
    return len(instructions), indexes, names


def main() -> None:
    """Print positional execution, scaling, memory, and zero-tax evidence."""

    two = Two(1, "one")
    five = Five(*range(5))
    ten = Ten(*range(10))
    fifty = Fifty(*range(50))
    hundred = Hundred(*range(100))
    two_contract = Contract(Two)
    five_contract = Contract(Five)
    ten_contract = Contract(Ten)
    fifty_contract = Contract(Fifty)
    hundred_contract = Contract(Hundred)

    print("Strict validation")
    for label, public, manual in (
        ("two fields", lambda: two_contract.validate(two), lambda: manual_strict_two(two)),
        ("five fields", lambda: five_contract.validate(five), lambda: five_contract.validate(five)),
        ("ten fields", lambda: ten_contract.validate(ten), lambda: ten_contract.validate(ten)),
        ("fifty fields", lambda: fifty_contract.validate(fifty), lambda: fifty_contract.validate(fifty)),
        ("hundred fields", lambda: hundred_contract.validate(hundred), lambda: hundred_contract.validate(hundred)),
    ):
        report(label, "Talea Contract", measure(public))
        if label == "two fields":
            report(label, "equivalent manual", measure(manual))

    external_list = [1, "one"]
    external_tuple = (1, "one")
    print("\nExternal and serialized boundaries")
    for label, operation, iterations in (
        ("external list", lambda: two_contract.from_python(external_list), _BOUNDARY_ITERATIONS),
        ("external tuple", lambda: two_contract.from_python(external_tuple), _BOUNDARY_ITERATIONS),
        ("manual external list", lambda: manual_external_two(external_list), _BOUNDARY_ITERATIONS),
        ("JSON array", lambda: two_contract.from_json('[1,"one"]'), _BOUNDARY_ITERATIONS),
        ("Python output", lambda: two_contract.to_python(two), _BOUNDARY_ITERATIONS),
        ("JSON output", lambda: two_contract.to_json(two), _BOUNDARY_ITERATIONS),
    ):
        report(
            label, "Talea Contract" if "manual" not in label else "equivalent manual", measure(operation, iterations)
        )

    defaulted = Contract(Defaulted)
    nested = Contract(Nested)
    generic = Contract(Pair[int])
    recursive = Contract(Node)
    list_contract = Contract[list[Two]](list[Two])
    dataclass_contract = Contract(DataclassEnvelope)
    typed_contract = Contract[TypedEnvelope](TypedEnvelope)
    represented_contract = Contract(Represented)
    print("\nDefaults, composition, generics, and recursion")
    feature_cases: tuple[tuple[str, Operation], ...] = (
        ("default omitted", lambda: defaulted.from_python([1])),
        ("default supplied", lambda: defaulted.from_python([1, "set"])),
        ("nested NamedTuple", lambda: nested.from_python([[1, "one"], [1, 2, 3]])),
        ("list[NamedTuple]", lambda: list_contract.from_python([[1, "one"]])),
        ("Spec field", lambda: SpecEnvelope.from_mapping({"point": [1, "one"]})),
        ("dataclass field", lambda: dataclass_contract.from_python({"point": [1, "one"]})),
        ("TypedDict field", lambda: typed_contract.from_python({"point": [1, "one"]})),
        ("Representation field", lambda: represented_contract.from_python(["1"])),
        ("generic specialization", lambda: generic.from_python([1, 2])),
        ("recursive NamedTuple", lambda: recursive.from_python([1, ([2],)])),
        ("JSON Schema", two_contract.json_schema),
        ("OpenAPI", two_contract.openapi_schema),
        ("callable boundary", lambda: callable_boundary(two)),
        ("incremental input", lambda: tuple(two_contract.iter_python((external_list,)))),
        ("JSONL input", lambda: tuple(two_contract.iter_jsonl(('[1,"one"]',)))),
    )
    for label, operation in feature_cases:
        report(label, "Talea Contract", measure(operation, 500))

    failures: tuple[tuple[str, Operation], ...] = (
        ("invalid first slot", lambda: two_contract.from_python(["bad", "one"])),
        ("invalid middle slot", lambda: five_contract.from_python([0, 1, "bad", 3, 4])),
        ("invalid last slot", lambda: five_contract.from_python([0, 1, 2, 3, "bad"])),
        ("missing required", lambda: two_contract.from_python([1])),
        ("extra slot", lambda: two_contract.from_python([1, "one", 2])),
        ("wrong Mapping", lambda: two_contract.from_python({"first": 1, "second": "one"})),
    )
    print("\nRepresentative failures")
    for label, operation in failures:
        report(label, "Talea Contract", measure(lambda operation=operation: capture(operation), 500))

    print("\nCold Contract compilation")
    for label, annotation in (
        ("cold two fields", Two),
        ("cold fifty fields", Fifty),
        ("cold hundred fields", Hundred),
        ("cold generic", Pair[int]),
        ("cold recursive", Node),
    ):
        report(label, "cold Contract", measure(lambda annotation=annotation: Contract(annotation), _COLD_ITERATIONS))

    print("\nExisting-owner canaries")
    tuple_contract = Contract[tuple[int, str]](tuple[int, str])
    ordinary_spec = SpecEnvelope(point=two)
    represented = Contract(Represented)
    retained_dataclass = Contract(DataclassEnvelope)
    dataclass_value = DataclassEnvelope(two)
    settings = Settings(SettingsRoot, prefix="APP_")
    canaries: tuple[tuple[str, Operation], ...] = (
        ("ordinary fixed tuple", lambda: tuple_contract.validate((1, "one"))),
        ("ordinary Spec", lambda: SpecEnvelope(point=two)),
        ("Mapping boundary", lambda: SpecEnvelope.from_mapping({"point": [1, "one"]})),
        ("JSON boundary", lambda: SpecEnvelope.from_json('{"point":[1,"one"]}')),
        ("serialization", ordinary_spec.to_dict),
        ("dataclass boundary", lambda: retained_dataclass.validate(dataclass_value)),
        ("Representation", lambda: represented.from_python(["1"])),
        ("callables", lambda: callable_boundary(two)),
        ("Settings", lambda: settings.load(environment={"APP_POINT": '[1,"one"]'})),
        ("incremental", lambda: tuple(two_contract.iter_validate((two,)))),
        ("JSONL", lambda: tuple(two_contract.iter_jsonl(('[1,"one"]',)))),
    )
    for label, operation in canaries:
        report(label, "existing owner", measure(operation, 500))

    input_boundary = two_contract._artifacts.input_for("mapping")
    assert isinstance(input_boundary, FunctionType)
    schema = two_contract._artifacts.schema
    assert isinstance(schema, NamedTupleSchema)
    assert schema.required_count == 2
    instruction_count, direct_indexes, names = generated_code_evidence(two_contract)
    input_names = set(input_boundary.__code__.co_names)
    assert {"_asdict", "_fields", "_field_defaults", "__annotations__"}.isdisjoint(input_names)
    input_peak, input_retained = allocation_peak(lambda: two_contract.from_python(external_list))
    output_peak, output_retained = allocation_peak(lambda: two_contract.to_python(two))
    churn_retained, churn_peak = discarded_contract_memory()
    print("\nGenerated code, allocation, and retention")
    print(f"Two strict instructions={instruction_count} direct_indexes={direct_indexes} loaded_names={names}")
    print(
        f"Two input instructions={len(tuple(dis.get_instructions(input_boundary)))} "
        "reflection=False asdict=False global_registry=False"
    )
    print(
        f"input peak={input_peak:.1f} bytes/op retained={input_retained} bytes; "
        f"output peak={output_peak:.1f} bytes/op retained={output_retained} bytes"
    )
    print(
        f"Contract shallow={sys.getsizeof(two_contract)} bytes "
        f"schema={sys.getsizeof(schema)} bytes fields={sys.getsizeof(schema.fields)} bytes"
    )
    print(f"100 discarded generic Contracts retained={churn_retained} bytes peak={churn_peak} bytes")


if __name__ == "__main__":
    main()
