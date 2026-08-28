"""Measure stdlib dataclass Contract interoperability and permanent canaries."""

import dis
import gc
import json
import sys
import tracemalloc
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from statistics import median
from timeit import Timer
from typing import cast

from talea import Contract, Spec

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

    print(f"{case:34} {owner:22} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


@dataclass
class OneField:
    """Single-field mutable dataclass canary."""

    value: int


@dataclass
class FiveFields:
    """Five-field mutable dataclass workload."""

    field_0: int
    field_1: int
    field_2: int
    field_3: int
    field_4: int


@dataclass
class TenFields:
    """Ten-field mutable dataclass workload."""

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


@dataclass(frozen=True, slots=True)
class FrozenSlots:
    """Frozen and slotted dataclass workload."""

    value: int
    label: str


@dataclass
class Nested:
    """Nested dataclass workload."""

    item: FiveFields
    values: list[int] = field(default_factory=list)


@dataclass
class Page[T]:
    """Concrete generic dataclass workload."""

    items: list[T]


class OrdinarySpec(Spec):
    """Unrelated Spec execution canary."""

    value: int


def strict_one(value: object) -> OneField:
    """Validate the exact one-field dataclass contract manually."""

    if type(value) is not OneField or type(value.value) is not int:
        raise TypeError
    return value


def strict_five(value: object) -> FiveFields:
    """Validate the exact five-field dataclass contract manually."""

    if type(value) is not FiveFields:
        raise TypeError
    if any(
        type(item) is not int for item in (value.field_0, value.field_1, value.field_2, value.field_3, value.field_4)
    ):
        raise TypeError
    return value


def strict_ten(value: object) -> TenFields:
    """Validate the exact ten-field dataclass contract manually."""

    if type(value) is not TenFields:
        raise TypeError
    if any(
        type(item) is not int
        for item in (
            value.field_0,
            value.field_1,
            value.field_2,
            value.field_3,
            value.field_4,
            value.field_5,
            value.field_6,
            value.field_7,
            value.field_8,
            value.field_9,
        )
    ):
        raise TypeError
    return value


def strict_frozen(value: object) -> FrozenSlots:
    """Validate the exact frozen/slotted dataclass contract manually."""

    if type(value) is not FrozenSlots or type(value.value) is not int or type(value.label) is not str:
        raise TypeError
    return value


def strict_nested(value: object) -> Nested:
    """Validate the exact nested mutable dataclass contract manually."""

    if type(value) is not Nested:
        raise TypeError
    strict_five(value.item)
    if type(value.values) is not list or any(type(item) is not int for item in value.values):
        raise TypeError
    return value


def strict_page(value: object) -> Page[OneField]:
    """Validate the concrete generic dataclass contract manually."""

    if type(value) is not Page or type(value.items) is not list:
        raise TypeError
    for item in value.items:
        strict_one(item)
    return cast(Page[OneField], value)


def mapping_one(value: Mapping[str, object]) -> OneField:
    """Implement the equivalent closed Mapping-to-dataclass boundary."""

    copied = dict(value)
    if copied.keys() != {"value"} or type(copied["value"]) is not int:
        raise TypeError
    result = OneField(copied["value"])
    return strict_one(result)


def project_nested(value: Nested) -> dict[str, object]:
    """Project the equivalent detached nested Python shape manually."""

    strict_nested(value)
    return {
        "item": {
            "field_0": value.item.field_0,
            "field_1": value.item.field_1,
            "field_2": value.item.field_2,
            "field_3": value.item.field_3,
            "field_4": value.item.field_4,
        },
        "values": list(value.values),
    }


def retained_bytes() -> tuple[int, int]:
    """Measure retained and peak bytes for discarded dataclass Contracts."""

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    contracts = [Contract(Page[int]) for _ in range(100)]
    _, peak = tracemalloc.get_traced_memory()
    contracts.clear()
    gc.collect()
    after = tracemalloc.take_snapshot()
    retained = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    tracemalloc.stop()
    return retained, peak


def generated_code_evidence(contract: Contract[FiveFields]) -> tuple[int, tuple[str, ...]]:
    """Prove strict execution uses direct known attribute loads without reflection."""

    instructions = tuple(dis.get_instructions(contract.validate))
    attributes = tuple(
        cast(str, instruction.argval) for instruction in instructions if instruction.opname == "LOAD_ATTR"
    )
    forbidden = {"fields", "vars", "get_type_hints", "getattr"}
    assert forbidden.isdisjoint(attributes)
    assert set(FiveFields.__annotations__) <= set(attributes)
    return len(instructions), attributes


def main() -> None:
    """Print permanent dataclass boundary, memory, and zero-tax evidence."""

    one = OneField(1)
    five = FiveFields(0, 1, 2, 3, 4)
    ten = TenFields(*range(10))
    frozen = FrozenSlots(1, "one")
    nested = Nested(five, [1, 2, 3])
    page = Page([one])
    one_contract = Contract(OneField)
    five_contract = Contract(FiveFields)
    ten_contract = Contract(TenFields)
    frozen_contract = Contract(FrozenSlots)
    nested_contract = Contract(Nested)
    page_contract = Contract(Page[OneField])

    print("Strict current-state validation")
    for label, public, manual in (
        ("one field", lambda: one_contract.validate(one), lambda: strict_one(one)),
        ("five fields", lambda: five_contract.validate(five), lambda: strict_five(five)),
        ("ten fields", lambda: ten_contract.validate(ten), lambda: strict_ten(ten)),
        ("frozen slots", lambda: frozen_contract.validate(frozen), lambda: strict_frozen(frozen)),
        ("nested", lambda: nested_contract.validate(nested), lambda: strict_nested(nested)),
        ("generic", lambda: page_contract.validate(page), lambda: strict_page(page)),
    ):
        report(label, "Talea Contract", measure(public))
        report(label, "equivalent manual", measure(manual))

    external = {"value": 1}
    encoded = '{"value":1}'
    one_contract.from_python(external)
    one_contract.from_json(encoded)
    print("\nMapping and JSON construction")
    report(
        "Mapping -> dataclass",
        "Talea Contract",
        measure(lambda: one_contract.from_python(external), _BOUNDARY_ITERATIONS),
    )
    report("Mapping -> dataclass", "equivalent manual", measure(lambda: mapping_one(external), _BOUNDARY_ITERATIONS))
    report("JSON decode only", "stdlib json", measure(lambda: json.loads(encoded), _BOUNDARY_ITERATIONS))
    report(
        "decoded boundary", "Talea Contract", measure(lambda: one_contract.from_python(external), _BOUNDARY_ITERATIONS)
    )
    report(
        "JSON -> dataclass", "Talea Contract", measure(lambda: one_contract.from_json(encoded), _BOUNDARY_ITERATIONS)
    )
    report(
        "JSON -> dataclass",
        "equivalent manual",
        measure(lambda: mapping_one(json.loads(encoded)), _BOUNDARY_ITERATIONS),
    )

    nested_contract.to_python(nested)
    nested_contract.to_json(nested)
    print("\nSerialization")
    report(
        "nested to_python", "Talea Contract", measure(lambda: nested_contract.to_python(nested), _BOUNDARY_ITERATIONS)
    )
    report("nested to_python", "equivalent manual", measure(lambda: project_nested(nested), _BOUNDARY_ITERATIONS))
    report("nested to_json", "Talea Contract", measure(lambda: nested_contract.to_json(nested), _BOUNDARY_ITERATIONS))
    report(
        "nested to_json",
        "equivalent manual",
        measure(lambda: json.dumps(project_nested(nested), separators=(",", ":")), _BOUNDARY_ITERATIONS),
    )

    print("\nCold Contract creation")
    for label, annotation in (
        ("simple mutable", OneField),
        ("frozen slots", FrozenSlots),
        ("generic concrete", Page[int]),
        ("recursive", RecursiveBenchmarkNode),
    ):
        report(label, "cold Contract", measure(lambda annotation=annotation: Contract(annotation), _COLD_ITERATIONS))

    ordinary_contract = Contract(int)
    ordinary_spec = OrdinarySpec(value=1)
    print("\nZero-feature-tax canaries")
    report("ordinary Contract(int)", "retained validate", measure(lambda: ordinary_contract.validate(1)))
    report("ordinary Spec", "trusted construct", measure(lambda: OrdinarySpec(value=1)))
    report("ordinary Spec", "to_dict", measure(ordinary_spec.to_dict))

    instruction_count, attributes = generated_code_evidence(five_contract)
    retained, peak = retained_bytes()
    assert vars(one) == {"value": 1}
    print("\nGenerated code and memory")
    print(f"FiveFields strict instructions={instruction_count} direct_attributes={attributes}")
    print(f"OneField instance shallow={sys.getsizeof(one)} bytes dict={sys.getsizeof(vars(one))} bytes")
    print(f"FrozenSlots instance shallow={sys.getsizeof(frozen)} bytes has_dict={hasattr(frozen, '__dict__')}")
    print(f"100 discarded generic dataclass Contracts retained={retained} bytes peak={peak} bytes")


@dataclass
class RecursiveBenchmarkNode:
    """Recursive dataclass cold-resolution workload."""

    value: int
    children: list[RecursiveBenchmarkNode] = field(default_factory=list)


if __name__ == "__main__":
    main()
