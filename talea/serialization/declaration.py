"""Attach outbound hook declarations to the effective Spec lifecycle."""

from collections.abc import Mapping
from inspect import (
    Parameter,
    isasyncgenfunction,
    iscoroutinefunction,
    isgeneratorfunction,
    signature,
)
from types import FunctionType

from talea.declaration.models import SerializationHook, SpecSchema
from talea.serialization.hooks import _SERIALIZER_MARKER, _SerializerMarker


def validate_callback_markers(namespace: Mapping[str, object], validation_marker: str) -> None:
    """Reject callbacks attempting to own both input and output lifecycles."""

    for value in namespace.values():
        if hasattr(value, validation_marker) and hasattr(value, _SERIALIZER_MARKER):
            raise TypeError("one function cannot be both a validation and serialization hook")


def inspect_serializers(
    namespace: dict[str, object],
    field_names: tuple[object, ...],
) -> tuple[SerializationHook, ...]:
    """Consume outbound serializer markers into canonical declaration truth."""

    serializers = []
    for name, value in tuple(namespace.items()):
        descriptor_function = value.__func__ if isinstance(value, (staticmethod, classmethod)) else None
        if descriptor_function is not None and hasattr(descriptor_function, _SERIALIZER_MARKER):
            raise TypeError("Talea serialization hooks cannot combine with staticmethod or classmethod")
        marker = getattr(value, _SERIALIZER_MARKER, None)
        if marker is None:
            continue
        if not isinstance(value, FunctionType) or not isinstance(marker, _SerializerMarker):
            raise TypeError("Talea serialization hook metadata requires a plain function")
        if name in field_names:
            raise TypeError(f"serialization hook conflicts with Spec field {name!r}")
        if iscoroutinefunction(value) or isasyncgenfunction(value):
            raise TypeError(f"serialization hook {name!r} must be synchronous")
        if isgeneratorfunction(value):
            raise TypeError(f"serialization hook {name!r} cannot be a generator")
        parameters = tuple(signature(value).parameters.values())
        if (
            len(parameters) != 1
            or parameters[0].kind not in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
            or parameters[0].default is not Parameter.empty
        ):
            raise TypeError(f"serialization hook {name!r} requires exactly one positional parameter")
        serializers.append(SerializationHook(name, marker.field, value))
        delattr(value, _SERIALIZER_MARKER)
        namespace[name] = staticmethod(value)
    return tuple(serializers)


def mro_shadowed_serializers(
    cls: type,
    inherited_schemas: tuple[SpecSchema, ...],
    spec_metaclass: type,
) -> frozenset[str]:
    """Find inherited serializers hidden by ordinary earlier-MRO attributes."""

    inherited_names = frozenset(serializer.name for schema in inherited_schemas for serializer in schema.serializers)
    shadowed = set()
    for name in inherited_names:
        owner = next((base for base in cls.__mro__[1:] if name in vars(base)), None)
        if owner is None or not isinstance(owner, spec_metaclass):
            shadowed.add(name)
            continue
        owner_schema = vars(owner)["__talea_artifacts__"].schema
        if all(serializer.name != name for serializer in owner_schema.serializers):
            shadowed.add(name)
    return frozenset(shadowed)
