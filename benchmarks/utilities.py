"""Measure Campaign 12 contracts, dynamic Specs, replacement, and introspection.

Rows separate definition/compilation from retained execution. Direct-owner
comparisons call the exact compiled artifact retained by the same Contract;
hand-written rows implement equivalent strict validation rather than omitting
semantics. Memory rows are explicitly shallow unless labelled as traced.
"""

import gc
import sys
import tracemalloc
from collections.abc import Callable
from copy import replace
from functools import partial
from statistics import median
from timeit import Timer
from typing import Annotated, Required, TypedDict, cast

from talea import Contract, Ge, Spec, check, create_spec
from talea.introspection import inspect_contract, inspect_spec
from talea.schema import resolve_annotation
from talea.validation import compile_validator

_REPEATS = 5
_HOT_ITERATIONS = 50_000
_BOUNDARY_ITERATIONS = 10_000
_COLD_ITERATIONS = 500

type Operation = Callable[[], object]


class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


def measure(operation: Operation, iterations: int = _HOT_ITERATIONS) -> Measurement:
    """Measure an operation across independent timer samples."""

    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def report(case: str, implementation: str, result: Measurement) -> None:
    """Print one stable benchmark row."""

    print(f"{case:34} {implementation:24} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


class User(Spec):
    """Nested Spec benchmark value."""

    name: str


class Payload(TypedDict):
    """TypedDict benchmark value."""

    id: Required[int]
    name: str


class Page[T](Spec):
    """Generic contract benchmark value."""

    items: list[T]


class Node(Spec):
    """Recursive contract benchmark value."""

    value: int
    children: list["Node"]


class Record(Spec):
    """Five-field replacement benchmark value."""

    field_0: int
    field_1: int
    field_2: int
    field_3: int
    field_4: int


class CheckedRange(Spec):
    """Whole-invariant replacement benchmark value."""

    start: int
    end: int

    @check("start", "end")
    def ordered(start: int, end: int) -> None:
        if start > end:
            raise ValueError


class MutableRecord(Spec):
    """Mutable-current-state replacement benchmark value."""

    name: str
    values: list[int]


class HandRecord:
    """Equivalent hand-written strict immutable replacement reference."""

    __slots__ = ("field_0", "field_1", "field_2", "field_3", "field_4")

    def __init__(self, field_0: int, field_1: int, field_2: int, field_3: int, field_4: int) -> None:
        values = (field_0, field_1, field_2, field_3, field_4)
        if any(type(value) is not int for value in values):
            raise TypeError
        for name, value in zip(self.__slots__, values, strict=True):
            object.__setattr__(self, name, value)

    def __setattr__(self, name: str, value: object) -> None:
        """Reject mutation after initialization."""

        raise AttributeError((name, value))

    def __replace__(self, /, **changes: object) -> "HandRecord":
        """Validate changed values and construct one immutable replacement."""

        unknown = changes.keys() - frozenset(self.__slots__)
        if unknown:
            raise TypeError
        values = {name: changes.get(name, getattr(self, name)) for name in self.__slots__}
        if any(type(value) is not int for value in values.values()):
            raise TypeError
        return HandRecord(**values)  # ty: ignore[invalid-argument-type]


def strict_int(value: object) -> int:
    """Equivalent direct strict integer check."""

    if type(value) is not int:
        raise TypeError
    return value


def strict_int_list(value: object) -> list[int]:
    """Equivalent direct strict list-of-integers check."""

    if type(value) is not list or any(type(item) is not int for item in value):
        raise TypeError
    return cast(list[int], value)


def make_static(count: int) -> type[Spec]:
    """Declare a Spec directly through its normal metaclass namespace."""

    return type(
        f"Static{count}",
        (Spec,),
        {"__annotations__": {f"field_{index}": int for index in range(count)}},
    )


def make_dynamic(count: int) -> type[Spec]:
    """Declare an equivalent Spec through the public dynamic API."""

    return create_spec("Dynamic", {f"field_{index}": int for index in range(count)})


def values(count: int) -> dict[str, object]:
    """Return deterministic valid keyword data."""

    return {f"field_{index}": index for index in range(count)}


def benchmark_contracts() -> None:
    """Measure representative retained strict Contract execution."""

    integer = Contract[int](int)
    integers = Contract[list[int]](list[int])
    positive = Contract[Annotated[int, Ge(0)]](Annotated[int, Ge(0)])
    user = User(name="Ada")
    users = [user]
    user_contract = Contract[User](User)
    users_contract = Contract[list[User]](list[User])
    payload_contract = Contract[Payload](Payload)
    page = Page[User](items=users)
    page_contract = Contract[Page[User]](Page[User])
    node = Node(value=1, children=[])
    node_contract = Contract[Node](Node)
    direct_integer = compile_validator(resolve_annotation(int))
    direct_integers = compile_validator(resolve_annotation(list[int]))

    cases: tuple[tuple[str, Operation], ...] = (
        ("Contract(int)", partial(integer.validate, 1)),
        ("Contract(list[int])", partial(integers.validate, [1, 2, 3])),
        ("Contract(Annotated)", partial(positive.validate, 1)),
        ("Contract(User)", partial(user_contract.validate, user)),
        ("Contract(list[User])", partial(users_contract.validate, users)),
        ("Contract(TypedDict)", partial(payload_contract.validate, {"id": 1, "name": "Ada"})),
        ("Contract(Page[User])", partial(page_contract.validate, page)),
        ("Contract(recursive Node)", partial(node_contract.validate, node)),
    )
    for name, operation in cases:
        report(name, "public retained", measure(operation))

    report("int validate", "direct compiler owner", measure(partial(direct_integer, 1)))
    report("int validate", "hand-written", measure(partial(strict_int, 1)))
    report(
        "list[int] validate",
        "direct compiler owner",
        measure(partial(direct_integers, [1, 2, 3])),
    )
    report("list[int] validate", "hand-written", measure(partial(strict_int_list, [1, 2, 3])))


def benchmark_contract_boundaries() -> None:
    """Measure cold/warm input, JSON, and output independently."""

    contract = Contract[list[User]](list[User])
    external = [{"name": "Ada"}]
    users = [User(name="Ada")]
    contract.from_python(external)
    contract.from_json('[{"name":"Ada"}]')
    contract.to_python(users)
    contract.to_json(users)

    report("Contract construction", "cold resolve/validator", measure(lambda: Contract(list[int]), _COLD_ITERATIONS))
    report(
        "schema resolve + validator",
        "direct compiler owner",
        measure(lambda: compile_validator(resolve_annotation(list[int])), _COLD_ITERATIONS),
    )
    report(
        "construction + first validate",
        "eager strict artifact",
        measure(lambda: Contract(list[int]).validate([1]), _COLD_ITERATIONS),
    )
    report(
        "construction + first JSON",
        "lazy input artifact",
        measure(lambda: Contract(list[int]).from_json("[1]"), _COLD_ITERATIONS),
    )
    report(
        "construction + first output",
        "lazy output artifact",
        measure(lambda: Contract(list[int]).to_python([1]), _COLD_ITERATIONS),
    )
    report("external Python input", "public warm", measure(partial(contract.from_python, external)))
    report(
        "external Python input",
        "direct artifact",
        measure(partial(contract._artifacts.input_for("mapping"), external)),
    )
    report("JSON input", "public warm", measure(partial(contract.from_json, '[{"name":"Ada"}]'), _BOUNDARY_ITERATIONS))
    report("Python output", "public warm", measure(partial(contract.to_python, users), _BOUNDARY_ITERATIONS))
    report("JSON output", "public warm", measure(partial(contract.to_json, users), _BOUNDARY_ITERATIONS))


def benchmark_dynamic_specs() -> None:
    """Measure definition separately from equivalent retained runtime paths."""

    for count in (1, 5, 10):
        report(f"declare {count} fields", "direct namespace", measure(partial(make_static, count), _COLD_ITERATIONS))
        report(f"declare {count} fields", "create_spec", measure(partial(make_dynamic, count), _COLD_ITERATIONS))
        static = make_static(count)
        dynamic = make_dynamic(count)
        data = values(count)
        report(f"construct {count} fields", "static Spec", measure(partial(static, **data)))
        report(f"construct {count} fields", "dynamic Spec", measure(partial(dynamic, **data)))

    static = make_static(5)
    dynamic = make_dynamic(5)
    data = values(5)
    encoded = '{"field_0":0,"field_1":1,"field_2":2,"field_3":3,"field_4":4}'
    static_instance = static(**data)
    dynamic_instance = dynamic(**data)
    static_instance.to_dict()
    static_instance.to_json()
    dynamic_instance.to_dict()
    dynamic_instance.to_json()
    report("from_mapping 5 fields", "static Spec", measure(partial(static.from_mapping, data)))
    report("from_mapping 5 fields", "dynamic Spec", measure(partial(dynamic.from_mapping, data)))
    report("from_json 5 fields", "static Spec", measure(partial(static.from_json, encoded), _BOUNDARY_ITERATIONS))
    report("from_json 5 fields", "dynamic Spec", measure(partial(dynamic.from_json, encoded), _BOUNDARY_ITERATIONS))
    report("to_dict 5 fields", "static Spec", measure(static_instance.to_dict))
    report("to_dict 5 fields", "dynamic Spec", measure(dynamic_instance.to_dict))
    report("to_json 5 fields", "static Spec", measure(static_instance.to_json, _BOUNDARY_ITERATIONS))
    report("to_json 5 fields", "dynamic Spec", measure(dynamic_instance.to_json, _BOUNDARY_ITERATIONS))


def benchmark_replacement() -> None:
    """Measure canonical replacement against equivalent hand-written code."""

    record = Record(field_0=0, field_1=1, field_2=2, field_3=3, field_4=4)
    hand = HandRecord(0, 1, 2, 3, 4)
    checked = CheckedRange(start=1, end=5)
    mutable = MutableRecord(name="one", values=[1, 2, 3])
    replace(record, field_0=1)
    replace(checked, start=2)
    replace(mutable, name="two")

    report("replace one field", "Talea copy.replace", measure(partial(replace, record, field_0=5)))
    report("replace one field", "hand-written", measure(partial(replace, hand, field_0=5)))
    report(
        "replace several fields",
        "Talea copy.replace",
        measure(partial(replace, record, field_0=5, field_2=7, field_4=9)),
    )
    report("replace whole check", "Talea copy.replace", measure(partial(replace, checked, start=2)))
    report("replace mutable state", "Talea copy.replace", measure(partial(replace, mutable, name="two")))


def released_contract_bytes(count: int = 1_000) -> int:
    """Measure net traced memory after short-lived Contracts are released."""

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for _ in range(count):
        Contract(list[int]).to_python([1])
    gc.collect()
    after = tracemalloc.take_snapshot()
    retained = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    tracemalloc.stop()
    return retained


def benchmark_introspection_and_memory() -> None:
    """Measure retained views and shallow utility ownership."""

    static = make_static(5)
    dynamic = make_dynamic(5)
    contract = Contract[list[int]](list[int])
    cold_contract_bytes = sys.getsizeof(contract) + sys.getsizeof(contract._artifacts)
    contract.from_python([1])
    contract.from_json("[1]")
    contract.to_python([1])
    contract.to_json([1])
    artifacts = contract._artifacts
    warm_contract_bytes = cold_contract_bytes + sum(
        sys.getsizeof(artifact)
        for artifact in (
            artifacts.python_input,
            artifacts.json_input,
            artifacts.python_output,
            artifacts.json_output,
        )
    )
    info = inspect_spec(dynamic)
    report(
        "inspect_spec first projection", "fresh class", measure(lambda: inspect_spec(make_static(5)), _COLD_ITERATIONS)
    )
    report("inspect_spec retained view", "cached", measure(partial(inspect_spec, dynamic)))
    report("inspect_contract projection", "immutable view", measure(partial(inspect_contract, contract)))
    print(f"Contract shallow cold ownership={cold_contract_bytes} B warm ownership={warm_contract_bytes} B")
    print(
        f"class/instance shallow static={sys.getsizeof(static)} B/{sys.getsizeof(static(**values(5)))} B "
        f"dynamic={sys.getsizeof(dynamic)} B/{sys.getsizeof(dynamic(**values(5)))} B"
    )
    print(f"SpecInfo shallow={sys.getsizeof(info)} B fields_tuple={sys.getsizeof(info.fields)} B")
    print(f"released warmed Contracts (1000) retained={released_contract_bytes()} bytes")


def main() -> None:
    """Run all Campaign 12 utility and zero-tax measurements."""

    print("Contract retained execution")
    benchmark_contracts()
    print("\nContract cold/warm boundaries")
    benchmark_contract_boundaries()
    print("\nDynamic Spec declaration and execution")
    benchmark_dynamic_specs()
    print("\nImmutable replacement")
    benchmark_replacement()
    print("\nIntrospection and memory")
    benchmark_introspection_and_memory()


if __name__ == "__main__":
    main()
