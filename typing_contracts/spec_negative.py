"""Negative static-typing probes for Talea Spec declarations."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from talea import (
    Alias,
    Contract,
    Ge,
    Representation,
    ResourcePolicy,
    Sensitive,
    Spec,
    Title,
    apply_patch,
    check,
    create_spec,
    derive_spec,
    field,
    serialize,
    transform,
    validate_call,
)
from talea.introspection import inspect_contract, inspect_spec


@dataclass
class DataclassUser:
    id: int


Contract(DataclassUser).to_python(object())  # ty: ignore[invalid-argument-type]
Contract(DataclassUser).to_json("wrong")  # ty: ignore[invalid-argument-type]


class User(Spec):
    id: int
    active: bool = True
    tags: list[str] = field(default_factory=list)


User()  # ty: ignore[missing-argument]
User(id="1")  # ty: ignore[invalid-argument-type]
User(id=1, active="yes")  # ty: ignore[invalid-argument-type]
User(id=1, tags=[1])  # ty: ignore[invalid-argument-type]
User(id=1, unknown=True)  # ty: ignore[unknown-argument]
User.from_mapping([])  # ty: ignore[invalid-argument-type]
User.from_json(1)  # ty: ignore[invalid-argument-type]
User.from_json("{}", loads=lambda: {})  # ty: ignore[invalid-argument-type]
User.from_mapping({"id": 1}, policy=object())  # ty: ignore[invalid-argument-type]
User.from_json("{}", policy=object())  # ty: ignore[invalid-argument-type]
User(id=1).to_dict(include=["id"])  # ty: ignore[invalid-argument-type]
User(id=1).to_dict(include={"id": False})  # ty: ignore[invalid-argument-type]
User(id=1).to_dict(include={"id": 1})  # ty: ignore[invalid-argument-type]
User(id=1).to_dict(include={"id": ["nested"]})  # ty: ignore[invalid-argument-type]
User(id=1).to_json(dumps=lambda value: 1)  # ty: ignore[invalid-argument-type]
Contract[int](int).to_python("1")  # ty: ignore[invalid-argument-type]
Contract[int](int).to_json("1")  # ty: ignore[invalid-argument-type]
Contract[int](int).from_json(1)  # ty: ignore[invalid-argument-type]
Contract[int](int, policy=object())  # ty: ignore[no-matching-overload]
Contract[int](int).from_python(1, policy=object())  # ty: ignore[invalid-argument-type]
Contract[int](int).to_python(1, policy=ResourcePolicy())  # ty: ignore[unknown-argument]
User(id=1).to_json(policy=ResourcePolicy())  # ty: ignore[unknown-argument]
Contract[int](int).json_schema(mode="validation")  # ty: ignore[invalid-argument-type]
User.json_schema(mode="serialization")  # ty: ignore[invalid-argument-type]
create_spec(1, {"value": int})  # ty: ignore[invalid-argument-type]
create_spec("Invalid", [])  # ty: ignore[invalid-argument-type]
create_spec("Invalid", {"value": int}, base=object)  # ty: ignore[no-matching-overload]
create_spec("Invalid", {"value": int}, metadata=1)  # ty: ignore[invalid-argument-type]
derive_spec(object)  # ty: ignore[invalid-argument-type]
derive_spec(User, partial=1)  # ty: ignore[invalid-argument-type]
derive_spec(User, mode="request")  # ty: ignore[invalid-argument-type]
apply_patch(User(id=1), object())  # ty: ignore[invalid-argument-type]
inspect_spec(User(id=1))  # ty: ignore[invalid-argument-type]
inspect_contract(int)  # ty: ignore[invalid-argument-type]
Title(1)  # ty: ignore[invalid-argument-type]
Sensitive("yes")  # ty: ignore[invalid-argument-type]


class TypedMoney:
    pass


async def async_money(value: str) -> TypedMoney:
    return TypedMoney()


bad_loader_result: Representation[str, TypedMoney, object] = Representation(
    input=str,
    load=lambda value: value,
)  # ty: ignore[invalid-assignment]
bad_async_loader: Representation[str, TypedMoney, object] = Representation(
    input=str,
    load=async_money,
)  # ty: ignore[invalid-assignment]
bad_dump_input: Representation[str, TypedMoney, str] = Representation(
    output=str,
    dump=lambda value: 1,
)  # ty: ignore[invalid-assignment]


type RecursiveValue = int | list[RecursiveValue]


class RecursivePayload(Spec):
    root: RecursiveValue


Contract[RecursiveValue](RecursiveValue).to_json(["wrong"])  # ty: ignore[invalid-argument-type]
RecursivePayload(root=["wrong"])  # ty: ignore[invalid-argument-type]


class Box[T](Spec):
    value: T


Box[int](value="1")  # ty: ignore[invalid-argument-type]

user = User(id=1)


@validate_call
def typed_transfer(amount: int, fee: int = 1) -> int:
    return amount - fee


typed_transfer("3")  # ty: ignore[invalid-argument-type]
typed_transfer(unexpected=3)  # ty: ignore[missing-argument, unknown-argument]
wrong_transfer_result: str = typed_transfer(3)  # ty: ignore[invalid-assignment]
user.id = 2  # ty: ignore[invalid-assignment]


class InvalidStaticDefault(Spec):
    active: bool = "yes"  # ty: ignore[invalid-assignment]


class InvalidFactory(Spec):
    tags: list[str] = field(default_factory=lambda: [1])  # ty: ignore[invalid-assignment]


class Person(Spec):
    name: str
    active: bool = True
    aliases: list[str] = field(default_factory=list)


class Employee(Person):
    employee_id: int


class Address(Spec):
    city: str


class Department(Spec):
    manager: Employee
    members: list[Person]
    deputy: Employee | None = None


Employee(employee_id=1)  # ty: ignore[missing-argument]
Employee(name="Ada")  # ty: ignore[missing-argument]
Employee(name="Ada", employee_id="1")  # ty: ignore[invalid-argument-type]
Department(manager=Person(name="Ada"), members=[])  # ty: ignore[invalid-argument-type]
Department(manager=Employee(name="Ada", employee_id=1), members=[Address(city="Zurich")])  # ty: ignore[invalid-argument-type]
Department(manager=Employee(name="Ada", employee_id=1), members=[], deputy=Address(city="Zurich"))  # ty: ignore[invalid-argument-type]
Department(manager=Employee(name="Ada", employee_id=1), members=[], unknown=True)  # ty: ignore[unknown-argument]

employee = Employee(name="Ada", employee_id=1)
employee.name = "Grace"  # ty: ignore[invalid-assignment]
employee.employee_id = 2  # ty: ignore[invalid-assignment]

base_only: Employee = Person(name="Ada")  # ty: ignore[invalid-assignment]
sibling: Person = Address(city="Zurich")  # ty: ignore[invalid-assignment]


class Status(StrEnum):
    ACTIVE = "active"


class StrictPayload(Spec):
    identifier: UUID
    status: Status
    operation: Literal["create", "delete"]
    score: Annotated[int, Ge(0)]


StrictPayload(
    identifier="00000000-0000-0000-0000-000000000000",  # ty: ignore[invalid-argument-type]
    status="active",  # ty: ignore[invalid-argument-type]
    operation="update",  # ty: ignore[invalid-argument-type]
    score="1",  # ty: ignore[invalid-argument-type]
)


class InvalidCheckReturn(Spec):
    value: int

    @check("value")  # ty: ignore[invalid-argument-type]
    def replaces(value: int) -> int:
        return value


class TypedTransform(Spec):
    value: int

    @transform("value")
    def parse(value: object) -> object:
        return value


TypedTransform(value="1")  # ty: ignore[invalid-argument-type]


class AsyncCheck(Spec):
    value: int

    @check("value")  # ty: ignore[invalid-argument-type]
    async def invalid(value: int) -> None:
        pass


class Aliased(Spec):
    internal_name: Annotated[str, Alias("externalName")]


Aliased(externalName="value")  # ty: ignore[missing-argument, unknown-argument]
Aliased()  # ty: ignore[missing-argument]


class InvalidSerializer(Spec):
    value: int

    @serialize("value")
    async def output(value: int) -> str:
        return str(value)
