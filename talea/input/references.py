"""Own recursive named input calls and operation-local cycle state."""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Literal

from talea.errors import ErrorCode
from talea.resources.state import UNLIMITED_RESOURCE_STATE, _ResourceState, _UnlimitedResourceState
from talea.schema.nodes import NamedReferenceSchema
from talea.validation.errors import ValidationError

type InputMode = Literal["mapping", "json"]
type InputResourceState = _ResourceState | _UnlimitedResourceState
type ValueInput = Callable[..., object]

_RECURSIVE_NAMED_INPUT: ContextVar[set[int] | None] = ContextVar(
    "talea_recursive_named_input",
    default=None,
)


class _NamedInputReference:
    """Call one graph-owned boundary across a named declaration back-edge."""

    __slots__ = ("mode", "reference", "sensitive", "title")

    def __init__(
        self,
        reference: NamedReferenceSchema,
        mode: InputMode,
        title: str,
        sensitive: bool,
    ) -> None:
        self.reference = reference
        self.mode = mode
        self.title = title
        self.sensitive = sensitive

    def __call__(
        self,
        value: object,
        resource_state: InputResourceState = UNLIMITED_RESOURCE_STATE,
    ) -> object:
        from talea.input.value import compile_value_input

        compiled: ValueInput = self.reference._target.operation(
            ("input", self.mode, self.sensitive),
            lambda schema: compile_value_input(
                schema,
                self.mode,
                self.title,
                sensitive=self.sensitive,
            ),
        )
        return compiled(value, resource_state)


class _NamedInputRoot:
    """Track active runtime identities only for a recursive named boundary."""

    __slots__ = ("boundary", "sensitive", "title")

    def __init__(self, boundary: ValueInput, title: str, sensitive: bool) -> None:
        self.boundary = boundary
        self.title = title
        self.sensitive = sensitive

    def __call__(
        self,
        value: object,
        resource_state: InputResourceState = UNLIMITED_RESOURCE_STATE,
    ) -> object:
        active = _RECURSIVE_NAMED_INPUT.get()
        token = None
        if active is None:
            active = set()
            token = _RECURSIVE_NAMED_INPUT.set(active)
        identity = id(value)
        if identity in active:
            raise ValidationError(
                None,
                value,
                (),
                ErrorCode.CYCLE,
                title=self.title,
                sensitive=self.sensitive,
            ) from None
        active.add(identity)
        try:
            return self.boundary(value, resource_state)
        finally:
            active.remove(identity)
            if token is not None:
                _RECURSIVE_NAMED_INPUT.reset(token)


def wrap_named_input_root(boundary: ValueInput, title: str, sensitive: bool) -> ValueInput:
    """Add operation-local cycle tracking to one recursive named root."""

    return _NamedInputRoot(boundary, title, sensitive)
