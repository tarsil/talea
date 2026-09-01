"""Emit validation and boundary dispatch for canonical tagged unions."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from talea.schema.nodes import LiteralValue, SpecReferenceSchema, TaggedUnionSchema
from talea.tagged.dispatch import nominal_dispatch

if TYPE_CHECKING:
    from talea.validation.emission import _ValidationEmitter


class _TaggedValidationEmission:
    """Own tagged operation emission beneath the validation orchestrator."""

    __slots__ = ("emitter",)

    def __init__(self, emitter: _ValidationEmitter) -> None:
        self.emitter = emitter

    def emit_union(
        self,
        schema: TaggedUnionSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Select exactly one tagged branch without validating alternatives."""

        emitter = self.emitter
        first = schema.branches[0].schema
        if isinstance(first, SpecReferenceSchema):
            instance_check = emitter.runtime("isinstance", isinstance)
            if len(schema.branches) > 4:
                branch_types = tuple(cast(SpecReferenceSchema, branch.schema).spec_type for branch in schema.branches)
                operations = tuple(
                    emitter.tagged_branch_operation(branch.schema, json=False) for branch in schema.branches
                )
                dispatch = emitter.bind(
                    "tagged_object_dispatch",
                    dict(zip(branch_types, operations, strict=True)),
                )
                operation = emitter.variable("tagged_operation")
                nominal = emitter.bind("nominal_dispatch", nominal_dispatch)
                emitter.emit(indentation, f"{operation} = {nominal}({value}, {dispatch})")
                emitter.emit(indentation, f"if {operation} is not None:")
                self.emit_operation_call(operation, value, location, indentation + 1)
                emitter.emit(indentation, "else:")
                emitter.emit_failure(schema, value, location, indentation + 1)
                return
            for index, branch in enumerate(schema.branches):
                assert isinstance(branch.schema, SpecReferenceSchema)
                branch_type = emitter.bind("tagged_branch_type", branch.schema.spec_type)
                keyword = "if" if index == 0 else "elif"
                emitter.emit(indentation, f"{keyword} {instance_check}({value}, {branch_type}):")
                emitter.emit_schema(branch.schema, value, location, indentation + 1)
            emitter.emit(indentation, "else:")
            emitter.emit_failure(schema, value, location, indentation + 1)
            return

        type_name = emitter.runtime("type", type)
        dictionary_type = emitter.runtime("dict", dict)
        emitter.emit(indentation, f"if {type_name}({value}) is not {dictionary_type}:")
        emitter.emit_failure(schema, value, location, indentation + 1)
        self.emit_dispatch(schema, value, location, indentation, json=False)

    def emit_dispatch(
        self,
        schema: TaggedUnionSchema,
        value: str,
        location: tuple[str, ...],
        indentation: int,
        *,
        json: bool,
    ) -> None:
        """Extract a tag and emit one direct selected-branch block."""

        emitter = self.emitter
        discriminator = emitter.bind("discriminator", schema.external_name)
        tag_location = (*location, discriminator)
        sensitive = schema.sensitive or emitter.sensitive
        tag = emitter.variable("discriminator_tag")
        if schema.legacy_names:
            accepted_names = emitter.bind("discriminator_accepted_names", schema.accepted_input_names)
            missing_value = emitter.bind("discriminator_missing_value", object())
            selected_name = emitter.variable("discriminator_selected_name")
            conflict = emitter.variable("discriminator_alias_conflict")
            emitter.emit(indentation, f"{tag} = {missing_value}")
            emitter.emit(indentation, f"{selected_name} = {missing_value}")
            emitter.emit(indentation, f"{conflict} = None")
            for accepted_index in range(len(schema.accepted_input_names)):
                emitter.emit(indentation, f"if {accepted_names}[{accepted_index}] in {value}:")
                emitter.emit(indentation + 1, f"if {tag} is {missing_value}:")
                emitter.emit(indentation + 2, f"{tag} = {value}[{accepted_names}[{accepted_index}]]")
                emitter.emit(indentation + 2, f"{selected_name} = {accepted_names}[{accepted_index}]")
                emitter.emit(indentation + 1, f"elif {conflict} is None:")
                emitter.emit(
                    indentation + 2,
                    f"{conflict} = {emitter.validation_error_name}._alias_conflict("
                    f"({selected_name}, {accepted_names}[{accepted_index}]), "
                    f"{emitter.location_expression(tag_location)}"
                    f"{emitter.title_argument()}{', sensitive=True' if sensitive else ''})",
                )
            emitter.emit(indentation, f"if {conflict} is not None:")
            emitter.emit(indentation + 1, f"raise {conflict} from None")
            missing_condition = f"{tag} is {missing_value}"
        else:
            missing_condition = f"{discriminator} not in {value}"
        emitter.emit(indentation, f"if {missing_condition}:")
        missing = emitter.variable("discriminator_error")
        emitter.emit(
            indentation + 1,
            f"{missing} = {emitter.validation_error_name}.discriminator_missing("
            f"{discriminator}, {emitter.location_expression(tag_location)}"
            f"{emitter.title_argument()}{', sensitive=True' if sensitive else ''})",
        )
        emitter.emit(indentation + 1, f"raise {missing} from None")
        if not schema.legacy_names:
            emitter.emit(indentation, f"{tag} = {value}[{discriminator}]")
        canonical_tags = tuple(branch.json_tag if json else branch.tag for branch in schema.branches)
        allowed_types = tuple(dict.fromkeys(item.python_type for item in canonical_tags))
        allowed = emitter.bind("discriminator_types", allowed_types)
        emitter.emit(indentation, f"if {emitter.runtime('type', type)}({tag}) not in {allowed}:")
        expected_types = " | ".join(item.__qualname__ for item in allowed_types)
        previous = emitter.sensitive
        emitter.sensitive = sensitive
        try:
            emitter.emit_failure(
                schema,
                tag,
                tag_location,
                indentation + 1,
                expected=f"discriminator tag of type {expected_types}",
                context=(("discriminator", schema.external_name),),
            )
        finally:
            emitter.sensitive = previous

        if len(schema.branches) <= 4:
            for index, (branch, canonical) in enumerate(zip(schema.branches, canonical_tags, strict=True)):
                keyword = "if" if index == 0 else "elif"
                emitter.emit(indentation, f"{keyword} {emitter.literal_condition(canonical, tag)}:")
                emitter.emit_schema(branch.schema, value, location, indentation + 1)
            emitter.emit(indentation, "else:")
            self.emit_unknown(schema, canonical_tags, tag, tag_location, indentation + 1)
            return

        operations = tuple(emitter.tagged_branch_operation(branch.schema, json=json) for branch in schema.branches)
        dispatch = emitter.bind(
            "discriminator_dispatch",
            {
                (item.python_type, item.value): operation
                for item, operation in zip(canonical_tags, operations, strict=True)
            },
        )
        operation = emitter.variable("tagged_operation")
        emitter.emit(
            indentation,
            f"{operation} = {dispatch}.get(({emitter.runtime('type', type)}({tag}), {tag}))",
        )
        emitter.emit(indentation, f"if {operation} is not None:")
        self.emit_operation_call(operation, value, location, indentation + 1)
        emitter.emit(indentation, "else:")
        self.emit_unknown(schema, canonical_tags, tag, tag_location, indentation + 1)

    def emit_operation_call(
        self,
        operation: str,
        value: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Call one selected operation and compose its root-relative failure."""

        emitter = self.emitter
        error = emitter.variable("tagged_error")
        prefixed = emitter.variable("prefixed_error")
        emitter.emit(indentation, "try:")
        emitter.emit(
            indentation + 1,
            f"{value} = {emitter.operation_call_expression(operation, value, location)}",
        )
        emitter.emit(indentation, f"except {emitter.validation_error_name} as {error}:")
        emitter.emit(
            indentation + 1,
            f"{prefixed} = {error}.prefixed({emitter.location_expression(location)}"
            f"{emitter.title_argument()}{emitter.sensitive_argument()})",
        )
        emitter.emit(indentation + 1, f"raise {prefixed} from {prefixed}.__cause__")

    def emit_unknown(
        self,
        schema: TaggedUnionSchema,
        tags: tuple[LiteralValue, ...],
        tag: str,
        location: tuple[str, ...],
        indentation: int,
    ) -> None:
        """Emit rich unknown-tag construction only on the failure path."""

        emitter = self.emitter
        expected = emitter.bind("discriminator_expected", tuple(item.value for item in tags))
        discriminator = emitter.bind("discriminator", schema.external_name)
        error = emitter.variable("discriminator_error")
        emitter.emit(
            indentation,
            f"{error} = {emitter.validation_error_name}.discriminator_unknown("
            f"{discriminator}, {tag}, {emitter.location_expression(location)}, {expected}"
            f"{emitter.title_argument()}"
            f"{', sensitive=True' if schema.sensitive or emitter.sensitive else ''})",
        )
        emitter.emit(indentation, f"raise {error} from None")
