"""Measure deterministic JSON Schema projection without runtime feature tax."""

import gc
import json
import sys
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from functools import reduce
from operator import or_
from statistics import median
from timeit import Timer
from typing import Annotated, Literal, NotRequired, TypedDict

from talea import Alias, Contract, Discriminator, Spec, create_spec, derive_spec

_REPEATS = 5
_COLD_ITERATIONS = 500
_GRAPH_ITERATIONS = 100

type Operation = Callable[[], object]


class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


def measure(operation: Operation, iterations: int = _COLD_ITERATIONS) -> Measurement:
    """Measure independent full projection operations."""

    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def report(case: str, result: Measurement) -> None:
    """Print one stable benchmark row."""

    print(f"{case:34} min={result.minimum:12.1f} ns/op median={result.median:12.1f} ns/op")


class PrimitiveFive(Spec):
    """Representative five-field object schema."""

    field_0: int
    field_1: str
    field_2: bool
    field_3: float
    field_4: bytes


Fifty = create_spec("Fifty", {f"field_{index}": int for index in range(50)})


class NestedLeaf(Spec):
    """Nested graph leaf."""

    value: int


class NestedRoot(Spec):
    """Nested graph root with reusable definitions."""

    primary: NestedLeaf
    values: list[NestedLeaf]
    lookup: dict[str, NestedLeaf]


class RecursiveSpec(Spec):
    """Recursive Spec benchmark graph."""

    value: int
    children: list[RecursiveSpec]


type RecursiveAlias = int | list[RecursiveAlias]


class RecursiveTypedDict(TypedDict):
    """Recursive TypedDict benchmark graph."""

    value: int
    child: NotRequired[RecursiveTypedDict]


class Page[T](Spec):
    """Generic specialization benchmark."""

    items: list[T]


Partial = derive_spec(Fifty, partial=True, name="FiftyPatch")


class MigratedOne(Spec):
    """One field with one historical input name."""

    value: Annotated[int, Alias("valueNow", legacy=("valueOld",))]


class MigratedFour(Spec):
    """One field with four historical input names."""

    value: Annotated[int, Alias("valueNow", legacy=("old0", "old1", "old2", "old3"))]


class MigratedSixteen(Spec):
    """One field with sixteen historical input names."""

    value: Annotated[
        int,
        Alias("valueNow", legacy=tuple(f"old{index}" for index in range(16))),
    ]


MigratedTen = create_spec(
    "MigratedTen",
    {f"field_{index}": Annotated[int, Alias(f"fieldNow{index}", legacy=(f"fieldOld{index}",))] for index in range(10)},
)
MigratedFifty = create_spec(
    "MigratedFifty",
    {f"field_{index}": Annotated[int, Alias(f"fieldNow{index}", legacy=(f"fieldOld{index}",))] for index in range(50)},
)
MigratedPartial = derive_spec(MigratedFifty, partial=True, name="MigratedFiftyPatch")


@dataclass
class MigratedRecord:
    """Representative dataclass migration projection."""

    value: Annotated[int, Alias("valueNow", legacy=("valueOld",))]


class MigratedTagA(Spec):
    """First migrated-discriminator benchmark branch."""

    kind: Annotated[Literal["a"], Alias("eventType", legacy=("type",))]


class MigratedTagB(Spec):
    """Second migrated-discriminator benchmark branch."""

    kind: Annotated[Literal["b"], Alias("eventType", legacy=("type",))]


type MigratedTagged = Annotated[MigratedTagA | MigratedTagB, Discriminator("kind")]


class MigratedPage[T](Spec):
    """Concrete generic with a migrated field."""

    items: Annotated[list[T], Alias("itemsNow", legacy=("itemsOld",))]


class MigratedRecursive(Spec):
    """Recursive Spec with migration alternatives and one definition identity."""

    value: Annotated[int, Alias("valueNow", legacy=("valueOld",))]
    children: list[MigratedRecursive]


def tagged_contract(branch_count: int) -> Contract[object]:
    """Return one retained tagged union with ``branch_count`` definitions."""

    branches = tuple(
        create_spec(
            f"Tagged{branch_count}_{index}",
            {"kind": Literal[f"tag-{index}"], "value": int},  # ty: ignore[invalid-type-form]
        )
        for index in range(branch_count)
    )
    union = reduce(or_, branches)
    annotation = Annotated[union, Discriminator("kind")]  # ty: ignore[invalid-type-form]
    return Contract[object](annotation)


TAGGED = {count: tagged_contract(count) for count in (2, 8, 32)}

_GRAPH_CHILDREN = tuple(create_spec(f"GraphNode{index}", {"value": int}) for index in range(99))
HundredDefinitions = create_spec(
    "HundredDefinitions",
    {f"node_{index}": child for index, child in enumerate(_GRAPH_CHILDREN)},
)


