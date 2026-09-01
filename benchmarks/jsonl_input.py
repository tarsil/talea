"""Measure bounded JSON Lines framing, conversion, failures, and canaries."""

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
    Alias,
    Contract,
    Discriminator,
    Representation,
    ResourceLimitError,
    Spec,
    ValidationError,
)
from talea.contract import ItemPolicy
from talea.input.json import _decode_strict_json
from talea.jsonl import JsonlError, JsonlPolicy
from talea.settings import Settings

_REPEATS = 3
type Operation = Callable[[], object]


def measure(operation: Operation, iterations: int) -> tuple[float, float]:
    """Return minimum and median nanoseconds for one complete operation."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    values = [sample * 1_000_000_000 / iterations for sample in samples]
    return min(values), median(values)


def report(name: str, operation: Operation, iterations: int = 50) -> None:
    """Print one stable JSONL benchmark row."""

    minimum, middle = measure(operation, iterations)
    print(f"{name:45} min={minimum:12.1f} ns/op median={middle:12.1f} ns/op")


def consume(values: Iterable[object]) -> None:
    """Consume an operation without retaining outputs."""

    for _ in values:
        pass


def capture(operation: Operation, expected: type[BaseException]) -> BaseException:
    """Return one expected failure without rendering it."""

    try:
        operation()
    except expected as error:
        return error
    raise AssertionError("failure benchmark unexpectedly succeeded")


class FiveField(Spec):
    identifier: int
    symbol: str
    quantity: int
    price: int
    active: bool


@dataclass(slots=True)
class DataclassEvent:
    value: int


class Payload(TypedDict):
    value: int


class Identifier:
    __slots__ = ("value",)

    def __init__(self, value: int) -> None:
        self.value = value


def load_identifier(value: str) -> Identifier:
    """Convert one represented JSON string."""

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


class Migrated(Spec):
    value: Annotated[int, Alias("current", legacy=("old",))]


class SettingsCanary(Spec):
    value: int


def manual_jsonl(
    records: Iterable[str],
    contract: Contract[FiveField],
    *,
    on_jsonl_error: Callable[[int], None] | None = None,
    on_error: Callable[[int], None] | None = None,
) -> Iterator[FiveField]:
    """Apply equivalent framing, strict decode, conversion, and finite budgets."""

    total_bytes = 0
    invalid = 0
    for index, record in enumerate(records):
        if index >= 1_000_000:
            raise ResourceLimitError("items", 1_000_000, index + 1)
        size = len(record.encode("utf-8"))
        if size > 8 * 1024 * 1024:
            raise ResourceLimitError("jsonl_line_size", 8 * 1024 * 1024, size)
        total_bytes += size
        payload = record.removesuffix("\n").removesuffix("\r")
        try:
            if not payload or payload.startswith("\ufeff") or "\n" in payload:
                raise ValueError
            decoded = _decode_strict_json(record)
        except ValueError:
            if on_jsonl_error is None:
                raise
            invalid += 1
            if invalid > 100:
                raise ResourceLimitError("invalid_items", 100, invalid) from None
            on_jsonl_error(index + 1)
            continue
        try:
            yield contract.from_python(decoded)
        except ValidationError:
            if on_error is None:
                raise
            invalid += 1
            if invalid > 100:
                raise ResourceLimitError("invalid_items", 100, invalid) from None
            on_error(index)
    del total_bytes


def benchmark_record_scaling() -> None:
    """Measure text/bytes records and terminator shapes at required scales."""

    contract = Contract(int)
    for count, iterations in ((1, 2_000), (100, 100), (10_000, 2)):
        text = tuple(f"{index}\n" for index in range(count))
        binary = tuple(record.encode() for record in text)
        report(f"text primitive {count:,}", lambda text=text: consume(contract.iter_jsonl(text)), iterations)
        report(f"bytes primitive {count:,}", lambda binary=binary: consume(contract.iter_jsonl(binary)), iterations)

    lf = tuple(f"{index}\n" for index in range(100))
    crlf = tuple(f"{index}\r\n" for index in range(100))
    bare = tuple(str(index) for index in range(100))
    report("LF records 100", lambda: consume(contract.iter_jsonl(lf)))
    report("CRLF records 100", lambda: consume(contract.iter_jsonl(crlf)))
    report("unterminated records 100", lambda: consume(contract.iter_jsonl(bare)))


def benchmark_contract_domains() -> None:
    """Measure retained JSON conversion across supported contract domains."""

    five = tuple(
        f'{{"identifier":{index},"symbol":"TAL","quantity":1,"price":15,"active":true}}\n' for index in range(100)
    )
    report("five-field Spec 100", lambda: consume(Contract(FiveField).iter_jsonl(five)), 30)
    report(
        "dataclass 100",
        lambda: consume(Contract(DataclassEvent).iter_jsonl(tuple(f'{{"value":{i}}}' for i in range(100)))),
        30,
    )
    report(
        "TypedDict 100",
        lambda: consume(Contract[Payload](Payload).iter_jsonl(tuple(f'{{"value":{i}}}' for i in range(100)))),
        30,
    )
    report(
        "Representation 100",
        lambda: consume(Contract[Identifier](IdentifierValue).iter_jsonl(tuple(f'"{i}"' for i in range(100)))),
        30,
    )
    tagged = tuple(f'{{"kind":"created","value":{index}}}' for index in range(100))
    report("tagged union 100", lambda: consume(Contract[TaggedEvent](TaggedEvent).iter_jsonl(tagged)), 30)
    recursive = tuple(f'{{"value":{index},"children":[{{"value":1,"children":[]}}]}}' for index in range(100))
    report("recursive Spec 100", lambda: consume(Contract(Node).iter_jsonl(recursive)), 20)
    current = tuple(f'{{"current":{index}}}' for index in range(100))
    legacy = tuple(f'{{"old":{index}}}' for index in range(100))
    migrated = Contract(Migrated)
    report("current alias 100", lambda: consume(migrated.iter_jsonl(current)), 30)
    report("legacy alias 100", lambda: consume(migrated.iter_jsonl(legacy)), 30)


def benchmark_failures_and_continuation() -> None:
    """Measure malformed, validation, continuation, and resource boundaries."""

    contract = Contract(int)
    for name, position in (("first", 0), ("middle", 50), ("late", 99)):
        malformed = [f"{index}\n" for index in range(100)]
        malformed[position] = "{"
        report(
            f"malformed {name}",
            lambda malformed=malformed: capture(lambda: consume(contract.iter_jsonl(malformed)), JsonlError),
        )
        invalid = [f"{index}\n" for index in range(100)]
        invalid[position] = '"bad"'
        report(
            f"validation failure {name}",
            lambda invalid=invalid: capture(lambda: consume(contract.iter_jsonl(invalid)), ValidationError),
        )

    framing_sparse = tuple("{" if index % 25 == 0 else str(index) for index in range(100))
    validation_sparse = tuple('"bad"' if index % 25 == 0 else str(index) for index in range(100))
    mixed = tuple("{" if index % 50 == 0 else '"bad"' if index % 25 == 0 else str(index) for index in range(100))
    report(
        "framing continuation sparse",
        lambda: consume(contract.iter_jsonl(framing_sparse, on_jsonl_error=lambda _line, _error: None)),
    )
    report(
        "validation continuation sparse",
        lambda: consume(contract.iter_jsonl(validation_sparse, on_error=lambda _index, _error: None)),
    )
    report(
        "mixed continuation sparse",
        lambda: consume(
            contract.iter_jsonl(
                mixed,
                on_jsonl_error=lambda _line, _error: None,
                on_error=lambda _index, _error: None,
            )
        ),
    )
    report(
        "invalid-budget rejection",
        lambda: capture(
            lambda: consume(
                contract.iter_jsonl(
                    itertools.repeat("{"),
                    on_jsonl_error=lambda _line, _error: None,
                    item_policy=ItemPolicy(max_items=None, max_invalid_items=10),
                )
            ),
            ResourceLimitError,
        ),
    )
    report(
        "item-budget rejection",
        lambda: capture(
            lambda: consume(contract.iter_jsonl(itertools.repeat("1"), item_policy=ItemPolicy(max_items=100))),
            ResourceLimitError,
        ),
    )
    report(
        "line-byte rejection",
        lambda: capture(
            lambda: consume(contract.iter_jsonl(("123",), jsonl_policy=JsonlPolicy(max_line_bytes=2))),
            ResourceLimitError,
        ),
    )
    report(
        "total-byte rejection",
        lambda: capture(
            lambda: consume(contract.iter_jsonl(("1", "2"), jsonl_policy=JsonlPolicy(max_total_bytes=1))),
            ResourceLimitError,
        ),
    )


def benchmark_scaling_and_manual_comparator() -> None:
    """Measure line size, strict decode, and an equivalent handwritten loop."""

    for size, iterations in ((1_024, 200), (1_048_576, 3)):
        record = '"' + "x" * size + '"\n'
        report(
            f"one text line {size:,} payload bytes",
            lambda record=record: next(Contract(str).iter_jsonl((record,))),
            iterations,
        )

    record = '{"identifier":1,"symbol":"TAL","quantity":1,"price":15,"active":true}\n'
    records = (record,) * 100
    contract = Contract(FiveField)
    report("strict JSON decoder lower bound 100", lambda: [_decode_strict_json(item) for item in records])
    report("handwritten equivalent JSONL 100", lambda: consume(manual_jsonl(records, contract)))
    report("Talea retained JSONL 100", lambda: consume(contract.iter_jsonl(records)))


def benchmark_lifecycle_and_canaries() -> None:
    """Measure allocations, retention, concurrency, and frozen-owner canaries."""

    contract = Contract(int)
    records = tuple(f"{index}\n" for index in range(100))
    report("cold iterator creation", lambda: contract.iter_jsonl(records), 5_000)
    consume(contract.iter_jsonl(("0",)))
    report("warm retained Contract JSONL 100", lambda: consume(contract.iter_jsonl(records)))

    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    consume(contract.iter_jsonl(tuple(str(index) for index in range(10_000))))
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"discarded 10,000 JSONL results              retained={retained - before:8d} B peak={peak - before:8d} B")

    class Source:
        def __iter__(self) -> Iterator[str]:
            return iter(("1", "2"))

    source = Source()
    source_ref = weakref.ref(source)
    consume(contract.iter_jsonl(source))
    del source
    gc.collect()
    assert source_ref() is None

    def callback(_line: int, _error: JsonlError) -> None:
        pass

    callback_ref = weakref.ref(callback)
    iterator = contract.iter_jsonl(("1",), on_jsonl_error=callback)
    consume(iterator)
    del iterator, callback
    gc.collect()
    assert callback_ref() is None

    close_source = Source()
    close_ref = weakref.ref(close_source)
    wrapper = contract.iter_jsonl(close_source)
    assert isinstance(wrapper, Generator)
    next(wrapper)
    wrapper.close()
    del wrapper, close_source
    gc.collect()
    assert close_ref() is None

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(lambda start: list(contract.iter_jsonl((str(start),))), range(4)))
    assert results == ([0], [1], [2], [3])

    report("ordinary Contract.from_json canary", lambda: contract.from_json("1"), 5_000)
    report("ordinary Contract.iter_python canary", lambda: consume(contract.iter_python(range(100))))
    settings = Settings(SettingsCanary)
    report("Settings override canary", lambda: settings.load(overrides={"value": 1}), 500)


def main() -> None:
    """Run the permanent JSON Lines input benchmark inventory."""

    benchmark_record_scaling()
    benchmark_contract_domains()
    benchmark_failures_and_continuation()
    benchmark_scaling_and_manual_comparator()
    benchmark_lifecycle_and_canaries()


if __name__ == "__main__":
    main()
