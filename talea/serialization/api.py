"""Implement the public Spec outbound methods at their domain owner."""

from collections.abc import Set
from typing import Protocol, cast

from talea.declaration.models import SpecSchema
from talea.serialization.artifacts import _OutputArtifacts
from talea.serialization.compilation import FilteredSpecSerializer, SpecSerializer
from talea.serialization.json import JsonDumps, encode_json


class _SpecOutputOwner(Protocol):
    @property
    def schema(self) -> SpecSchema: ...

    @property
    def outputs(self) -> _OutputArtifacts: ...

    @property
    def contains_sensitive(self) -> bool: ...


class _SpecInstance(Protocol):
    @property
    def __talea_artifacts__(self) -> _SpecOutputOwner: ...


def _normalize_field_selection(
    selection: Set[str] | None,
    schema: SpecSchema,
    parameter: str,
) -> frozenset[str] | None:
    """Validate and freeze one dynamic canonical-field selection."""

    if selection is None:
        return None
    if not isinstance(selection, Set) or isinstance(selection, (str, bytes)):
        raise TypeError(f"{parameter} must be a set of canonical field names")
    normalized = frozenset(selection)
    if any(type(name) is not str for name in normalized):
        raise TypeError(f"{parameter} field names must be exact strings")
    known = frozenset(field.name for field in schema.fields)
    unknown = normalized - known
    if unknown:
        raise ValueError(f"{parameter} contains unknown field {min(unknown)!r}")
    return normalized


def to_dict(
    self: _SpecInstance,
    *,
    by_alias: bool = True,
    include: Set[str] | None = None,
    exclude: Set[str] | None = None,
    exclude_none: bool = False,
) -> dict[str, object]:
    """Return a detached Python mapping representation of this Spec.

    Nested Specs become dictionaries. Python-native scalar values remain
    unchanged, while declared list, set, frozenset, tuple, and dictionary
    structure is rebuilt so mutable output never aliases mutable storage
    retained by the Spec. Mapping keys keep their hashable Python form.

    Args:
        by_alias: Use declared external aliases as output keys. This is the
            default so the result feeds the same external input contract.
        include: Optional set of canonical Python field names to retain.
        exclude: Optional set of canonical Python field names to omit;
            exclusion wins when a name appears in both selections.
        exclude_none: Omit selected fields whose current value is ``None``.

    Returns:
        A new dictionary. Plain calls use a lazily compiled branch-free
        serializer retained by the Spec declaration.

    Raises:
        SerializationError: If a hook fails or a mapping key cannot be
            projected without losing hashability.
        TypeError: If options do not have their documented strict types.
        ValueError: If a selection names an unknown canonical field.

    ``exclude_defaults`` is absent because factory defaults do not own one
    comparable value. ``exclude_unset`` is absent because Specs retain no
    per-instance input-provenance metadata.
    """

    artifacts = vars(type(self))["__talea_declaration__"].artifacts()
    if include is None and exclude is None and exclude_none is False:
        if by_alias is True:
            if artifacts.outputs.recursive:
                plain = cast(
                    SpecSerializer,
                    artifacts.outputs.output_for(artifacts.schema, "python", True, False),
                )
                return plain(self)
            plain = artifacts.outputs.public_python_for(artifacts.schema, to_dict)
            owner = type(self)
            if vars(owner).get("to_dict") is to_dict:
                plain.__name__ = "to_dict"
                plain.__qualname__ = f"{owner.__qualname__}.to_dict"
                plain.__module__ = owner.__module__
                type.__setattr__(owner, "to_dict", plain)
            return plain(self)
        elif by_alias is False:
            plain = cast(
                SpecSerializer,
                artifacts.outputs.output_for(artifacts.schema, "python", False, False),
            )
        else:
            raise TypeError("by_alias and exclude_none must be bool values")
        return plain(self)
    if type(by_alias) is not bool or type(exclude_none) is not bool:
        raise TypeError("by_alias and exclude_none must be bool values")
    normalized_include = _normalize_field_selection(include, artifacts.schema, "include")
    normalized_exclude = _normalize_field_selection(exclude, artifacts.schema, "exclude")
    serializer = cast(
        FilteredSpecSerializer,
        artifacts.outputs.output_for(artifacts.schema, "python", by_alias, True),
    )
    return serializer(self, normalized_include, normalized_exclude, exclude_none)


def to_json(
    self: _SpecInstance,
    *,
    dumps: JsonDumps | None = None,
    by_alias: bool = True,
    include: Set[str] | None = None,
    exclude: Set[str] | None = None,
    exclude_none: bool = False,
) -> str:
    """Project this Spec to strict JSON and return UTF-8 text.

    Talea first creates a JSON-native tree with a lazy schema-specialized
    serializer. Standard-library, Enum, Decimal, timedelta, bytes, nested Spec,
    and container semantics are independent of the selected syntax codec.
    Non-finite float and Decimal values are rejected before codec invocation.

    Args:
        dumps: Optional one-argument encoder receiving the projected tree. It
            may return ``str``, UTF-8 ``bytes``, or UTF-8 ``bytearray``; Talea
            normalizes every successful result to ``str``.
        by_alias: Use declared external aliases as object keys.
        include: Optional set of canonical Python field names to retain.
        exclude: Optional set of canonical Python field names to omit.
        exclude_none: Omit selected fields whose value is ``None``.

    Returns:
        JSON text. The default encoder emits compact strict JSON.

    Raises:
        SerializationError: If projection, a serializer hook, codec output, or
            strict JSON compatibility fails.
        Exception: Non-``ValueError`` custom-codec exceptions propagate.
    """

    artifacts = vars(type(self))["__talea_declaration__"].artifacts()
    if include is None and exclude is None and exclude_none is False:
        if by_alias is True:
            plain = artifacts.outputs.json_alias
        elif by_alias is False:
            plain = cast(
                SpecSerializer,
                artifacts.outputs.output_for(artifacts.schema, "json", False, False),
            )
        else:
            raise TypeError("by_alias and exclude_none must be bool values")
        if plain is None:
            plain = cast(
                SpecSerializer,
                artifacts.outputs.output_for(artifacts.schema, "json", by_alias, False),
            )
        return encode_json(plain(self), dumps, sensitive=artifacts.contains_sensitive)
    if type(by_alias) is not bool or type(exclude_none) is not bool:
        raise TypeError("by_alias and exclude_none must be bool values")
    normalized_include = _normalize_field_selection(include, artifacts.schema, "include")
    normalized_exclude = _normalize_field_selection(exclude, artifacts.schema, "exclude")
    serializer = cast(
        FilteredSpecSerializer,
        artifacts.outputs.output_for(artifacts.schema, "json", by_alias, True),
    )
    projected = serializer(self, normalized_include, normalized_exclude, exclude_none)
    return encode_json(projected, dumps, sensitive=artifacts.contains_sensitive)
