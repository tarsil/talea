"""Public declaration vocabulary for canonical tagged unions."""

from dataclasses import dataclass

__all__ = ["Discriminator"]


@dataclass(frozen=True, slots=True)
class Discriminator:
    """Select a union branch through one common literal field.

    ``name`` may be the branches' common Python field name or their common
    external alias. Resolution retains both names from the field declarations;
    the marker does not create a second alias or tag declaration.

    Args:
        name: The non-empty discriminator field or external key name.
    """

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise TypeError("Discriminator requires a non-empty string")
