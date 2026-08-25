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
