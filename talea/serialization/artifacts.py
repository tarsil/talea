"""Own lazy class-level publication of compiled outbound functions."""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from types import FunctionType
from typing import cast

from talea.declaration.models import SpecSchema
from talea.serialization.compilation import (
    FilteredSpecSerializer,
    SpecSerializer,
    compile_plain_to_dict,
    compile_serialization,
)
from talea.serialization.emission import OutputMode
from talea.serialization.errors import SerializationError

_OUTPUT_COMPILATION_LOCK = RLock()
_RECURSIVE_OUTPUT: ContextVar[set[int] | None] = ContextVar("talea_recursive_output", default=None)


@dataclass(slots=True)
class _OutputArtifacts:
    """Own independently lazy compiled outbound functions for one Spec."""

    recursive: bool = False
    python_alias: SpecSerializer | None = None
    json_alias: SpecSerializer | None = None
    variants: dict[tuple[OutputMode, bool, bool], SpecSerializer | FilteredSpecSerializer] | None = None
    compiling: set[tuple[OutputMode, bool, bool]] | None = None

    def public_python_for(
        self,
        schema: SpecSchema,
        fallback: Callable[..., dict[str, object]],
    ) -> FunctionType:
        """Return the public plain serializer, compiling and publishing it once."""

        compiled = self.python_alias
        if compiled is not None and getattr(compiled, "__wrapped__", None) is fallback:
            return cast(FunctionType, compiled)
        with _OUTPUT_COMPILATION_LOCK:
            compiled = self.python_alias
            if compiled is None or getattr(compiled, "__wrapped__", None) is not fallback:
                compiled = compile_plain_to_dict(schema, fallback)
                self.python_alias = compiled
        return cast(FunctionType, compiled)

    def output_for(
        self,
        schema: SpecSchema,
        mode: OutputMode,
        by_alias: bool,
        filtered: bool,
    ) -> SpecSerializer | FilteredSpecSerializer:
        """Return one serializer, publishing first-use compilation atomically."""

        key = (mode, by_alias, filtered)
        default_alias = by_alias and not filtered
        if default_alias:
            compiled = self.python_alias if mode == "python" else self.json_alias
        else:
            compiled = None if self.variants is None else self.variants.get((mode, by_alias, filtered))
        if compiled is not None:
            return compiled
        with _OUTPUT_COMPILATION_LOCK:
            if default_alias:
                compiled = self.python_alias if mode == "python" else self.json_alias
            else:
                compiled = None if self.variants is None else self.variants.get((mode, by_alias, filtered))
            if compiled is None:
                if self.compiling is None:
                    self.compiling = set()
                self.compiling.add(key)
                try:
                    compiled = compile_serialization(schema, mode, by_alias, filtered)
                finally:
                    self.compiling.remove(key)
                if self.recursive:
                    compiled = cast(SpecSerializer | FilteredSpecSerializer, _RecursiveSerializer(compiled))
                if default_alias:
                    if mode == "python":
                        self.python_alias = cast(SpecSerializer, compiled)
                    else:
                        self.json_alias = cast(SpecSerializer, compiled)
                else:
                    if self.variants is None:
                        self.variants = {}
                    self.variants[(mode, by_alias, filtered)] = compiled
        return compiled

    def reference_for(
        self,
        schema: SpecSchema,
        mode: OutputMode,
        by_alias: bool,
    ) -> SpecSerializer:
        """Return a direct nested serializer or a deferred recursive back edge."""

        key = (mode, by_alias, False)
        if self.compiling is not None and key in self.compiling:
            return cast(SpecSerializer, _RecursiveOutputReference(self, schema, mode, by_alias))
        return cast(SpecSerializer, self.output_for(schema, mode, by_alias, False))


class _RecursiveOutputReference:
    """Resolve a recursive serializer after the owning artifact publishes."""

    __slots__ = ("artifacts", "by_alias", "mode", "schema")

    def __init__(self, artifacts: _OutputArtifacts, schema: SpecSchema, mode: OutputMode, by_alias: bool) -> None:
        self.artifacts = artifacts
        self.schema = schema
        self.mode = mode
        self.by_alias = by_alias

    def __call__(self, instance: object) -> dict[str, object]:
        serializer = self.artifacts.output_for(self.schema, self.mode, self.by_alias, False)
        return cast(SpecSerializer, serializer)(instance)


class _RecursiveSerializer:
    """Reject cyclic object graphs while sharing one operation-local identity set."""

    __slots__ = ("serializer",)

    def __init__(self, serializer: SpecSerializer | FilteredSpecSerializer) -> None:
        self.serializer = serializer

    def __call__(self, instance: object, *args: object) -> dict[str, object]:
        active = _RECURSIVE_OUTPUT.get()
        token = None
        if active is None:
            active = set()
            token = _RECURSIVE_OUTPUT.set(active)
        identity = id(instance)
        if identity in active:
            raise SerializationError("cyclic object graphs cannot be serialized")
        active.add(identity)
        try:
            if args:
                include, exclude, by_alias = args
                filtered = cast(FilteredSpecSerializer, self.serializer)
                return filtered(
                    instance,
                    cast(frozenset[str] | None, include),
                    cast(frozenset[str] | None, exclude),
                    cast(bool, by_alias),
                )
            return cast(SpecSerializer, self.serializer)(instance)
        finally:
            active.remove(identity)
            if token is not None:
                _RECURSIVE_OUTPUT.reset(token)
