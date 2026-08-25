"""Compile one flat specialized constructor from effective Spec truth."""

from collections.abc import Callable
from types import FunctionType
from typing import cast

from talea.declaration.models import SpecSchema
from talea.spec.fields import FACTORY_SENTINEL
from talea.validation.emission import _GeneratedNames, _ValidationEmitter


class _ConstructorCompiler:
    """Compile a keyword-only initializer specialized for one Spec schema."""

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
        exception_type_name = names.allocate("exception_type") if factory_names else ""
        type_error_name = names.allocate("type_error") if factory_names else ""
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
                    parameters.append(f"{field.name}={default_names[index]}")
                else:
                    parameters.append(f"{field.name}={factory_sentinel_name}")
            lines = [f"def __init__({instance_name}, *, {', '.join(parameters)}):"]
            namespace = {field_names_name: field_names, "__name__": __name__}
            emitter = _ValidationEmitter(lines, names, namespace)
            for index, field in enumerate(fields):
                field_name = field.name
                if field.default_factory is not None:
                    factory_name = factory_names[index]
                    error_name = factory_error_names[index]
                    lines.extend(
                        (
                            f"    if {field_name} is {factory_sentinel_name}:",
                            "        try:",
                            f"            {field_name} = {factory_name}()",
                            f"        except {exception_type_name} as {error_name}:",
                            f'            raise {type_error_name}("default factory for field '
                            f"'{field_name}' failed\") from {error_name}",
                        )
                    )
                elif field.has_static_default:
                    lines.append(f"    if {field_name} is not {default_names[index]}:")
                emitter.emit_schema(
                    field.schema,
                    field_name,
                    (f"{field_names_name}[{index}]",),
                    2 if field.has_static_default else 1,
                )
            for field_name, slot_setter_name in zip(field_names, slot_setter_names, strict=True):
                lines.append(f"    {slot_setter_name}({instance_name}, {field_name})")
            source = "\n".join(lines)

        if factory_names:
            namespace[factory_sentinel_name] = FACTORY_SENTINEL
            namespace[exception_type_name] = Exception
            namespace[type_error_name] = TypeError
        for index, field in enumerate(fields):
            if field.has_static_default:
                namespace[default_names[index]] = field.default
            if field.default_factory is not None:
                namespace[factory_names[index]] = field.default_factory
        for slot_setter_name, slot_setter in zip(slot_setter_names, slot_setters, strict=True):
            namespace[slot_setter_name] = slot_setter
        exec(compile(source, "<talea Spec constructor>", "exec"), namespace)
        return cast(FunctionType, namespace["__init__"])
