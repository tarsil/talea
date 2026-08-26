"""Public failure contract for outbound Talea representation."""

from talea.errors.safety import snapshot_input

__all__ = ["SerializationError"]


class SerializationError(TypeError):
    """Report that a valid Spec value cannot be represented for output.

    Args:
        reason: Stable human-readable description of the output failure.
        location: Canonical path to the field or container member that failed.

    Serialization failures are separate from input ``ValidationError`` values:
    they describe an already-constructed object's current output state or a
    user serializer/codec contract failure. Wrapped hook and codec exceptions
    remain available as ``__cause__``.
    """

    def __init__(self, reason: str, location: tuple[object, ...] = ()) -> None:
        self.reason = reason
        self.location = location
        super().__init__(reason)

    def prefixed(self, prefix: tuple[object, ...]) -> "SerializationError":
        """Return the same failure beneath a longer output location."""

        error = type(self)(self.reason, (*prefix, *self.location))
        error.__cause__ = self.__cause__
        return error

    def __str__(self) -> str:
        """Render the reason with a compact dotted/indexed location."""

        location = (
            "<root>" if not self.location else ".".join(snapshot_input(segment).rendered for segment in self.location)
        )
        return f"Serialization failed at {location}: {self.reason}"
