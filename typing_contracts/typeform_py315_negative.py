"""Negative Python 3.15 TypeForm contracts for Talea public APIs."""

from typing import TypedDict

from talea import Contract, Representation, Spec, create_spec, serialize


class Payload(TypedDict):
    value: int


class Model(Spec):
    value: int


Contract(123)  # ty: ignore[invalid-type-form]
Contract("not a type form")  # ty: ignore[invalid-syntax-in-forward-annotation]
Contract(object())  # ty: ignore[invalid-type-form]
create_spec("Invalid", {"value": 123})  # ty: ignore[invalid-type-form]

Representation(input=123, load=lambda value: Model(value=value))  # ty: ignore[invalid-type-form]
Representation(
    input="not a type form",  # ty: ignore[invalid-syntax-in-forward-annotation]
    load=lambda value: Model(value=1),
)
Representation(output=object(), dump=lambda value: {"value": value.value})  # ty: ignore[invalid-type-form]


def bad_load(value: str) -> str:
    return value


bad_loader: Representation[str, Model, Payload] = Representation(  # ty: ignore[invalid-assignment]
    input=str,
    load=bad_load,
    output=Payload,
    dump=lambda value: {"value": value.value},  # ty: ignore[invalid-argument-type]
)


class InvalidSerialized(Spec):
    value: int

    @serialize("value", output=123)  # ty: ignore[invalid-type-form]
    def dump_value(value: int) -> int:
        return value


class MismatchedSerialized(Spec):
    value: int

    @serialize("value", output=Payload)  # ty: ignore[invalid-argument-type]
    def dump_value(value: int) -> str:
        return str(value)
