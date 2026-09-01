"""Positive static-typing contract for Talea Spec declarations."""

from collections.abc import Callable
from copy import replace
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, NotRequired, TypedDict, Unpack, assert_type
from uuid import UUID

from talea import (
    Alias,
    Contract,
    Deprecated,
    Description,
    Discriminator,
    ErrorCode,
    ErrorData,
    ErrorTree,
    Examples,
    Ge,
    MaxLength,
    MinLength,
    ReadOnly,
    Representation,
    ResourceLimitError,
    ResourcePolicy,
    SchemaProjectionError,
    Sensitive,
    Spec,
    Title,
    ValidationError,
    WriteOnly,
    apply_patch,
    check,
    create_spec,
    derive_spec,
    field,
    serialize,
    transform,
    validate_call,
)
from talea.errors import ErrorTreeData
from talea.introspection import (
    CallableInfo,
    ContractInfo,
    RepresentationInfo,
    SpecInfo,
    inspect_callable,
    inspect_contract,
    inspect_spec,
)


@dataclass
class DataclassUser:
    id: int
    name: str


@dataclass
class DataclassPage[T]:
    items: list[T]


dataclass_contract = Contract(DataclassUser)
generic_dataclass_contract: Contract[DataclassPage[int]] = Contract(DataclassPage[int])

assert_type(dataclass_contract.validate(DataclassUser(1, "Ada")), DataclassUser)
assert_type(dataclass_contract.from_python({"id": 1, "name": "Ada"}), DataclassUser)
assert_type(dataclass_contract.from_json('{"id":1,"name":"Ada"}'), DataclassUser)
assert_type(generic_dataclass_contract.from_python({"items": [1]}), DataclassPage[int])


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
assert_type(User.from_mapping({"id": 1, "name": "Tiago"}, policy=ResourcePolicy()), User)
assert_type(User.from_json('{"id": 1, "name": "Tiago"}'), User)
assert_type(User.from_json('{"id": 1, "name": "Tiago"}', policy=ResourcePolicy()), User)
assert_type(User.json_schema(), dict[str, object])
assert_type(User.json_schema(mode="output"), dict[str, object])
assert_type(User.openapi_schema(), dict[str, object])
assert_type(user.to_dict(), dict[str, object])
assert_type(user.to_dict(include={"id"}, exclude_none=True), dict[str, object])
assert_type(
    user.to_dict(include={"id": True, "tags": {"unused": True}}),
    dict[str, object],
)
assert_type(user.to_dict(exclude=frozenset({"active"})), dict[str, object])
assert_type(user.to_json(), str)
assert_type(replace(user, name="Grace"), User)
assert_type(inspect_spec(User), SpecInfo)
assert_type(inspect_contract(Contract(int)), ContractInfo)
assert (identifier, name, tags) == (1, "Tiago", [])

assert_type(Contract(int).validate(1), int)
assert_type(Contract[list[int]](list[int]).validate([1]), list[int])
assert_type(Contract[list[int]](list[int]).from_python([1]), list[int])
assert_type(
    Contract[list[int]](list[int], policy=ResourcePolicy()).from_python(
        [1],
        policy=ResourcePolicy(max_nodes=None),
    ),
    list[int],
)
assert_type(Contract[list[int]](list[int]).from_json("[1]"), list[int])
assert_type(Contract[list[int]](list[int]).to_python([1]), object)
assert_type(Contract[list[int]](list[int]).to_json([1]), str)
assert_type(Contract[list[int]](list[int]).json_schema(), dict[str, object])
assert_type(Contract[list[int]](list[int]).openapi_schema(mode="output"), dict[str, object])

schema_projection_error: type[TypeError] = SchemaProjectionError
resource_limit_error: type[Exception] = ResourceLimitError


class TypedMoney:
    pass


def typed_money_from_text(value: str) -> TypedMoney:
    return TypedMoney()


def typed_money_to_text(value: TypedMoney) -> str:
    return "money"


typed_input: Representation[str, TypedMoney, object] = Representation(
    input=str,
    load=typed_money_from_text,
)
typed_full: Representation[str, TypedMoney, str] = Representation(
    input=str,
    load=typed_money_from_text,
    output=str,
    dump=typed_money_to_text,
)
typed_output: Representation[object, TypedMoney, str] = Representation(
    output=str,
    dump=typed_money_to_text,
)
type TypedMoneyValue = Annotated[TypedMoney, typed_input]
type FullTypedMoneyValue = Annotated[TypedMoney, typed_full]
typed_money_contract: Contract[TypedMoney] = Contract(TypedMoneyValue)
assert_type(typed_money_contract.validate(TypedMoney()), TypedMoney)
assert_type(typed_money_contract.from_python("money"), TypedMoney)
assert_type(Contract[list[TypedMoney]](list[TypedMoneyValue]).from_python(["money"]), list[TypedMoney])
assert_type(Contract[TypedMoney](FullTypedMoneyValue).to_python(TypedMoney()), object)
assert_type(Contract[TypedMoney](FullTypedMoneyValue).to_json(TypedMoney()), str)
assert_type(inspect_contract(Contract[TypedMoney](FullTypedMoneyValue)).representations[0], RepresentationInfo)
assert_type(typed_output.dump, Callable[[TypedMoney], str] | None)


