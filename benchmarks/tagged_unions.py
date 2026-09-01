"""Measure Campaign 14 tagged-union dispatch and complete boundaries."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial, reduce
from operator import or_
from statistics import median
from timeit import Timer
from types import FunctionType
from typing import Annotated, Literal, cast

from talea import Alias, Contract, Discriminator, Spec, ValidationError, create_spec
from talea.input.json import _default_loads
from talea.schema import TaggedUnionSchema

_REPEATS = 7
_DISPATCH_ITERATIONS = 100_000
_MAPPING_ITERATIONS = 30_000
_JSON_ITERATIONS = 10_000
_FAILURE_ITERATIONS = 5_000
_BRANCH_COUNTS = (2, 4, 8, 16, 32)

type Operation = Callable[[], object]
type Selector = Callable[[object], int]


@dataclass(frozen=True, slots=True)
class Measurement:
    """Retain minimum and median nanoseconds for one warmed operation."""

    minimum: float
    median: float


def measure(operation: Operation, iterations: int) -> Measurement:
    """Measure one warmed callable across independent samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def report(count: int, case: str, implementation: str, result: Measurement) -> None:
    """Print one stable branch-count result row."""

    print(
        f"{count:2} branches {case:29} {implementation:8} "
        f"min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op"
    )


def make_contract(count: int) -> tuple[Contract[object], tuple[type[Spec], ...]]:
    """Build one finite integer-tagged contract with ``count`` branches."""

    branches = tuple(
        create_spec(
            f"TaggedBenchmark{count}_{index}",
            {"kind": Literal[index], "value": int},  # ty: ignore[invalid-type-form]
        )
        for index in range(count)
    )
    union = reduce(or_, branches)
    annotation = Annotated[union, Discriminator("kind")]  # ty: ignore[invalid-type-form]
    return Contract(annotation), branches


def make_migrated_contract(
    count: int,
    legacy_count: int,
) -> tuple[Contract[object], tuple[type[Spec], ...], tuple[str, ...]]:
    """Build direct-dispatch branches with one shared accepted-key vocabulary."""

    legacy_names = tuple(f"legacy_{index}" for index in range(legacy_count))
    branches = tuple(
        create_spec(
            f"MigratedTaggedBenchmark{count}_{legacy_count}_{index}",
            {
                "kind": Annotated[
                    Literal[index],  # ty: ignore[invalid-type-form]
                    Alias("type", legacy=legacy_names),
                ],
                "value": int,
            },
        )
        for index in range(count)
    )
    union = reduce(or_, branches)
    annotation = Annotated[union, Discriminator("kind")]  # ty: ignore[invalid-type-form]
    return Contract(annotation), branches, legacy_names


def make_linear_selector(count: int) -> Selector:
    """Compile the same exact comparison shape used by small tagged unions."""

    lines = ["def select(tag):"]
    for index in range(count):
        keyword = "if" if index == 0 else "elif"
        lines.append(f"    {keyword} type(tag) is int and tag == {index}:")
        lines.append(f"        return {index}")
    lines.append("    return -1")
    namespace: dict[str, object] = {}
    exec(compile("\n".join(lines), "<tagged benchmark linear selector>", "exec"), namespace)
    return cast(Selector, namespace["select"])


def make_hand_branch(expected: int) -> type[object]:
    """Create one slotted strict branch for the handwritten comparison."""

    def initialize(self: object, *, kind: object, value: object) -> None:
        if type(kind) is not int or kind != expected or type(value) is not int:
            raise TypeError("invalid hand branch")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "value", value)

    def immutable(self: object, name: str, value: object) -> None:
        raise AttributeError("hand branches are immutable")

    return type(
        f"HandTagged{expected}",
        (),
        {"__slots__": ("kind", "value"), "__init__": initialize, "__setattr__": immutable},
    )


def manual_from_mapping(
    data: Mapping[str, object],
    branches: tuple[type[object], ...],
) -> object:
    """Run semantically equivalent exact integer dispatch and construction."""

    tag = data["kind"]
    if type(tag) is not int or tag < 0 or tag >= len(branches):
        raise ValueError("unknown tag")
    value = data["value"]
    if type(value) is not int:
        raise TypeError("value must be int")
    return branches[tag](kind=tag, value=value)


def manual_from_json(data: str, branches: tuple[type[object], ...]) -> object:
    """Decode through stdlib JSON before equivalent manual dispatch."""

    return manual_from_mapping(cast(dict[str, object], _default_loads(data)), branches)


def manual_migrated_from_mapping(
    data: Mapping[str, object],
    branches: tuple[type[object], ...],
    legacy_names: tuple[str, ...],
) -> object:
    """Run equivalent accepted-key conflict detection before direct dispatch."""

    accepted = ("type", *legacy_names)
    supplied = tuple(name for name in accepted if name in data)
    if len(supplied) != 1 or frozenset(data) != {supplied[0], "value"}:
        raise ValueError("missing, conflicting, or unexpected discriminator")
    tag = data[supplied[0]]
    if type(tag) is not int or tag < 0 or tag >= len(branches):
        raise ValueError("unknown tag")
    member = data["value"]
    if type(member) is not int:
        raise TypeError("value must be int")
    return branches[tag](kind=tag, value=member)


