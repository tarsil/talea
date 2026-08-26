"""Compile one flat outbound serializer from effective Spec declaration truth."""

from collections.abc import Callable
from types import FunctionType
from typing import cast

from talea.declaration.models import SpecSchema
from talea.serialization.emission import OutputMode, _ValueProjectionCompiler, project_hook_value
from talea.validation.emission import _GeneratedNames

type SpecSerializer = Callable[[object], dict[str, object]]
type FilteredSpecSerializer = Callable[
    [object, frozenset[str] | None, frozenset[str] | None, bool],
    dict[str, object],
]


class _SerializationCompiler:
    """Compile one Spec projection specialized by mode, key policy, and filtering."""

    __slots__ = ("by_alias", "filtered", "mode")

    def __init__(self, mode: OutputMode, by_alias: bool, filtered: bool) -> None:
        self.mode = mode
        self.by_alias = by_alias
        self.filtered = filtered

    def compile(self, schema: SpecSchema) -> FunctionType:
        """Return one direct field-reading serializer for ``schema``."""

        names = _GeneratedNames(("instance", "include", "exclude", "exclude_none"))
        namespace: dict[str, object] = {"__name__": __name__}
        parameters = "instance, include, exclude, exclude_none" if self.filtered else "instance"
        lines = [f"def serialize({parameters}):"]
        hook_by_field = {hook.field: hook for hook in schema.serializers}
        compiler = _ValueProjectionCompiler(self.mode, self.by_alias)
        if not self.filtered:
            entries = []
            for field in schema.fields:
                key = field.external_name if self.by_alias else field.name
                value = f"instance.{field.name}"
                location = self._bind(names, namespace, "location", (key,))
                hook = hook_by_field.get(field.name)
                if hook is None:
                    expression = compiler.expression(field.schema, value, location, names, namespace)
                else:
                    projector = self._bind(names, namespace, "project_hook", project_hook_value)
                    function = self._bind(names, namespace, "serialization_hook", hook.function)
                    expression = f"{projector}({function}, {value}, {self.mode!r}, {self.by_alias!r}, {location})"
                entries.append(f"{key!r}: {expression}")
            lines.append(f"    return {{{', '.join(entries)}}}")
        else:
            lines.append("    result = {}")
            for field in schema.fields:
                key = field.external_name if self.by_alias else field.name
                value = f"instance.{field.name}"
                conditions = [
                    f"(include is None or {field.name!r} in include)",
                    f"(exclude is None or {field.name!r} not in exclude)",
                    f"(not exclude_none or {value} is not None)",
                ]
                lines.append(f"    if {' and '.join(conditions)}:")
                location = self._bind(names, namespace, "location", (key,))
                hook = hook_by_field.get(field.name)
                if hook is None:
                    expression = compiler.expression(field.schema, value, location, names, namespace)
                else:
                    projector = self._bind(names, namespace, "project_hook", project_hook_value)
                    function = self._bind(names, namespace, "serialization_hook", hook.function)
                    expression = f"{projector}({function}, {value}, {self.mode!r}, {self.by_alias!r}, {location})"
                lines.append(f"        result[{key!r}] = {expression}")
            lines.append("    return result")
        exec(compile("\n".join(lines), f"<talea {self.mode} Spec serialization>", "exec"), namespace)
        function = cast(FunctionType, namespace["serialize"])
        function.__doc__ = f"Project one Spec to its compiled {self.mode} representation."
        return function

    @staticmethod
    def _bind(names: _GeneratedNames, namespace: dict[str, object], purpose: str, value: object) -> str:
        name = names.allocate(purpose)
        namespace[name] = value
        return name


def compile_serialization(
    schema: SpecSchema,
    mode: OutputMode,
    by_alias: bool,
    filtered: bool,
) -> SpecSerializer | FilteredSpecSerializer:
    """Compile one lazy outbound artifact without altering input or construction."""

    return _SerializationCompiler(mode, by_alias, filtered).compile(schema)
