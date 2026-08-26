"""Measure Campaign 9 Mapping and JSON input boundaries independently."""

import gc
import importlib
import sys
import tracemalloc
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from statistics import median
from time import perf_counter_ns
from timeit import Timer
from typing import Annotated, cast
from uuid import UUID

from talea import ErrorCode, Ge, Spec, ValidationError, check, field, transform
from talea.input.json import _default_loads

_REPEATS = 7
_SUCCESS_ITERATIONS = 50_000
_FAILURE_ITERATIONS = 10_000
_DECLARATION_ITERATIONS = 500
_ALLOCATION_SAMPLES = 500
_FIRST_USE_SAMPLES = 100

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


def measure_first_use(mode: str, count: int = 5) -> Measurement:
    """Measure only a fresh declaration's first selected boundary call."""

    mapping_data = values(count)
    json_data = "{" + ",".join(f'"field_{index}":{index}' for index in range(count)) + "}"
    samples: list[int] = []
    for _ in range(_FIRST_USE_SAMPLES):
        spec = make_spec(count)
        started = perf_counter_ns()
        if mode == "mapping":
            spec.from_mapping(mapping_data)
        else:
            spec.from_json(json_data)
        samples.append(perf_counter_ns() - started)
    return Measurement(min(samples), median(samples))


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
    """Compile an equivalent aggregated Mapping boundary and public classmethod."""

    field_names = names(count)
    cls = type(f"HandInput{count}", (), {"__slots__": field_names, "__setattr__": immutable})
    lines = [
        "def construct(data):",
        "    errors = None",
        "    missing_fields = False",
        "    exact_dict = type(data) is dict",
        "    if not exact_dict and not isinstance(data, Mapping):",
        "        raise ValidationError('Mapping[str, object]', data, (), ErrorCode.TYPE, title=title) from None",
    ]
    namespace: dict[str, object] = {
        "Mapping": Mapping,
        "ErrorCode": ErrorCode,
        "ValidationError": ValidationError,
        "allocator": object.__new__,
        "cls": cls,
        "missing": object(),
        "known_names": frozenset(field_names),
        "title": cls.__name__,
    }
    for index, name in enumerate(field_names):
        lines.extend(
            (
                "    try:",
                f"        {name} = data[{name!r}]",
                "    except KeyError:",
                f"        {name} = missing",
                f"    if {name} is missing:",
                "        missing_fields = True",
                f"        error = ValidationError._missing(({name!r},), title=title)",
                "        if errors is None: errors = [error]",
                "        else: errors.append(error)",
                "    else:",
                f"        if type({name}) is not int:",
                f"            error = ValidationError('int', {name}, ({name!r},), ErrorCode.TYPE, title=title)",
                "            if errors is None: errors = [error]",
                "            else: errors.append(error)",
            )
        )
        namespace[f"slot_{index}"] = vars(cls)[name].__set__
    lines.extend(
        (
            f"    if not exact_dict or missing_fields or len(data) != {count}:",
            "        for key in data:",
            "            if type(key) is not str or key not in known_names:",
            "                error = ValidationError(None, data[key], (key,), ErrorCode.UNEXPECTED, title=title)",
            "                if errors is None: errors = [error]",
            "                else: errors.append(error)",
            "    if errors is not None:",
            "        raise ValidationError._aggregate(tuple(errors), title=title) from None",
        )
    )
    lines.append("    instance = allocator(cls)")
    for index, name in enumerate(field_names):
        lines.append(f"    slot_{index}(instance, {name})")
    lines.append("    return instance")
    exec(compile("\n".join(lines), "<hand Mapping boundary>", "exec"), namespace)
    construct = cast(Callable[[Mapping[str, object]], object], namespace["construct"])

    def from_mapping(cls: type[object], data: Mapping[str, object]) -> object:
        return construct(data)

    descriptor = classmethod(from_mapping)
    type.__setattr__(cls, "from_mapping", descriptor)
    return cast(Callable[[Mapping[str, object]], object], descriptor.__get__(None, cls))