class TypedMoneySpec(Spec):
    amount: TypedMoneyValue


@dataclass
class TypedMoneyDataclass:
    amount: TypedMoneyValue


class TypedMoneyPayload(TypedDict):
    amount: TypedMoneyValue


assert_type(TypedMoneySpec(amount=TypedMoney()).amount, TypedMoney)
assert_type(Contract(TypedMoneyDataclass).from_python({"amount": "money"}), TypedMoneyDataclass)
assert_type(Contract[TypedMoneyPayload](TypedMoneyPayload).from_python({"amount": "money"}), TypedMoneyPayload)


class ContractPayload(TypedDict):
    id: int


assert_type(Contract[ContractPayload](ContractPayload).validate({"id": 1}), ContractPayload)
assert_type(Contract[User](User).validate(user), User)


@validate_call
def typed_transfer(amount: int, fee: int = 1) -> int:
    return amount - fee


@validate_call
def typed_payload(payload: ContractPayload) -> ContractPayload:
    return payload


assert_type(typed_transfer(3), int)
assert_type(typed_transfer(amount=3, fee=1), int)
assert_type(typed_payload({"id": 1}), ContractPayload)
assert_type(inspect_callable(typed_transfer), CallableInfo)


class CallableOptions(TypedDict):
    timeout: float
    trace_id: NotRequired[str]


@validate_call
def typed_complete(
    identifier: int,
    /,
    value: str,
    *items: int,
    flag: bool,
    **metadata: str,
) -> tuple[int, str]:
    del items, flag, metadata
    return identifier, value


@validate_call
def typed_unpack(**kwargs: Unpack[CallableOptions]) -> CallableOptions:
    return kwargs


class TypedService:
    @validate_call
    def method(self, value: int, /, *, enabled: bool = True) -> int:
        return value if enabled else 0

    @validate_call
    @classmethod
    def create(cls, value: int) -> int:
        del cls
        return value

    @validate_call
    @staticmethod
    def normalize(value: int) -> int:
        return value


@validate_call
async def typed_async_complete(
    identifier: int,
    /,
    value: str,
    *items: int,
    flag: bool,
    **metadata: str,
) -> tuple[int, str]:
    del items, flag, metadata
    return identifier, value


@validate_call
async def typed_async_unpack(**kwargs: Unpack[CallableOptions]) -> CallableOptions:
    return kwargs


@validate_call
async def typed_async_payload(payload: ContractPayload) -> ContractPayload:
    return payload


@validate_call
async def typed_async_structures(
    money: TypedMoneyValue,
    user: User,
    dataclass_user: DataclassUser,
    payload: ContractPayload,
) -> TypedMoneyValue:
    del user, dataclass_user, payload
    return money


class TypedAsyncService:
    @validate_call
    async def method(self, value: int, /, *, enabled: bool = True) -> int:
        return value if enabled else 0

    @validate_call
    @classmethod
    async def create(cls, value: int) -> int:
        del cls
        return value

    @validate_call
    @staticmethod
    async def normalize(value: int) -> int:
        return value


async def check_async_callable_types() -> None:
    service = TypedAsyncService()
    assert_type(await typed_async_complete(1, "value", 2, flag=True, source="sdk"), tuple[int, str])
    assert_type(await typed_async_unpack(timeout=1.0, trace_id="trace"), CallableOptions)
    assert_type(await typed_async_payload({"id": 1}), ContractPayload)
    assert_type(
        await typed_async_structures(TypedMoney(), user, DataclassUser(1, "Ada"), {"id": 1}),
        TypedMoney,
    )
    assert_type(await service.method(1, enabled=False), int)
    assert_type(await TypedAsyncService.create(1), int)
    assert_type(await TypedAsyncService.normalize(1), int)


