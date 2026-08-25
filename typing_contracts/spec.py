"""Positive static-typing contract for Talea Spec declarations."""

from typing import assert_type

from talea import Spec, field


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
assert (identifier, name, tags) == (1, "Tiago", [])


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
assert_type(Employee(name="Ada", employee_id=1, active=False, aliases=["A"]), Employee)
assert_type(NamedEmployee(employee_id=2), NamedEmployee)
assert_type(department.manager, Employee)
assert_type(department.members, list[Person])
assert_type(department.deputy, Employee | None)
assert_type(NarrowIdentity(value="staff", person=employee).value, str)
