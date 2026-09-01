"""Public field-level metadata consumed by Talea declarations."""

from dataclasses import dataclass, field

__all__ = ["Alias"]


@dataclass(frozen=True, slots=True)
class Alias:
    """Declare current and legacy external names for a Spec field.

    Use ``Alias`` as top-level :class:`typing.Annotated` metadata. The Python
    constructor and instance attribute retain the annotated field name, while
    Mapping and JSON input accept ``name`` or exactly one declared ``legacy``
    name. Outbound serialization always uses ``name``.

    Args:
        name: A non-empty external field name. It need not be a Python
            identifier, but it must not collide with another field's canonical
            or external name in the same effective Spec declaration.
        legacy: Ordered historical input names. Tuple order is retained for
            introspection and later projection; it never establishes
            precedence when more than one accepted name is supplied.

    Raises:
        TypeError: If a name is empty or not a string, ``legacy`` is not an
            exact tuple of non-empty strings, a legacy name is duplicated, or
            the current name also appears in ``legacy``.
    """

    name: str
    legacy: tuple[str, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise TypeError("Alias requires a non-empty string")
        if type(self.legacy) is not tuple:
            raise TypeError("Alias legacy names must be a tuple of strings")
        if any(type(name) is not str or not name for name in self.legacy):
            raise TypeError("Alias legacy names must be non-empty strings")
        if len(self.legacy) != len(set(self.legacy)):
            raise TypeError("Alias legacy names must be unique")
        if self.name in self.legacy:
            raise TypeError("Alias current name cannot also be a legacy name")