def public_hand_boundary(
    operation: Callable[[Mapping[str, object]], object],
) -> Callable[[Mapping[str, object]], object]:
    """Expose a hand-written operation through the same classmethod call shape."""

    class Boundary:
        @classmethod
        def from_mapping(cls, data: Mapping[str, object]) -> object:
            return operation(data)

    return Boundary.from_mapping


def audit_hand_baseline() -> None:
    """Prove the scaling baseline performs the relevant Talea boundary contract."""

    spec = make_spec(3)
    hand = make_hand_boundary(3)
    invalid: dict[str, object] = {
        "field_0": "bad",
        "field_2": "bad",
        "extra": True,
    }
    talea_error = capture(partial(spec.from_mapping, invalid))
    hand_error = capture(partial(hand, invalid))

    def projection(error: ValidationError) -> list[tuple[object, object]]:
        return [(item["code"], item["location"]) for item in error.errors()]

    if projection(talea_error) != projection(hand_error):
        raise AssertionError("handwritten Mapping baseline is not semantically equivalent")
    hand_value = hand(values(3))
    try:
        hand_value.__setattr__("field_0", 2)
    except AttributeError:
        return
    raise AssertionError("handwritten Mapping result is mutable")


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
_HAND_MISSING = object()


def hand_address(data: Mapping[str, object]) -> HandAddress:
    """Construct the nested value with strict aggregated boundary behavior."""

    errors: list[ValidationError] | None = None
    missing_fields = False
    exact_dict = type(data) is dict
    if not exact_dict and not isinstance(data, Mapping):
        raise ValidationError("Mapping[str, object]", data, (), ErrorCode.TYPE, title="HandAddress")
    try:
        city = data["city"]
    except KeyError:
        city = _HAND_MISSING
    if city is _HAND_MISSING:
        missing_fields = True
        errors = [ValidationError._missing(("city",), title="HandAddress")]
    elif type(city) is not str:
        errors = [ValidationError("str", city, ("city",), ErrorCode.TYPE, title="HandAddress")]
    try:
        postcode = data["postcode"]
    except KeyError:
        postcode = _HAND_MISSING
    if postcode is _HAND_MISSING:
        missing_fields = True
        error = ValidationError._missing(("postcode",), title="HandAddress")
        if errors is None:
            errors = [error]
        else:
            errors.append(error)
    elif type(postcode) is not str:
        error = ValidationError("str", postcode, ("postcode",), ErrorCode.TYPE, title="HandAddress")
        if errors is None:
            errors = [error]
        else:
            errors.append(error)
    if not exact_dict or missing_fields or len(data) != 2:
        for key in data:
            if type(key) is not str or key not in {"city", "postcode"}:
                error = ValidationError(None, data[key], (key,), ErrorCode.UNEXPECTED, title="HandAddress")
                if errors is None:
                    errors = [error]
                else:
                    errors.append(error)
    if errors is not None:
        raise ValidationError._aggregate(tuple(errors), title="HandAddress")
    instance = object.__new__(HandAddress)
    _HAND_ADDRESS_CITY(instance, city)
    _HAND_ADDRESS_POSTCODE(instance, postcode)
    return instance


