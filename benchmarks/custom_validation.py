"""Measure Campaign 7 callback overhead against equivalent direct Python.

Every hand-written row performs the same strict structural checks, calls the
same user callback shape, enforces the check-return contract, and commits with
bound slot setters only after validation. The comparison therefore isolates
Talea's generated dispatch and failure-boundary cost around unavoidable Python
callbacks. Unhooked 1/5/10-field canaries remain separate.
"""

import dis
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from statistics import median
from timeit import Timer
from typing import Annotated, cast

from talea import Ge, Spec, check, transform

_REPEATS = 7
_ITERATIONS = 100_000

type Operation = Callable[[], object]
type Constructor = Callable[..., object]


@dataclass(frozen=True, slots=True)
class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    minimum: float
    median: float


def measure(operation: Operation) -> Measurement:
    """Measure one warmed operation across fixed independent samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=_ITERATIONS)
    nanoseconds = [sample * 1_000_000_000 / _ITERATIONS for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def print_comparison(case: str, talea: Operation, handwritten: Operation) -> None:
    """Print paired timings and Talea's median overhead beyond direct Python."""

    talea_result = measure(talea)
    hand_result = measure(handwritten)
    overhead = talea_result.median - hand_result.median
    ratio = talea_result.median / hand_result.median
    print(
        f"{case:32} talea min={talea_result.minimum:8.1f} median={talea_result.median:8.1f} ns/op | "
        f"hand min={hand_result.minimum:8.1f} median={hand_result.median:8.1f} ns/op | "
        f"overhead={overhead:7.1f} ns ({ratio:.2f}x)"
    )


def immutable(instance: object, name: str, value: object) -> None:
    """Reject writes for equivalent hand-written comparison classes."""

    raise AttributeError("instances are immutable")


def make_hand_constructor(
    name: str,
    fields: tuple[str, ...],
    validation: tuple[str, ...],
    namespace: dict[str, object],
) -> Constructor:
    """Compile one flat atomic hand-written constructor from explicit lines."""

    cls = type(name, (), {"__slots__": fields, "__setattr__": immutable})
    lines = [f"def __init__(self, *, {', '.join(fields)}):", *(f"    {line}" for line in validation)]
    for index, field_name in enumerate(fields):
        setter = f"slot_{index}"
        namespace[setter] = vars(cls)[field_name].__set__
        lines.append(f"    {setter}(self, {field_name})")
    exec(compile("\n".join(lines), "<hand-written custom validation benchmark>", "exec"), namespace)
    type.__setattr__(cls, "__init__", namespace["__init__"])
    return cast(Constructor, cls)


class FieldChecked(Spec):
    """One structurally validated field with one check."""

    value: int

    @check("value")
    def positive(value: int) -> None:
        if value <= 0:
            raise ValueError


class Transformed(Spec):
    """One field with one identity-fast inbound transform."""

    value: int

    @transform("value")
    def parse(value: object) -> object:
        return int(value) if isinstance(value, str) else value


class TransformedChecked(Spec):
    """One field with a transform and post-structural check."""

    value: int

    @transform("value")
    def parse(value: object) -> object:
        return int(value) if isinstance(value, str) else value

    @check("value")
    def positive(value: int) -> None:
        if value <= 0:
            raise ValueError


class MultipleChecked(Spec):
    """One field with two ordered checks."""

    value: int

    @check("value")
    def positive(value: int) -> None:
        if value <= 0:
            raise ValueError

    @check("value")
    def bounded(value: int) -> None:
        if value > 100:
            raise ValueError


class Interval(Spec):
    """Two fields with one whole-Spec check."""

    start: int
    end: int

    @check("start", "end")
    def ordered(start: int, end: int) -> None:
        if end < start:
            raise ValueError


class CheckedBase(Spec):
    """Base declaration contributing an inherited hook."""

    value: int

    @check("value")
    def positive(value: int) -> None:
        if value <= 0:
            raise ValueError


class InheritedChecked(CheckedBase):
    """Flat child constructor consuming its inherited hook."""

    other: int


class ConstrainedChecked(Spec):
    """Representative built-in constraint followed by a custom check."""

    value: Annotated[int, Ge(0)]

    @check("value")
    def even(value: int) -> None:
        if value % 2:
            raise ValueError


