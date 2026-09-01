"""Measure lazy retained Contract item validation and its zero-tax canaries."""

import gc
import itertools
import tracemalloc
import weakref
from collections.abc import Callable, Generator, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from statistics import median
from timeit import Timer
from typing import Annotated, Literal, TypedDict

from talea import (
    Contract,
    Discriminator,
    Representation,
    ResourceLimitError,
    Spec,
    ValidationError,
)
from talea.contract import ItemPolicy

_REPEATS = 3
type Operation = Callable[[], object]


def measure(operation: Operation, iterations: int) -> tuple[float, float]:
    """Return minimum and median nanoseconds for one complete consumption."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    values = [sample * 1_000_000_000 / iterations for sample in samples]
    return min(values), median(values)


def report(name: str, operation: Operation, iterations: int = 100) -> None:
    """Print one stable incremental benchmark row."""

    minimum, middle = measure(operation, iterations)
    print(f"{name:43} min={minimum:12.1f} ns/op median={middle:12.1f} ns/op")


class Event(Spec):
    """Structured benchmark item."""

    value: int


@dataclass(slots=True)
class DataclassEvent:
    """Stdlib dataclass benchmark item."""

    value: int


class Payload(TypedDict):
    """TypedDict benchmark item."""

    value: int


class Identifier:
    """Represented benchmark item."""

    __slots__ = ("value",)

    def __init__(self, value: int) -> None:
        self.value = value


def load_identifier(value: str) -> Identifier:
    """Convert one external identifier."""

    return Identifier(int(value))


type IdentifierValue = Annotated[Identifier, Representation(input=str, load=load_identifier)]


class Created(Spec):
    kind: Literal["created"]
    value: int


class Deleted(Spec):
    kind: Literal["deleted"]
    value: int


type TaggedEvent = Annotated[Created | Deleted, Discriminator("kind")]


class Node(Spec):
    value: int
    children: list["Node"]


def manual_strict(values: Iterable[object], contract: Contract[int]) -> Iterator[int]:
    """Apply the equivalent bounded fail-fast loop used as primary comparator."""

    for index, value in enumerate(values):
        if index >= 1_000_000:
            raise ResourceLimitError("items", 1_000_000, index + 1)
        try:
            yield contract.validate(value)
        except ValidationError as error:
            located = error.prefixed((index,))
            raise located from located.__cause__


def consume(iterator: Iterable[object]) -> None:
    """Consume values without retaining the outputs."""

    for _ in iterator:
        pass


def benchmark_external_domain[T](name: str, contract: Contract[T], factory: Callable[[int], object]) -> None:
    """Measure one exactly typed external item domain at two scales."""

    for count, iterations in ((100, 100), (10_000, 2)):
        source = tuple(factory(index) for index in range(count))
        report(
            f"external {name} {count:,}",
            lambda source=source: consume(contract.iter_python(source)),
            iterations,
        )


def benchmark_success_paths() -> None:
    """Measure strict and external retained execution across item domains."""

    integer = Contract(int)
    for count, iterations in ((1, 2_000), (100, 200), (10_000, 3)):
        strict = tuple(range(count))
        external = tuple(range(count))
        report(f"strict primitive {count:,}", lambda strict=strict: consume(integer.iter_validate(strict)), iterations)
        report(
            f"external primitive {count:,}",
            lambda external=external: consume(integer.iter_python(external)),
            iterations,
        )
    values = tuple(range(100))
    report("manual bounded strict primitive 100", lambda: consume(manual_strict(values, integer)), 200)

    benchmark_external_domain("Spec", Contract(Event), lambda index: {"value": index})
    benchmark_external_domain("dataclass", Contract(DataclassEvent), lambda index: {"value": index})
    benchmark_external_domain("TypedDict", Contract[Payload](Payload), lambda index: {"value": index})
    benchmark_external_domain("Representation", Contract[Identifier](IdentifierValue), str)
    benchmark_external_domain(
        "tagged union",
        Contract[TaggedEvent](TaggedEvent),
        lambda index: {"kind": "created" if index % 2 else "deleted", "value": index},
    )

    recursive = Contract(Node)
    nodes = tuple({"value": index, "children": [{"value": index + 1, "children": []}]} for index in range(100))
    report("external recursive Spec 100", lambda: consume(recursive.iter_python(nodes)), 50)


def capture(operation: Operation, expected: type[BaseException]) -> BaseException:
    """Return one expected failure without rendering it."""

    try:
        operation()
    except expected as error:
        return error
    raise AssertionError("failure benchmark unexpectedly succeeded")


def benchmark_failures_and_bounds() -> None:
    """Measure indexed failures, continuation, and terminal stream limits."""

    contract = Contract(int)
    for name, position in (("early", 0), ("middle", 50), ("late", 99)):
        values: list[object] = list(range(100))
        values[position] = "bad"
        report(
            f"fail-fast {name} item",
            lambda values=values: capture(lambda: consume(contract.iter_validate(values)), ValidationError),
        )

    sparse: tuple[object, ...] = tuple("bad" if index % 25 == 0 else index for index in range(100))
    dense: tuple[object, ...] = tuple("bad" if index % 2 == 0 else index for index in range(100))
    report("continuation sparse invalid", lambda: consume(contract.iter_validate(sparse, on_error=lambda _i, _e: None)))
    report("continuation dense invalid", lambda: consume(contract.iter_validate(dense, on_error=lambda _i, _e: None)))
    report(
        "record-limit rejection",
        lambda: capture(
            lambda: consume(contract.iter_validate(range(11), item_policy=ItemPolicy(max_items=10))),
            ResourceLimitError,
        ),
    )
    report(
        "invalid-record-limit rejection",
        lambda: capture(
            lambda: consume(
                contract.iter_validate(
                    itertools.repeat("bad"),
                    on_error=lambda _i, _e: None,
                    item_policy=ItemPolicy(max_items=None, max_invalid_items=10),
                )
            ),
            ResourceLimitError,
        ),
    )
    report(
        "infinite source caller stop", lambda: consume(itertools.islice(contract.iter_validate(itertools.count()), 100))
    )
    report(
        "infinite source policy stop",
        lambda: capture(
            lambda: consume(contract.iter_validate(itertools.count(), item_policy=ItemPolicy(max_items=100))),
            ResourceLimitError,
        ),
    )


def benchmark_lifecycle_and_canaries() -> None:
    """Measure creation, retention, concurrency, and unchanged ordinary paths."""

    contract = Contract(int)
    report("cold iterator creation", lambda: contract.iter_validate(range(100)), 5_000)
    report("warm retained iterator consumption", lambda: consume(contract.iter_validate(range(100))))

    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    consume(contract.iter_validate(range(10_000)))
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"discarded 10,000 results                     retained={retained - before:8d} B peak={peak - before:8d} B")

    class Source:
        def __iter__(self) -> Iterator[int]:
            return iter((1, 2))

    source = Source()
    source_ref = weakref.ref(source)
    consume(contract.iter_validate(source))
    del source
    gc.collect()
    assert source_ref() is None

    def callback(_index: int, _error: ValidationError) -> None:
        return None

    callback_ref = weakref.ref(callback)
    iterator = contract.iter_validate((1,), on_error=callback)
    consume(iterator)
    del iterator, callback
    gc.collect()
    assert callback_ref() is None

    close_source = Source()
    close_ref = weakref.ref(close_source)
    wrapper = contract.iter_validate(close_source)
    assert isinstance(wrapper, Generator)
    next(wrapper)
    wrapper.close()
    del wrapper, close_source
    gc.collect()
    assert close_ref() is None

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(lambda start: list(contract.iter_validate(range(start, start + 100))), range(4)))
    assert [result[0] for result in results] == [0, 1, 2, 3]

    class Canary(Spec):
        value: int

    canary = Canary(value=1)
    report("ordinary Contract.validate canary", lambda: contract.validate(1), 10_000)
    report("ordinary Contract.from_python canary", lambda: contract.from_python(1), 5_000)
    report("ordinary Spec construction canary", lambda: Canary(value=1), 5_000)
    report("ordinary Mapping canary", lambda: Canary.from_mapping({"value": 1}), 2_000)
    report("ordinary JSON canary", lambda: Canary.from_json('{"value":1}'), 2_000)
    report("ordinary serialization canary", canary.to_dict, 5_000)


def main() -> None:
    """Run the permanent incremental Contract benchmark inventory."""

    benchmark_success_paths()
    benchmark_failures_and_bounds()
    benchmark_lifecycle_and_canaries()


if __name__ == "__main__":
    main()
