"""Compile resource-aware validation for existing mutable Spec graphs."""

from collections.abc import Callable
from contextvars import ContextVar
from threading import RLock
from typing import cast

from talea.codegen import _GeneratedNames
from talea.declaration.models import SpecSchema
from talea.input.emission import _resource_visit_depth
from talea.resources.state import _ResourceState, _UnlimitedResourceState
from talea.schema.nodes import NamedReferenceSchema, Schema
from talea.validation.compilation import _emit_current_state_validation
from talea.validation.emission import _ValidationEmitter

type ResourceState = _ResourceState | _UnlimitedResourceState
type ResourceValidator = Callable[[object, ResourceState], object]

_RESOURCE_VALIDATION_LOCK = RLock()
_RESOURCE_VALIDATION: ContextVar[set[int] | None] = ContextVar(
    "talea_resource_validation",
    default=None,
)


def _call_recursive(operation: ResourceValidator, value: object, state: ResourceState) -> object:
    """Run one current-state back-edge without revisiting cyclic identities."""

    active = _RESOURCE_VALIDATION.get()
    token = None
    if active is None:
        active = set()
        token = _RESOURCE_VALIDATION.set(active)
    identity = id(value)
    if identity in active:
        return value
    active.add(identity)
    try:
        return operation(value, state)
    finally:
        active.remove(identity)
        if token is not None:
            _RESOURCE_VALIDATION.reset(token)


class _ResourceSpecValidator:
    """Lazily own one resource-aware recursive Spec current-state validator."""

    __slots__ = ("compiled", "spec_type")

    def __init__(self, spec_type: type[object]) -> None:
        self.spec_type = spec_type
        self.compiled: ResourceValidator | None = None

    def __call__(self, value: object, state: ResourceState) -> object:
        compiled = self.compiled
        if compiled is None:
            with _RESOURCE_VALIDATION_LOCK:
                compiled = self.compiled
                if compiled is None:
                    artifacts = vars(self.spec_type)["__talea_artifacts__"]
                    compiled = compile_resource_current_validator(artifacts.schema)
                    self.compiled = compiled
        return _call_recursive(compiled, value, state)


class _ResourceNamedValidator:
    """Resolve one resource-aware validator across a named graph edge."""

    __slots__ = ("reference", "sensitive")

    def __init__(self, reference: NamedReferenceSchema, sensitive: bool) -> None:
        self.reference = reference
        self.sensitive = sensitive

    def __call__(self, value: object, state: ResourceState) -> object:
        compiled = self.reference._target.operation(
            ("resource_validation", self.sensitive),
            lambda schema: compile_resource_validator(schema, sensitive=self.sensitive),
        )
        return _call_recursive(compiled, value, state)


class _ResourceValidationEmitter(_ValidationEmitter):
    """Add operation-local resource accounting to canonical strict emission."""

    def __init__(
        self,
        lines: list[str],
        names: _GeneratedNames,
        namespace: dict[str, object],
        state: str,
        *,
        title: str | None = None,
    ) -> None:
        super().__init__(lines, names, namespace, title=title)
        self.state = state

    def emit_schema(
        self,
        schema: Schema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        *,
        sensitive: bool | None = None,
    ) -> None:
        depth = _resource_visit_depth(schema, location)
        if depth is not None:
            self.emit(indentation, f"{self.state}.consume_node({depth})")
        super().emit_schema(
            schema,
            value,
            location,
            indentation,
            sensitive=sensitive,
        )

    def emit_named_reference(
        self,
        schema: NamedReferenceSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Call a resource-aware named validator with shared depth and work."""

        operation = self.bind(
            "resource_named_validator",
            _ResourceNamedValidator(schema, self.sensitive),
        )
        error = self.variable("named_error")
        prefixed = self.variable("prefixed_error")
        self.emit(indentation, "try:")
        self.emit(
            indentation + 1,
            self.operation_call_expression(operation, value, location),
        )
        self.emit(indentation, f"except {self.validation_error_name} as {error}:")
        self.emit(
            indentation + 1,
            f"{prefixed} = {error}.prefixed({self.location_expression(location)}"
            f"{self.title_argument()}{self.sensitive_argument()})",
        )
        self.emit(indentation + 1, f"raise {prefixed} from {prefixed}.__cause__")

    def operation_call_expression(
        self,
        operation: str,
        value: str,
        location: tuple[str, ...],
    ) -> str:
        """Share state and absolute depth across a compiled graph edge."""

        return f"{self.state}.call_nested({operation}, {value}, {len(location)})"

    def tagged_branch_operation(self, schema: Schema, *, json: bool):
        """Compile a selected strict branch with the same resource semantics."""

        del json
        return compile_resource_validator(schema, sensitive=self.sensitive)

    @staticmethod
    def recursive_spec_validator(spec_type: type[object]) -> object:
        """Return a resource-aware current-state recursive back-edge."""

        return _ResourceSpecValidator(spec_type)

    def recursive_spec_call_expression(
        self,
        operation: str,
        value: str,
        location: tuple[str, ...],
    ) -> str:
        """Share resource state across recursive Spec current-state calls."""

        return self.operation_call_expression(operation, value, location)


def compile_resource_validator(
    schema: Schema,
    *,
    sensitive: bool = False,
) -> ResourceValidator:
    """Compile one strict validator that consumes explicit resource state."""

    names = _GeneratedNames(("value", "resource_state"))
    state = names.allocate("resource_state")
    lines = [f"def validate(value, {state}):"]
    namespace: dict[str, object] = {"__name__": __name__}
    emitter = _ResourceValidationEmitter(lines, names, namespace, state)
    emitter.emit_schema(schema, "value", (), 1, sensitive=sensitive)
    emitter.emit(1, "return value")
    exec(compile("\n".join(lines), "<talea resource validator>", "exec"), namespace)
    return cast(ResourceValidator, namespace["validate"])


def compile_resource_current_validator(schema: SpecSchema) -> ResourceValidator:
    """Compile resource-aware direct-field checks for one mutable Spec."""

    names = _GeneratedNames(("value", "resource_state"))
    state = names.allocate("resource_state")
    lines = [f"def validate(value, {state}):"]
    namespace: dict[str, object] = {"__name__": __name__}
    emitter = _ResourceValidationEmitter(lines, names, namespace, state)
    _emit_current_state_validation(emitter, schema, "value", 1)
    emitter.emit(1, "return value")
    exec(compile("\n".join(lines), "<talea resource current-state validator>", "exec"), namespace)
    return cast(ResourceValidator, namespace["validate"])
