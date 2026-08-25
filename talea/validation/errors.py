"""Minimal lazy failure transport for compiled Talea validation."""

from typing import Literal

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


class CustomValidationError(ValidationError):
    """Transport a deliberate custom-hook rejection into Talea validation.

    Args:
        stage: The lifecycle phase that invoked the callback.
        hook: The method name providing declaration and override identity.
        value: The rejected field value or lazily built tuple of values for a
            cross-field check.
        locations: Every root-relative field path involved in the failure.

    ``stage`` distinguishes inbound transformation, field checking, and
    whole-Spec checking. ``hook`` retains the Python method name that owns the
    declaration. ``locations`` contains every exact affected path; the legacy
    single ``location`` remains that path for a field-local failure and the root
    for a multi-field invariant. Exception chaining retains the callback's
    original ``ValueError``.
    """

    def __init__(
        self,
        stage: Literal["transform", "field_check", "spec_check"],
        hook: str,
        value: object,
        locations: tuple[Location, ...],
    ) -> None:
        self.stage = stage
        self.hook = hook
        self.locations = locations
        location = locations[0] if len(locations) == 1 else ()
        super().__init__(f"custom {stage} {hook!r}", value, location, stage)
