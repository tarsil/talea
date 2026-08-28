"""Measure Campaign 10 compiled Python projection and JSON encoding.

The primary comparison is a direct hand-written dictionary literal reading the
same immutable slotted values. Container rows necessarily include the fresh
copying required by Talea's no-alias output contract. JSON work is split into
schema projection, syntax encoding, and the public full operation.
"""

import gc
import importlib
import sys
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
from statistics import median
from time import perf_counter_ns
from timeit import Timer
from types import FunctionType, MethodType
from typing import Annotated, Literal, TypedDict, cast
from uuid import UUID

from talea import Alias, Discriminator, Representation, SerializationError, Spec, serialize
from talea.serialization.compilation import compile_selected_serialization
from talea.serialization.json import _default_dumps
from talea.serialization.selection import SerializationSelection, normalize_selection

_REPEATS = 7
_SUCCESS_ITERATIONS = 50_000
_DECLARATION_ITERATIONS = 500
_FIRST_USE_SAMPLES = 100
_ALLOCATION_SAMPLES = 500
_SELECTION_ITERATIONS = 2_000

type Operation = Callable[[], object]


class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


class AllocationMeasurement:
    """Retain minimum steady-state traced-memory deltas."""

    __slots__ = ("peak", "retained")

    def __init__(self, retained: int, peak: int) -> None:
        self.retained = retained
        self.peak = peak


