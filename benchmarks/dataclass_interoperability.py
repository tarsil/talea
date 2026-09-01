"""Measure stdlib dataclass Contract interoperability and permanent canaries."""

import dis
import gc
import json
import sys
import tracemalloc
from collections import UserDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from statistics import median
from timeit import Timer
from types import FunctionType
from typing import Annotated, cast

from talea import Alias, Contract, Ge, ResourceLimitError, ResourcePolicy, Spec
from talea.input.json import decode_json
from talea.resources.policy import DEFAULT_RESOURCE_POLICY
from talea.resources.state import resource_state
from talea.schema import DataclassSchema

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


@dataclass
class Defaulted:
    """Static-default lifecycle workload."""

    value: int = 1


@dataclass
class FactoryDefaulted:
    """Factory-owned lifecycle workload."""

    values: list[int] = field(default_factory=list)


@dataclass(kw_only=True)
class KeywordOnly:
    """Keyword-only transparent construction workload."""

    value: int


@dataclass
class PostInitialized:
    """Lifecycle-sensitive post-init workload."""

    value: int

    def __post_init__(self) -> None:
        self.value += 1


@dataclass
class InitFalse:
    """Lifecycle-sensitive derived-state workload."""

    value: int
    doubled: int = field(init=False)

    def __post_init__(self) -> None:
        self.doubled = self.value * 2


@dataclass
class Aliased:
    """External-name specialization workload."""

    value: Annotated[int, Alias("external")]


@dataclass
class MigratedOne:
    """Single historical-name workload."""

    value: Annotated[int, Alias("current", legacy=("old_0",))]


@dataclass
class MigratedFour:
    """Four historical-name workload."""

    value: Annotated[int, Alias("current", legacy=tuple(f"old_{index}" for index in range(4)))]


@dataclass
class MigratedSixteen:
    """Sixteen historical-name workload."""

    value: Annotated[int, Alias("current", legacy=tuple(f"old_{index}" for index in range(16)))]


@dataclass
class MigratedPostInitialized:
    """Migration lookup followed by a lifecycle-sensitive constructor."""

    value: Annotated[int, Alias("current", legacy=("old",))]

    def __post_init__(self) -> None:
        self.value += 1


@dataclass
class Constrained:
    """Compiled constraint workload."""

    value: Annotated[int, Ge(0)]


@dataclass
class InvalidPostInit:
    """Retained-state failure workload."""

    value: int

    def __post_init__(self) -> None:
        self.value = "invalid"  # ty: ignore[invalid-assignment]


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


def mapping_one(
    value: Mapping[str, object],
    policy: ResourcePolicy = DEFAULT_RESOURCE_POLICY,
) -> OneField:
    """Implement the equivalent resource-aware Mapping-to-dataclass boundary."""

    state = resource_state(policy)
    state.consume_node(1)
    exact_dict = type(value) is dict
    if not exact_dict and not isinstance(value, Mapping):
        raise TypeError
    if exact_dict and len(value) == 1:
        try:
            member = value["value"]
        except KeyError:
            pass
        else:
            state.consume_node(1)
            if type(member) is not int:
                raise TypeError
            return OneField(value=member)
    copied = dict(value)
    if copied.keys() != {"value"}:
        raise TypeError
    state.consume_node(1)
    member = copied["value"]
    if type(member) is not int:
        raise TypeError
    return OneField(value=member)


def json_one(data: str) -> OneField:
    """Use Talea's canonical strict decoder with the manual boundary."""

    decoded = decode_json(data, None, title="OneField", policy=DEFAULT_RESOURCE_POLICY)
    if not isinstance(decoded, Mapping):
        raise TypeError
    return mapping_one(decoded)


