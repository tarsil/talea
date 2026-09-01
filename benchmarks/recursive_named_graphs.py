"""Measure recursive alias and TypedDict graph resolution and execution."""

import gc
import tracemalloc
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from statistics import median
from timeit import Timer
from typing import Annotated, Literal, TypedDict, cast

from talea import Contract, Discriminator, SerializationError, ValidationError

_REPEATS = 5
_EXECUTION_ITERATIONS = 200
_COLD_ITERATIONS = 100

type Operation = Callable[[], object]
type JSONValue = str | int | list[JSONValue] | dict[str, JSONValue]
type SimpleAlias = list[int]
type Tree[T] = T | list[Tree[T]]


class Node(TypedDict):
    value: int
    children: list[Node]


class LiteralNode(TypedDict):
    kind: Literal["literal"]
    value: int


class AddNode(TypedDict):
    kind: Literal["add"]
    left: Expr
    right: Expr


type Expr = Annotated[LiteralNode | AddNode, Discriminator("kind")]


@dataclass(frozen=True, slots=True)
class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    minimum: float
    median: float


def measure(operation: Operation, iterations: int = _EXECUTION_ITERATIONS) -> Measurement:
    """Measure one warmed operation across independent samples."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    values = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(values), median(values))


def report(case: str, result: Measurement) -> None:
    """Print one stable timing row."""

    print(f"{case:39} min={result.minimum:11.1f} ns/op median={result.median:11.1f} ns/op")


def node_data(depth: int) -> dict[str, object]:
    """Build one acyclic single-branch TypedDict tree."""

    node: dict[str, object] = {"value": depth, "children": []}
    for value in reversed(range(depth)):
        node = {"value": value, "children": [node]}
    return node


def expression_data(depth: int) -> dict[str, object]:
    """Build one left-deep tagged expression."""

    expression: dict[str, object] = {"kind": "literal", "value": 1}
    for _ in range(depth):
        expression = {
            "kind": "add",
            "left": expression,
            "right": {"kind": "literal", "value": 1},
        }
    return expression


def broad_expression(depth: int) -> dict[str, object]:
    """Build one balanced binary tagged expression."""

    if depth == 0:
        return {"kind": "literal", "value": 1}
    child = broad_expression(depth - 1)
    return {"kind": "add", "left": child, "right": child.copy()}


def manual_node_validation(value: Mapping[str, object]) -> None:
    """Validate the benchmark shape with equivalent direct recursion."""

    if type(value) is not dict or type(value.get("value")) is not int:
        raise TypeError
    children = value.get("children")
    if type(children) is not list:
        raise TypeError
    for child in children:
        manual_node_validation(cast(Mapping[str, object], child))


def capture_cycle(operation: Operation) -> None:
    """Consume one expected cycle failure."""

    try:
        operation()
    except (ValidationError, SerializationError):
        return
    raise AssertionError("cycle operation succeeded")


def retained_bytes() -> tuple[int, int]:
    """Measure retained and peak bytes after short-lived Contract graphs."""

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    contracts = [Contract(Tree[int]) for _ in range(100)]
    _, peak = tracemalloc.get_traced_memory()
    contracts.clear()
    gc.collect()
    after = tracemalloc.take_snapshot()
    retained = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    tracemalloc.stop()
    return retained, peak


def main() -> None:
    """Print Campaign 15 cold, depth, tagged, cycle, and memory evidence."""

    simple = Contract(SimpleAlias)
    recursive = Contract(JSONValue)
    generic = Contract(Tree[int])
    nodes = Contract(Node)
    expressions = Contract(Expr)

    print("Alias resolution and execution")
    report("simple alias validation", measure(lambda: simple.validate([1, 2])))
    report("recursive alias cold Contract", measure(lambda: Contract(JSONValue), _COLD_ITERATIONS))
    report("recursive alias warm validation", measure(lambda: recursive.validate([1, [2]])))
    report("generic recursive cold Contract", measure(lambda: Contract(Tree[int]), _COLD_ITERATIONS))
    report("generic recursive warm validation", measure(lambda: generic.validate([1, [2]])))

    print("\nRecursive TypedDict depths")
    for depth in (1, 3, 10, 20):
        value = cast(Node, node_data(depth))
        encoded = nodes.to_json(value)
        report(f"depth {depth:2} manual validation", measure(lambda value=value: manual_node_validation(value)))
        report(f"depth {depth:2} strict validation", measure(lambda value=value: nodes.validate(value)))
        report(f"depth {depth:2} from_python", measure(lambda value=value: nodes.from_python(value)))
        report(f"depth {depth:2} from_json", measure(lambda encoded=encoded: nodes.from_json(encoded)))
        report(f"depth {depth:2} to_python", measure(lambda value=value: nodes.to_python(value)))
        report(f"depth {depth:2} to_json", measure(lambda value=value: nodes.to_json(value)))

    print("\nRecursive tagged AST")
    for label, value in (
        ("leaf", expression_data(0)),
        ("depth 5", expression_data(5)),
        ("depth 15", expression_data(15)),
        ("broad depth 6", broad_expression(6)),
    ):
        typed_value = cast(LiteralNode | AddNode, value)
        encoded = expressions.to_json(typed_value)
        report(f"{label} Mapping input", measure(lambda value=value: expressions.from_python(value)))
        report(f"{label} JSON input", measure(lambda encoded=encoded: expressions.from_json(encoded)))
        report(
            f"{label} JSON output",
            measure(lambda value=typed_value: expressions.to_json(value)),
        )

    cyclic: list[object] = []
    cyclic.append(cyclic)
    report("cycle detection input", measure(lambda: capture_cycle(lambda: recursive.from_python(cyclic))))
    report(
        "cycle detection output",
        measure(lambda: capture_cycle(lambda: recursive.to_python(cast(JSONValue, cyclic)))),
    )
    retained, peak = retained_bytes()
    print(f"\n100 discarded recursive Contracts retained={retained} bytes peak={peak} bytes")


if __name__ == "__main__":
    main()
