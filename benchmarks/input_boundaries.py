"""Measure Campaign 9 Mapping and JSON input boundaries independently."""

import gc
import importlib
import sys
import tracemalloc
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from statistics import median
from timeit import Timer
from typing import Annotated, cast
from uuid import UUID

from talea import Ge, Spec, ValidationError, check, field, transform
from talea.input.json import _default_loads

_REPEATS = 7
_SUCCESS_ITERATIONS = 50_000
_FAILURE_ITERATIONS = 10_000
_DECLARATION_ITERATIONS = 500
_ALLOCATION_SAMPLES = 500

type Operation = Callable[[], object]


@dataclass(frozen=True, slots=True)
class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    minimum: float
    median: float


@dataclass(frozen=True, slots=True)
class AllocationMeasurement:
    """Retain minimum steady-state traced-memory deltas."""

    retained: int
    peak: int


def measure(operation: Operation, iterations: int) -> Measurement:
    """Measure one warmed operation across independent samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def measure_allocations(operation: Operation) -> AllocationMeasurement:
    """Measure retained and peak traced bytes for one warmed operation."""

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


def print_measurement(case: str, implementation: str, result: Measurement) -> None:
    """Print one stable timing row."""

    print(f"{case:36} {implementation:18} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def capture(operation: Operation) -> ValidationError:
    """Return the expected Talea failure without measuring rendering."""

    try:
        operation()
    except ValidationError as error:
        return error
    raise AssertionError("failure benchmark succeeded")


def immutable(instance: object, name: str, value: object) -> None:
    """Reject assignment on hand-written comparison values."""

    raise AttributeError("instances are immutable")


def names(count: int) -> tuple[str, ...]:
    """Return deterministic scaling field names."""

    return tuple(f"field_{index}" for index in range(count))


def values(count: int) -> dict[str, object]:
    """Return valid scaling input."""

    return {name: index for index, name in enumerate(names(count))}


def make_spec(count: int) -> type[Spec]:
    """Declare a strict integer Spec for scaling measurements."""

    return type(f"Input{count}", (Spec,), {"__annotations__": dict.fromkeys(names(count), int)})


def make_hand_boundary(count: int) -> Callable[[Mapping[str, object]], object]:
    """Compile equivalent direct Mapping checks and immutable slot writes."""

    field_names = names(count)
    cls = type(f"HandInput{count}", (), {"__slots__": field_names, "__setattr__": immutable})
    lines = [
        "def construct(data):",
        "    if not isinstance(data, Mapping): raise TypeError",
        f"    if len(data) != {count}: raise TypeError",
    ]
    namespace: dict[str, object] = {"Mapping": Mapping, "allocator": object.__new__, "cls": cls}
    for index, name in enumerate(field_names):
        lines.extend((f"    {name} = data[{name!r}]", f"    if type({name}) is not int: raise TypeError"))
        namespace[f"slot_{index}"] = vars(cls)[name].__set__
    lines.append("    instance = allocator(cls)")
    for index, name in enumerate(field_names):
        lines.append(f"    slot_{index}(instance, {name})")
    lines.append("    return instance")
    exec(compile("\n".join(lines), "<hand Mapping boundary>", "exec"), namespace)
    return cast(Callable[[Mapping[str, object]], object], namespace["construct"])


class Address(Spec):
    """Nested Mapping and JSON benchmark value."""

    city: str
    postcode: str


class Nested(Spec):
    """Nested object boundary benchmark."""

    identifier: int
    address: Address


class Container(Spec):
    """Container conversion benchmark."""

    values: list[int]
    pair: tuple[int, ...]


class Standard(Spec):
    """Standard-library conversion benchmark."""

    identifier: UUID


class Hooked(Spec):
    """Transform and check boundary benchmark."""

    value: Annotated[int, Ge(0)]

    @transform("value")
    def parse(value: object) -> object:
        return int(value) if isinstance(value, str) else value

    @check("value")
    def bounded(value: int) -> None:
        if value > 100:
            raise ValueError


class Defaulted(Spec):
    """Static-default boundary benchmark."""

    value: int = 1


class FactoryDefault(Spec):
    """Factory-default boundary benchmark."""

    values: list[int] = field(default_factory=list)


class Aggregate(Spec):
    """Independent failure aggregation benchmark."""

    identifier: int
    name: str
    age: Annotated[int, Ge(18)]


class JsonFive(Spec):
    """Representative five-field JSON payload."""

    identifier: int
    name: str
    active: bool
    scores: list[int]
    request_id: UUID


class HandAddress:
    """Hand-written immutable nested value used by boundary comparisons."""

    __slots__ = ("city", "postcode")
    __setattr__ = immutable


class HandNested:
    """Hand-written immutable parent used by boundary comparisons."""

    __slots__ = ("identifier", "address")
    __setattr__ = immutable


class HandContainer:
    """Hand-written immutable container value used by boundary comparisons."""

    __slots__ = ("values", "pair")
    __setattr__ = immutable


class HandSingle:
    """Hand-written immutable one-field value used by boundary comparisons."""

    __slots__ = ("value",)
    __setattr__ = immutable


_HAND_ADDRESS_CITY = vars(HandAddress)["city"].__set__
_HAND_ADDRESS_POSTCODE = vars(HandAddress)["postcode"].__set__
_HAND_NESTED_IDENTIFIER = vars(HandNested)["identifier"].__set__
_HAND_NESTED_ADDRESS = vars(HandNested)["address"].__set__
_HAND_CONTAINER_VALUES = vars(HandContainer)["values"].__set__
_HAND_CONTAINER_PAIR = vars(HandContainer)["pair"].__set__
_HAND_SINGLE_VALUE = vars(HandSingle)["value"].__set__


def hand_address(data: Mapping[str, object]) -> HandAddress:
    """Construct the valid nested comparison value with direct strict checks."""

    if not isinstance(data, Mapping) or len(data) != 2:
        raise TypeError
    city = data["city"]
    postcode = data["postcode"]
    if type(city) is not str or type(postcode) is not str:
        raise TypeError
    instance = object.__new__(HandAddress)
    _HAND_ADDRESS_CITY(instance, city)
    _HAND_ADDRESS_POSTCODE(instance, postcode)
    return instance


def hand_nested(data: Mapping[str, object]) -> HandNested:
    """Construct the valid nested comparison value with direct strict checks."""

    if not isinstance(data, Mapping) or len(data) != 2:
        raise TypeError
    identifier = data["identifier"]
    address = data["address"]
    if type(identifier) is not int or not isinstance(address, Mapping):
        raise TypeError
    nested = hand_address(address)
    instance = object.__new__(HandNested)
    _HAND_NESTED_IDENTIFIER(instance, identifier)
    _HAND_NESTED_ADDRESS(instance, nested)
    return instance


def hand_container(data: Mapping[str, object]) -> HandContainer:
    """Construct the valid container comparison value with direct strict checks."""

    if not isinstance(data, Mapping) or len(data) != 2:
        raise TypeError
    sequence = data["values"]
    pair = data["pair"]
    if (
        type(sequence) is not list
        or any(type(item) is not int for item in sequence)
        or type(pair) is not tuple
        or any(type(item) is not int for item in pair)
    ):
        raise TypeError
    instance = object.__new__(HandContainer)
    _HAND_CONTAINER_VALUES(instance, sequence)
    _HAND_CONTAINER_PAIR(instance, pair)
    return instance


def hand_standard(data: Mapping[str, object]) -> HandSingle:
    """Construct the valid UUID comparison value with direct strict checks."""

    if not isinstance(data, Mapping) or len(data) != 1:
        raise TypeError
    value = data["identifier"]
    if type(value) is not UUID:
        raise TypeError
    instance = object.__new__(HandSingle)
    _HAND_SINGLE_VALUE(instance, value)
    return instance


def hand_hooked(data: Mapping[str, object]) -> HandSingle:
    """Construct the valid transform-and-check comparison value."""

    if not isinstance(data, Mapping) or len(data) != 1:
        raise TypeError
    value = data["value"]
    if isinstance(value, str):
        value = int(value)
    if type(value) is not int or value < 0 or value > 100:
        raise ValueError
    instance = object.__new__(HandSingle)
    _HAND_SINGLE_VALUE(instance, value)
    return instance


def hand_static_default(data: Mapping[str, object]) -> HandSingle:
    """Construct the valid static-default comparison value."""

    if not isinstance(data, Mapping) or data:
        raise TypeError
    instance = object.__new__(HandSingle)
    _HAND_SINGLE_VALUE(instance, 1)
    return instance


def hand_factory_default(data: Mapping[str, object]) -> HandSingle:
    """Construct the valid factory-default comparison value."""

    if not isinstance(data, Mapping) or data:
        raise TypeError
    instance = object.__new__(HandSingle)
    _HAND_SINGLE_VALUE(instance, [])
    return instance


MAPPING_NESTED: dict[str, object] = {
    "identifier": 1,
    "address": {"city": "Zurich", "postcode": "8001"},
}
JSON_FIVE = (
    '{"identifier":1,"name":"Ada","active":true,"scores":[1,2,3],"request_id":"00000000-0000-0000-0000-000000000000"}'
)
JSON_NESTED = '{"identifier":1,"address":{"city":"Zurich","postcode":"8001"}}'
JSON_CONTAINER = '{"values":[1,2,3],"pair":[1,2,3]}'
JSON_STANDARD = '{"identifier":"00000000-0000-0000-0000-000000000000"}'


def benchmark_mapping() -> None:
    """Measure Mapping success, failure, allocation, and compilation costs."""

    print(f"Mapping boundary ({_REPEATS} samples x {_SUCCESS_ITERATIONS:,} successes)")
    for count in (1, 5, 10):
        spec = make_spec(count)
        hand = make_hand_boundary(count)
        data = values(count)
        print_measurement(
            f"Mapping -> Spec {count} fields",
            "talea",
            measure(partial(spec.from_mapping, data), _SUCCESS_ITERATIONS),
        )
        print_measurement(
            f"Mapping -> Spec {count} fields",
            "handwritten",
            measure(partial(hand, data), _SUCCESS_ITERATIONS),
        )
    container_data: dict[str, object] = {"values": [1, 2], "pair": (1, 2)}
    standard_data: dict[str, object] = {"identifier": UUID(int=0)}
    hooked_data: dict[str, object] = {"value": "2"}
    cases: tuple[tuple[str, Operation, Operation], ...] = (
        ("nested Mapping", partial(Nested.from_mapping, MAPPING_NESTED), partial(hand_nested, MAPPING_NESTED)),
        (
            "list/container input",
            partial(Container.from_mapping, container_data),
            partial(hand_container, container_data),
        ),
        (
            "standard-library input",
            partial(Standard.from_mapping, standard_data),
            partial(hand_standard, standard_data),
        ),
        ("transform + check", partial(Hooked.from_mapping, hooked_data), partial(hand_hooked, hooked_data)),
        ("static default", partial(Defaulted.from_mapping, {}), partial(hand_static_default, {})),
        ("factory default", partial(FactoryDefault.from_mapping, {}), partial(hand_factory_default, {})),
    )
    for name, operation, hand_operation in cases:
        print_measurement(name, "talea", measure(operation, _SUCCESS_ITERATIONS))
        print_measurement(name, "handwritten", measure(hand_operation, _SUCCESS_ITERATIONS))

    failures: tuple[tuple[str, Operation], ...] = (
        (
            "one field failure",
            partial(capture, lambda: Aggregate.from_mapping({"identifier": 1, "name": "Ada", "age": 1})),
        ),
        (
            "aggregated failure",
            partial(
                capture,
                lambda: Aggregate.from_mapping({"identifier": "bad", "age": 1, "extra": True}),
            ),
        ),
    )
    print(f"Mapping failure ({_REPEATS} samples x {_FAILURE_ITERATIONS:,} failures)")
    for name, operation in failures:
        print_measurement(name, "talea", measure(operation, _FAILURE_ITERATIONS))

    print(f"Mapping allocations ({_ALLOCATION_SAMPLES:,} warmed operations)")
    allocation_cases: tuple[tuple[str, Operation], ...] = (
        ("successful Mapping", partial(make_spec(5).from_mapping, values(5))),
        ("nested Mapping", partial(Nested.from_mapping, MAPPING_NESTED)),
        ("aggregated failure", failures[1][1]),
    )
    for name, operation in allocation_cases:
        result = measure_allocations(operation)
        print(f"{name:36} retained={result.retained:5} B peak={result.peak:5} B")

    print(f"Boundary declaration ({_REPEATS} samples x {_DECLARATION_ITERATIONS:,} declarations)")
    for count in (1, 5, 10):
        print_measurement(
            f"declare {count} fields",
            "talea full declaration",
            measure(partial(make_spec, count), _DECLARATION_ITERATIONS),
        )


def benchmark_json() -> None:
    """Measure decoding, compiled boundary work, full JSON, and allocations."""

    artifacts = vars(JsonFive)["__talea_artifacts__"]
    decoded_five = _default_loads(JSON_FIVE)
    print(f"JSON boundary ({_REPEATS} samples x {_SUCCESS_ITERATIONS:,} successes)")
    print_measurement(
        "five-field decode only", "stdlib strict", measure(partial(_default_loads, JSON_FIVE), _SUCCESS_ITERATIONS)
    )
    print_measurement(
        "five-field boundary only", "talea", measure(partial(artifacts.json_input, decoded_five), _SUCCESS_ITERATIONS)
    )
    print_measurement(
        "five-field full JSON", "talea + stdlib", measure(partial(JsonFive.from_json, JSON_FIVE), _SUCCESS_ITERATIONS)
    )
    for name, operation in (
        ("nested full JSON", partial(Nested.from_json, JSON_NESTED)),
        ("container-heavy full JSON", partial(Container.from_json, JSON_CONTAINER)),
        ("UUID full JSON", partial(Standard.from_json, JSON_STANDARD)),
    ):
        print_measurement(name, "talea + stdlib", measure(operation, _SUCCESS_ITERATIONS))

    malformed = partial(capture, lambda: JsonFive.from_json('{"identifier":]'))
    invalid = partial(capture, lambda: JsonFive.from_json('{"identifier":"bad"}'))
    print(f"JSON failure ({_REPEATS} samples x {_FAILURE_ITERATIONS:,} failures)")
    print_measurement("malformed JSON", "talea + stdlib", measure(malformed, _FAILURE_ITERATIONS))
    print_measurement("invalid decoded payload", "talea + stdlib", measure(invalid, _FAILURE_ITERATIONS))

    try:
        orjson = importlib.import_module("orjson")
    except ModuleNotFoundError:
        print("optional orjson: unavailable")
    else:
        orjson_loads = cast(Callable[[str | bytes | bytearray], object], orjson.loads)
        print_measurement(
            "five-field decode only", "orjson", measure(partial(orjson_loads, JSON_FIVE), _SUCCESS_ITERATIONS)
        )
        print_measurement(
            "five-field full JSON",
            "talea + orjson",
            measure(partial(JsonFive.from_json, JSON_FIVE, loads=orjson_loads), _SUCCESS_ITERATIONS),
        )

    print(f"JSON allocations ({_ALLOCATION_SAMPLES:,} warmed operations)")
    for name, operation in (
        ("successful JSON", partial(JsonFive.from_json, JSON_FIVE)),
        ("nested JSON", partial(Nested.from_json, JSON_NESTED)),
        ("aggregated decoded failure", invalid),
    ):
        result = measure_allocations(operation)
        print(f"{name:36} retained={result.retained:5} B peak={result.peak:5} B")


def main() -> None:
    """Run one explicitly selected Campaign 9 benchmark family."""

    if sys.argv[1:] == ["mapping"]:
        benchmark_mapping()
    elif sys.argv[1:] == ["json"]:
        benchmark_json()
    else:
        raise SystemExit("usage: input_boundaries.py mapping|json")


if __name__ == "__main__":
    main()
