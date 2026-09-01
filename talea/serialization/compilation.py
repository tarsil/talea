"""Compile one flat outbound serializer from effective Spec declaration truth."""

from collections.abc import Callable
from types import FunctionType
from typing import cast

from talea.codegen import _GeneratedNames
from talea.declaration.models import SerializationHook, SpecField, SpecSchema
from talea.declaration.policies import schema_contains_sensitive_metadata
from talea.serialization.emission import (
    OutputMode,
    _ValueProjectionCompiler,
    project_declared_hook_value,
    project_hook_value,
)
from talea.serialization.selection import _Selection
from talea.validation import compile_validator

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

    def compile(
        self,
        schema: SpecSchema,
        to_dict_fallback: Callable[..., dict[str, object]] | None = None,
    ) -> FunctionType:
        """Return one direct field-reading serializer for ``schema``."""

        names = _GeneratedNames(("instance", "include", "exclude", "exclude_none"))
        namespace: dict[str, object] = {"__name__": __name__}
        if to_dict_fallback is not None:
            parameters = "instance, **options"
        else:
            parameters = "instance, include, exclude, exclude_none" if self.filtered else "instance"
        lines = [f"def serialize({parameters}):"]
        if to_dict_fallback is not None:
            fallback = self._bind(names, namespace, "to_dict_fallback", to_dict_fallback)
            lines.extend(("    if options:", f"        return {fallback}(instance, **options)"))
        hook_by_field = {hook.field: hook for hook in schema.serializers}
        compiler = _ValueProjectionCompiler(self.mode, self.by_alias)
        if not self.filtered:
            if schema.presence_aware:
                lines.append("    result = {}")
                presence = "instance.__talea_presence__"
                for index, field in enumerate(schema.fields):
                    key = field.external_name if self.by_alias else field.name
                    key_name = self._bind(names, namespace, "output_key", key)
                    expression = self._field_expression(
                        field,
                        key,
                        hook_by_field,
                        compiler,
                        names,
                        namespace,
                    )
                    lines.append(f"    if {presence} & {1 << index}:")
                    lines.append(f"        result[{key_name}] = {expression}")
                lines.append("    return result")
            else:
                entries = []
                for field in schema.fields:
                    key = field.external_name if self.by_alias else field.name
                    key_name = self._bind(names, namespace, "output_key", key)
                    expression = self._field_expression(
                        field,
                        key,
                        hook_by_field,
                        compiler,
                        names,
                        namespace,
                    )
                    entries.append(f"{key_name}: {expression}")
                lines.append(f"    return {{{', '.join(entries)}}}")
        else:
            lines.append("    result = {}")
            for index, field in enumerate(schema.fields):
                key = field.external_name if self.by_alias else field.name
                key_name = self._bind(names, namespace, "output_key", key)
                value = f"instance.{field.name}"
                conditions = [
                    f"(include is None or {field.name!r} in include)",
                    f"(exclude is None or {field.name!r} not in exclude)",
                    f"(not exclude_none or {value} is not None)",
                ]
                if schema.presence_aware:
                    conditions.insert(0, f"(instance.__talea_presence__ & {1 << index})")
                lines.append(f"    if {' and '.join(conditions)}:")
                expression = self._field_expression(
                    field,
                    key,
                    hook_by_field,
                    compiler,
                    names,
                    namespace,
                )
                lines.append(f"        result[{key_name}] = {expression}")
            lines.append("    return result")
        exec(compile("\n".join(lines), f"<talea {self.mode} Spec serialization>", "exec"), namespace)
        function = cast(FunctionType, namespace["serialize"])
        if to_dict_fallback is None:
            function.__doc__ = f"Project one Spec to its compiled {self.mode} representation."
        else:
            function.__doc__ = to_dict_fallback.__doc__
            function.__annotations__ = to_dict_fallback.__annotations__
            function.__dict__["__wrapped__"] = to_dict_fallback
        return function

    def compile_selected(
        self,
        schema: SpecSchema,
        include: _Selection | None,
        exclude: _Selection | None,
        exclude_none: bool,
    ) -> FunctionType:
        """Return one direct serializer specialized to a nested selection."""

        names = _GeneratedNames(("instance",))
        namespace: dict[str, object] = {"__name__": __name__}
        lines = ["def serialize(instance):", "    result = {}"]
        hook_by_field = {hook.field: hook for hook in schema.serializers}
        compiler = _ValueProjectionCompiler(self.mode, self.by_alias)
        for index, field, child_include, child_exclude in _selected_fields(schema.fields, include, exclude):
            key = field.external_name if self.by_alias else field.name
            key_name = self._bind(names, namespace, "output_key", key)
            value = f"instance.{field.name}"
            conditions = []
            if schema.presence_aware:
                conditions.append(f"instance.__talea_presence__ & {1 << index}")
            if exclude_none:
                conditions.append(f"{value} is not None")
            indent = "    "
            if conditions:
                lines.append(f"    if {' and '.join(conditions)}:")
                indent = "        "
            expression = self._field_expression(
                field,
                key,
                hook_by_field,
                compiler,
                names,
                namespace,
                include=child_include,
                exclude=child_exclude,
                exclude_none=exclude_none and (child_include is not None or child_exclude is not None),
            )
            lines.append(f"{indent}result[{key_name}] = {expression}")
        lines.append("    return result")
        exec(compile("\n".join(lines), f"<talea selected {self.mode} Spec serialization>", "exec"), namespace)
        function = cast(FunctionType, namespace["serialize"])
        function.__doc__ = f"Project one Spec to its selected compiled {self.mode} representation."
        return function

    def _field_expression(
        self,
        field: SpecField,
        key: str,
        hook_by_field: dict[str, SerializationHook],
        compiler: _ValueProjectionCompiler,
        names: _GeneratedNames,
        namespace: dict[str, object],
        *,
        include: _Selection | None = None,
        exclude: _Selection | None = None,
        exclude_none: bool = False,
    ) -> str:
        """Emit one retained field projection for either storage policy."""

        value = f"instance.{field.name}"
        location = self._bind(names, namespace, "location", (key,))
        hook = hook_by_field.get(field.name)
        if hook is None:
            return compiler.expression(
                field.schema,
                value,
                location,
                names,
                namespace,
                sensitive=bool(field.metadata.sensitive),
                include=include,
                exclude=exclude,
                exclude_none=exclude_none,
            )
        if hook.output_schema is not None:
            sensitive_output = bool(field.metadata.sensitive) or schema_contains_sensitive_metadata(hook.output_schema)
            project = self._bind(names, namespace, "project_declared_hook", project_declared_hook_value)
            function = self._bind(names, namespace, "serialization_hook", hook.function)
            hook_name = self._bind(names, namespace, "serialization_hook_name", hook.name)
            validator = self._bind(
                names,
                namespace,
                "serialization_output_validator",
                compile_validator(hook.output_schema, sensitive=sensitive_output),
            )
            projector = self._bind(
                names,
                namespace,
                "serialization_output_projector",
                _ValueProjectionCompiler(self.mode, self.by_alias).compile(
                    hook.output_schema,
                    sensitive=sensitive_output,
                    include=include,
                    exclude=exclude,
                    exclude_none=exclude_none,
                ),
            )
            sensitive = ", True" if sensitive_output else ""
            return f"{project}({function}, {hook_name}, {validator}, {projector}, {value}, {location}{sensitive})"
        projector = self._bind(names, namespace, "project_hook", project_hook_value)
        function = self._bind(names, namespace, "serialization_hook", hook.function)
        sensitive = ", True" if field.metadata.sensitive else ""
        return f"{projector}({function}, {value}, {self.mode!r}, {self.by_alias!r}, {location}{sensitive})"

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


