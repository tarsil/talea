"""Positive Python 3.15 TypeForm contracts for Talea public APIs."""

from dataclasses import dataclass
from typing import Annotated, Literal, NewType, TypedDict, assert_type

from talea import Contract, Representation, Spec, create_spec, serialize


class Payload(TypedDict):
    value: int


class Model(Spec):
    value: int


class Box[T](Spec):
    value: T


@dataclass
class DataclassPayload:
    value: int


type Identifier = int
type RecursiveValue = int | list[RecursiveValue]
UserId = NewType("UserId", int)
type MetadataAlias = Annotated[int, object()]

assert_type(Contract(int).validate(1), int)
assert_type(Contract(str | int).validate("value"), str | int)
assert_type(Contract(list[int]).validate([1]), list[int])
assert_type(Contract(dict[str, int]).validate({"value": 1}), dict[str, int])
assert_type(Contract(Payload).validate({"value": 1}), Payload)
assert_type(Contract(Model).validate(Model(value=1)), Model)
assert_type(Contract(Box[int]).validate(Box[int](value=1)), Box[int])
assert_type(Contract(DataclassPayload).validate(DataclassPayload(1)), DataclassPayload)
assert_type(Contract(Identifier).validate(1), int)
assert_type(Contract(Annotated[int, "metadata"]).validate(1), int)
assert_type(Contract(Literal["value"]).validate("value"), Literal["value"])
assert_type(Contract(RecursiveValue).validate([1]), RecursiveValue)
assert_type(Contract(UserId).validate(UserId(1)), UserId)
assert_type(Contract(MetadataAlias).validate(1), int)
assert_type(create_spec("Dynamic", {"value": int}), type[Spec])

# TypeForm accepts this type expression statically; Talea still rejects the
# executable open generic at runtime in favor of a concrete specialization.
Contract(Box)


def load_text(value: str) -> Model:
    return Model(value=int(value))


def dump_model(value: Model) -> Payload:
    return {"value": value.value}


full = Representation(input=str, load=load_text, output=Payload, dump=dump_model)
input_only: Representation[str | int, Model, object] = Representation(
    input=str | int,
    load=lambda value: Model(value=int(value)),
)
output_only: Representation[object, Model, Payload] = Representation(output=Payload, dump=dump_model)

assert_type(full, Representation[str, Model, Payload])
assert_type(input_only, Representation[str | int, Model, object])
assert_type(output_only, Representation[object, Model, Payload])


class Serialized(Spec):
    value: int

    @serialize("value", output=Payload)
    def dump_value(value: int) -> Payload:
        return {"value": value}


assert_type(Serialized.dump_value(1), Payload)
