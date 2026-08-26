"""Cold-bound nominal dispatch operations shared by tagged emitters."""

from collections.abc import Mapping


def nominal_dispatch[T](value: object, dispatch: Mapping[type[object], T]) -> T | None:
    """Return the first MRO-owned operation without scanning union branches."""

    for owner in type(value).__mro__:
        operation = dispatch.get(owner)
        if operation is not None:
            return operation
    return None


def nominal_member(value: object, branches: frozenset[type[object]]) -> bool:
    """Return whether one runtime MRO reaches a declared branch type."""

    return any(owner in branches for owner in type(value).__mro__)
