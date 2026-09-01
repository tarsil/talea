"""Compile specialized external-object construction for one Spec declaration."""

from collections.abc import Callable, Mapping
from types import FunctionType
from typing import cast

from talea.codegen import _GeneratedNames
from talea.declaration.models import SpecField, SpecSchema
from talea.declaration.policies import schema_contains_representation
from talea.errors import ErrorCode
from talea.input.emission import (
    InputMode,
    _BoundaryValidationEmitter,
    schema_may_construct_spec,
)
from talea.resources.state import UNLIMITED_RESOURCE_STATE
from talea.spec.fields import FACTORY_SENTINEL

type InputCallable = Callable[..., object]


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
        presence_setter: Callable[[object, object], None] | None = None,
    ) -> FunctionType:
        """Return a boundary callable specialized to fields, hooks, and storage."""

        fields = schema.fields
        field_names = tuple(field.external_name for field in fields)
        has_legacy_names = any(field.legacy_names for field in fields)
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
        resource_state = names.allocate("resource_state")
        unlimited_resource_state = names.allocate("unlimited_resource_state")
        lines = [
            f"def construct(data, {resource_state}={unlimited_resource_state}):",
            f"    {resource_state}.consume_node(1)",
            f"    {errors} = None",
            f"    {missing_fields} = False",
        ]
        if trusted is not None:
            lines.append(f"    {trusted} = None")
        namespace: dict[str, object] = {
            "__name__": __name__,
            missing: FACTORY_SENTINEL,
            field_names_name: field_names,
            known_names: (
                frozenset(name for field in fields for name in field.accepted_input_names)
                if has_legacy_names
                else frozenset(field_names)
            ),
            unlimited_resource_state: UNLIMITED_RESOURCE_STATE,
        }
        emitter = _BoundaryValidationEmitter(
            lines,
            names,
            namespace,
            title=self.title,
            trusted_instances=trusted,
            mode=self.mode,
            resource_state=resource_state,
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
        if not has_legacy_names and all(field.required for field in fields):
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
                presence_setter,
            )
        for index, (field, value) in enumerate(zip(fields, values, strict=True)):
            if field.legacy_names:
                self._emit_legacy_lookup(
                    emitter,
                    schema,
                    field,
                    index,
                    value,
                    errors,
                    missing_fields,
                    missing,
                    key_error,
                    field_names_name,
                    validation_errors[index],
                )
                continue
            input_name = self._input_name_expression(field, index, field_names_name)
            lines.extend(
                (
                    "    try:",
                    f"        {value} = data[{input_name}]",
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
                self._emit_collect(lines, emitter, errors, missing_error, 2)
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
            self._emit_collect(lines, emitter, errors, error, 3)

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
        self._emit_collect(lines, emitter, errors, unexpected_error, 4)
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
            sensitive_argument = ", sensitive=True" if field.metadata.sensitive else ""
            lines.extend(
                (
                    f"    if {value} is {missing}:",
                    "        try:",
                    f"            {value} = {factory}()",
                    f"        except {emitter.runtime('exception', Exception)} as {factory_error}:",
                    f"            {factory_failure} = {emitter.validation_error_name}("
                    f"None, {factory_error}, ({field_names_name}[{index}],), "
                    f"{emitter.runtime('error_code_factory', ErrorCode.FACTORY)}, "
                    f"title={emitter.title_name}, context={context}{sensitive_argument})",
                )
            )
            self._emit_collect(lines, emitter, errors, factory_failure, 3)
            lines.append("        else:")
            lines.append("            try:")
            self._emit_field_pipeline(emitter, schema, index, value, 4)
            error = validation_errors[index]
            lines.append(f"            except {emitter.validation_error_name} as {error}:")
            self._emit_collect(lines, emitter, errors, error, 4)
        if has_factories:
            self._emit_raise_aggregate(lines, emitter, errors, 1)

        self._emit_commit(
            emitter,
            schema,
            spec_type,
            slot_setters,
            values,
            field_names_name,
            1,
            presence_setter,
        )

        source = "\n".join(lines)
        exec(compile(source, f"<talea {self.mode} input>", "exec"), namespace)
        function = cast(FunctionType, namespace["construct"])
        function.__doc__ = f"Construct one {self.title} from untrusted {self.mode} data."
        return function

    def _emit_legacy_lookup(
        self,
        emitter: _BoundaryValidationEmitter,
        schema: SpecSchema,
        field: SpecField,
        index: int,
        value: str,
        errors: str,
        missing_fields: str,
        missing: str,
        key_error: str,
        field_names_name: str,
        validation_error: str,
    ) -> None:
        """Emit direct accepted-name probes and deterministic conflict rejection."""

        accepted_names = emitter.bind(f"accepted_names_{index}", field.accepted_input_names)
        selected_name = emitter.variable(f"selected_name_{index}")
        candidate = emitter.variable(f"candidate_{index}")
        conflict = emitter.variable(f"alias_conflict_{index}")
        emitter.emit(1, f"{value} = {missing}")
        emitter.emit(1, f"{selected_name} = {missing}")
        emitter.emit(1, f"{conflict} = None")
        for accepted_index in range(len(field.accepted_input_names)):
            emitter.emit(1, "try:")
            emitter.emit(2, f"{candidate} = data[{accepted_names}[{accepted_index}]]")
            emitter.emit(1, f"except {key_error}:")
            emitter.emit(2, "pass")
            emitter.emit(1, "else:")
            emitter.emit(2, f"if {value} is {missing}:")
            emitter.emit(3, f"{value} = {candidate}")
            emitter.emit(3, f"{selected_name} = {accepted_names}[{accepted_index}]")
            emitter.emit(2, f"elif {conflict} is None:")
            sensitive_argument = ", sensitive=True" if field.metadata.sensitive else ""
            emitter.emit(
                3,
                f"{conflict} = {emitter.validation_error_name}._alias_conflict("
                f"({selected_name}, {accepted_names}[{accepted_index}]), "
                f"({field_names_name}[{index}],), title={emitter.title_name}{sensitive_argument})",
            )
        emitter.emit(1, f"if {conflict} is not None:")
        self._emit_collect(emitter.lines, emitter, errors, conflict, 2)
        emitter.emit(1, f"elif {value} is {missing}:")
        emitter.emit(2, f"{missing_fields} = True")
        if field.required:
            missing_error = emitter.variable(f"missing_error_{index}")
            emitter.emit(
                2,
                f"{missing_error} = {emitter.validation_error_name}._missing("
                f"({field_names_name}[{index}],), title={emitter.title_name})",
            )
            self._emit_collect(emitter.lines, emitter, errors, missing_error, 2)
        elif field.has_static_default:
            default_name = emitter.bind("static_default", field.default)
            emitter.emit(2, f"{value} = {default_name}")
        else:
            emitter.emit(2, "pass")
        emitter.emit(1, "else:")
        emitter.emit(2, "try:")
        self._emit_field_pipeline(emitter, schema, index, value, 3)
        emitter.emit(2, f"except {emitter.validation_error_name} as {validation_error}:")
        self._emit_collect(emitter.lines, emitter, errors, validation_error, 3)

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
        presence_setter: Callable[[object, object], None] | None,
    ) -> None:
        """Emit complete exact-dict construction before general Mapping analysis."""

        emitter.emit(1, f"if {exact_dict} and {emitter.runtime('len', len)}(data) == {len(schema.fields)}:")
        emitter.emit(2, "try:")
        if values:
            for index, (field, value) in enumerate(zip(schema.fields, values, strict=True)):
                input_name = self._input_name_expression(field, index, field_names_name)
                emitter.emit(3, f"{value} = data[{input_name}]")
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
            self._emit_collect(emitter.lines, emitter, errors, error, 4)
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
        self._emit_collect(emitter.lines, emitter, errors, unexpected_error, 6)
        self._emit_raise_aggregate(emitter.lines, emitter, errors, 3)
        self._emit_commit(
            emitter,
            schema,
            spec_type,
            slot_setters,
            values,
            field_names_name,
            3,
            presence_setter,
        )

    @staticmethod
    def _input_name_expression(field: SpecField, index: int, field_names_name: str) -> str:
        """Return source that binds arbitrary aliases as data, never code."""

        return f"{field_names_name}[{index}]" if field.alias is not None else repr(field.name)

    @staticmethod
    def _emit_commit(
        emitter: _BoundaryValidationEmitter,
        schema: SpecSchema,
        spec_type: type[object],
        slot_setters: tuple[Callable[[object, object], None], ...],
        values: tuple[str, ...],
        field_names_name: str,
        indentation: int,
        presence_setter: Callable[[object, object], None] | None,
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
                    sensitive=any(bool(schema.fields[field_indices[name]].metadata.sensitive) for name in hook.fields),
                )
        instance = emitter.variable("instance")
        allocator = emitter.bind("instance_allocator", object.__new__)
        spec_type_name = emitter.bind("spec_type", spec_type)
        emitter.emit(indentation, f"{instance} = {allocator}({spec_type_name})")
        if schema.presence_aware:
            bound_setter = cast(Callable[[object, object], None], presence_setter)
            missing = emitter.bind("missing", FACTORY_SENTINEL)
            presence = emitter.variable("presence")
            emitter.emit(indentation, f"{presence} = 0")
            for index, (value, setter) in enumerate(zip(values, slot_setters, strict=True)):
                setter_name = emitter.bind(f"slot_{index}", setter)
                emitter.emit(indentation, f"if {value} is not {missing}:")
                emitter.emit(indentation + 1, f"{setter_name}({instance}, {value})")
                emitter.emit(indentation + 1, f"{presence} |= {1 << index}")
            bound_presence_setter = emitter.bind("presence_setter", bound_setter)
            emitter.emit(indentation, f"{bound_presence_setter}({instance}, {presence})")
        else:
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
        represented = schema_contains_representation(field.schema)
        if represented:
            emitter.emit_conversion(
                field.schema,
                value,
                (field_name,),
                indentation,
                sensitive=bool(field.metadata.sensitive),
            )
        for hook in schema.hooks:
            if hook.kind == "transform" and hook.fields == (field.name,):
                emitter.emit_transform(
                    hook,
                    value,
                    (field_name,),
                    indentation,
                    sensitive=bool(field.metadata.sensitive),
                )
        if represented:
            emitter.emit_strict_schema(
                field.schema,
                value,
                (field_name,),
                indentation,
                sensitive=bool(field.metadata.sensitive),
            )
        else:
            emitter.emit_schema(
                field.schema,
                value,
                (field_name,),
                indentation,
                sensitive=bool(field.metadata.sensitive),
            )
        for hook in schema.hooks:
            if hook.kind == "check" and hook.fields == (field.name,):
                emitter.emit_check(
                    hook,
                    (value,),
                    ((field_name,),),
                    indentation,
                    sensitive=bool(field.metadata.sensitive),
                )

    @staticmethod
    def _emit_collect(
        lines: list[str],
        emitter: _BoundaryValidationEmitter,
        errors: str,
        error: str,
        indentation: int,
    ) -> None:
        prefix = "    " * indentation
        resource_state = emitter.resource_state
        title = emitter.title_name
        assert title is not None
        lines.extend(
            (
                f"{prefix}if {error}.truncated:",
                f"{prefix}    raise {error}",
                f"{prefix}if {errors} is None:",
                f"{prefix}    {errors} = [{error}]",
                f"{prefix}else:",
                f"{prefix}    {errors}.append({error})",
                f"{prefix}if {resource_state}.error_limit_reached(len({errors})):",
                f"{prefix}    raise {emitter.validation_error_name}._aggregate("
                f"tuple({errors}), title={title}, truncated=True) from None",
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
    presence_setter: Callable[[object, object], None] | None = None,
) -> InputCallable:
    """Compile one external input path without changing ordinary construction."""

    return _InputCompiler(mode, spec_type.__name__).compile(
        schema,
        spec_type,
        slot_setters,
        presence_setter,
    )