def compile_plain_to_dict(
    schema: SpecSchema,
    fallback: Callable[..., dict[str, object]],
) -> FunctionType:
    """Compile the public no-option Python path with generic option fallback."""

    return _SerializationCompiler("python", True, False).compile(schema, fallback)


def compile_selected_serialization(
    schema: SpecSchema,
    mode: OutputMode,
    by_alias: bool,
    include: _Selection | None,
    exclude: _Selection | None,
    exclude_none: bool,
) -> SpecSerializer:
    """Compile one nested selection for bounded retention by its Spec artifacts."""

    return _SerializationCompiler(mode, by_alias, False).compile_selected(
        schema,
        include,
        exclude,
        exclude_none,
    )


def _selected_fields(
    fields: tuple[SpecField, ...],
    include: _Selection | None,
    exclude: _Selection | None,
) -> tuple[tuple[int, SpecField, _Selection | None, _Selection | None], ...]:
    """Resolve compile-time field and descendant selection with exclusion precedence."""

    include_by_name = None if include is None else dict(include.entries)
    exclude_by_name = None if exclude is None else dict(exclude.entries)
    selected = []
    for index, field in enumerate(fields):
        if include_by_name is not None and field.name not in include_by_name:
            continue
        if exclude_by_name is not None and field.name in exclude_by_name and exclude_by_name[field.name] is None:
            continue
        child_include = None if include_by_name is None else include_by_name.get(field.name)
        child_exclude = None if exclude_by_name is None else exclude_by_name.get(field.name)
        selected.append((index, field, child_include, child_exclude))
    return tuple(selected)