def schema_size(operation: Operation) -> int:
    """Return compact deterministic JSON bytes for one projected document."""

    return len(json.dumps(operation(), separators=(",", ":"), ensure_ascii=False).encode())


def benchmark_projection() -> None:
    """Measure representative cold, repeated, recursive, and scaling cases."""

    primitive = Contract(int)
    report("primitive Contract cold", measure(lambda: Contract(int).json_schema()))
    report("primitive Contract repeated", measure(primitive.json_schema))
    report("five-field Spec", measure(PrimitiveFive.json_schema))
    report("50-field Spec", measure(Fifty.json_schema, _GRAPH_ITERATIONS))
    report("nested graph", measure(NestedRoot.json_schema))
    report("recursive Spec", measure(RecursiveSpec.json_schema))
    report("recursive alias", measure(Contract(RecursiveAlias).json_schema))
    report("recursive TypedDict", measure(Contract(RecursiveTypedDict).json_schema))
    report("generic Page[int]", measure(Page[int].json_schema))
    report("partial 50-field Spec", measure(Partial.json_schema, _GRAPH_ITERATIONS))
    report("migrated 1 legacy", measure(MigratedOne.json_schema))
    report("migrated 4 legacy", measure(MigratedFour.json_schema))
    report("migrated 16 legacy", measure(MigratedSixteen.json_schema))
    report("migrated 10 fields", measure(MigratedTen.json_schema, _GRAPH_ITERATIONS))
    report("migrated 50 fields", measure(MigratedFifty.json_schema, _GRAPH_ITERATIONS))
    report("migrated partial 50", measure(MigratedPartial.json_schema, _GRAPH_ITERATIONS))
    report("migrated dataclass", measure(Contract(MigratedRecord).json_schema))
    report("migrated tagged", measure(Contract(MigratedTagged).openapi_schema))
    report("migrated generic", measure(MigratedPage[int].json_schema))
    report("migrated recursive", measure(MigratedRecursive.json_schema))
    for count, contract in TAGGED.items():
        report(f"tagged {count} branches", measure(contract.openapi_schema, _GRAPH_ITERATIONS))
    report("100-definition graph", measure(HundredDefinitions.json_schema, 20))


def benchmark_sizes() -> None:
    """Report serialized document sizes to detect definition duplication."""

    cases: tuple[tuple[str, Operation], ...] = (
        ("primitive", Contract(int).json_schema),
        ("five-field", PrimitiveFive.json_schema),
        ("50-field", Fifty.json_schema),
        ("recursive Spec", RecursiveSpec.json_schema),
        ("migrated 1 legacy", MigratedOne.json_schema),
        ("migrated 4 legacy", MigratedFour.json_schema),
        ("migrated 16 legacy", MigratedSixteen.json_schema),
        ("migrated 10 fields", MigratedTen.json_schema),
        ("migrated 50 fields", MigratedFifty.json_schema),
        ("migrated partial 50", MigratedPartial.json_schema),
        ("migrated dataclass", Contract(MigratedRecord).json_schema),
        ("migrated tagged", Contract(MigratedTagged).openapi_schema),
        ("migrated generic", MigratedPage[int].json_schema),
        ("migrated recursive", MigratedRecursive.json_schema),
        ("tagged 32", TAGGED[32].openapi_schema),
        ("100 definitions", HundredDefinitions.json_schema),
    )
    for name, operation in cases:
        print(f"{name:34} size={schema_size(operation):8} bytes")


def projection_retention(count: int = 1_000) -> tuple[int, int]:
    """Return net and peak traced bytes after discarded fresh projections."""

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for _ in range(count):
        PrimitiveFive.json_schema()
    gc.collect()
    after = tracemalloc.take_snapshot()
    retained = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return retained, peak


def benchmark_memory() -> None:
    """Report unchanged shallow owners and discarded projection retention."""

    retained, peak = projection_retention()
    contract = Contract(int)
    print(
        f"shallow Spec class={sys.getsizeof(PrimitiveFive)} B "
        f"instance={sys.getsizeof(PrimitiveFive(field_0=1, field_1='x', field_2=True, field_3=1.0, field_4=b'x'))} B "
        f"Contract={sys.getsizeof(contract)} B"
    )
    print(f"1000 discarded projections retained={retained} bytes peak={peak} bytes")


def main() -> None:
    """Run Campaign 17 schema projection benchmarks."""

    print("JSON Schema projection")
    benchmark_projection()
    print("\nSerialized schema size")
    benchmark_sizes()
    print("\nProjection memory")
    benchmark_memory()


if __name__ == "__main__":
    main()
