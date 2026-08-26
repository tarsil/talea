"""Compile standalone validators through Talea's shared operation emitter."""

from collections.abc import Callable
from typing import cast

from talea.codegen import _GeneratedNames
from talea.declaration.models import SpecSchema
from talea.schema.nodes import Schema
from talea.validation.emission import _ValidationEmitter

type Validator = Callable[[object], object]


class _ValidatorCompiler:
    """Wrap shared validation emission in a standalone one-argument function."""

    def compile(self, schema: Schema, *, sensitive: bool = False) -> Validator:
        """Compile ``schema`` and return its single-argument validation function."""

        lines = ["def validate(value):"]
        namespace: dict[str, object] = {"__name__": __name__}
        emitter = _ValidationEmitter(lines, _GeneratedNames(("value",)), namespace)
        emitter.emit_schema(schema, "value", (), 1, sensitive=sensitive)
        emitter.emit(1, "return value")
        source = "\n".join(lines)
        exec(compile(source, "<talea validator>", "exec"), namespace)
        validator = cast(Validator, namespace["validate"])
        validator.__doc__ = "Validate one existing Python value strictly and return that same object on success."
        return validator


def compile_validator(schema: Schema, *, sensitive: bool = False) -> Validator:
    """Compile canonical schema into a strict reusable validator.

    The returned callable preserves the input object on success. Compilation
    removes schema dispatch, annotation reflection, and constraint iteration
    from runtime execution; each call compiles independently because caching
    belongs to declaration lifecycle owners.
    """

    return _ValidatorCompiler().compile(schema, sensitive=sensitive)


def compile_current_state_validator(schema: SpecSchema) -> Validator:
    """Compile direct current-state checks for one non-permanently-trusted Spec."""

    lines = ["def validate(value):"]
    namespace: dict[str, object] = {"__name__": __name__}
    emitter = _ValidationEmitter(lines, _GeneratedNames(("value",)), namespace)
    _emit_current_state_validation(emitter, schema, "value", 1)
    emitter.emit(1, "return value")
    namespace["__name__"] = __name__
    exec(compile("\n".join(lines), "<talea current-state validator>", "exec"), namespace)
    return cast(Validator, namespace["validate"])


def _emit_current_state_validation(
    emitter: _ValidationEmitter,
    schema: SpecSchema,
    value: str,
    indentation: int,
) -> None:
    """Emit canonical direct-field validation into one specialized target."""

    field_names = tuple(field.name for field in schema.fields)
    names = emitter.bind("field_names", field_names)
    for index, field in enumerate(schema.fields):
        nested = emitter.variable("current_field")
        emitter.emit(indentation, f"{nested} = {value}.{field.name}")
        emitter.emit_schema(
            field.schema,
            nested,
            (f"{names}[{index}]",),
            indentation,
            sensitive=bool(field.metadata.sensitive),
        )
        for hook in schema.hooks:
            if hook.kind == "check" and hook.fields == (field.name,):
                emitter.emit_check(
                    hook,
                    (nested,),
                    ((f"{names}[{index}]",),),
                    indentation,
                    sensitive=bool(field.metadata.sensitive),
                )
    indices = {field.name: index for index, field in enumerate(schema.fields)}
    for hook in schema.hooks:
        if hook.kind == "check" and len(hook.fields) > 1:
            emitter.emit_check(
                hook,
                tuple(f"{value}.{name}" for name in hook.fields),
                tuple((f"{names}[{indices[name]}]",) for name in hook.fields),
                indentation,
                sensitive=any(bool(schema.fields[indices[name]].metadata.sensitive) for name in hook.fields),
            )