HAND_FIELD_CHECKED = make_hand_constructor(
    "HandFieldChecked",
    ("value",),
    (
        "if type(value) is not int: raise TypeError",
        "if positive(value) is not None: raise TypeError",
    ),
    {"positive": FieldChecked.positive},
)
HAND_TRANSFORMED = make_hand_constructor(
    "HandTransformed",
    ("value",),
    (
        "value = parse(value)",
        "if type(value) is not int: raise TypeError",
    ),
    {"parse": Transformed.parse},
)
HAND_TRANSFORMED_CHECKED = make_hand_constructor(
    "HandTransformedChecked",
    ("value",),
    (
        "value = parse(value)",
        "if type(value) is not int: raise TypeError",
        "if positive(value) is not None: raise TypeError",
    ),
    {"parse": TransformedChecked.parse, "positive": TransformedChecked.positive},
)
HAND_MULTIPLE_CHECKED = make_hand_constructor(
    "HandMultipleChecked",
    ("value",),
    (
        "if type(value) is not int: raise TypeError",
        "if positive(value) is not None: raise TypeError",
        "if bounded(value) is not None: raise TypeError",
    ),
    {"positive": MultipleChecked.positive, "bounded": MultipleChecked.bounded},
)
HAND_INTERVAL = make_hand_constructor(
    "HandInterval",
    ("start", "end"),
    (
        "if type(start) is not int: raise TypeError",
        "if type(end) is not int: raise TypeError",
        "if ordered(start, end) is not None: raise TypeError",
    ),
    {"ordered": Interval.ordered},
)
HAND_INHERITED = make_hand_constructor(
    "HandInheritedChecked",
    ("value", "other"),
    (
        "if type(value) is not int: raise TypeError",
        "if positive(value) is not None: raise TypeError",
        "if type(other) is not int: raise TypeError",
    ),
    {"positive": CheckedBase.positive},
)
HAND_CONSTRAINED = make_hand_constructor(
    "HandConstrainedChecked",
    ("value",),
    (
        "if type(value) is not int or value < 0: raise TypeError",
        "if even(value) is not None: raise TypeError",
    ),
    {"even": ConstrainedChecked.even},
)


def benchmark_hooks() -> None:
    """Measure every required custom-validation workload."""

    cases: tuple[tuple[str, Operation, Operation], ...] = (
        ("one field check", partial(FieldChecked, value=2), partial(HAND_FIELD_CHECKED, value=2)),
        ("one transform", partial(Transformed, value=2), partial(HAND_TRANSFORMED, value=2)),
        (
            "transform + check",
            partial(TransformedChecked, value=2),
            partial(HAND_TRANSFORMED_CHECKED, value=2),
        ),
        (
            "multiple checks",
            partial(MultipleChecked, value=2),
            partial(HAND_MULTIPLE_CHECKED, value=2),
        ),
        ("whole-Spec check", partial(Interval, start=1, end=2), partial(HAND_INTERVAL, start=1, end=2)),
        (
            "inherited check",
            partial(InheritedChecked, value=2, other=3),
            partial(HAND_INHERITED, value=2, other=3),
        ),
        (
            "constraint + check",
            partial(ConstrainedChecked, value=2),
            partial(HAND_CONSTRAINED, value=2),
        ),
    )
    for case, talea_operation, hand_operation in cases:
        print_comparison(case, talea_operation, hand_operation)


def field_names(count: int) -> tuple[str, ...]:
    """Return deterministic names for unhooked scaling canaries."""

    return tuple(f"field_{index}" for index in range(count))


def benchmark_unhooked_canaries() -> None:
    """Measure unhooked 1/5/10-field construction and inspect generated code."""

    for count in (1, 5, 10):
        names = field_names(count)
        values = {name: index for index, name in enumerate(names)}
        talea_type = type(f"Unhooked{count}", (Spec,), {"__annotations__": dict.fromkeys(names, int)})
        hand_type = make_hand_constructor(
            f"HandUnhooked{count}",
            names,
            tuple(f"if type({name}) is not int: raise TypeError" for name in names),
            {},
        )
        print_comparison(
            f"unhooked {count} fields",
            partial(talea_type, **values),
            partial(hand_type, **values),
        )
        initializer = vars(talea_type)["__init__"]
        callbacks = tuple(
            value
            for value in initializer.__globals__.values()
            if callable(value) and getattr(value, "__module__", None) == __name__
        )
        hook_branches = tuple(
            instruction.opname
            for instruction in dis.get_instructions(initializer)
            if instruction.opname in {"FOR_ITER", "GET_ITER"}
        )
        print(f"{'':32} retained_callbacks={callbacks!r} hook_loops={hook_branches!r}")


def main() -> None:
    """Print Campaign 7 callback and permanent-canary evidence."""

    print(f"Custom validation ({_REPEATS} samples x {_ITERATIONS:,} constructions)")
    benchmark_hooks()
    print(f"Unhooked permanent canaries ({_REPEATS} samples x {_ITERATIONS:,} constructions)")
    benchmark_unhooked_canaries()


if __name__ == "__main__":
    main()
