"""Positive static-typing contract for Talea Spec declarations."""

from talea import Spec


class User(Spec):
    id: int
    name: str
    tags: list[str]


user: User = User(id=1, name="Tiago", tags=["maintainer"])
identifier: int = user.id
name: str = user.name
tags: list[str] = user.tags

assert (identifier, name, tags) == (1, "Tiago", ["maintainer"])