def hand_nested(data: Mapping[str, object]) -> HandNested:
    """Construct the parent with strict aggregated nested conversion."""

    errors: list[ValidationError] | None = None
    missing_fields = False
    exact_dict = type(data) is dict
    if not exact_dict and not isinstance(data, Mapping):
        raise ValidationError("Mapping[str, object]", data, (), ErrorCode.TYPE, title="HandNested")
    try:
        identifier = data["identifier"]
    except KeyError:
        identifier = _HAND_MISSING
    if identifier is _HAND_MISSING:
        missing_fields = True
        errors = [ValidationError._missing(("identifier",), title="HandNested")]
    elif type(identifier) is not int:
        errors = [ValidationError("int", identifier, ("identifier",), ErrorCode.TYPE, title="HandNested")]
    try:
        address = data["address"]
    except KeyError:
        address = _HAND_MISSING
    if address is _HAND_MISSING:
        missing_fields = True
        error = ValidationError._missing(("address",), title="HandNested")
        if errors is None:
            errors = [error]
        else:
            errors.append(error)
    elif isinstance(address, HandAddress):
        nested = address
    elif isinstance(address, Mapping):
        try:
            nested = hand_address(address)
        except ValidationError as error:
            prefixed = error.prefixed(("address",), title="HandNested")
            if errors is None:
                errors = [prefixed]
            else:
                errors.append(prefixed)
    else:
        error = ValidationError("HandAddress", address, ("address",), ErrorCode.TYPE, title="HandNested")
        if errors is None:
            errors = [error]
        else:
            errors.append(error)
    if not exact_dict or missing_fields or len(data) != 2:
        for key in data:
            if type(key) is not str or key not in {"identifier", "address"}:
                error = ValidationError(None, data[key], (key,), ErrorCode.UNEXPECTED, title="HandNested")
                if errors is None:
                    errors = [error]
                else:
                    errors.append(error)
    if errors is not None:
        raise ValidationError._aggregate(tuple(errors), title="HandNested")
    instance = object.__new__(HandNested)
    _HAND_NESTED_IDENTIFIER(instance, identifier)
    _HAND_NESTED_ADDRESS(instance, nested)
    return instance


def hand_container(data: Mapping[str, object]) -> HandContainer:
    """Construct the container with strict aggregated boundary behavior."""

    errors: list[ValidationError] | None = None
    missing_fields = False
    exact_dict = type(data) is dict
    if not exact_dict and not isinstance(data, Mapping):
        raise ValidationError("Mapping[str, object]", data, (), ErrorCode.TYPE, title="HandContainer")
    try:
        sequence = data["values"]
    except KeyError:
        sequence = _HAND_MISSING
    if sequence is _HAND_MISSING:
        missing_fields = True
        errors = [ValidationError._missing(("values",), title="HandContainer")]
    elif type(sequence) is not list or any(type(item) is not int for item in sequence):
        errors = [ValidationError("list[int]", sequence, ("values",), ErrorCode.TYPE, title="HandContainer")]
    try:
        pair = data["pair"]
    except KeyError:
        pair = _HAND_MISSING
    if pair is _HAND_MISSING:
        missing_fields = True
        error = ValidationError._missing(("pair",), title="HandContainer")
        if errors is None:
            errors = [error]
        else:
            errors.append(error)
    elif type(pair) is not tuple or any(type(item) is not int for item in pair):
        error = ValidationError("tuple[int, ...]", pair, ("pair",), ErrorCode.TYPE, title="HandContainer")
        if errors is None:
            errors = [error]
        else:
            errors.append(error)
    if not exact_dict or missing_fields or len(data) != 2:
        for key in data:
            if type(key) is not str or key not in {"values", "pair"}:
                error = ValidationError(None, data[key], (key,), ErrorCode.UNEXPECTED, title="HandContainer")
                if errors is None:
                    errors = [error]
                else:
                    errors.append(error)
    if errors is not None:
        raise ValidationError._aggregate(tuple(errors), title="HandContainer")
    instance = object.__new__(HandContainer)
    _HAND_CONTAINER_VALUES(instance, sequence)
    _HAND_CONTAINER_PAIR(instance, pair)
    return instance


def hand_standard(data: Mapping[str, object]) -> HandSingle:
    """Construct the UUID comparison with equivalent boundary bookkeeping."""

    errors: list[ValidationError] | None = None
    missing_fields = False
    exact_dict = type(data) is dict
    if not exact_dict and not isinstance(data, Mapping):
        raise ValidationError("Mapping[str, object]", data, (), ErrorCode.TYPE, title="HandSingle")
    try:
        value = data["identifier"]
    except KeyError:
        value = _HAND_MISSING
    if value is _HAND_MISSING:
        missing_fields = True
        errors = [ValidationError._missing(("identifier",), title="HandSingle")]
    elif type(value) is not UUID:
        errors = [ValidationError("UUID", value, ("identifier",), ErrorCode.TYPE, title="HandSingle")]
    if not exact_dict or missing_fields or len(data) != 1:
        for key in data:
            if type(key) is not str or key != "identifier":
                error = ValidationError(None, data[key], (key,), ErrorCode.UNEXPECTED, title="HandSingle")
                if errors is None:
                    errors = [error]
                else:
                    errors.append(error)
    if errors is not None:
        raise ValidationError._aggregate(tuple(errors), title="HandSingle")
    instance = object.__new__(HandSingle)
    _HAND_SINGLE_VALUE(instance, value)
    return instance


