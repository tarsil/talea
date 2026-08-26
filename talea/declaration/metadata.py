"""Public field-level metadata consumed by Talea declarations."""

from dataclasses import dataclass

__all__ = ["Alias"]


@dataclass(frozen=True, slots=True)
class Alias:
    """Declare one external name for a Spec field.

    Use ``Alias`` as top-level :class:`typing.Annotated` metadata. The Python
    constructor and instance attribute retain the annotated field name, while
    Mapping, JSON, and outbound serialization boundaries use ``name``.

    Args:
        name: A non-empty external field name. It need not be a Python
            identifier, but it must not collide with another field's canonical
            or external name in the same effective Spec declaration.
    """

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise TypeError("Alias requires a non-empty string")
