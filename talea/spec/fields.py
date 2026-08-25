"""Own public field-factory declaration syntax for Spec class bodies."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True, slots=True)
class _FactoryDeclaration[T]:
    """Carry factory syntax from a class body into canonical declaration truth."""

    default_factory: Callable[[], T]


def field[T](*, default_factory: Callable[[], T]) -> T:
    """Declare a field whose omitted value is produced for each Spec instance.

    Args:
        default_factory: A zero-argument callable. Talea calls it once for each
            construction that omits the field, then validates its result using
            the field's compiled validator. Explicit values bypass the factory.

    Returns:
        A class-body declaration marker consumed before the Spec class becomes
        constructible. It is never stored on instances or used on hot paths.

    Raises:
        TypeError: If ``default_factory`` is not callable.
    """

    if not callable(default_factory):
        raise TypeError("field default_factory must be callable")
    return cast(T, _FactoryDeclaration(default_factory))


class _FactorySentinel:
    """Provide a readable generated-signature marker for factory fields."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<factory>"


FACTORY_SENTINEL = _FactorySentinel()


class _StaticDefaultSentinel:
    """Distinguish an omitted hooked default while preserving its signature text."""

    __slots__ = ("default",)

    def __init__(self, default: object) -> None:
        self.default = default

    def __repr__(self) -> str:
        return repr(self.default)