def mapping_migrated(
    value: Mapping[str, object],
    legacy_names: tuple[str, ...],
) -> MigratedSixteen:
    """Implement equivalent declaration-bounded lookup with no precedence."""

    state = resource_state(DEFAULT_RESOURCE_POLICY)
    state.consume_node(1)
    if not isinstance(value, Mapping):
        raise TypeError
    accepted = ("current", *legacy_names)
    supplied = tuple(name for name in accepted if name in value)
    if len(supplied) != 1 or len(value) != 1:
        raise ValueError("missing, conflicting, or unexpected field name")
    member = value[supplied[0]]
    state.consume_node(1)
    if type(member) is not int:
        raise TypeError
    return MigratedSixteen(member)


def capture(operation: Operation) -> BaseException:
    """Return one expected benchmark failure without rendering it."""

    try:
        operation()
    except BaseException as error:
        return error
    raise AssertionError("benchmark operation did not fail")


def audit_manual_boundary() -> None:
    """Prove the success comparator retains the benchmarked public semantics."""

    assert mapping_one({"value": 1}) == OneField(1)
    assert mapping_one(UserDict({"value": 1})) == OneField(1)
    for invalid in ({}, {"value": 1, "extra": 2}, {"value": "1"}):
        assert isinstance(capture(lambda invalid=invalid: mapping_one(invalid)), TypeError)
    resource_failure = capture(lambda: mapping_one({"value": 1}, ResourcePolicy(max_nodes=1)))
    assert isinstance(resource_failure, ResourceLimitError)
    assert resource_failure.code == "nodes"
    assert json_one('{"value":1}') == OneField(1)


