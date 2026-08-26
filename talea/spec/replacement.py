"""Compile trust-aware immutable replacement for Talea Spec instances."""

from collections.abc import Callable
from threading import RLock
from types import FunctionType
from typing import cast

from talea.codegen import _GeneratedNames
from talea.declaration.models import SpecSchema
from talea.declaration.policies import schema_values_are_immutable
from talea.spec.declaration import _SpecArtifacts
from talea.validation.emission import _ValidationEmitter

type Replacer = Callable[[object, dict[str, object]], object]

_REPLACEMENT_COMPILATION_LOCK = RLock()


class _ReplacementCompiler:
    """Emit one atomic replacement path for an effective Spec schema."""

    __slots__ = ("title",)

    def __init__(self, title: str) -> None:
        self.title = title

    def compile(
        self,
        schema: SpecSchema,
        spec_type: type[object],
        slot_setters: tuple[Callable[[object, object], None], ...],
    ) -> Replacer:
        """Return a replacer specialized to fields, trust, hooks, and slots."""

        fields = schema.fields
        field_names = tuple(field.name for field in fields)
        names = _GeneratedNames(("instance", "changes"))
        field_names_name = names.allocate("field_names")
        known_names = names.allocate("known_names")
        unknown = names.allocate("unknown_names")
        lines = [
            "def replace(instance, changes):",
            f"    {unknown} = changes.keys() - {known_names}",
            f"    if {unknown}:",
            f"        raise TypeError(f'unexpected replacement field {{min({unknown})!r}}')",
        ]
        namespace: dict[str, object] = {
            "__name__": __name__,
            field_names_name: field_names,
            known_names: frozenset(field_names),
        }
        emitter = _ValidationEmitter(lines, names, namespace, title=self.title)
        values = tuple(names.allocate(f"field_{index}") for index in range(len(fields)))
        transforms_by_field = {
            field.name: tuple(
                hook for hook in schema.hooks if hook.kind == "transform" and hook.fields == (field.name,)
            )
            for field in fields
        }
        checks_by_field = {
            field.name: tuple(hook for hook in schema.hooks if hook.kind == "check" and hook.fields == (field.name,))
            for field in fields
        }
        for index, (field, value) in enumerate(zip(fields, values, strict=True)):
            location = (f"{field_names_name}[{index}]",)
            lines.append(f"    if {field.name!r} in changes:")
            lines.append(f"        {value} = changes[{field.name!r}]")
            for hook in transforms_by_field[field.name]:
                emitter.emit_transform(hook, value, location, 2)
            emitter.emit_schema(field.schema, value, location, 2)
            for hook in checks_by_field[field.name]:
                emitter.emit_check(hook, (value,), (location,), 2)
            lines.append("    else:")
            lines.append(f"        {value} = instance.{field.name}")
            if not schema_values_are_immutable(field.schema):
                emitter.emit_schema(field.schema, value, location, 2)
                for hook in checks_by_field[field.name]:
                    emitter.emit_check(hook, (value,), (location,), 2)

        field_indices = {field.name: index for index, field in enumerate(fields)}
        for hook in schema.hooks:
            if hook.kind == "check" and len(hook.fields) > 1:
                emitter.emit_check(
                    hook,
                    tuple(values[field_indices[name]] for name in hook.fields),
                    tuple((f"{field_names_name}[{field_indices[name]}]",) for name in hook.fields),
                    1,
                )
        spec_type_name = emitter.bind("spec_type", spec_type)
        object_new = emitter.bind("object_new", object.__new__)
        replacement = names.allocate("replacement")
        lines.append(f"    {replacement} = {object_new}({spec_type_name})")
        for value, setter in zip(values, slot_setters, strict=True):
            setter_name = emitter.bind("slot_setter", setter)
            lines.append(f"    {setter_name}({replacement}, {value})")
        lines.append(f"    return {replacement}")
        exec(compile("\n".join(lines), "<talea Spec replacement>", "exec"), namespace)
        return cast(FunctionType, namespace["replace"])


def replacement_for(spec_type: type[object], artifacts: _SpecArtifacts) -> Replacer:
    """Return the class-owned replacer, publishing it once on first use."""

    replacer = vars(spec_type).get("__talea_replacer__")
    if replacer is not None:
        return cast(Replacer, replacer)
    with _REPLACEMENT_COMPILATION_LOCK:
        replacer = vars(spec_type).get("__talea_replacer__")
        if replacer is None:
            replacer = _ReplacementCompiler(spec_type.__name__).compile(
                artifacts.schema,
                spec_type,
                artifacts.inputs.slot_setters,
            )
            type.__setattr__(spec_type, "__talea_replacer__", replacer)
    return cast(Replacer, replacer)
