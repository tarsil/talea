"""Compile specialized external-object construction for one Spec declaration."""

from collections.abc import Callable, Mapping
from types import FunctionType
from typing import cast

from talea.declaration.models import SpecSchema
from talea.errors import ErrorCode
from talea.input.emission import (
    InputMode,
    _BoundaryValidationEmitter,
    schema_may_construct_spec,
)
from talea.spec.fields import FACTORY_SENTINEL
from talea.validation.emission import _GeneratedNames

type InputCallable = Callable[[object], object]


class _InputCompiler:
    """Compile one aggregated Mapping or decoded-JSON construction path."""

    __slots__ = ("mode", "title")

    def __init__(self, mode: InputMode, title: str) -> None:
        self.mode = mode
        self.title = title

    def compile(
        self,
        schema: SpecSchema,
        spec_type: type[object],
        slot_setters: tuple[Callable[[object, object], None], ...],
    ) -> FunctionType:
        """Return a boundary callable specialized to fields, hooks, and storage."""

        fields = schema.fields
        field_names = tuple(field.external_name for field in fields)
        names = _GeneratedNames((*field_names, "data"))
        errors = names.allocate("errors")
        missing_fields = names.allocate("missing_fields")
        exact_dict = names.allocate("exact_dict")
        trusted = (
            names.allocate("trusted_instances")
            if any(schema_may_construct_spec(field.schema) for field in fields)
            else None
        )
        missing = names.allocate("missing")
        field_names_name = names.allocate("field_names")
        known_names = names.allocate("known_names")
        lines = [
            "def construct(data):",
            f"    {errors} = None",
            f"    {missing_fields} = False",
        ]
        if trusted is not None:
            lines.append(f"    {trusted} = None")
        namespace: dict[str, object] = {
            "__name__": __name__,
            missing: FACTORY_SENTINEL,
            field_names_name: field_names,
            known_names: frozenset(field_names),
        }
        emitter = _BoundaryValidationEmitter(
            lines,
            names,
            namespace,
            title=self.title,
            trusted_instances=trusted,
            mode=self.mode,
        )
        lines.append(f"    {exact_dict} = {emitter.runtime('type', type)}(data) is {emitter.runtime('dict', dict)}")
        self._emit_root_check(emitter, "data", exact_dict, 1)
        values = tuple(names.allocate(f"field_{index}") for index in range(len(fields)))
        validation_errors = {index: names.allocate(f"field_error_{index}") for index in range(len(fields))}
        key_error = emitter.runtime("key_error", KeyError)
        unexpected_key = names.allocate("unexpected_key")
        unexpected_value = names.allocate("unexpected_value")
        unexpected_error = names.allocate("unexpected_error")
        string_type = emitter.runtime("str", str)
        if all(field.required for field in fields):
            self._emit_exact_dict_path(
                emitter,
                schema,
                spec_type,
                slot_setters,
                values,
                validation_errors,
                errors,
                exact_dict,
                key_error,
                field_names_name,
                known_names,
                unexpected_key,
                unexpected_value,
                unexpected_error,
                string_type,
            )
        for index, (field, value) in enumerate(zip(fields, values, strict=True)):
            lines.extend(
                (
                    "    try:",
                    f"        {value} = data[{field.external_name!r}]",
                    f"    except {key_error}:",
                    f"        {value} = {missing}",
                    f"    if {value} is {missing}:",
                    f"        {missing_fields} = True",
                )
            )
            if field.required:
                missing_error = names.allocate(f"missing_error_{index}")
                lines.append(
                    f"        {missing_error} = {emitter.validation_error_name}._missing("
                    f"({field_names_name}[{index}],), title={emitter.title_name})"
                )
                self._emit_collect(lines, errors, missing_error, 2)
            elif field.has_static_default:
                default_name = emitter.bind("static_default", field.default)
                lines.append(f"        {value} = {default_name}")
            else:
                lines.append("        pass")
            lines.append("    else:")
            lines.append("        try:")
            self._emit_field_pipeline(emitter, schema, index, value, 3)
            error = validation_errors[index]
            lines.append(f"        except {emitter.validation_error_name} as {error}:")
            self._emit_collect(lines, errors, error, 3)

        lines.extend(
            (
                f"    if not {exact_dict} or {missing_fields} or {emitter.runtime('len', len)}(data) != {len(fields)}:",
                f"        for {unexpected_key} in data:",
                f"            if {emitter.runtime('type', type)}({unexpected_key}) is not {string_type} "
                f"or {unexpected_key} not in {known_names}:",
                f"                {unexpected_value} = data[{unexpected_key}]",
                f"                {unexpected_error} = {emitter.validation_error_name}("
                f"None, {unexpected_value}, ({unexpected_key},), "
                f"{emitter.runtime('error_code_unexpected', ErrorCode.UNEXPECTED)}, "
                f"title={emitter.title_name})",
            )
        )
        self._emit_collect(lines, errors, unexpected_error, 4)
        self._emit_raise_aggregate(lines, emitter, errors, 1)

        has_factories = False
        for index, (field, value) in enumerate(zip(fields, values, strict=True)):
            if field.default_factory is None:
                continue
            has_factories = True
            factory = emitter.bind("default_factory", field.default_factory)
            factory_error = names.allocate(f"factory_error_{index}")
            factory_failure = names.allocate(f"factory_failure_{index}")
            context = emitter.bind("factory_error_context", (("field", field.external_name),))
            lines.extend(
                (
                    f"    if {value} is {missing}:",
                    "        try:",
                    f"            {value} = {factory}()",
                    f"        except {emitter.runtime('exception', Exception)} as {factory_error}:",
                    f"            {factory_failure} = {emitter.validation_error_name}("
                    f"None, {factory_error}, ({field_names_name}[{index}],), "
                    f"{emitter.runtime('error_code_factory', ErrorCode.FACTORY)}, "
                    f"title={emitter.title_name}, context={context})",
                )
            )
            self._emit_collect(lines, errors, factory_failure, 3)
            lines.append("        else:")
            lines.append("            try:")
            self._emit_field_pipeline(emitter, schema, index, value, 4)
            error = validation_errors[index]
            lines.append(f"            except {emitter.validation_error_name} as {error}:")
            self._emit_collect(lines, errors, error, 4)
        if has_factories:
            self._emit_raise_aggregate(lines, emitter, errors, 1)

        self._emit_commit(emitter, schema, spec_type, slot_setters, values, field_names_name, 1)

        source = "\n".join(lines)
        exec(compile(source, f"<talea {self.mode} input>", "exec"), namespace)
        function = cast(FunctionType, namespace["construct"])
        function.__doc__ = f"Construct one {self.title} from untrusted {self.mode} data."
        return function

    def _emit_exact_dict_path(
        self,
        emitter: _BoundaryValidationEmitter,
        schema: SpecSchema,
        spec_type: type[object],
        slot_setters: tuple[Callable[[object, object], None], ...],
        values: tuple[str, ...],
        validation_errors: dict[int, str],
        errors: str,
        exact_dict: str,
        key_error: str,
        field_names_name: str,
        known_names: str,
        unexpected_key: str,
        unexpected_value: str,
        unexpected_error: str,
        string_type: str,
    ) -> None:
        """Emit complete exact-dict construction before general Mapping analysis."""

        emitter.emit(1, f"if {exact_dict} and {emitter.runtime('len', len)}(data) == {len(schema.fields)}:")
        emitter.emit(2, "try:")
        if values:
            for field, value in zip(schema.fields, values, strict=True):
                emitter.emit(3, f"{value} = data[{field.external_name!r}]")
        else:
            emitter.emit(3, "pass")
        emitter.emit(2, f"except {key_error}:")
        emitter.emit(3, "pass")
        emitter.emit(2, "else:")
        for index, value in enumerate(values):
            emitter.emit(3, "try:")
            self._emit_field_pipeline(emitter, schema, index, value, 4)
            error = validation_errors[index]
            emitter.emit(3, f"except {emitter.validation_error_name} as {error}:")
            self._emit_collect(emitter.lines, errors, error, 4)
        emitter.emit(3, f"if {emitter.runtime('len', len)}(data) != {len(schema.fields)}:")
        emitter.emit(4, f"for {unexpected_key} in data:")
        emitter.emit(
            5,
            f"if {emitter.runtime('type', type)}({unexpected_key}) is not {string_type} "
            f"or {unexpected_key} not in {known_names}:",
        )
        emitter.emit(6, f"{unexpected_value} = data[{unexpected_key}]")
        emitter.emit(
            6,
            f"{unexpected_error} = {emitter.validation_error_name}("
            f"None, {unexpected_value}, ({unexpected_key},), "
            f"{emitter.runtime('error_code_unexpected', ErrorCode.UNEXPECTED)}, "
            f"title={emitter.title_name})",
        )
        self._emit_collect(emitter.lines, errors, unexpected_error, 6)
        self._emit_raise_aggregate(emitter.lines, emitter, errors, 3)
        self._emit_commit(
            emitter,
            schema,
            spec_type,
            slot_setters,
            values,
            field_names_name,
            3,
        )

    @staticmethod
    def _emit_commit(
        emitter: _BoundaryValidationEmitter,
        schema: SpecSchema,
        spec_type: type[object],
        slot_setters: tuple[Callable[[object, object], None], ...],
        values: tuple[str, ...],
        field_names_name: str,
        indentation: int,
    ) -> None:
        """Emit whole-Spec checks and direct immutable slot commitment."""

        field_indices = {field.name: index for index, field in enumerate(schema.fields)}
        for hook in schema.hooks:
            if hook.kind == "check" and len(hook.fields) > 1:
                emitter.emit_check(
                    hook,
                    tuple(values[field_indices[name]] for name in hook.fields),
                    tuple((f"{field_names_name}[{field_indices[name]}]",) for name in hook.fields),
                    indentation,
                )
        instance = emitter.variable("instance")
        allocator = emitter.bind("instance_allocator", object.__new__)
        spec_type_name = emitter.bind("spec_type", spec_type)
        emitter.emit(indentation, f"{instance} = {allocator}({spec_type_name})")
        for index, (value, setter) in enumerate(zip(values, slot_setters, strict=True)):
            setter_name = emitter.bind(f"slot_{index}", setter)
            emitter.emit(indentation, f"{setter_name}({instance}, {value})")
        emitter.emit(indentation, f"return {instance}")

    def _emit_root_check(
        self,
        emitter: _BoundaryValidationEmitter,
        value: str,
        exact_dict: str,
        indentation: int,
    ) -> None:
        if self.mode == "mapping":
            mapping_type = emitter.runtime("mapping", Mapping)
            condition = f"not {exact_dict} and not {emitter.runtime('isinstance', isinstance)}({value}, {mapping_type})"
            expected = "Mapping[str, object]"
        else:
            condition = f"not {exact_dict}"
            expected = "JSON object"
        emitter.emit(indentation, f"if {condition}:")
        code = emitter.runtime("error_code_type", ErrorCode.TYPE)
        emitter.emit(
            indentation + 1,
            f"raise {emitter.validation_error_name}({expected!r}, {value}, (), {code}, "
            f"title={emitter.title_name}) from None",
        )

    def _emit_field_pipeline(
        self,
        emitter: _BoundaryValidationEmitter,
        schema: SpecSchema,
        index: int,
        value: str,
        indentation: int,
    ) -> None:
        field = schema.fields[index]
        field_name = emitter.bind("field_name", field.external_name)
        for hook in schema.hooks:
            if hook.kind == "transform" and hook.fields == (field.name,):
                emitter.emit_transform(hook, value, (field_name,), indentation)
        emitter.emit_schema(field.schema, value, (field_name,), indentation)
        for hook in schema.hooks:
            if hook.kind == "check" and hook.fields == (field.name,):
                emitter.emit_check(hook, (value,), ((field_name,),), indentation)

    @staticmethod
    def _emit_collect(lines: list[str], errors: str, error: str, indentation: int) -> None:
        prefix = "    " * indentation
        lines.extend(
            (
                f"{prefix}if {errors} is None:",
                f"{prefix}    {errors} = [{error}]",
                f"{prefix}else:",
                f"{prefix}    {errors}.append({error})",
            )
        )

    @staticmethod
    def _emit_raise_aggregate(
        lines: list[str],
        emitter: _BoundaryValidationEmitter,
        errors: str,
        indentation: int,
    ) -> None:
        prefix = "    " * indentation
        lines.extend(
            (
                f"{prefix}if {errors} is not None:",
                f"{prefix}    raise {emitter.validation_error_name}._aggregate("
                f"tuple({errors}), title={emitter.title_name}) from None",
            )
        )


def compile_input(
    schema: SpecSchema,
    spec_type: type[object],
    slot_setters: tuple[Callable[[object, object], None], ...],
    mode: InputMode,
) -> InputCallable:
    """Compile one external input path without changing ordinary construction."""

    return _InputCompiler(mode, spec_type.__name__).compile(schema, spec_type, slot_setters)
