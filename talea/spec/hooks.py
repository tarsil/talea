"""Declare Talea's inbound transformation and custom-check vocabulary."""

from collections.abc import Callable
from dataclasses import dataclass
from types import FunctionType
from typing import Literal, cast

__all__ = ["check", "transform"]

type _DeclaredHookKind = Literal["transform", "check"]

_HOOK_MARKER = "__talea_validation_hook__"


@dataclass(frozen=True, slots=True)
class _HookMarker:
    """Carry class-body hook syntax into canonical declaration processing."""

    kind: _DeclaredHookKind
    fields: tuple[str, ...]


def transform[**P, R](field_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Declare an explicit inbound transformation for one Spec field.

    The decorated plain function receives the supplied value before Talea's
    structural validation and returns the value that structural validation must
    accept. Transforms therefore enable only the conversions a declaration
    names; fields without transforms remain strict. Explicit constructor values
    and default-factory outputs run transforms in declaration order. Static
    defaults do not: they are developer-provided state and must already satisfy
    the field schema and checks when the class is declared.

    A transform is inherited using its method name as normal Python override
    identity. A same-named subclass transform replaces it in place, while a
    same-named ordinary method removes it. Subclass additions follow inherited
    callbacks. The declaration compiler binds direct calls only into hooked
    paths, so unhooked constructors perform no registry lookup or dispatch.

    Args:
        field_name: The declared field whose inbound value is transformed.

    Returns:
        A decorator that preserves the callback's static signature.

    Raises:
        TypeError: If the target is not a plain function, the field name is not
            a string, or the function already owns a Talea hook declaration.

    Async functions, generators, descriptors, and callbacks with an incompatible
    positional signature are rejected when the containing Spec is declared.
    ``ValueError`` raised by the callback becomes a field-located Talea
    validation failure with the original exception as its cause. Other
    exceptions remain programming errors and propagate unchanged.
    """

    _validate_field_names((field_name,), "transform")

    def declare(function: Callable[P, R]) -> Callable[P, R]:
        return cast(Callable[P, R], _mark_hook(function, _HookMarker("transform", (field_name,))))

    return declare


def check[**P](*field_names: str) -> Callable[[Callable[P, None]], Callable[P, None]]:
    """Declare a post-structural assertion over one or more Spec fields.

    With one target, the decorated plain function receives that field's already
    structurally valid typed value immediately after built-in constraints. With
    two or more targets, it receives those validated local values positionally
    after every field-local pipeline has completed and before any immutable slot
    is written. This supplies cross-field validation without constructing a
    values dictionary or exposing a partially initialized instance.

    Checks return ``None``. Their result is never assigned, so a check cannot
    become a second transformation path. Multiple checks on one field preserve
    declaration order; fields themselves use canonical field order. Cross-field
    checks preserve declaration order in their pre-commit phase. Inheritance and
    override identity follow the same method-name rules as :func:`transform`.

    Static defaults run applicable field checks once when their declaration or
    effective check contract changes. Factory results and explicit values run
    checks during construction. At a non-permanently-trusted nested boundary,
    field and cross-field checks run again against current state; inbound
    transforms never do.

    Args:
        *field_names: One or more unique declared fields, in callback argument
            order. More than one target declares a whole-Spec invariant.

    Returns:
        A decorator that requires a ``None``-returning callback while preserving
        its parameter types for static analysis.

    Raises:
        TypeError: If targets are invalid, the target is not a plain function,
            or the function already owns a Talea hook declaration.

    A callback's ``ValueError`` becomes a Talea validation failure retaining the
    hook name and all affected field locations. Unexpected exceptions propagate.
    Async and generator callbacks are rejected at Spec declaration time.
    """

    _validate_field_names(field_names, "check")

    def declare(function: Callable[P, None]) -> Callable[P, None]:
        return cast(Callable[P, None], _mark_hook(function, _HookMarker("check", field_names)))

    return declare


def _validate_field_names(field_names: tuple[object, ...], kind: str) -> None:
    if not field_names:
        raise TypeError(f"{kind} requires at least one field name")
    if any(not isinstance(name, str) or not name for name in field_names):
        raise TypeError(f"{kind} field names must be non-empty strings")
    if len(field_names) != len(set(field_names)):
        raise TypeError(f"{kind} field names must be unique")


def _mark_hook(function: object, marker: _HookMarker) -> FunctionType:
    if not isinstance(function, FunctionType):
        raise TypeError("Talea validation hooks require a plain function")
    if hasattr(function, _HOOK_MARKER):
        raise TypeError("a function can declare only one Talea validation hook")
    setattr(function, _HOOK_MARKER, marker)
    return function