def hand_transform(value: object) -> object:
    """Apply the same explicit transform used by the Talea comparison."""

    return int(value) if isinstance(value, str) else value


def hand_check(value: int) -> None:
    """Apply the same explicit field check used by the Talea comparison."""

    if value > 100:
        raise ValueError


def hand_hooked(data: Mapping[str, object]) -> HandSingle:
    """Construct the transform/check comparison with boundary bookkeeping."""

    errors: list[ValidationError] | None = None
    missing_fields = False
    exact_dict = type(data) is dict
    if not exact_dict and not isinstance(data, Mapping):
        raise ValidationError("Mapping[str, object]", data, (), ErrorCode.TYPE, title="HandSingle")
    try:
        value = data["value"]
    except KeyError:
        value = _HAND_MISSING
    if value is _HAND_MISSING:
        missing_fields = True
        errors = [ValidationError._missing(("value",), title="HandSingle")]
    else:
        value = hand_transform(value)
        if type(value) is not int or value < 0:
            errors = [ValidationError("bounded int", value, ("value",), ErrorCode.TYPE, title="HandSingle")]
        else:
            hand_check(value)
    if not exact_dict or missing_fields or len(data) != 1:
        for key in data:
            if type(key) is not str or key != "value":
                error = ValidationError(None, data[key], (key,), ErrorCode.UNEXPECTED, title="HandSingle")
                if errors is None:
                    errors = [error]
                else:
                    errors.append(error)
    if errors is not None:
        raise ValidationError._aggregate(tuple(errors), title="HandSingle")
    instance = object.__new__(HandSingle)
    _HAND_SINGLE_VALUE(instance, value)
    return instance


def hand_static_default(data: Mapping[str, object]) -> HandSingle:
    """Construct the static-default comparison with boundary bookkeeping."""

    errors: list[ValidationError] | None = None
    missing_fields = False
    exact_dict = type(data) is dict
    if not exact_dict and not isinstance(data, Mapping):
        raise ValidationError("Mapping[str, object]", data, (), ErrorCode.TYPE, title="HandSingle")
    try:
        value = data["value"]
    except KeyError:
        value = _HAND_MISSING
    if value is _HAND_MISSING:
        missing_fields = True
        value = 1
    elif type(value) is not int:
        errors = [ValidationError("int", value, ("value",), ErrorCode.TYPE, title="HandSingle")]
    if not exact_dict or missing_fields or len(data) != 1:
        for key in data:
            if type(key) is not str or key != "value":
                error = ValidationError(None, data[key], (key,), ErrorCode.UNEXPECTED, title="HandSingle")
                if errors is None:
                    errors = [error]
                else:
                    errors.append(error)
    if errors is not None:
        raise ValidationError._aggregate(tuple(errors), title="HandSingle")
    instance = object.__new__(HandSingle)
    _HAND_SINGLE_VALUE(instance, value)
    return instance


