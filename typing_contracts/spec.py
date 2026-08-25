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
