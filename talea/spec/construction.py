"""Compile one flat specialized constructor from effective Spec truth."""

from collections.abc import Callable
from inspect import signature
from types import FunctionType
from typing import cast

from talea.declaration.models import SpecSchema
from talea.errors import ErrorCode, ValidationError
from talea.spec.fields import FACTORY_SENTINEL, _StaticDefaultSentinel
from talea.validation.emission import _GeneratedNames, _ValidationEmitter


class _ConstructorCompiler:
    """Compile a keyword-only initializer specialized for one Spec schema."""

    __slots__ = ("title",)

    def __init__(self, title: str | None = None) -> None:
        self.title = title

    def compile(
        self,
        schema: SpecSchema,
        slot_setters: tuple[Callable[[object, object], None], ...],
    ) -> FunctionType:
        """Return an initializer containing inline validation and slot writes.

        Bound C-level member-descriptor setters write slots only after every
        value validates, preserving public immutability without making an
        instance temporarily writable.
        """

        fields = schema.fields
        field_names = tuple(field.name for field in fields)
        if schema.hooks:
            transforms_by_field = {
                field.name: tuple(
                    hook for hook in schema.hooks if hook.kind == "transform" and hook.fields == (field.name,)
                )
                for field in fields
            }
            checks_by_field = {
                field.name: tuple(
                    hook for hook in schema.hooks if hook.kind == "check" and hook.fields == (field.name,)
                )
                for field in fields
            }
            spec_checks = tuple(hook for hook in schema.hooks if hook.kind == "check" and len(hook.fields) > 1)
        else:
            transforms_by_field = {}
            checks_by_field = {}
            spec_checks = ()
        names = _GeneratedNames(field_names)
        instance_name = names.allocate("instance")
        if all(field.required for field in fields):
            default_names: dict[int, str] = {}
            factory_names: dict[int, str] = {}
        else:
            default_names = {
                index: names.allocate(f"default_{index}")
                for index, field in enumerate(fields)
                if field.has_static_default
            }
            factory_names = {
                index: names.allocate(f"factory_{index}")
                for index, field in enumerate(fields)
                if field.default_factory is not None
            }
        factory_sentinel_name = names.allocate("factory_sentinel") if factory_names else ""
        static_sentinel_names = {
            index: names.allocate(f"static_sentinel_{index}")
            for index, field in enumerate(fields)
            if field.has_static_default and transforms_by_field.get(field.name)
        }
        exception_type_name = names.allocate("exception_type") if factory_names else ""
        slot_setter_names: tuple[str, ...] = ()
        namespace: dict[str, object]
        if not field_names:
            source = f"def __init__({instance_name}):\n    pass"
            namespace = {"__name__": __name__}
        else:
            field_names_name = names.allocate("field_names")
            factory_error_names = {index: names.allocate(f"factory_error_{index}") for index in factory_names}
            slot_setter_names = tuple(names.allocate(f"slot_{index}") for index in range(len(field_names)))
            parameters = []
            for index, field in enumerate(fields):
                if field.required:
                    parameters.append(field.name)
                elif field.has_static_default:
                    parameter_default = static_sentinel_names.get(index, default_names[index])
                    parameters.append(f"{field.name}={parameter_default}")
                else:
                    parameters.append(f"{field.name}={factory_sentinel_name}")
            lines = [f"def __init__({instance_name}, *, {', '.join(parameters)}):"]
            namespace = {field_names_name: field_names, "__name__": __name__}
            emitter = _ValidationEmitter(lines, names, namespace, title=self.title)
            for index, field in enumerate(fields):
                field_name = field.name
                transforms = transforms_by_field.get(field_name, ())
                checks = checks_by_field.get(field_name, ())
                indentation = 1
                if field.default_factory is not None:
                    factory_name = factory_names[index]
                    error_name = factory_error_names[index]
                    validation_error_name = emitter.runtime("validation_error", ValidationError)
                    factory_code_name = emitter.runtime("error_code_factory", ErrorCode.FACTORY)
                    factory_context_name = emitter.bind("factory_error_context", (("field", field_name),))
                    error_title = emitter.title_argument()
                    lines.extend(
                        (
                            f"    if {field_name} is {factory_sentinel_name}:",
                            "        try:",
                            f"            {field_name} = {factory_name}()",
                            f"        except {exception_type_name} as {error_name}:",
                            f"            raise {validation_error_name}(None, {error_name}, "
                            f"({field_names_name}[{index}],), {factory_code_name}{error_title}, "
                            f"context={factory_context_name}) from {error_name}",
                        )
                    )
                elif field.has_static_default:
                    if transforms:
                        lines.extend(
                            (
                                f"    if {field_name} is {static_sentinel_names[index]}:",
                                f"        {field_name} = {default_names[index]}",
                                "    else:",
                            )
                        )
                    else:
                        lines.append(f"    if {field_name} is not {default_names[index]}:")
                    indentation = 2
                for hook in transforms:
                    emitter.emit_transform(
                        hook,
                        field_name,
                        (f"{field_names_name}[{index}]",),
                        indentation,
                    )
                emitter.emit_schema(
                    field.schema,
                    field_name,
                    (f"{field_names_name}[{index}]",),
                    indentation,
                )
                for hook in checks:
                    emitter.emit_check(
                        hook,
                        (field_name,),
                        (((f"{field_names_name}[{index}]"),),),
                        indentation,
                    )
            field_indices = {field.name: index for index, field in enumerate(fields)}
            for hook in spec_checks:
                emitter.emit_check(
                    hook,
                    hook.fields,
                    tuple((f"{field_names_name}[{field_indices[name]}]",) for name in hook.fields),
                    1,
                )
            for field_name, slot_setter_name in zip(field_names, slot_setter_names, strict=True):
                lines.append(f"    {slot_setter_name}({instance_name}, {field_name})")
            source = "\n".join(lines)

        if factory_names:
            namespace[factory_sentinel_name] = FACTORY_SENTINEL
            namespace[exception_type_name] = Exception
        for index, field in enumerate(fields):
            if field.has_static_default:
                namespace[default_names[index]] = field.default
                if index in static_sentinel_names:
                    namespace[static_sentinel_names[index]] = _StaticDefaultSentinel(field.default)
            if field.default_factory is not None:
                namespace[factory_names[index]] = field.default_factory
        for slot_setter_name, slot_setter in zip(slot_setter_names, slot_setters, strict=True):
            namespace[slot_setter_name] = slot_setter
        exec(compile(source, "<talea Spec constructor>", "exec"), namespace)
        initializer = cast(FunctionType, namespace["__init__"])
        if static_sentinel_names:
            public_defaults = {fields[index].name: fields[index].default for index in static_sentinel_names}
            declared_signature = signature(initializer)
            signature_attribute = "__signature__"
            setattr(
                initializer,
                signature_attribute,
                declared_signature.replace(
                    parameters=tuple(
                        parameter.replace(default=public_defaults[parameter.name])
                        if parameter.name in public_defaults
                        else parameter
                        for parameter in declared_signature.parameters.values()
                    )
                ),
            )
        return initializer