def capture(operation: Operation) -> ValidationError:
    """Return one expected unknown-tag failure without rendering it."""

    try:
        operation()
    except ValidationError as error:
        return error
    raise AssertionError("tagged-union failure benchmark succeeded")


def main() -> None:
    """Print extraction, selection, validation, boundary, and output evidence."""

    print(f"Tagged unions ({_REPEATS} independent warmed samples)")
    for count in _BRANCH_COUNTS:
        contract, branches = make_contract(count)
        hand_branches = tuple(make_hand_branch(index) for index in range(count))
        payload = {"kind": count - 1, "value": 1}
        encoded = f'{{"kind":{count - 1},"value":1}}'
        instance = contract.from_python(payload)
        dispatch = {(int, index): branches[index] for index in range(count)}
        linear = make_linear_selector(count)
        tag = payload["kind"]

        report(
            count,
            "tag extraction",
            "manual",
            measure(partial(dict.__getitem__, payload, "kind"), _DISPATCH_ITERATIONS),
        )
        report(
            count,
            "linear tag selection",
            "strategy",
            measure(partial(linear, tag), _DISPATCH_ITERATIONS),
        )
        report(
            count,
            "table tag selection",
            "strategy",
            measure(partial(dispatch.get, (type(tag), tag)), _DISPATCH_ITERATIONS),
        )
        report(
            count,
            "strict existing object",
            "talea",
            measure(partial(contract.validate, instance), _DISPATCH_ITERATIONS),
        )
        report(
            count,
            "selected branch validation",
            "talea",
            measure(partial(branches[-1], kind=count - 1, value=1), _MAPPING_ITERATIONS),
        )
        report(
            count,
            "Mapping to branch object",
            "manual",
            measure(partial(manual_from_mapping, payload, hand_branches), _MAPPING_ITERATIONS),
        )
        report(
            count,
            "Mapping to branch object",
            "talea",
            measure(partial(contract.from_python, payload), _MAPPING_ITERATIONS),
        )
        report(
            count,
            "JSON to branch object",
            "manual",
            measure(partial(manual_from_json, encoded, hand_branches), _JSON_ITERATIONS),
        )
        report(
            count,
            "JSON to branch object",
            "talea",
            measure(partial(contract.from_json, encoded), _JSON_ITERATIONS),
        )
        report(
            count,
            "Python output",
            "talea",
            measure(partial(contract.to_python, instance), _MAPPING_ITERATIONS),
        )
        report(
            count,
            "JSON output",
            "talea",
            measure(partial(contract.to_json, instance), _JSON_ITERATIONS),
        )
        unknown = {"kind": count, "value": 1}
        report(
            count,
            "unknown tag failure",
            "talea",
            measure(partial(capture, partial(contract.from_python, unknown)), _FAILURE_ITERATIONS),
        )

    print("\nMigrated discriminator direct dispatch")
    for count in (2, 8, 32):
        contract, branches, legacy_names = make_migrated_contract(count, 4)
        current = {"type": count - 1, "value": 1}
        legacy = {"legacy_3": count - 1, "value": 1}
        report(
            count,
            "migrated current Mapping",
            "talea",
            measure(partial(contract.from_python, current), _MAPPING_ITERATIONS),
        )
        report(
            count,
            "migrated late Mapping",
            "talea",
            measure(partial(contract.from_python, legacy), _MAPPING_ITERATIONS),
        )
        report(
            count,
            "migrated late Mapping",
            "manual",
            measure(
                partial(manual_migrated_from_mapping, legacy, branches, legacy_names),
                _MAPPING_ITERATIONS,
            ),
        )

    migrated, _, legacy_names = make_migrated_contract(8, 16)
    report(
        8,
        "migrated late JSON",
        "talea",
        measure(partial(migrated.from_json, '{"legacy_15":7,"value":1}'), _JSON_ITERATIONS),
    )
    report(
        8,
        "migrated conflict",
        "talea",
        measure(
            partial(
                capture,
                partial(migrated.from_python, {"type": 7, legacy_names[0]: 7, "value": 1}),
            ),
            _FAILURE_ITERATIONS,
        ),
    )
    report(
        8,
        "migrated unknown",
        "talea",
        measure(
            partial(capture, partial(migrated.from_python, {legacy_names[-1]: 8, "value": 1})),
            _FAILURE_ITERATIONS,
        ),
    )
    migrated.from_python({legacy_names[-1]: 7, "value": 1})
    generated = migrated._artifacts.python_input
    assert generated is not None
    generated = cast(FunctionType, generated)
    schema = migrated._artifacts.schema
    assert isinstance(schema, TaggedUnionSchema)
    retained = [value for name, value in generated.__globals__.items() if "discriminator_accepted_names" in name]
    assert retained == [schema.accepted_input_names]
    print(
        "Migrated generated dispatch "
        f"accepted_names={len(schema.accepted_input_names)} "
        f"bound_identity={retained[0] is schema.accepted_input_names} global_registry=False"
    )


if __name__ == "__main__":
    main()
