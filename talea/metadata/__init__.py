"""Declare and normalize Talea documentation and security metadata.

Public marker values are immutable ``Annotated`` vocabulary. The internal
``DeclarationMetadata`` record is the single normalized owner consumed by Spec,
Contract, introspection, error, and future schema-projection domains.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Annotated, cast, get_args, get_origin

__all__ = [
    "Deprecated",
    "Description",
    "Examples",
    "ReadOnly",
    "Sensitive",
    "Title",
    "WriteOnly",
]


def _nonempty_text(value: object, declaration: str) -> None:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{declaration} requires a non-empty string")


@dataclass(frozen=True, slots=True)
class Title:
    """Declare a human-facing title for a field, Spec, or Contract.

    A local marker replaces an inherited title of the same kind. Introspection
    exposes the normalized value, and future JSON Schema generation can project
    it without reading annotations again. Titles are cold documentation truth:
    they do not affect validation, errors, input conversion, serialization, or
    security policy.
    """

    value: str

    def __post_init__(self) -> None:
        _nonempty_text(self.value, type(self).__name__)


@dataclass(frozen=True, slots=True)
class Description:
    """Declare descriptive documentation for a field, Spec, or Contract.

    A local marker replaces an inherited description; on a Spec it also takes
    precedence over the class-docstring fallback. Introspection exposes the
    normalized value for documentation and future JSON Schema projection.
    Descriptions are never inspected during validation or serialization and
    must not be used to communicate sensitive values.
    """

    value: str

    def __post_init__(self) -> None:
        _nonempty_text(self.value, type(self).__name__)


type ExampleValue = None | bool | int | float | str | tuple["ExampleValue", ...] | Mapping[str, "ExampleValue"]


@dataclass(frozen=True, slots=True)
class _ExampleMapping(Mapping[str, ExampleValue]):
    """Retain a hashable immutable example object in declaration order."""

    items: tuple[tuple[str, ExampleValue], ...]

    def __getitem__(self, key: str) -> ExampleValue:
        for candidate, value in self.items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return (key for key, _ in self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return False
        return dict(self.items) == dict(other.items())

    def __hash__(self) -> int:
        return hash(self.items)


def _freeze_example(value: object) -> ExampleValue:
    if value is None or type(value) in (bool, int, str):
        return cast(None | bool | int | str, value)
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("Examples require finite floating-point values")
        return value
    if type(value) in (list, tuple):
        sequence = cast(list[object] | tuple[object, ...], value)
        return tuple(_freeze_example(item) for item in sequence)
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("Examples mapping keys must be exact strings")
        return _ExampleMapping(tuple((key, _freeze_example(item)) for key, item in value.items()))
    raise TypeError("Examples values must be JSON-compatible scalars, sequences, or string-keyed mappings")


@dataclass(frozen=True, slots=True, init=False)
class Examples:
    """Declare one or more immutable documentation examples.

    Values use JSON-compatible scalars, sequences, and string-keyed mappings.
    Talea snapshots containers into recursively immutable values at declaration
    time. A local marker replaces inherited examples. Introspection exposes the
    snapshot for documentation and future JSON Schema projection. Examples are
    not executed or validated against hooks and factories, do not affect input
    or serialization, and must never contain credentials or other secrets.
    """

    values: tuple[ExampleValue, ...]

    def __init__(self, *values: object) -> None:
        if not values:
            raise TypeError("Examples requires at least one value")
        object.__setattr__(self, "values", tuple(_freeze_example(value) for value in values))


@dataclass(frozen=True, slots=True)
class Deprecated:
    """Mark a field, Spec, or Contract as deprecated documentation truth.

    Introspection and future JSON Schema projection consume the normalized
    flag. It does not affect validation, errors, or serialization, and Talea
    does not emit runtime warnings. A local value replaces inherited state;
    ``False`` is the explicit opt-out.
    """

    enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("Deprecated requires a bool value")


@dataclass(frozen=True, slots=True)
class ReadOnly:
    """Classify a field or Contract as read-only boundary metadata.

    Introspection and future framework or JSON Schema consumers can project the
    normalized flag. It does not reject constructors, Mapping input, or JSON
    input, and has no error-redaction or serialization effect. A local value
    replaces inherited state; ``False`` explicitly clears the classification.
    """

    enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("ReadOnly requires a bool value")


@dataclass(frozen=True, slots=True)
class WriteOnly:
    """Classify a field or Contract as write-only boundary metadata.

    Introspection and future framework or JSON Schema consumers can project the
    normalized flag. Talea does not omit write-only values from successful
    serialization; output policy remains separate from sensitive error
    redaction. A local value replaces inherited state, and ``False`` explicitly
    clears the classification.
    """

    enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("WriteOnly requires a bool value")


@dataclass(frozen=True, slots=True)
class Sensitive:
    """Classify a field or Contract as sensitive error-facing data.

    Talea-controlled validation and serialization failures redact values under
    this declaration and do not retain their raw rejected object or callback
    cause. Normal successful validation and serialization remain unchanged.
    Introspection exposes the normalized flag for adapters and future schema
    extensions. Field sensitivity persists through ordinary inheritance;
    ``False`` is the explicit field opt-out. Arbitrary user callbacks still
    receive raw values and therefore remain responsible for external logging or
    side effects performed before Talea catches an exception.
    """

    enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("Sensitive requires a bool value")


type MetadataMarker = Title | Description | Examples | Deprecated | ReadOnly | WriteOnly | Sensitive
_MARKER_TYPES = (Title, Description, Examples, Deprecated, ReadOnly, WriteOnly, Sensitive)


@dataclass(frozen=True, slots=True)
class DeclarationMetadata:
    """Own one normalized declaration's documentation and security truth."""

    title: str | None = None
    description: str | None = None
    examples: tuple[ExampleValue, ...] | None = None
    deprecated: bool | None = None
    read_only: bool | None = None
    write_only: bool | None = None
    sensitive: bool | None = None

    def merged(self, local: "DeclarationMetadata") -> "DeclarationMetadata":
        """Overlay explicitly declared local values on inherited truth."""

        if local is EMPTY_METADATA:
            return self
        if self is EMPTY_METADATA:
            return local
        return DeclarationMetadata(
            local.title if local.title is not None else self.title,
            local.description if local.description is not None else self.description,
            local.examples if local.examples is not None else self.examples,
            local.deprecated if local.deprecated is not None else self.deprecated,
            local.read_only if local.read_only is not None else self.read_only,
            local.write_only if local.write_only is not None else self.write_only,
            local.sensitive if local.sensitive is not None else self.sensitive,
        )


