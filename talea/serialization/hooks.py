"""Declare Talea's outbound field-serialization vocabulary."""

from collections.abc import Callable
from dataclasses import dataclass
from types import FunctionType
from typing import cast

from talea.declaration.models import MISSING_SERIALIZER_OUTPUT

__all__ = ["serialize"]

_SERIALIZER_MARKER = "__talea_serialization_hook__"


@dataclass(frozen=True, slots=True)
class _SerializerMarker:
    field: str
    output: object = MISSING_SERIALIZER_OUTPUT


def serialize[**P, R](
    field_name: str,
    *,
    output: object = MISSING_SERIALIZER_OUTPUT,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Declare one synchronous outbound replacement for a Spec field.

    The decorated plain function receives the validated Python field value
    before normal Python or JSON projection. Its return value replaces the
    declared representation for that operation. ``output`` may declare any
    supported Talea annotation as the callback result contract. Declared
    results are validated and projected through that schema; omitted output
    contracts keep the callback result opaque.

    Serializer methods inherit and override by normal Python method name. A
    Spec may have at most one effective serializer per field. Async functions,
    generators, descriptors, and incompatible signatures are rejected when the
    containing Spec is declared.
    """

    if not isinstance(field_name, str) or not field_name:
        raise TypeError("serialize requires a non-empty field name")

    def declare(function: Callable[P, R]) -> Callable[P, R]:
        if not isinstance(function, FunctionType):
            raise TypeError("Talea serialization hooks require a plain function")
        if hasattr(function, _SERIALIZER_MARKER):
            raise TypeError("a function can declare only one Talea serialization hook")
        setattr(function, _SERIALIZER_MARKER, _SerializerMarker(field_name, output))
        return cast(Callable[P, R], function)

    return declare
