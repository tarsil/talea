"""Minimal lazy failure transport for compiled Talea validation."""

type Location = tuple[object, ...]


class ValidationError(TypeError):
    """Describe one strict type or constraint failure.

    Args:
        expected: Deterministic text for the required contract.
        value: The exact rejected object, retained only after failure.
        location: Root-relative field and container path.
        code: Stable machine-oriented category for the failed check.

    This intentionally remains a small transport rather than Talea's future
    polished Error Experience. Successful validation constructs no instance.
    """

    def __init__(self, expected: str, value: object, location: Location, code: str = "type") -> None:
        self.expected = expected
        self.value = value
        self.location = location
        self.code = code
        super().__init__()

    @property
    def received_type(self) -> type[object]:
        """Return the concrete type of the rejected value."""

        return type(self.value)

    def __str__(self) -> str:
        """Render a stable description without precomputing it on failure."""

        location = "".join(f"[{segment!r}]" for segment in self.location) or "<root>"
        return (
            f"Validation failed at {location}: expected {self.expected}, "
            f"received {self.received_type.__name__} ({self.value!r})"
        )
