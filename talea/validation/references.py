"""Own recursive validation calls across finalized canonical graph edges."""

from collections.abc import Callable
from contextvars import ContextVar

from talea.schema.nodes import NamedReferenceSchema

_RECURSIVE_VALIDATION: ContextVar[set[int] | None] = ContextVar(
    "talea_recursive_validation",
    default=None,
)


class _RecursiveSpecValidator:
    """Call a finalized current-state artifact across one recursive Spec edge."""

    __slots__ = ("spec_type",)

    def __init__(self, spec_type: type[object]) -> None:
        self.spec_type = spec_type

    def __call__(self, value: object) -> object:
        active = _RECURSIVE_VALIDATION.get()
        token = None
        if active is None:
            active = set()
            token = _RECURSIVE_VALIDATION.set(active)
        identity = id(value)
        if identity in active:
            return value
        active.add(identity)
        try:
            artifacts = vars(self.spec_type)["__talea_artifacts__"]
            validator = artifacts.current_validator
            assert validator is not None
            return validator(value)
        finally:
            active.remove(identity)
            if token is not None:
                _RECURSIVE_VALIDATION.reset(token)


class _NamedValidationReference:
    """Call one graph-owned validator across a named declaration back-edge."""

    __slots__ = ("reference", "sensitive")

    def __init__(self, reference: NamedReferenceSchema, sensitive: bool) -> None:
        self.reference = reference
        self.sensitive = sensitive

    def __call__(self, value: object) -> object:
        from talea.validation.compilation import compile_validator

        compiled: Callable[[object], object] = self.reference._target.operation(
            ("validation", self.sensitive),
            lambda schema: compile_validator(schema, sensitive=self.sensitive),
        )
        active = _RECURSIVE_VALIDATION.get()
        token = None
        if active is None:
            active = set()
            token = _RECURSIVE_VALIDATION.set(active)
        identity = id(value)
        if identity in active:
            return value
        active.add(identity)
        try:
            return compiled(value)
        finally:
            active.remove(identity)
            if token is not None:
                _RECURSIVE_VALIDATION.reset(token)
