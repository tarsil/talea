"""Negative static-typing probes for Talea Spec declarations."""

from talea import Spec, field


class User(Spec):
    id: int
    active: bool = True
    tags: list[str] = field(default_factory=list)


User()  # ty: ignore[missing-argument]
User(id="1")  # ty: ignore[invalid-argument-type]
User(id=1, active="yes")  # ty: ignore[invalid-argument-type]
User(id=1, tags=[1])  # ty: ignore[invalid-argument-type]
User(id=1, unknown=True)  # ty: ignore[unknown-argument]

user = User(id=1)
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