EMPTY_METADATA = DeclarationMetadata()


def annotation_metadata(annotation: object) -> DeclarationMetadata:
    """Normalize Talea-owned top-level ``Annotated`` metadata."""

    if get_origin(annotation) is not Annotated:
        return EMPTY_METADATA
    return normalize_metadata(get_args(annotation)[1:])


def normalize_metadata(
    items: Iterable[object],
    *,
    spec: bool = False,
    doc: str | None = None,
) -> DeclarationMetadata:
    """Normalize marker declarations with deterministic duplicate rejection."""

    if isinstance(items, (str, bytes)):
        raise TypeError("metadata must be an iterable of Talea marker values")
    seen: set[type[object]] = set()
    title = description = None
    examples = None
    deprecated = read_only = write_only = sensitive = None
    for item in items:
        if not isinstance(item, _MARKER_TYPES):
            continue
        marker_type = type(item)
        if marker_type in seen:
            raise TypeError(f"metadata can declare only one {marker_type.__name__}")
        if spec and isinstance(item, (ReadOnly, WriteOnly, Sensitive)):
            raise TypeError(f"{marker_type.__name__} applies to fields and Contracts, not Specs")
        seen.add(marker_type)
        if isinstance(item, Title):
            title = item.value
        elif isinstance(item, Description):
            description = item.value
        elif isinstance(item, Examples):
            examples = item.values
        elif isinstance(item, Deprecated):
            deprecated = item.enabled
        elif isinstance(item, ReadOnly):
            read_only = item.enabled
        elif isinstance(item, WriteOnly):
            write_only = item.enabled
        else:
            assert isinstance(item, Sensitive)
            sensitive = item.enabled
    if description is None and doc is not None:
        _nonempty_text(doc, "Spec docstring")
    if not seen and doc is None:
        return EMPTY_METADATA
    return DeclarationMetadata(
        title,
        description if description is not None else doc,
        examples,
        deprecated,
        read_only,
        write_only,
        sensitive,
    )
