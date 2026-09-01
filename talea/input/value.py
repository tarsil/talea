"""Compile schema-specialized input boundaries for arbitrary root values."""

from collections.abc import Callable
from typing import cast

from talea.codegen import _GeneratedNames
from talea.declaration.policies import schema_contains_named_reference
from talea.input.emission import (
    InputMode,
    _BoundaryValidationEmitter,
    schema_may_construct_spec,
)
from talea.input.references import wrap_named_input_root
from talea.resources.state import UNLIMITED_RESOURCE_STATE
from talea.schema.nodes import Schema

type ValueInput = Callable[..., object]


def compile_value_input(
    schema: Schema,
    mode: InputMode,
    title: str,
    *,
    sensitive: bool = False,
    cycle_sensitive: bool | None = None,
) -> ValueInput:
    """Compile one arbitrary root boundary without a wrapper Spec.

    The generated callable shares the exact conversion and validation emitter
    used by Spec fields. It returns the declared root representation and retains
    no annotation or runtime schema walker.
    """

    names = _GeneratedNames(("value",))
    trusted = names.allocate("trusted_instances") if schema_may_construct_spec(schema) else None
    state = names.allocate("resource_state")
    unlimited = names.allocate("unlimited_resource_state")
    lines = [f"def convert(value, {state}={unlimited}):"]
    if trusted is not None:
        lines.append(f"    {trusted} = None")
    namespace: dict[str, object] = {"__name__": __name__, unlimited: UNLIMITED_RESOURCE_STATE}
    emitter = _BoundaryValidationEmitter(
        lines,
        names,
        namespace,
        mode=mode,
        title=title,
        trusted_instances=trusted,
        resource_state=state,
    )
    emitter.emit_root_schema(schema, "value", (), 1, sensitive=sensitive)
    emitter.emit(1, "return value")
    exec(compile("\n".join(lines), f"<talea {mode} value input>", "exec"), namespace)
    compiled = cast(ValueInput, namespace["convert"])
    if schema_contains_named_reference(schema):
        return wrap_named_input_root(
            compiled,
            title,
            sensitive if cycle_sensitive is None else cycle_sensitive,
        )
    return compiled