def python_calls(operation: Operation, iterations: int = 1_000) -> float:
    """Count steady-state Python frames without trusting profiler timings."""

    calls = 0

    def profile(frame: object, event: str, argument: object) -> None:
        del frame, argument
        nonlocal calls
        if event == "call":
            calls += 1

    sys.setprofile(profile)
    try:
        for _ in range(iterations):
            operation()
    finally:
        sys.setprofile(None)
    return calls / iterations


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

    audit_manual_boundary()
    print(
        "Handwritten audit: Mapping acceptance, exact dict, closed required shape, strict field type, "
        "normal dataclass lifecycle, resource accounting, and canonical strict JSON decode"
    )

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
    direct_boundary = one_contract._artifacts.python_input
    assert direct_boundary is not None
    assert isinstance(direct_boundary, FunctionType)
    print("\nMapping and JSON construction")
    report(
        "Mapping -> dataclass",
        "Talea Contract",
        measure(lambda: one_contract.from_python(external), _BOUNDARY_ITERATIONS),
    )
    report(
        "Mapping -> dataclass",
        "equivalent manual",
        measure(lambda: mapping_one(external), _BOUNDARY_ITERATIONS),
    )
    report(
        "JSON decode only",
        "bare stdlib",
        measure(lambda: json.loads(encoded), _BOUNDARY_ITERATIONS),
    )
    report(
        "JSON decode only",
        "strict Talea semantics",
        measure(
            lambda: decode_json(
                encoded,
                None,
                title="OneField",
                policy=DEFAULT_RESOURCE_POLICY,
            ),
            _BOUNDARY_ITERATIONS,
        ),
    )
    report(
        "decoded boundary",
        "Talea Contract",
        measure(lambda: one_contract.from_python(external), _BOUNDARY_ITERATIONS),
    )
    report(
        "JSON -> dataclass",
        "Talea Contract",
        measure(lambda: one_contract.from_json(encoded), _BOUNDARY_ITERATIONS),
    )
    report(
        "JSON -> dataclass",
        "equivalent manual",
        measure(lambda: json_one(encoded), _BOUNDARY_ITERATIONS),
    )

    print("\nMigration-safe Mapping and JSON construction")
    migration_cases = (
        ("one legacy", MigratedOne, ("old_0",), "old_0"),
        ("four legacy", MigratedFour, tuple(f"old_{index}" for index in range(4)), "old_3"),
        ("sixteen legacy", MigratedSixteen, tuple(f"old_{index}" for index in range(16)), "old_15"),
    )
    for label, annotation, legacy_names, selected in migration_cases:
        contract = Contract(annotation)
        current_payload = {"current": 1}
        legacy_payload = {selected: 1}
        report(
            f"{label} current",
            "Talea Contract",
            measure(
                lambda contract=contract, current_payload=current_payload: contract.from_python(current_payload),
                _BOUNDARY_ITERATIONS,
            ),
        )
        report(
            f"{label} legacy",
            "Talea Contract",
            measure(
                lambda contract=contract, legacy_payload=legacy_payload: contract.from_python(legacy_payload),
                _BOUNDARY_ITERATIONS,
            ),
        )
        report(
            f"{label} manual",
            "equivalent manual",
            measure(
                lambda legacy_names=legacy_names, legacy_payload=legacy_payload: mapping_migrated(
                    legacy_payload, legacy_names
                ),
                _BOUNDARY_ITERATIONS,
            ),
        )
    migrated_contract = Contract(MigratedSixteen)
    report(
        "sixteen legacy JSON",
        "Talea Contract",
        measure(lambda: migrated_contract.from_json('{"old_15":1}'), _BOUNDARY_ITERATIONS),
    )
    report(
        "equal-value conflict",
        "Talea Contract",
        measure(
            lambda: capture(lambda: migrated_contract.from_python({"current": 1, "old_0": 1})),
            1_000,
        ),
    )
    lifecycle_contract = Contract(MigratedPostInitialized)
    report(
        "migrated post_init",
        "Talea Contract",
        measure(lambda: lifecycle_contract.from_python({"old": 1}), _BOUNDARY_ITERATIONS),
    )

    print("\nSuccessful boundary cost decomposition")
    report("raw dataclass constructor", "stdlib lifecycle", measure(lambda: OneField(1)))
    report(
        "resource state allocation",
        "required policy",
        measure(lambda: resource_state(DEFAULT_RESOURCE_POLICY)),
    )
    report(
        "retained boundary",
        "unlimited direct",
        measure(lambda: direct_boundary(external), _BOUNDARY_ITERATIONS),
    )
    report(
        "retained boundary",
        "finite direct",
        measure(
            lambda: direct_boundary(external, resource_state(DEFAULT_RESOURCE_POLICY)),
            _BOUNDARY_ITERATIONS,
        ),
    )
    OrdinarySpec.from_mapping(external)
    print(f"Contract public Python calls/op={python_calls(lambda: one_contract.from_python(external)):.1f}")
    print(f"Spec public Python calls/op={python_calls(lambda: OrdinarySpec.from_mapping(external)):.1f}")
    print(f"manual Python calls/op={python_calls(lambda: mapping_one(external)):.1f}")

    feature_cases: tuple[tuple[str, object, Mapping[str, object]], ...] = (
        ("one field", OneField, external),
        ("five fields", FiveFields, {f"field_{index}": index for index in range(5)}),
        ("ten fields", TenFields, {f"field_{index}": index for index in range(10)}),
        ("default", Defaulted, {}),
        ("factory", FactoryDefaulted, {}),
        ("kw_only", KeywordOnly, external),
        ("frozen slots", FrozenSlots, {"value": 1, "label": "one"}),
        (
            "nested",
            Nested,
            {
                "item": {f"field_{index}": index for index in range(5)},
                "values": [1, 2, 3],
            },
        ),
        ("mutable", OneField, external),
        ("post_init", PostInitialized, external),
        ("init_false", InitFalse, external),
        ("alias", Aliased, {"external": 1}),
        ("migration 1", MigratedOne, {"old_0": 1}),
        ("migration 4", MigratedFour, {"old_3": 1}),
        ("migration 16", MigratedSixteen, {"old_15": 1}),
        ("constraint", Constrained, external),
        ("generic", Page[int], {"items": [1, 2, 3]}),
        ("recursive", RecursiveBenchmarkNode, {"value": 1, "children": [{"value": 2}]}),
    )
    print("\nFeature-bearing construction")
    for label, annotation, payload in feature_cases:
        contract = Contract(annotation)
        contract.from_python(payload)
        report(
            f"{label} Mapping",
            "Talea Contract",
            measure(
                lambda contract=contract, payload=payload: contract.from_python(payload),
                _BOUNDARY_ITERATIONS,
            ),
        )
        feature_json = json.dumps(payload, separators=(",", ":"))
        contract.from_json(feature_json)
        report(
            f"{label} JSON",
            "Talea Contract",
            measure(
                lambda contract=contract, feature_json=feature_json: contract.from_json(feature_json),
                _BOUNDARY_ITERATIONS,
            ),
        )

    invalid_post_init = Contract(InvalidPostInit)
    resource_failure_policy = ResourcePolicy(max_nodes=1)
    failures: tuple[tuple[str, Operation], ...] = (
        ("missing", lambda: one_contract.from_python({})),
        ("unexpected", lambda: one_contract.from_python({"value": 1, "extra": 2})),
        ("wrong type", lambda: one_contract.from_python({"value": "1"})),
        (
            "nested location",
            lambda: nested_contract.from_python(
                {
                    "item": {
                        "field_0": 0,
                        "field_1": 1,
                        "field_2": "invalid",
                        "field_3": 3,
                        "field_4": 4,
                    },
                    "values": [],
                }
            ),
        ),
        ("invalid post_init", lambda: invalid_post_init.from_python(external)),
        (
            "resource limit",
            lambda: one_contract.from_python(external, policy=resource_failure_policy),
        ),
    )
    print("\nRepresentative failures")
    for label, operation in failures:
        report(label, "Talea Contract", measure(lambda operation=operation: capture(operation), 1_000))

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
    boundary_instructions = tuple(dis.get_instructions(direct_boundary))
    first_return = next(
        instruction.offset for instruction in boundary_instructions if instruction.opname == "RETURN_VALUE"
    )
    retained_state_loads = tuple(
        instruction.offset
        for instruction in boundary_instructions
        if instruction.opname == "LOAD_ATTR" and instruction.argval == "value"
    )
    retained, peak = retained_bytes()
    assert vars(one) == {"value": 1}
    assert retained_state_loads and all(offset > first_return for offset in retained_state_loads)
    print("\nGenerated code and memory")
    print(f"FiveFields strict instructions={instruction_count} direct_attributes={attributes}")
    print(
        f"OneField input instructions={len(boundary_instructions)} "
        f"fast_return={first_return} retained_state_loads={retained_state_loads}"
    )
    print(
        f"Contract shallow={sys.getsizeof(one_contract)} bytes "
        f"warmed artifacts={sys.getsizeof(one_contract._artifacts)} bytes "
        f"input function={sys.getsizeof(direct_boundary)} bytes "
        f"retained globals={sys.getsizeof(direct_boundary.__globals__)} bytes"
    )
    print(f"OneField instance shallow={sys.getsizeof(one)} bytes dict={sys.getsizeof(vars(one))} bytes")
    print(f"FrozenSlots instance shallow={sys.getsizeof(frozen)} bytes has_dict={hasattr(frozen, '__dict__')}")
    migration_schema = migrated_contract._artifacts.schema
    assert isinstance(migration_schema, DataclassSchema)
    migration_field = migration_schema.fields[0]
    migrated_input = migrated_contract._artifacts.python_input
    assert migrated_input is not None
    migrated_input = cast(FunctionType, migrated_input)
    assert "_alias_conflict" in migrated_input.__code__.co_names
    assert "_alias_conflict" not in direct_boundary.__code__.co_names
    print(
        "Migration retained shallow="
        f"field={sys.getsizeof(migration_field)} bytes "
        f"accepted={sys.getsizeof(migration_field.accepted_input_names)} bytes "
        "global_registry=False"
    )
    print(f"100 discarded generic dataclass Contracts retained={retained} bytes peak={peak} bytes")


@dataclass
class RecursiveBenchmarkNode:
    """Recursive dataclass cold-resolution workload."""

    value: int
    children: list[RecursiveBenchmarkNode] = field(default_factory=list)


if __name__ == "__main__":
    main()
