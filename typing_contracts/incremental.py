"""Static contracts for lazy retained Contract item execution."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, TypedDict, assert_type

from talea import Contract, Representation, Spec, ValidationError
from talea.contract import ItemPolicy


class User(Spec):
    name: str


class Payload(TypedDict):
    value: int


@dataclass
class Event:
    value: int


class Identifier:
    def __init__(self, value: int) -> None:
        self.value = value


def load_identifier(value: str) -> Identifier:
    return Identifier(int(value))


type IdentifierValue = Annotated[Identifier, Representation(input=str, load=load_identifier)]


def report(index: int, error: ValidationError) -> None:
    assert_type(index, int)
    assert_type(error, ValidationError)


assert_type(Contract(int).iter_validate([1]), Iterator[int])
assert_type(Contract(User).iter_validate([User(name="Ada")], on_error=report), Iterator[User])
assert_type(Contract[Payload](Payload).iter_python([{"value": 1}]), Iterator[Payload])
assert_type(Contract(Event).iter_python([{"value": 1}]), Iterator[Event])
assert_type(Contract[Identifier](IdentifierValue).iter_python(["1"]), Iterator[Identifier])
assert_type(
    Contract[list[int]](list[int]).iter_python([[1]], item_policy=ItemPolicy(max_items=None)),
    Iterator[list[int]],
)

Contract(int).iter_validate(1)  # ty: ignore[invalid-argument-type]
Contract(int).iter_validate([1], on_error=lambda _index, _error: 1)  # ty: ignore[invalid-argument-type]
Contract(int).iter_validate([1], item_policy=object())  # ty: ignore[invalid-argument-type]
Contract(int).iter_python([1], policy=ItemPolicy())  # ty: ignore[invalid-argument-type]
