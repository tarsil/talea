"""Own lazy class-level publication of compiled inbound functions."""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock

from talea.declaration.models import SpecSchema
from talea.errors import ErrorCode
from talea.input.compilation import InputCallable, compile_input
from talea.input.emission import InputMode
from talea.validation.errors import ValidationError

_INPUT_COMPILATION_LOCK = RLock()
_RECURSIVE_INPUT: ContextVar[set[int] | None] = ContextVar("talea_recursive_input", default=None)


@dataclass(slots=True)
class _InputArtifacts:
    """Own lazily compiled input functions for one Spec declaration."""

    slot_setters: tuple[Callable[[object, object], None], ...]
    recursive: bool = False
    mapping_input: InputCallable | None = None
    json_input: InputCallable | None = None
    compiling: set[InputMode] | None = None

    @property
    def presence_setter(self) -> Callable[[object, object], None] | None:
        """Return no presence storage for ordinary Spec declarations."""

        return None

    def input_for(
        self,
        schema: SpecSchema,
        spec_type: type[object],
        mode: InputMode,
    ) -> InputCallable:
        """Return one boundary, compiling and publishing it atomically on first use."""

        compiled = self.mapping_input if mode == "mapping" else self.json_input
        if compiled is not None:
            return compiled
        with _INPUT_COMPILATION_LOCK:
            compiled = self.mapping_input if mode == "mapping" else self.json_input
            if compiled is None:
                if self.compiling is None:
                    self.compiling = set()
                self.compiling.add(mode)
                try:
                    compiled = compile_input(
                        schema,
                        spec_type,
                        self.slot_setters,
                        mode,
                        self.presence_setter,
                    )
                finally:
                    self.compiling.remove(mode)
                if self.recursive:
                    compiled = _RecursiveInput(compiled, spec_type)
                if mode == "mapping":
                    self.mapping_input = compiled
                else:
                    self.json_input = compiled
        return compiled

    def reference_for(
        self,
        schema: SpecSchema,
        spec_type: type[object],
        mode: InputMode,
    ) -> InputCallable:
        """Return a direct nested boundary or a deferred recursive back edge."""

        if self.compiling is not None and mode in self.compiling:
            return _RecursiveInputReference(spec_type, mode)
        return self.input_for(schema, spec_type, mode)


class _PresenceInputArtifacts(_InputArtifacts):
    """Own the one extra slot required only by presence-aware declarations."""

    __slots__ = ("_presence_setter",)

    def __init__(
        self,
        slot_setters: tuple[Callable[[object, object], None], ...],
        recursive: bool,
        presence_setter: Callable[[object, object], None],
    ) -> None:
        super().__init__(slot_setters, recursive)
        self._presence_setter = presence_setter

    @property
    def presence_setter(self) -> Callable[[object, object], None]:
        """Return the class-bound compact presence-slot setter."""

        return self._presence_setter


class _RecursiveInputReference:
    """Resolve a recursive input artifact after its first compilation publishes."""

    __slots__ = ("mode", "spec_type")

    def __init__(self, spec_type: type[object], mode: InputMode) -> None:
        self.spec_type = spec_type
        self.mode = mode

    def __call__(self, data: object) -> object:
        artifacts = vars(self.spec_type)["__talea_artifacts__"]
        boundary = artifacts.inputs.input_for(artifacts.schema, self.spec_type, self.mode)
        return boundary(data)


class _RecursiveInput:
    """Reject cyclic Mapping graphs with operation-local identity tracking."""

    __slots__ = ("boundary", "spec_type")

    def __init__(self, boundary: InputCallable, spec_type: type[object]) -> None:
        self.boundary = boundary
        self.spec_type = spec_type

    def __call__(self, data: object) -> object:
        active = _RECURSIVE_INPUT.get()
        token = None
        if active is None:
            active = set()
            token = _RECURSIVE_INPUT.set(active)
        identity = id(data)
        if identity in active:
            raise ValidationError(None, data, (), ErrorCode.CYCLE, title=self.spec_type.__name__) from None
        active.add(identity)
        try:
            return self.boundary(data)
        finally:
            active.remove(identity)
            if token is not None:
                _RECURSIVE_INPUT.reset(token)
