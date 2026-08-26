"""Compile and expose presence state for omittable Spec fields."""

from collections.abc import Callable
from types import FunctionType
from typing import cast

from talea.codegen import _GeneratedNames
from talea.declaration.models import SpecSchema
from talea.declaration.policies import schema_values_are_immutable
from talea.validation.emission import _ValidationEmitter


class _Omitted:
    """Mark an unsupplied constructor parameter without becoming field data."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<omitted>"


OMITTED = _Omitted()


def compile_presence_constructor(
    schema: SpecSchema,
    slot_setters: tuple[Callable[[object, object], None], ...],
    presence_setter: Callable[[object, object], None],
    title: str | None,
) -> FunctionType:
    """Return a constructor that validates and commits only supplied fields."""

    fields = schema.fields
    field_names = tuple(field.name for field in fields)
    names = _GeneratedNames(field_names)
    instance = names.allocate("instance")
    omitted = names.allocate("omitted")
    mask = names.allocate("presence")
    field_names_name = names.allocate("field_names")
    parameters = ", ".join(f"{field.name}={omitted}" for field in fields)
    suffix = f", *, {parameters}" if parameters else ""
    lines = [f"def __init__({instance}{suffix}):", f"    {mask} = 0"]
    namespace: dict[str, object] = {
        "__name__": __name__,
        omitted: OMITTED,
        field_names_name: field_names,
    }
    emitter = _ValidationEmitter(lines, names, namespace, title=title)
    transforms = {
        field.name: tuple(hook for hook in schema.hooks if hook.kind == "transform" and hook.fields == (field.name,))
        for field in fields
    }
    checks = {
        field.name: tuple(hook for hook in schema.hooks if hook.kind == "check" and hook.fields == (field.name,))
        for field in fields
    }
    for index, field in enumerate(fields):
        lines.append(f"    if {field.name} is not {omitted}:")
        for hook in transforms[field.name]:
            emitter.emit_transform(
                hook,
                field.name,
                (f"{field_names_name}[{index}]",),
                2,
                sensitive=bool(field.metadata.sensitive),
            )
        emitter.emit_schema(
            field.schema,
            field.name,
            (f"{field_names_name}[{index}]",),
            2,
            sensitive=bool(field.metadata.sensitive),
        )
        for hook in checks[field.name]:
            emitter.emit_check(
                hook,
                (field.name,),
                ((f"{field_names_name}[{index}]",),),
                2,
                sensitive=bool(field.metadata.sensitive),
            )
        lines.append(f"        {mask} |= {1 << index}")
    for index, (field, setter) in enumerate(zip(fields, slot_setters, strict=True)):
        setter_name = emitter.bind("slot_setter", setter)
        lines.append(f"    if {mask} & {1 << index}:")
        lines.append(f"        {setter_name}({instance}, {field.name})")
    presence_name = emitter.bind("presence_setter", presence_setter)
    lines.append(f"    {presence_name}({instance}, {mask})")
    exec(compile("\n".join(lines), "<talea presence constructor>", "exec"), namespace)
    return cast(FunctionType, namespace["__init__"])


def compile_presence_replacer(
    schema: SpecSchema,
    spec_type: type[object],
    slot_setters: tuple[Callable[[object, object], None], ...],
    presence_setter: Callable[[object, object], None],
    title: str,
) -> FunctionType:
    """Return atomic copy-replacement that preserves absent field slots."""

    fields = schema.fields
    field_names = tuple(field.name for field in fields)
    names = _GeneratedNames(("instance", "changes"))
    known = names.allocate("known_names")
    unknown = names.allocate("unknown_names")
    mask = names.allocate("presence")
    omitted = names.allocate("omitted")
    field_names_name = names.allocate("field_names")
    lines = [
        "def replace(instance, changes):",
        f"    {unknown} = changes.keys() - {known}",
        f"    if {unknown}:",
        f"        raise TypeError(f'unexpected replacement field {{min({unknown})!r}}')",
        f"    {mask} = instance.__talea_presence__",
    ]
    namespace: dict[str, object] = {
        "__name__": __name__,
        known: frozenset(field_names),
        omitted: OMITTED,
        field_names_name: field_names,
    }
    emitter = _ValidationEmitter(lines, names, namespace, title=title)
    values = tuple(names.allocate(f"field_{index}") for index in range(len(fields)))
    transforms = {
        field.name: tuple(hook for hook in schema.hooks if hook.kind == "transform" and hook.fields == (field.name,))
        for field in fields
    }
    checks = {
        field.name: tuple(hook for hook in schema.hooks if hook.kind == "check" and hook.fields == (field.name,))
        for field in fields
    }
    for index, (field, value) in enumerate(zip(fields, values, strict=True)):
        location = (f"{field_names_name}[{index}]",)
        lines.append(f"    if {field.name!r} in changes:")
        lines.append(f"        {value} = changes[{field.name!r}]")
        for hook in transforms[field.name]:
            emitter.emit_transform(hook, value, location, 2, sensitive=bool(field.metadata.sensitive))
        emitter.emit_schema(field.schema, value, location, 2, sensitive=bool(field.metadata.sensitive))
        for hook in checks[field.name]:
            emitter.emit_check(hook, (value,), (location,), 2, sensitive=bool(field.metadata.sensitive))
        lines.append(f"        {mask} |= {1 << index}")
        lines.append(f"    elif {mask} & {1 << index}:")
        lines.append(f"        {value} = instance.{field.name}")
        if not schema_values_are_immutable(field.schema):
            emitter.emit_schema(field.schema, value, location, 2, sensitive=bool(field.metadata.sensitive))
            for hook in checks[field.name]:
                emitter.emit_check(hook, (value,), (location,), 2, sensitive=bool(field.metadata.sensitive))
        lines.append("    else:")
        lines.append(f"        {value} = {omitted}")
    allocator = emitter.bind("instance_allocator", object.__new__)
    spec_type_name = emitter.bind("spec_type", spec_type)
    replacement = names.allocate("replacement")
    lines.append(f"    {replacement} = {allocator}({spec_type_name})")
    for index, (value, setter) in enumerate(zip(values, slot_setters, strict=True)):
        setter_name = emitter.bind("slot_setter", setter)
        lines.append(f"    if {mask} & {1 << index}:")
        lines.append(f"        {setter_name}({replacement}, {value})")
    presence_name = emitter.bind("presence_setter", presence_setter)
    lines.append(f"    {presence_name}({replacement}, {mask})")
    lines.append(f"    return {replacement}")
    exec(compile("\n".join(lines), "<talea presence replacement>", "exec"), namespace)
    return cast(FunctionType, namespace["replace"])


def presence_mask(instance: object) -> int | None:
    """Return the compact supplied-field mask, or ``None`` for an ordinary Spec."""

    artifacts = vars(type(instance))["__talea_declaration__"].artifacts()
    if artifacts.inputs.presence_setter is None:
        return None
    return cast(int, object.__getattribute__(instance, "__talea_presence__"))


def present_field_names(instance: object) -> frozenset[str]:
    """Project immutable canonical names from one instance's presence truth."""

    artifacts = vars(type(instance))["__talea_declaration__"].artifacts()
    mask = (
        None
        if artifacts.inputs.presence_setter is None
        else cast(int, object.__getattribute__(instance, "__talea_presence__"))
    )
    if mask is None:
        return frozenset(field.name for field in artifacts.schema.fields)
    return frozenset(field.name for index, field in enumerate(artifacts.schema.fields) if mask & (1 << index))