typed_service = TypedService()
assert_type(typed_complete(1, "value", 2, 3, flag=True, source="sdk"), tuple[int, str])
assert_type(typed_unpack(timeout=1.0), CallableOptions)
assert_type(typed_unpack(timeout=1.0, trace_id="trace"), CallableOptions)
assert_type(typed_service.method(1, enabled=False), int)
assert_type(TypedService.create(1), int)
assert_type(TypedService.normalize(1), int)
assert_type(inspect_callable(typed_async_complete), CallableInfo)
assert_type(create_spec("Dynamic", {"value": int}), type[Spec])
UserPatch = derive_spec(User, partial=True)
UserInput = derive_spec(User, mode="input")
UserOutputPatch = derive_spec(User, mode="output", partial=True)
user_patch: Spec = UserPatch.from_mapping({"name": "Grace"})
assert_type(UserPatch, type[Spec])
assert_type(UserInput, type[Spec])
assert_type(UserOutputPatch, type[Spec])
assert_type(user_patch.present_fields, frozenset[str])
assert_type(apply_patch(user, user_patch), User)
assert_type(
    create_spec("DocumentedDynamic", {"value": int}, metadata=(Title("Dynamic"), Deprecated())),
    type[Spec],
)


type RecursiveValue = int | list[RecursiveValue]


class RecursivePayload(TypedDict):
    value: int
    children: list[RecursivePayload]


class RecursiveDocument(Spec):
    root: RecursiveValue


type RecursiveTree[T] = T | list[RecursiveTree[T]]

recursive_value: Contract[RecursiveValue] = Contract(RecursiveValue)
recursive_payload: Contract[RecursivePayload] = Contract(RecursivePayload)
recursive_tree: Contract[RecursiveTree[int]] = Contract(RecursiveTree[int])

assert_type(recursive_value.validate([1, [2]]), int | list[RecursiveValue])
assert_type(recursive_payload.validate({"value": 1, "children": []}), RecursivePayload)
assert_type(recursive_tree.validate([1, [2]]), int | list[RecursiveTree[int]])
assert_type(RecursiveDocument(root=[1, [2]]).root, int | list[RecursiveValue])


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


class TypedCard(Spec):
    kind: Literal["card"]
    number: str


class TypedBank(Spec):
    kind: Literal["bank"]
    iban: str


type TypedPayment = Annotated[TypedCard | TypedBank, Discriminator("kind")]
payment_contract: Contract[TypedPayment] = Contract(TypedPayment)
typed_card = TypedCard(kind="card", number="1")

assert_type(payment_contract.validate(typed_card), TypedCard | TypedBank)
assert_type(payment_contract.from_python({"kind": "card", "number": "1"}), TypedCard | TypedBank)
assert_type(payment_contract.from_json('{"kind":"bank","iban":"CH1"}'), TypedCard | TypedBank)


class PaymentEnvelope(Spec):
    payment: TypedPayment
    optional_payment: TypedPayment | None = None


assert_type(PaymentEnvelope(payment=typed_card).payment, TypedCard | TypedBank)
assert_type(PaymentEnvelope(payment=typed_card).optional_payment, TypedCard | TypedBank | None)


class TypedSuccess[T](Spec):
    kind: Literal["success"]
    value: T


class TypedFailure[T](Spec):
    kind: Literal["failure"]
    error: T


type TypedResult = Annotated[TypedSuccess[User] | TypedFailure[str], Discriminator("kind")]
assert_type(
    Contract[TypedResult](TypedResult).validate(TypedSuccess[User](kind="success", value=user)),
    TypedSuccess[User] | TypedFailure[str],
)


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


class MigrationAliased(Spec):
    internal_name: Annotated[str, Alias("externalName", legacy=("old_name", "internal_name"))]


assert_type(MigrationAliased.from_mapping({"old_name": "value"}), MigrationAliased)
assert_type(MigrationAliased.from_json('{"internal_name":"value"}'), MigrationAliased)


class MigratedBase(Spec):
    identifier: Annotated[int, Alias("accountId", legacy=("id",))]


class MigratedChild(MigratedBase):
    identifier: int


class MigratedBox[T](Spec):
    payload: Annotated[T, Alias("body", legacy=("payload",))]


@dataclass
class MigratedRecord:
    identifier: Annotated[int, Alias("accountId", legacy=("id",))]


assert_type(MigratedChild.from_mapping({"id": 1}), MigratedChild)
assert_type(MigratedBox[str].from_mapping({"payload": "value"}), MigratedBox[str])
assert_type(Contract(MigratedRecord).from_python({"id": 1}), MigratedRecord)
assert_type(derive_spec(MigrationAliased, partial=True), type[Spec])


class Serialized(Spec):
    value: int

    @serialize("value")
    def output(value: int) -> str:
        return str(value)


assert_type(Serialized.output(1), str)


class DeclaredSerialized(Spec):
    value: int

    @serialize("value", output=str)
    def output(value: int) -> str:
        return str(value)


assert_type(DeclaredSerialized.output(1), str)


class SerializedBox[T](Spec):
    value: T

    @serialize("value", output=T)
    def output(value: T) -> T:
        return value


assert_type(SerializedBox[int].output(1), int)


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
    tree = assert_type(error.error_tree(), ErrorTree)
    assert_type(tree.errors, tuple[ErrorData, ...])
    assert_type(tree.to_dict(), ErrorTreeData)
    return error.errors()