def measure(operation: Operation, iterations: int = _SUCCESS_ITERATIONS) -> Measurement:
    """Measure one warmed operation across independent timer samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def measure_allocations(operation: Operation) -> AllocationMeasurement:
    """Measure minimum retained and peak traced bytes for one warmed operation."""

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

    print(f"{case:34} {implementation:18} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def names(count: int) -> tuple[str, ...]:
    """Return deterministic field names for scaling measurements."""

    return tuple(f"field_{index}" for index in range(count))


def values(count: int) -> dict[str, int]:
    """Return one valid scaling payload."""

    return {name: index for index, name in enumerate(names(count))}


def make_spec(count: int) -> type[Spec]:
    """Declare one strict integer Spec for scaling measurements."""

    return type(f"Output{count}", (Spec,), {"__annotations__": dict.fromkeys(names(count), int)})


def make_declared_serializer_spec() -> type[Spec]:
    """Declare one fresh Spec with a scalar serializer output contract."""

    def output(value: int) -> str:
        return str(value)

    return type(
        "DeclaredOutput",
        (Spec,),
        {
            "__annotations__": {"value": int},
            "output": serialize("value", output=str)(output),
        },
    )


def make_hand_serializer(count: int) -> Callable[[object], dict[str, object]]:
    """Compile the equivalent direct hand-written dictionary literal."""

    entries = ", ".join(f"{name!r}: instance.{name}" for name in names(count))
    namespace: dict[str, object] = {}
    exec(compile(f"def serialize(instance):\n    return {{{entries}}}", "<hand serializer>", "exec"), namespace)
    return cast(Callable[[object], dict[str, object]], namespace["serialize"])


class Address(Spec):
    city: str
    postcode: str


class Nested(Spec):
    identifier: int
    address: Address


class Container(Spec):
    values: list[int]
    pair: tuple[int, ...]


class Standard(Spec):
    identifier: UUID
    created_at: datetime
    amount: Decimal


class Parent(Spec):
    identifier: int
    name: str


class Inherited(Parent):
    active: bool


class Hooked(Spec):
    value: int

    @serialize("value")
    def output(value: int) -> str:
        return str(value)


class SerializerSummary(Spec):
    name: str
    score: int


@dataclass
class SerializerDataclass:
    name: str


class SerializerDictionary(TypedDict):
    name: str


class SerializerDomain:
    __slots__ = ("value",)

    def __init__(self, value: int) -> None:
        self.value = value


type SerializerRepresentation = Annotated[
    SerializerDomain,
    Representation(output=str, dump=lambda value: str(value.value)),
]


class DeclaredScalar(Spec):
    value: int

    @serialize("value", output=str)
    def output(value: int) -> str:
        return str(value)


class DeclaredStructured(Spec):
    value: int

    @serialize("value", output=SerializerSummary)
    def output(value: int) -> SerializerSummary:
        return SerializerSummary(name=str(value), score=value)


class DeclaredModels(Spec):
    dataclass_value: int
    dictionary_value: int
    representation_value: int
    list_value: int
    mapping_value: int

    @serialize("dataclass_value", output=SerializerDataclass)
    def dataclass_output(value: int) -> SerializerDataclass:
        return SerializerDataclass(str(value))

    @serialize("dictionary_value", output=SerializerDictionary)
    def dictionary_output(value: int) -> SerializerDictionary:
        return {"name": str(value)}

    @serialize("representation_value", output=SerializerRepresentation)
    def representation_output(value: int) -> SerializerDomain:
        return SerializerDomain(value)

    @serialize("list_value", output=list[SerializerSummary])
    def list_output(value: int) -> list[SerializerSummary]:
        return [SerializerSummary(name=str(value), score=value)]

    @serialize("mapping_value", output=dict[str, SerializerSummary])
    def mapping_output(value: int) -> dict[str, SerializerSummary]:
        return {"value": SerializerSummary(name=str(value), score=value)}


class InvalidDeclaredScalar(Spec):
    value: int

    @serialize("value", output=str)
    def output(value: int) -> int:
        return value


class FailingDeclaredScalar(Spec):
    value: int

    @serialize("value", output=str)
    def output(value: int) -> str:
        raise ValueError(value)


class Aliased(Spec):
    identifier: Annotated[int, Alias("id")]
    name: str


class ProjectionAddress(Spec):
    city: str
    country: str
    internal_code: str


class ProjectionProfile(Spec):
    name: str
    address: ProjectionAddress
    internal_note: str


class ProjectionAccount(Spec):
    identifier: int
    profile: ProjectionProfile
    audit: str


class ProjectionCollection(Spec):
    members: list[ProjectionProfile]
    indexed: dict[str, ProjectionProfile]


class ProjectionPerson(Spec):
    name: str
    email: str


class ProjectionAdmin(Spec):
    name: str
    level: int


class ProjectionUnion(Spec):
    subject: ProjectionPerson | ProjectionAdmin


class ProjectionCard(Spec):
    kind: Literal["card"]
    number: str


class ProjectionBank(Spec):
    kind: Literal["bank"]
    iban: str


type ProjectionPayment = Annotated[ProjectionCard | ProjectionBank, Discriminator("kind")]


class DeclaredTagged(Spec):
    value: int

    @serialize("value", output=ProjectionPayment)
    def output(value: int) -> ProjectionPayment:
        return ProjectionCard(kind="card", number=str(value))


class ProjectionTagged(Spec):
    payment: ProjectionPayment


class ProjectionDepth5(Spec):
    value: int
    sibling: int


class ProjectionDepth4(Spec):
    child: ProjectionDepth5
    sibling: int


class ProjectionDepth3(Spec):
    child: ProjectionDepth4
    sibling: int


class ProjectionDepth2(Spec):
    child: ProjectionDepth3
    sibling: int


class ProjectionDepth1(Spec):
    child: ProjectionDepth2
    sibling: int


NESTED = Nested(identifier=1, address=Address(city="Zurich", postcode="8001"))
CONTAINER = Container(values=[1, 2, 3], pair=(1, 2, 3))
STANDARD = Standard(
    identifier=UUID(int=0),
    created_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    amount=Decimal("1234567890.123456789"),
)
INHERITED = Inherited(identifier=1, name="Ada", active=True)
HOOKED = Hooked(value=1)
DECLARED_SCALAR = DeclaredScalar(value=1)
DECLARED_STRUCTURED = DeclaredStructured(value=1)
DECLARED_MODELS = DeclaredModels(
    dataclass_value=1,
    dictionary_value=1,
    representation_value=1,
    list_value=1,
    mapping_value=1,
)
INVALID_DECLARED_SCALAR = InvalidDeclaredScalar(value=1)
FAILING_DECLARED_SCALAR = FailingDeclaredScalar(value=1)
ALIASED = Aliased(identifier=1, name="Ada")
PROJECTION_PROFILE = ProjectionProfile(
    name="Ada",
    address=ProjectionAddress(city="Zurich", country="CH", internal_code="8001"),
    internal_note="private",
)
PROJECTION_ACCOUNT = ProjectionAccount(
    identifier=1,
    profile=PROJECTION_PROFILE,
    audit="internal",
)
PROJECTION_COLLECTION = ProjectionCollection(
    members=[PROJECTION_PROFILE, PROJECTION_PROFILE],
    indexed={"first": PROJECTION_PROFILE, "second": PROJECTION_PROFILE},
)
PROJECTION_UNION = ProjectionUnion(subject=ProjectionAdmin(name="Ada", level=3))
PROJECTION_TAGGED = ProjectionTagged(payment=ProjectionCard(kind="card", number="4111"))
DECLARED_TAGGED = DeclaredTagged(value=4111)
PROJECTION_DEEP = ProjectionDepth1(
    child=ProjectionDepth2(
        child=ProjectionDepth3(
            child=ProjectionDepth4(child=ProjectionDepth5(value=1, sibling=2), sibling=2),
            sibling=2,
        ),
        sibling=2,
    ),
    sibling=2,
)
NESTED_INCLUDE_1: SerializationSelection = {"profile": {"name": True}}
NESTED_INCLUDE_3: SerializationSelection = {"profile": {"address": {"city": True}}}
NESTED_EXCLUDE_1: SerializationSelection = {"profile": {"internal_note": True}}
NESTED_EXCLUDE_3: SerializationSelection = {"profile": {"address": {"internal_code": True}}}
NESTED_INCLUDE_5: SerializationSelection = {"child": {"child": {"child": {"child": {"value": True}}}}}
NESTED_EXCLUDE_5: SerializationSelection = {"child": {"child": {"child": {"child": {"sibling": True}}}}}
DECLARED_INCLUDE: SerializationSelection = {"value": {"name": True}}
DECLARED_EXCLUDE: SerializationSelection = {"value": {"score": True}}


def project_account(value: ProjectionAccount) -> dict[str, object]:
    """Project the equivalent nested response shape directly by hand."""

    return {"profile": {"address": {"city": value.profile.address.city}}}


def project_declared_scalar(value: DeclaredScalar) -> dict[str, object]:
    """Perform the equivalent callback, result check, and scalar projection."""

    replacement = DeclaredScalar.output(value.value)
    if type(replacement) is not str:
        raise TypeError
    return {"value": replacement}


def project_declared_structured(value: DeclaredStructured) -> dict[str, object]:
    """Perform the equivalent callback, result check, and structural projection."""

    replacement = DeclaredStructured.output(value.value)
    if type(replacement) is not SerializerSummary:
        raise TypeError
    return {"value": {"name": replacement.name, "score": replacement.score}}


def ignore_serialization_error(operation: Operation) -> None:
    """Consume one expected serializer failure for stable failure-path timing."""

    try:
        operation()
    except SerializationError:
        pass


def measure_first_use(mode: str) -> Measurement:
    """Measure compilation plus execution for fresh five-field declarations."""

    samples: list[int] = []
    for _ in range(_FIRST_USE_SAMPLES):
        spec = make_spec(5)
        instance = spec(**values(5))
        started = perf_counter_ns()
        instance.to_dict() if mode == "python" else instance.to_json()
        samples.append(perf_counter_ns() - started)
    return Measurement(min(samples), median(samples))


def benchmark_python_projection() -> None:
    """Measure plain and feature-bearing Python mapping projection."""

    print(f"Python projection ({_REPEATS} samples x {_SUCCESS_ITERATIONS:,} operations)")
    for count in (1, 5, 10):
        spec = make_spec(count)
        instance = spec(**values(count))
        hand = make_hand_serializer(count)
        instance.to_dict()
        projector = cast(
            Callable[[object], dict[str, object]],
            instance.__talea_artifacts__.outputs.python_alias,
        )
        print_measurement(f"to_dict {count} fields", "talea", measure(instance.to_dict))
        print_measurement(
            f"to_dict {count} fields",
            "direct projector",
            measure(MethodType(projector, instance)),
        )
        print_measurement(f"to_dict {count} fields", "handwritten", measure(MethodType(hand, instance)))

    for instance in (
        NESTED,
        CONTAINER,
        STANDARD,
        INHERITED,
        HOOKED,
        DECLARED_SCALAR,
        DECLARED_STRUCTURED,
        DECLARED_MODELS,
        DECLARED_TAGGED,
        ALIASED,
    ):
        instance.to_dict()
    cases: tuple[tuple[str, Operation], ...] = (
        ("nested Spec", NESTED.to_dict),
        ("list/container", CONTAINER.to_dict),
        ("standard-library", STANDARD.to_dict),
        ("inherited Spec", INHERITED.to_dict),
        ("serialization hook", HOOKED.to_dict),
        ("declared scalar hook", DECLARED_SCALAR.to_dict),
        ("declared structured hook", DECLARED_STRUCTURED.to_dict),
        ("declared model outputs", DECLARED_MODELS.to_dict),
        ("declared tagged output", DECLARED_TAGGED.to_dict),
        ("alias", ALIASED.to_dict),
        ("include", partial(INHERITED.to_dict, include={"identifier", "active"})),
        ("exclude", partial(INHERITED.to_dict, exclude={"name"})),
        ("exclude_none", partial(INHERITED.to_dict, exclude_none=True)),
    )
    for name, operation in cases:
        print_measurement(name, "talea", measure(operation))
    print_measurement(
        "declared scalar hook", "manual equivalent", measure(partial(project_declared_scalar, DECLARED_SCALAR))
    )
    print_measurement(
        "declared structured hook",
        "manual equivalent",
        measure(partial(project_declared_structured, DECLARED_STRUCTURED)),
    )
    print_measurement("declared callback", "lower bound", measure(partial(DeclaredScalar.output, 1)))
    print_measurement(
        "declared invalid result",
        "talea failure",
        measure(partial(ignore_serialization_error, INVALID_DECLARED_SCALAR.to_dict)),
    )
    print_measurement(
        "declared callback failure",
        "talea failure",
        measure(partial(ignore_serialization_error, FAILING_DECLARED_SCALAR.to_dict)),
    )


def benchmark_nested_selection() -> None:
    """Measure operation-local normalization and direct nested projection."""

    schema = vars(ProjectionAccount)["__talea_artifacts__"].schema
    collection_include: SerializationSelection = {"members": {"name": True}}
    mapping_include: SerializationSelection = {"indexed": {"address": {"city": True}}}
    union_include: SerializationSelection = {"subject": {"name": True, "email": True, "level": True}}
    tagged_include: SerializationSelection = {"payment": {"kind": True, "number": True, "iban": True}}
    print(f"Nested selection ({_REPEATS} samples x {_SELECTION_ITERATIONS:,} operations)")
    cases: tuple[tuple[str, Operation], ...] = (
        ("nested include depth 1", partial(PROJECTION_ACCOUNT.to_dict, include=NESTED_INCLUDE_1)),
        ("nested include depth 3", partial(PROJECTION_ACCOUNT.to_dict, include=NESTED_INCLUDE_3)),
        ("nested include depth 5", partial(PROJECTION_DEEP.to_dict, include=NESTED_INCLUDE_5)),
        ("nested exclude depth 1", partial(PROJECTION_ACCOUNT.to_dict, exclude=NESTED_EXCLUDE_1)),
        ("nested exclude depth 3", partial(PROJECTION_ACCOUNT.to_dict, exclude=NESTED_EXCLUDE_3)),
        ("nested exclude depth 5", partial(PROJECTION_DEEP.to_dict, exclude=NESTED_EXCLUDE_5)),
        ("list[Spec] selection", partial(PROJECTION_COLLECTION.to_dict, include=collection_include)),
        ("dict[str, Spec] selection", partial(PROJECTION_COLLECTION.to_dict, include=mapping_include)),
        ("ordinary union selection", partial(PROJECTION_UNION.to_dict, include=union_include)),
        ("tagged union selection", partial(PROJECTION_TAGGED.to_dict, include=tagged_include)),
        ("normalization only", partial(normalize_selection, NESTED_INCLUDE_3, schema, "include")),
        ("repeated equivalent selection", partial(PROJECTION_ACCOUNT.to_dict, include=NESTED_INCLUDE_3)),
        (
            "declared output include",
            partial(DECLARED_STRUCTURED.to_dict, include=DECLARED_INCLUDE),
        ),
        (
            "declared output exclude",
            partial(DECLARED_STRUCTURED.to_dict, exclude=DECLARED_EXCLUDE),
        ),
    )
    for name, operation in cases:
        print_measurement(name, "talea", measure(operation, _SELECTION_ITERATIONS))
    print_measurement(
        "nested include depth 3",
        "handwritten",
        measure(partial(project_account, PROJECTION_ACCOUNT), _SELECTION_ITERATIONS),
    )

    normalized = normalize_selection(NESTED_INCLUDE_3, schema, "include")
    assert normalized is not None
    print_measurement(
        "selection compile miss",
        "compile direct",
        measure(
            partial(
                compile_selected_serialization,
                schema,
                "python",
                True,
                normalized,
                None,
                False,
            ),
            _DECLARATION_ITERATIONS,
        ),
    )

    broad_type = make_spec(100)
    broad_parent = cast(type[Spec], type("BroadProjection", (Spec,), {"__annotations__": {"child": broad_type}}))
    broad = broad_parent.from_mapping({"child": broad_type(**values(100))})
    broad_selection: SerializationSelection = {"child": dict.fromkeys(names(100), True)}
    print_measurement(
        "nested broad 100 fields",
        "talea",
        measure(partial(broad.to_dict, include=broad_selection), _SELECTION_ITERATIONS),
    )

    variants = vars(ProjectionAccount)["__talea_artifacts__"].outputs.variants
    assert variants is not None
    selected = tuple(value for key, value in variants.items() if key and key[0] == "selected")
    selected_bytes = sys.getsizeof(variants) + sum(
        sys.getsizeof(value) + sys.getsizeof(cast(FunctionType, value).__globals__) for value in selected
    )
    print(
        "Selected cache retained shallow memory "
        f"entries={len(selected)}/32 bytes={selected_bytes} policy=per-class FIFO"
    )


def benchmark_json_projection() -> None:
    """Measure projection, dumps-only, full encoding, and optional codec context."""

    five_type = make_spec(5)
    five = five_type(**values(5))
    artifacts = vars(five_type)["__talea_artifacts__"]
    projection = cast(
        Callable[[object], dict[str, object]],
        artifacts.outputs.output_for(artifacts.schema, "json", True, False),
    )
    tree = projection(five)
    print(f"JSON output ({_REPEATS} samples x {_SUCCESS_ITERATIONS:,} operations)")
    print_measurement("five-field projection", "talea", measure(partial(projection, five)))
    print_measurement("five-field dumps only", "stdlib", measure(partial(_default_dumps, tree)))
    print_measurement("five-field full to_json", "talea + stdlib", measure(five.to_json))
    for name, instance in (
        ("nested full to_json", NESTED),
        ("container full to_json", CONTAINER),
        ("UUID/datetime/Decimal", STANDARD),
        ("declared scalar to_json", DECLARED_SCALAR),
        ("declared structured to_json", DECLARED_STRUCTURED),
        ("declared models to_json", DECLARED_MODELS),
        ("declared tagged to_json", DECLARED_TAGGED),
    ):
        print_measurement(name, "talea + stdlib", measure(instance.to_json))

    try:
        orjson = importlib.import_module("orjson")
    except ModuleNotFoundError:
        print("optional orjson: unavailable")
    else:
        dumps = cast(Callable[[object], bytes], orjson.dumps)
        print_measurement("five-field dumps only", "orjson", measure(partial(dumps, tree)))
        print_measurement("five-field full to_json", "talea + orjson", measure(partial(five.to_json, dumps=dumps)))


def benchmark_costs() -> None:
    """Measure declaration, first use, allocations, and retained memory."""

    print(f"Output declaration ({_REPEATS} samples x {_DECLARATION_ITERATIONS:,} declarations)")
    for count in (1, 5, 10):
        print_measurement(
            f"declare {count} fields",
            "serialization unused",
            measure(partial(make_spec, count), _DECLARATION_ITERATIONS),
        )
    print_measurement(
        "declare scalar output",
        "serialization unused",
        measure(make_declared_serializer_spec, _DECLARATION_ITERATIONS),
    )
    print(f"Output first use ({_FIRST_USE_SAMPLES:,} fresh five-field declarations)")
    print_measurement("first to_dict", "compile + execute", measure_first_use("python"))
    print_measurement("first to_json", "compile + execute", measure_first_use("json"))
    warm_type = make_spec(5)
    warm = warm_type(**values(5))
    warm.to_dict()
    warm.to_json()
    print_measurement("warm to_dict", "retained", measure(warm.to_dict))
    print_measurement("warm to_json", "retained", measure(warm.to_json))

    print(f"Output allocations ({_ALLOCATION_SAMPLES:,} warmed operations)")
    allocation_python = make_spec(5)(**values(5))
    allocation_json = make_spec(5)(**values(5))
    allocation_python.to_dict()
    NESTED.to_dict()
    allocation_json.to_json()
    for name, operation in (
        ("five-field to_dict", allocation_python.to_dict),
        ("nested to_dict", NESTED.to_dict),
        ("selected nested to_dict", partial(PROJECTION_ACCOUNT.to_dict, include=NESTED_INCLUDE_3)),
        ("five-field to_json", allocation_json.to_json),
        ("selected nested to_json", partial(PROJECTION_ACCOUNT.to_json, include=NESTED_INCLUDE_3)),
        ("declared scalar to_dict", DECLARED_SCALAR.to_dict),
        ("declared structured to_dict", DECLARED_STRUCTURED.to_dict),
        ("declared structured to_json", DECLARED_STRUCTURED.to_json),
        ("declared models to_dict", DECLARED_MODELS.to_dict),
    ):
        result = measure_allocations(operation)
        print(f"{name:34} retained={result.retained:5} B peak={result.peak:5} B")

    cold_type = make_spec(5)
    artifacts = vars(cold_type)["__talea_artifacts__"]
    instance = cold_type(**values(5))
    cold_bytes = sys.getsizeof(artifacts.outputs)
    instance_bytes = sys.getsizeof(instance)
    instance.to_dict()
    serializer = artifacts.outputs.python_alias
    assert serializer is not None
    warm_bytes = cold_bytes + sys.getsizeof(serializer) + sys.getsizeof(serializer.__globals__)
    print(
        "Output retained shallow memory "
        f"cold={cold_bytes} B python-warm={warm_bytes} B instance={instance_bytes} B "
        f"json_compiled={artifacts.outputs.json_alias is not None}"
    )
    declared_artifacts = vars(DeclaredStructured)["__talea_artifacts__"].outputs
    assert declared_artifacts.python_alias is not None and declared_artifacts.json_alias is not None
    declared_retained = (
        sys.getsizeof(declared_artifacts)
        + sys.getsizeof(declared_artifacts.python_alias)
        + sys.getsizeof(declared_artifacts.python_alias.__globals__)
        + sys.getsizeof(declared_artifacts.json_alias)
        + sys.getsizeof(declared_artifacts.json_alias.__globals__)
    )
    print(f"Declared output retained shallow memory python+json-warm={declared_retained} B")


def main() -> None:
    """Run all Campaign 10 serialization benchmark families."""

    benchmark_python_projection()
    benchmark_nested_selection()
    benchmark_json_projection()
    benchmark_costs()


if __name__ == "__main__":
    main()
