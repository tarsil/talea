"""Measure nested Spec and flat inherited construction against equivalent Python."""

import dis
import gc
import sys
import tracemalloc
from collections.abc import Callable
from functools import partial
from statistics import median
from timeit import Timer
from typing import cast

from talea import Spec
from talea.validation import ValidationError

_REPEATS = 7
_DECLARATION_ITERATIONS = 1_000
_CONSTRUCTION_ITERATIONS = 100_000
_FAILURE_ITERATIONS = 20_000
_MEMORY_INSTANCES = 20_000

type Operation = Callable[[], object]
type Constructor = Callable[..., object]


class Measurement:
    """Minimum and median nanoseconds for one operation."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


def measure(operation: Operation, iterations: int) -> Measurement:
    """Measure one operation across independent timer samples."""

    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def print_measurement(case: str, implementation: str, result: Measurement) -> None:
    """Print one stable timing row."""

    print(f"{case:32} {implementation:24} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


def swallowed_failure(operation: Operation, error_type: type[BaseException]) -> Operation:
    """Return a timer-safe operation that consumes one expected failure."""

    def fail() -> object:
        try:
            return operation()
        except error_type:
            return None

    return fail


def immutable(instance: object, name: str, value: object) -> None:
    """Reject writes for hand-written immutable comparison classes."""

    raise AttributeError("instances are immutable")


class Point(Spec):
    """Two-field permanently trusted nested value."""

    x: int
    y: int


class Shape(Spec):
    """Direct nested Spec benchmark declaration."""

    point: Point


class PointCloud(Spec):
    """Container-of-Spec benchmark declaration."""

    points: list[Point]


class Basket(Spec):
    """Nested value whose list requires current-state revalidation."""

    items: list[int]


class Order(Spec):
    """Direct mutable nested-Spec benchmark declaration."""

    basket: Basket


class BasketGroup(Spec):
    """Container-of-mutable-Spec benchmark declaration."""

    baskets: list[Basket]


class HandPoint:
    """Equivalent hand-written immutable point."""

    __slots__ = ("x", "y")
    __setattr__ = immutable

    def __init__(self, *, x: int, y: int) -> None:
        if type(x) is not int or type(y) is not int:
            raise TypeError
        _HAND_POINT_X(self, x)
        _HAND_POINT_Y(self, y)


_HAND_POINT_X = vars(HandPoint)["x"].__set__
_HAND_POINT_Y = vars(HandPoint)["y"].__set__


class HandShape:
    """Equivalent hand-written immutable nested value."""

    __slots__ = ("point",)
    __setattr__ = immutable

    def __init__(self, *, point: HandPoint) -> None:
        if not isinstance(point, HandPoint):
            raise TypeError
        _HAND_SHAPE_POINT(self, point)


_HAND_SHAPE_POINT = vars(HandShape)["point"].__set__


class HandPointCloud:
    """Equivalent hand-written immutable point-list value."""

    __slots__ = ("points",)
    __setattr__ = immutable

    def __init__(self, *, points: list[HandPoint]) -> None:
        if type(points) is not list:
            raise TypeError
        for point in points:
            if not isinstance(point, HandPoint):
                raise TypeError
        _HAND_POINT_CLOUD_POINTS(self, points)


_HAND_POINT_CLOUD_POINTS = vars(HandPointCloud)["points"].__set__


class HandBasket:
    """Equivalent hand-written mutable nested value."""

    __slots__ = ("items",)
    __setattr__ = immutable
    items: list[int]

    def __init__(self, *, items: list[int]) -> None:
        if type(items) is not list:
            raise TypeError
        for item in items:
            if type(item) is not int:
                raise TypeError
        _HAND_BASKET_ITEMS(self, items)


_HAND_BASKET_ITEMS = vars(HandBasket)["items"].__set__


class HandOrder:
    """Equivalent hand-written current-state nested validator."""

    __slots__ = ("basket",)
    __setattr__ = immutable
    basket: HandBasket

    def __init__(self, *, basket: HandBasket) -> None:
        if not isinstance(basket, HandBasket):
            raise TypeError
        if type(basket.items) is not list:
            raise TypeError
        for item in basket.items:
            if type(item) is not int:
                raise TypeError
        _HAND_ORDER_BASKET(self, basket)


_HAND_ORDER_BASKET = vars(HandOrder)["basket"].__set__


class HandBasketGroup:
    """Equivalent hand-written container of current-state nested validators."""

    __slots__ = ("baskets",)
    __setattr__ = immutable
    baskets: list[HandBasket]

    def __init__(self, *, baskets: list[HandBasket]) -> None:
        if type(baskets) is not list:
            raise TypeError
        for basket in baskets:
            if not isinstance(basket, HandBasket):
                raise TypeError
            if type(basket.items) is not list:
                raise TypeError
            for item in basket.items:
                if type(item) is not int:
                    raise TypeError
        _HAND_BASKET_GROUP_BASKETS(self, baskets)


_HAND_BASKET_GROUP_BASKETS = vars(HandBasketGroup)["baskets"].__set__


def field_names(field_count: int) -> tuple[str, ...]:
    """Return deterministic names for inherited scaling declarations."""

    return tuple(f"field_{index}" for index in range(field_count))


def field_values(field_count: int) -> dict[str, int]:
    """Return valid values for inherited scaling declarations."""

    return {name: index for index, name in enumerate(field_names(field_count))}


def make_inherited_spec(field_count: int) -> Constructor:
    """Create a child Spec with a split inherited effective field set."""

    split = max(1, field_count // 2)
    names = field_names(field_count)
    base = type(
        f"InheritedBase{field_count}",
        (Spec,),
        {"__annotations__": dict.fromkeys(names[:split], int)},
    )
    return type(
        f"InheritedChild{field_count}",
        (base,),
        {"__annotations__": dict.fromkeys(names[split:], int)},
    )


def make_handwritten_inherited(field_count: int) -> Constructor:
    """Create equivalent slotted inheritance with one flat child initializer."""

    split = max(1, field_count // 2)
    names = field_names(field_count)
    base = type(
        f"HandBase{field_count}",
        (),
        {"__slots__": names[:split], "__setattr__": immutable},
    )
    child = type(
        f"HandChild{field_count}",
        (base,),
        {"__slots__": names[split:]},
    )
    lines = [f"def __init__(self, *, {', '.join(names)}):"]
    namespace: dict[str, object] = {}
    for index, name in enumerate(names):
        lines.extend((f"    if type({name}) is not int:", "        raise TypeError"))
        owner = base if index < split else child
        setter_name = f"_slot_{index}"
        namespace[setter_name] = vars(owner)[name].__set__
        lines.append(f"    {setter_name}(self, {name})")
    exec(compile("\n".join(lines), "<hand-written inherited benchmark>", "exec"), namespace)
    type.__setattr__(child, "__init__", namespace["__init__"])
    return child


def benchmark_declaration() -> None:
    """Measure nested and inherited class-definition costs."""

    simple_base = type("SimpleBase", (Spec,), {"__annotations__": {"field_0": int}})
    inherited_5_base = type(
        "Inherited5Base",
        (Spec,),
        {"__annotations__": dict.fromkeys(field_names(2), int)},
    )
    inherited_10_base = type(
        "Inherited10Base",
        (Spec,),
        {"__annotations__": dict.fromkeys(field_names(5), int)},
    )
    cases: dict[str, Operation] = {
        "declare nested reference": lambda: type("NestedDeclaration", (Spec,), {"__annotations__": {"point": Point}}),
        "declare simple child": lambda: type("SimpleChild", (simple_base,), {"__annotations__": {"field_1": int}}),
        "declare inherited 5 fields": lambda: type(
            "Inherited5Child",
            (inherited_5_base,),
            {"__annotations__": dict.fromkeys(field_names(5)[2:], int)},
        ),
        "declare inherited 10 fields": lambda: type(
            "Inherited10Child",
            (inherited_10_base,),
            {"__annotations__": dict.fromkeys(field_names(10)[5:], int)},
        ),
    }
    for name, operation in cases.items():
        print_measurement(name, "talea", measure(operation, _DECLARATION_ITERATIONS))


def benchmark_nested_construction() -> None:
    """Measure direct and list nesting with already-created values."""

    point = Point(x=1, y=2)
    hand_point = HandPoint(x=1, y=2)
    points = [point, point]
    hand_points = [hand_point, hand_point]
    cases: dict[str, tuple[Operation, Operation]] = {
        "construct nested Point": (partial(Shape, point=point), partial(HandShape, point=hand_point)),
        "construct list[Point]": (
            partial(PointCloud, points=points),
            partial(HandPointCloud, points=hand_points),
        ),
    }
    for name, (talea, handwritten) in cases.items():
        print_measurement(name, "talea validating", measure(talea, _CONSTRUCTION_ITERATIONS))
        print_measurement(name, "handwritten immutable", measure(handwritten, _CONSTRUCTION_ITERATIONS))


def benchmark_mutable_nested_construction() -> None:
    """Measure required current-state revalidation separately from trusted nesting."""

    basket = Basket(items=[1, 2])
    hand_basket = HandBasket(items=[1, 2])
    invalid_basket = Basket(items=[1, 2])
    invalid_hand_basket = HandBasket(items=[1, 2])
    cast(list[object], invalid_basket.items).append("invalid")
    cast(list[object], invalid_hand_basket.items).append("invalid")
    baskets = [basket, basket]
    hand_baskets = [hand_basket, hand_basket]
    success_cases: dict[str, tuple[Operation, Operation]] = {
        "construct nested Basket valid": (
            partial(Order, basket=basket),
            partial(HandOrder, basket=hand_basket),
        ),
        "construct list[Basket] valid": (
            partial(BasketGroup, baskets=baskets),
            partial(HandBasketGroup, baskets=hand_baskets),
        ),
    }
    for name, (talea, handwritten) in success_cases.items():
        print_measurement(name, "talea validating", measure(talea, _CONSTRUCTION_ITERATIONS))
        print_measurement(name, "handwritten immutable", measure(handwritten, _CONSTRUCTION_ITERATIONS))
    print_measurement(
        "construct nested Basket invalid",
        "talea validating",
        measure(
            swallowed_failure(partial(Order, basket=invalid_basket), ValidationError),
            _FAILURE_ITERATIONS,
        ),
    )
    print_measurement(
        "construct nested Basket invalid",
        "handwritten immutable",
        measure(
            swallowed_failure(partial(HandOrder, basket=invalid_hand_basket), TypeError),
            _FAILURE_ITERATIONS,
        ),
    )


def benchmark_inherited_construction() -> None:
    """Measure flat child construction at effective-field scaling canaries."""

    for count in (2, 5, 10):
        values = field_values(count)
        talea = make_inherited_spec(count)
        handwritten = make_handwritten_inherited(count)
        print_measurement(
            f"construct inherited {count} fields",
            "talea validating",
            measure(partial(talea, **values), _CONSTRUCTION_ITERATIONS),
        )
        print_measurement(
            f"construct inherited {count} fields",
            "handwritten immutable",
            measure(partial(handwritten, **values), _CONSTRUCTION_ITERATIONS),
        )


def retained_bytes_per_instance(operation: Operation) -> float:
    """Approximate retained traced bytes while keeping results alive."""

    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    instances = [operation() for _ in range(_MEMORY_INSTANCES)]
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if not instances:
        raise RuntimeError("memory benchmark did not retain instances")
    return (current - before) / _MEMORY_INSTANCES


def benchmark_memory() -> None:
    """Measure base, child, and independently allocated nested graphs."""

    talea_child = make_inherited_spec(2)
    hand_child = make_handwritten_inherited(2)
    cases: dict[str, tuple[Operation, Operation]] = {
        "memory base Point": (partial(Point, x=1, y=2), partial(HandPoint, x=1, y=2)),
        "memory inherited child": (
            partial(talea_child, **field_values(2)),
            partial(hand_child, **field_values(2)),
        ),
        "memory nested graph": (
            lambda: Shape(point=Point(x=1, y=2)),
            lambda: HandShape(point=HandPoint(x=1, y=2)),
        ),
    }
    for name, (talea, handwritten) in cases.items():
        print(f"{name:32} talea retained={retained_bytes_per_instance(talea):8.1f} B/result")
        print(f"{name:32} handwritten retained={retained_bytes_per_instance(handwritten):8.1f} B/result")
    print(f"sys.getsizeof Point={sys.getsizeof(Point(x=1, y=2))} HandPoint={sys.getsizeof(HandPoint(x=1, y=2))}")


def print_flat_constructor_evidence() -> None:
    """Report trusted-nesting and flat-inheritance bytecode evidence."""

    child = make_inherited_spec(5)
    initializer = vars(child)["__init__"]
    parent_initializer = vars(cast(type, child).__base__)["__init__"]
    calls = sum(instruction.opname == "CALL" for instruction in dis.Bytecode(initializer))
    print(
        "flat constructor evidence: "
        f"CALL opcodes={calls}, parent_init_global={parent_initializer in initializer.__globals__.values()}, "
        f"init_name_lookup={'__init__' in initializer.__code__.co_names}"
    )
    shape_initializer = vars(Shape)["__init__"]
    order_initializer = vars(Order)["__init__"]
    shape_calls = sum(instruction.opname == "CALL" for instruction in dis.Bytecode(shape_initializer))
    print(
        "trusted nesting evidence: "
        f"CALL opcodes={shape_calls}, nested_field_reads={bool({'x', 'y'} & set(shape_initializer.__code__.co_names))}, "
        f"trust_branch_names={tuple(name for name in shape_initializer.__code__.co_names if 'trust' in name)}"
    )
    print(f"mutable nesting evidence: canonical_field_reads={('items' in order_initializer.__code__.co_names)}")


def main() -> None:
    """Print Campaign 5 composition, inheritance, declaration, and memory evidence."""

    print(f"Composition declaration ({_REPEATS} samples x {_DECLARATION_ITERATIONS:,} declarations)")
    benchmark_declaration()
    print(f"Nested construction ({_REPEATS} samples x {_CONSTRUCTION_ITERATIONS:,} operations)")
    benchmark_nested_construction()
    print(
        f"Mutable nested construction ({_REPEATS} samples x {_CONSTRUCTION_ITERATIONS:,} successes; "
        f"{_FAILURE_ITERATIONS:,} failures)"
    )
    benchmark_mutable_nested_construction()
    print(f"Inherited construction ({_REPEATS} samples x {_CONSTRUCTION_ITERATIONS:,} operations)")
    benchmark_inherited_construction()
    print(f"Retained memory ({_MEMORY_INSTANCES:,} live results)")
    benchmark_memory()
    print_flat_constructor_evidence()


if __name__ == "__main__":
    main()
