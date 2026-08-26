"""Own recursive named output calls and operation-local cycle state."""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Literal

from talea.schema.nodes import NamedReferenceSchema
from talea.serialization.errors import SerializationError

type OutputMode = Literal["python", "json"]
type ValueProjector = Callable[[object, tuple[object, ...]], object]

_RECURSIVE_NAMED_OUTPUT: ContextVar[set[int] | None] = ContextVar(
    "talea_recursive_named_output",
    default=None,
)


class _NamedOutputReference:
    """Call one graph-owned projector across a named declaration back-edge."""

    __slots__ = ("by_alias", "mode", "reference", "sensitive")

    def __init__(
        self,
        reference: NamedReferenceSchema,
        mode: OutputMode,
        by_alias: bool,
        sensitive: bool,
    ) -> None:
        self.reference = reference
        self.mode = mode
        self.by_alias = by_alias
        self.sensitive = sensitive

    def __call__(self, value: object, location: tuple[object, ...]) -> object:
        from talea.serialization.emission import compile_value_projector

        compiled: ValueProjector = self.reference._target.operation(
            ("output", self.mode, self.by_alias, self.sensitive),
            lambda schema: compile_value_projector(
                schema,
                self.mode,
                self.by_alias,
                sensitive=self.sensitive,
            ),
        )
        return compiled(value, location)


class _NamedOutputRoot:
    """Reject cycles at the first repeated identity in a named graph."""

    __slots__ = ("projector", "sensitive")

    def __init__(self, projector: ValueProjector, sensitive: bool) -> None:
        self.projector = projector
        self.sensitive = sensitive

    def __call__(self, value: object, location: tuple[object, ...]) -> object:
        active = _RECURSIVE_NAMED_OUTPUT.get()
        token = None
        if active is None:
            active = set()
            token = _RECURSIVE_NAMED_OUTPUT.set(active)
        identity = id(value)
        if identity in active:
            raise SerializationError(
                "cyclic object graphs cannot be serialized",
                location,
                sensitive=self.sensitive,
            ) from None
        active.add(identity)
        try:
            return self.projector(value, location)
        finally:
            active.remove(identity)
            if token is not None:
                _RECURSIVE_NAMED_OUTPUT.reset(token)
