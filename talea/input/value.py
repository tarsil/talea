"""Compile schema-specialized input boundaries for arbitrary root values."""

from collections.abc import Callable
from typing import cast

from talea.codegen import _GeneratedNames
from talea.input.emission import InputMode, _BoundaryValidationEmitter, schema_may_construct_spec
from talea.schema.nodes import Schema

type ValueInput = Callable[[object], object]


def compile_value_input(schema: Schema, mode: InputMode, title: str) -> ValueInput:
    """Compile one arbitrary root boundary without a wrapper Spec.

    The generated callable shares the exact conversion and validation emitter
    used by Spec fields. It returns the declared root representation and retains
    no annotation or runtime schema walker.
    """

    names = _GeneratedNames(("value",))
    trusted = names.allocate("trusted_instances") if schema_may_construct_spec(schema) else None
    lines = ["def convert(value):"]
    if trusted is not None:
        lines.append(f"    {trusted} = None")
    namespace: dict[str, object] = {"__name__": __name__}
    emitter = _BoundaryValidationEmitter(
        lines,
        names,
        namespace,
        mode=mode,
        title=title,
        trusted_instances=trusted,
    )
    emitter.emit_schema(schema, "value", (), 1)
    emitter.emit(1, "return value")
    exec(compile("\n".join(lines), f"<talea {mode} value input>", "exec"), namespace)
    return cast(ValueInput, namespace["convert"])
