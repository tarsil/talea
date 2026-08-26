"""Own finite identity and finalize-once targets for named schema graphs."""

from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING, Callable, Literal, cast

if TYPE_CHECKING:
    from talea.schema.nodes import Schema

type NamedSchemaKind = Literal["alias", "typed_dict"]


@dataclass(frozen=True, slots=True)
class NamedSchemaIdentity:
    """Identify one alias or TypedDict declaration and concrete specialization.

    Python declaration identity, rather than display text, is canonical. Generic
    arguments complete the identity because Python may create a fresh but equal
    specialization wrapper for every subscription.
    """

    kind: NamedSchemaKind
    name: str
    module: str
    declaration: object = field(repr=False)
    arguments: tuple[object, ...] = ()


class _NamedSchemaTarget:
    """Publish one immutable named schema after its reachable graph resolves."""

    __slots__ = ("_lock", "_operations", "_schema", "identity")

    def __init__(self, identity: NamedSchemaIdentity) -> None:
        self.identity = identity
        self._schema: Schema | None = None
        self._operations: dict[tuple[object, ...], object] = {}
        self._lock = RLock()

    @property
    def finalized(self) -> bool:
        """Return whether canonical target truth has been published."""

        return self._schema is not None

    @property
    def schema(self) -> "Schema":
        """Return finalized target truth, rejecting stale artifact compilation."""

        schema = self._schema
        if schema is None:
            raise RuntimeError(f"named schema {self.identity.name!r} is not finalized")
        return schema

    def finalize(self, schema: "Schema") -> None:
        """Publish one target exactly once after successful graph resolution."""

        with self._lock:
            if self._schema is not None:
                if self._schema is not schema:
                    raise RuntimeError(f"named schema {self.identity.name!r} was finalized twice")
                return
            self._schema = schema

    def operation[T](
        self,
        key: tuple[object, ...],
        compile_operation: Callable[["Schema"], T],
    ) -> T:
        """Return one graph-owned operation, publishing it atomically once."""

        operation = self._operations.get(key)
        if operation is not None:
            return cast(T, operation)
        with self._lock:
            operation = self._operations.get(key)
            if operation is None:
                operation = compile_operation(self.schema)
                self._operations[key] = operation
        return cast(T, operation)


@dataclass(frozen=True, slots=True)
class NamedReferenceSchema:
    """Finite canonical back-edge to a named alias or TypedDict declaration.

    Public identity is immutable and sufficient for introspection and
    definition naming. The private target is a finalize-once resolution owner;
    execution artifacts may consume it only after the root resolution call has
    completed.
    """

    identity: NamedSchemaIdentity
    _target: _NamedSchemaTarget = field(repr=False, compare=False)

    @property
    def target(self) -> "Schema":
        """Return the finalized canonical schema at this graph edge."""

        return self._target.schema