def hand_factory_default(data: Mapping[str, object]) -> HandSingle:
    """Construct the factory-default comparison with boundary bookkeeping."""

    errors: list[ValidationError] | None = None
    missing_fields = False
    exact_dict = type(data) is dict
    if not exact_dict and not isinstance(data, Mapping):
        raise ValidationError("Mapping[str, object]", data, (), ErrorCode.TYPE, title="HandSingle")
    try:
        value = data["values"]
    except KeyError:
        value = _HAND_MISSING
    if value is _HAND_MISSING:
        missing_fields = True
    elif type(value) is not list or any(type(item) is not int for item in value):
        errors = [ValidationError("list[int]", value, ("values",), ErrorCode.TYPE, title="HandSingle")]
    if not exact_dict or missing_fields or len(data) != 1:
        for key in data:
            if type(key) is not str or key != "values":
                error = ValidationError(None, data[key], (key,), ErrorCode.UNEXPECTED, title="HandSingle")
                if errors is None:
                    errors = [error]
                else:
                    errors.append(error)
    if errors is not None:
        raise ValidationError._aggregate(tuple(errors), title="HandSingle")
    if value is _HAND_MISSING:
        value = []
        if type(value) is not list or any(type(item) is not int for item in value):
            errors = [ValidationError("list[int]", value, ("values",), ErrorCode.TYPE, title="HandSingle")]
    if errors is not None:
        raise ValidationError._aggregate(tuple(errors), title="HandSingle")
    instance = object.__new__(HandSingle)
    _HAND_SINGLE_VALUE(instance, value)
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

    audit_hand_baseline()
    print("Handwritten audit: Mapping root, required keys, extras, aggregation, validation, and immutability")
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
    hand_nested_public = public_hand_boundary(hand_nested)
    hand_container_public = public_hand_boundary(hand_container)
    hand_standard_public = public_hand_boundary(hand_standard)
    hand_hooked_public = public_hand_boundary(hand_hooked)
    hand_static_public = public_hand_boundary(hand_static_default)
    hand_factory_public = public_hand_boundary(hand_factory_default)
    cases: tuple[tuple[str, Operation, Operation], ...] = (
        (
            "nested Mapping",
            partial(Nested.from_mapping, MAPPING_NESTED),
            partial(hand_nested_public, MAPPING_NESTED),
        ),
        (
            "list/container input",
            partial(Container.from_mapping, container_data),
            partial(hand_container_public, container_data),
        ),
        (
            "standard-library input",
            partial(Standard.from_mapping, standard_data),
            partial(hand_standard_public, standard_data),
        ),
        (
            "transform + check",
            partial(Hooked.from_mapping, hooked_data),
            partial(hand_hooked_public, hooked_data),
        ),
        (
            "static default",
            partial(Defaulted.from_mapping, {}),
            partial(hand_static_public, {}),
        ),
        (
            "factory default",
            partial(FactoryDefault.from_mapping, {}),
            partial(hand_factory_public, {}),
        ),
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

    print(f"Boundary first use ({_FIRST_USE_SAMPLES:,} fresh 5-field declarations)")
    print_measurement("first from_mapping", "compile + execute", measure_first_use("mapping"))
    print_measurement("first from_json", "decode + compile + execute", measure_first_use("json"))

    cold = make_spec(5)
    artifacts = vars(cold)["__talea_artifacts__"]
    cold_boundary_bytes = sys.getsizeof(artifacts.inputs) + sys.getsizeof(artifacts.inputs.slot_setters)
    instance_size = sys.getsizeof(cold(**values(5)))
    cold.from_mapping(values(5))
    mapping_input = artifacts.inputs.mapping_input
    assert mapping_input is not None
    warm_boundary_bytes = cold_boundary_bytes + sys.getsizeof(mapping_input) + sys.getsizeof(mapping_input.__globals__)
    print(
        "Boundary retained shallow memory "
        f"cold={cold_boundary_bytes} B mapping-warm={warm_boundary_bytes} B "
        f"instance={instance_size} B json_compiled={artifacts.inputs.json_input is not None}"
    )


def benchmark_json() -> None:
    """Measure decoding, compiled boundary work, full JSON, and allocations."""

    artifacts = vars(JsonFive)["__talea_artifacts__"]
    json_input = artifacts.inputs.input_for(artifacts.schema, JsonFive, "json")
    decoded_five = _default_loads(JSON_FIVE)
    print(f"JSON boundary ({_REPEATS} samples x {_SUCCESS_ITERATIONS:,} successes)")
    print_measurement(
        "five-field decode only", "stdlib strict", measure(partial(_default_loads, JSON_FIVE), _SUCCESS_ITERATIONS)
    )
    print_measurement(
        "five-field boundary only", "talea", measure(partial(json_input, decoded_five), _SUCCESS_ITERATIONS)
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
