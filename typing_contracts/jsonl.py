"""Static contracts for bounded JSON Lines input."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, TypedDict, assert_type

from talea import Contract, Representation, Spec, ValidationError
from talea.contract import ItemPolicy
from talea.jsonl import JsonlError, JsonlPolicy


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


def report_validation(index: int, error: ValidationError) -> None:
    assert_type(index, int)
    assert_type(error, ValidationError)


def report_jsonl(line: int, error: JsonlError) -> None:
    assert_type(line, int)
    assert_type(error, JsonlError)


async def report_async(_line: int, _error: JsonlError) -> None:
    pass


assert_type(Contract(int).iter_jsonl(["1"]), Iterator[int])
assert_type(Contract(User).iter_jsonl([b'{"name":"Ada"}']), Iterator[User])
assert_type(Contract[Payload](Payload).iter_jsonl(['{"value":1}']), Iterator[Payload])
assert_type(Contract(Event).iter_jsonl(['{"value":1}']), Iterator[Event])
assert_type(Contract[Identifier](IdentifierValue).iter_jsonl(['"1"']), Iterator[Identifier])
assert_type(
    Contract[list[int]](list[int]).iter_jsonl(
        ["[1]"],
        on_error=report_validation,
        on_jsonl_error=report_jsonl,
        item_policy=ItemPolicy(max_items=None),
        jsonl_policy=JsonlPolicy(max_total_bytes=None),
    ),
    Iterator[list[int]],
)

Contract(int).iter_jsonl([1])  # ty: ignore[invalid-argument-type]
Contract(int).iter_jsonl(["1"], on_error=lambda _index, _error: 1)  # ty: ignore[invalid-argument-type]
Contract(int).iter_jsonl(["1"], on_jsonl_error=lambda _line, _error: 1)  # ty: ignore[invalid-argument-type]
Contract(int).iter_jsonl(["1"], on_jsonl_error=report_async)  # ty: ignore[invalid-argument-type]
Contract(int).iter_jsonl(["1"], item_policy=JsonlPolicy())  # ty: ignore[invalid-argument-type]
Contract(int).iter_jsonl(["1"], jsonl_policy=ItemPolicy())  # ty: ignore[invalid-argument-type]
