"""Positive static-typing contract for Talea Spec declarations."""

from copy import replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, TypedDict, assert_type
from uuid import UUID

from talea import (
    Alias,
    Contract,
    Deprecated,
    Description,
    ErrorCode,
    ErrorData,
    Examples,
    Ge,
    MaxLength,
    MinLength,
    ReadOnly,
    Sensitive,
    Spec,
    Title,
    ValidationError,
    WriteOnly,
    check,
    create_spec,
    field,
    serialize,
    transform,
)
from talea.introspection import ContractInfo, SpecInfo, inspect_contract, inspect_spec


class User(Spec):
    id: int
    name: str
    active: bool = True
    tags: list[str] = field(default_factory=list)


user: User = User(id=1, name="Tiago")
identifier: int = user.id
name: str = user.name
tags: list[str] = user.tags

assert_type(User(id=1, name="Tiago"), User)
assert_type(User(id=1, name="Tiago", active=False, tags=["maintainer"]), User)
assert_type(User.from_mapping({"id": 1, "name": "Tiago"}), User)
assert_type(User.from_json('{"id": 1, "name": "Tiago"}'), User)
assert_type(user.to_dict(), dict[str, object])
assert_type(user.to_dict(include={"id"}, exclude_none=True), dict[str, object])
assert_type(user.to_json(), str)
assert_type(replace(user, name="Grace"), User)
assert_type(inspect_spec(User), SpecInfo)
assert_type(inspect_contract(Contract(int)), ContractInfo)
assert (identifier, name, tags) == (1, "Tiago", [])

assert_type(Contract(int).validate(1), int)
assert_type(Contract[list[int]](list[int]).validate([1]), list[int])
assert_type(Contract[list[int]](list[int]).from_python([1]), list[int])
assert_type(Contract[list[int]](list[int]).from_json("[1]"), list[int])
assert_type(Contract[list[int]](list[int]).to_python([1]), object)
assert_type(Contract[list[int]](list[int]).to_json([1]), str)


class ContractPayload(TypedDict):
    id: int


assert_type(Contract[ContractPayload](ContractPayload).validate({"id": 1}), ContractPayload)
assert_type(Contract[User](User).validate(user), User)
assert_type(create_spec("Dynamic", {"value": int}), type[Spec])
assert_type(
    create_spec("DocumentedDynamic", {"value": int}, metadata=(Title("Dynamic"), Deprecated())),
    type[Spec],
)


class MetadataPayload(Spec):
    secret: Annotated[
        str,
        Title("Secret"),
        Description("Sensitive text."),
        Examples("example"),
        Deprecated(),
        ReadOnly(),
        WriteOnly(),
        Sensitive(),
    ]


assert_type(MetadataPayload(secret="value").secret, str)
assert_type(Contract[int](Annotated[int, Sensitive()]).validate(1), int)


class DynamicBase(Spec):
    base: int


assert_type(create_spec("DynamicChild", {"value": str}, base=DynamicBase), type[DynamicBase])


def custom_loads(data: str | bytes | bytearray) -> object:
    """Exercise the external decoder callable contract."""

    return {"id": 1, "name": "Tiago"}


assert_type(User.from_json("encoded", loads=custom_loads), User)


def custom_dumps(value: object) -> bytes:
    """Exercise the external encoder callable contract."""

    return b"{}"


assert_type(user.to_json(dumps=custom_dumps), str)


class Aliased(Spec):
    internal_name: Annotated[str, Alias("externalName")]


aliased = Aliased(internal_name="value")
assert_type(aliased.internal_name, str)
assert_type(aliased.to_dict(), dict[str, object])


class Serialized(Spec):
    value: int

    @serialize("value")
    def output(value: int) -> str:
        return str(value)


assert_type(Serialized.output(1), str)


class Person(Spec):
    name: str
    active: bool = True
    aliases: list[str] = field(default_factory=list)


class Employee(Person):
    employee_id: int


class Department(Spec):
    manager: Employee
    members: list[Person]
    deputy: Employee | None = None


class NamedEmployee(Employee):
    name: str = "unknown"


class Identity(Spec):
    value: int | str
    person: Person


class NarrowIdentity(Identity):
    value: str
    person: Employee


employee = Employee(name="Ada", employee_id=1)
person: Person = employee
department = Department(manager=employee, members=[person, employee])
inherited_name: str = employee.name
inherited_aliases: list[str] = employee.aliases

assert_type(Employee(name="Ada", employee_id=1), Employee)
assert_type(Employee.from_mapping({"name": "Ada", "employee_id": 1}), Employee)
assert_type(Employee(name="Ada", employee_id=1, active=False, aliases=["A"]), Employee)
assert_type(NamedEmployee(employee_id=2), NamedEmployee)
assert_type(department.manager, Employee)
assert_type(department.members, list[Person])
assert_type(department.deputy, Employee | None)
assert_type(NarrowIdentity(value="staff", person=employee).value, str)


class Box[T](Spec):
    value: T


class Page[T](Spec):
    items: list[T]


class Response[T](Spec):
    page: Page[T]


class Tree[T](Spec):
    value: T
    children: list[Tree[T]]


assert_type(Box[int](value=1), Box[int])
assert_type(Box[int](value=1).value, int)
assert_type(Contract[Page[User]](Page[User]).validate(Page[User](items=[user])), Page[User])
assert_type(Response[str](page=Page[str](items=["typed"])).page, Page[str])
assert_type(Tree[int](value=1, children=[]).children, list[Tree[int]])


class Status(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ProductionPayload(Spec):
    identifier: UUID
    day: date
    status: Status
    operation: Literal["create", "delete"]
    score: Annotated[int, Ge(0)]
    tags: Annotated[list[str], MinLength(1), MaxLength(5)]
    amount: Decimal


production = ProductionPayload(
    identifier=UUID(int=0),
    day=date.min,
    status=Status.ACTIVE,
    operation="create",
    score=1,
    tags=["typed"],
    amount=Decimal("1.0"),
)

assert_type(production.identifier, UUID)
assert_type(production.status, Status)
assert_type(production.operation, Literal["create", "delete"])
assert_type(production.score, int)
assert_type(production.tags, list[str])


class Interval(Spec):
    start: int
    end: int

    @transform("start")
    def parse_start(value: object) -> object:
        return int(value) if isinstance(value, str) else value

    @check("start")
    def non_negative(start: int) -> None:
        if start < 0:
            raise ValueError("start must be non-negative")

    @check("start", "end")
    def ordered(start: int, end: int) -> None:
        if end < start:
            raise ValueError("end must not precede start")


class BoundedInterval(Interval):
    @check("end")
    def finite_end(end: int) -> None:
        if end > 1_000:
            raise ValueError("end is too large")


interval = BoundedInterval(start=1, end=2)
assert_type(interval.start, int)
assert_type(BoundedInterval.parse_start("3"), object)


def project_validation_error(error: ValidationError) -> list[ErrorData]:
    """Exercise the public typed handling contract without manufacturing a failure."""

    assert_type(error.code, ErrorCode)
    assert_type(error.location, tuple[object, ...])
    assert_type(error.errors(), list[ErrorData])
    return error.errors()
