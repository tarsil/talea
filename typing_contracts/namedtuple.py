"""Static contracts for canonical ``typing.NamedTuple`` interoperability."""

from collections.abc import Iterator
from typing import Generic, NamedTuple, TypeVar, assert_type

from talea import Contract, Spec, validate_call


class Point(NamedTuple):
    x: int
    y: int


ValueT = TypeVar("ValueT")


class Pair(NamedTuple, Generic[ValueT]):
    first: ValueT
    second: ValueT


class Envelope(Spec):
    point: Point


point_contract = Contract(Point)
pair_contract = Contract(Pair[int])

assert_type(point_contract, Contract[Point])
assert_type(point_contract.validate(Point(1, 2)), Point)
assert_type(point_contract.from_python([1, 2]), Point)
assert_type(point_contract.from_json("[1,2]"), Point)
assert_type(point_contract.iter_validate([Point(1, 2)]), Iterator[Point])
assert_type(point_contract.iter_python([[1, 2]]), Iterator[Point])
assert_type(point_contract.iter_jsonl(["[1,2]"]), Iterator[Point])
assert_type(pair_contract, Contract[Pair[int]])
assert_type(pair_contract.validate(Pair(1, 2)), Pair[int])
assert_type(Contract(Envelope).validate(Envelope(point=Point(1, 2))), Envelope)
assert_type(point_contract.to_python(Point(1, 2)), object)
assert_type(point_contract.to_json(Point(1, 2)), str)


@validate_call
def identity(value: Point) -> Point:
    return value


assert_type(identity(Point(1, 2)), Point)
