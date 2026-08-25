from __future__ import annotations

import builtins
import inspect
from dataclasses import FrozenInstanceError

import pytest

import talea
import talea.annotations
import talea.spec as spec_module
from talea import Spec
from talea._declaration import SpecSchema
from talea.annotations import AnnotationResolutionError
from talea.schema import MappingSchema, PrimitiveSchema, SequenceSchema, UnionSchema
from talea.validation import ValidationError


class User(Spec):
    id: int
    name: str


class Payload(Spec):
    values: list[int]
    metadata: dict[str, int | None]


def test_root_package_exports_only_the_spec_declaration_api() -> None:
    assert talea.__all__ == ["Spec"]
    assert talea.Spec is Spec
    assert not hasattr(talea, "SpecSchema")
    assert not hasattr(talea, "compile_validator")
    assert not hasattr(talea, "ValidationError")


def test_empty_spec_declaration_constructs_without_instance_metadata() -> None:
    class Empty(Spec):
        pass

    empty = Empty()

    assert repr(empty) == "Empty()"
    assert Empty.__slots__ == ()
    assert not hasattr(empty, "__dict__")
    assert vars(Empty)["__talea_artifacts__"].schema == SpecSchema(())
    with pytest.raises(TypeError, match="takes 1 positional argument but 2 were given"):
        Empty(1)  # type: ignore[too-many-positional-arguments]


def test_constructs_one_and_multiple_field_specs() -> None:
    class Identifier(Spec):
        value: int

    identifier = Identifier(value=1)
    user = User(id=1, name="Tiago")

    assert identifier.value == 1
    assert (user.id, user.name) == (1, "Tiago")
    assert repr(identifier) == "Identifier(value=1)"
    assert repr(user) == "User(id=1, name='Tiago')"


def test_nested_values_validate_and_preserve_mutable_identity() -> None:
    values = [1, 2]
    metadata = {"one": 1, "none": None}

    payload = Payload(values=values, metadata=metadata)

    assert payload.values is values
    assert payload.metadata is metadata


def test_spec_schema_is_the_single_ordered_declaration_truth() -> None:
    artifacts = vars(Payload)["__talea_artifacts__"]

    assert tuple(field.name for field in artifacts.schema.fields) == ("values", "metadata")
    assert artifacts.schema.fields[0].schema == SequenceSchema("list", PrimitiveSchema("int"))
    assert artifacts.schema.fields[1].schema == MappingSchema(
        PrimitiveSchema("str"),
        UnionSchema(frozenset({PrimitiveSchema("int"), PrimitiveSchema("none")})),
    )
    assert len(artifacts.validators) == len(artifacts.schema.fields)

    with pytest.raises(FrozenInstanceError):
        artifacts.schema = SpecSchema(())


def test_constructor_is_keyword_only_and_rejects_missing_unknown_and_positional_values() -> None:
    assert str(inspect.signature(User)) == "(*, id, name)"

    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'name'"):
        User(id=1)  # type: ignore[missing-argument]
    with pytest.raises(TypeError, match="unexpected keyword argument 'email'"):
        User(id=1, name="Tiago", email="tiago@example.com")  # type: ignore[unknown-argument]
    with pytest.raises(TypeError, match="takes 1 positional argument but 3 were given"):
        User(1, "Tiago")  # type: ignore[too-many-positional-arguments]


@pytest.mark.parametrize(
    ("values", "field", "expected"),
    [
        ({"id": True, "name": "Tiago"}, "id", "int"),
        ({"id": 1, "name": b"Tiago"}, "name", "str"),
    ],
)
def test_invalid_primitive_field_reports_the_field(values: dict[str, object], field: str, expected: str) -> None:
    with pytest.raises(ValidationError) as raised:
        User(**values)  # type: ignore[invalid-argument-type]

    assert raised.value.location == (field,)
    assert raised.value.expected == expected


def test_invalid_nested_field_composes_the_complete_location() -> None:
    with pytest.raises(ValidationError) as raised:
        Payload(values=[1, 2], metadata={"ok": 1, "wrong": "2"})  # type: ignore[invalid-argument-type]

    assert raised.value.location == ("metadata", "wrong")
    assert str(raised.value) == ("Validation failed at ['metadata']['wrong']: expected int | None, received str ('2')")


def test_instances_are_compact_and_retain_only_declared_values() -> None:
    user = User(id=1, name="Tiago")

    assert User.__slots__ == ("id", "name")
    assert not hasattr(user, "__dict__")
    assert not hasattr(user, "__weakref__")
    assert not any(slot.startswith("__talea") for slot in User.__slots__)


def test_spec_equality_and_hashing_use_object_identity() -> None:
    first = User(id=1, name="Tiago")
    second = User(id=1, name="Tiago")

    assert first == first
    assert first != second
    assert User.__eq__ is object.__eq__
    assert User.__hash__ is object.__hash__


def test_defaults_custom_construction_slots_and_inheritance_are_rejected() -> None:
    with pytest.raises(TypeError, match="Spec field defaults are not supported: 'value'"):

        class Defaulted(Spec):
            value: int = 1

    with pytest.raises(TypeError, match="Spec manages construction"):

        class CustomInit(Spec):
            value: int

            def __init__(self, *, value: int) -> None:
                self.value = value

    with pytest.raises(TypeError, match="Spec manages instance slots"):

        class CustomSlots(Spec):
            __slots__ = ()

    with pytest.raises(TypeError, match="Spec inheritance is not supported"):

        class Admin(User):
            active: bool

    class Mixin:
        pass

    with pytest.raises(TypeError, match="Spec inheritance is not supported"):

        class Mixed(Spec, Mixin):
            value: int


def test_fields_cannot_replace_inherited_spec_behavior() -> None:
    with pytest.raises(TypeError, match="conflicts with an inherited attribute: '__repr__'"):
        type("Conflict", (Spec,), {"__annotations__": {"__repr__": str}})


def test_unsupported_field_annotation_fails_during_declaration() -> None:
    with pytest.raises(AnnotationResolutionError) as raised:

        class Unsupported(Spec):
            value: complex

    assert raised.value.annotation is complex


def test_future_annotations_resolve_during_declaration() -> None:
    namespace = {"Spec": Spec}
    exec(
        compile(
            "from __future__ import annotations\nclass FutureSpec(Spec):\n    values: list[int]\n",
            "<future Spec test>",
            "exec",
        ),
        namespace,
    )
    future_spec = namespace["FutureSpec"]

    value = [1, 2]
    assert future_spec(values=value).values is value


def test_python_314_deferred_annotations_resolve_during_declaration() -> None:
    namespace = {"Spec": Spec}
    source = compile(
        "class DeferredSpec(Spec):\n    value: int\n",
        "<deferred Spec test>",
        "exec",
        dont_inherit=True,
    )

    exec(source, namespace)
    deferred_spec = namespace["DeferredSpec"]

    assert deferred_spec(value=1).value == 1


def test_declaration_resolves_and_compiles_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    resolution_calls = 0
    validator_calls = 0
    constructor_calls = 0
    compile_calls = 0
    exec_calls = 0
    original_resolve = spec_module.resolve_annotation
    original_validator = spec_module.compile_validator
    original_constructor = spec_module._ConstructorCompiler.compile
    original_compile = builtins.compile
    original_exec = builtins.exec

    def counted_resolve(annotation: object) -> object:
        nonlocal resolution_calls
        resolution_calls += 1
        return original_resolve(annotation)

    def counted_validator(schema: object) -> object:
        nonlocal validator_calls
        validator_calls += 1
        return original_validator(schema)  # type: ignore[invalid-argument-type]

    def counted_constructor(compiler: object, schema: object, validators: object) -> object:
        nonlocal constructor_calls
        constructor_calls += 1
        return original_constructor(compiler, schema, validators)  # type: ignore[invalid-argument-type]

    def counted_compile(*args: object, **kwargs: object) -> object:
        nonlocal compile_calls
        compile_calls += 1
        return original_compile(*args, **kwargs)  # type: ignore[invalid-argument-type]

    def counted_exec(*args: object, **kwargs: object) -> object:
        nonlocal exec_calls
        exec_calls += 1
        return original_exec(*args, **kwargs)  # type: ignore[invalid-argument-type]

    monkeypatch.setattr(spec_module, "resolve_annotation", counted_resolve)
    monkeypatch.setattr(spec_module, "compile_validator", counted_validator)
    monkeypatch.setattr(spec_module._ConstructorCompiler, "compile", counted_constructor)
    monkeypatch.setattr(builtins, "compile", counted_compile)
    monkeypatch.setattr(builtins, "exec", counted_exec)

    class Lifecycle(Spec):
        id: int
        labels: list[str]

    declaration_compile_calls = compile_calls
    declaration_exec_calls = exec_calls
    assert Lifecycle(id=1, labels=["one"]).id == 1
    assert Lifecycle(id=2, labels=["two"]).id == 2
    assert resolution_calls == 2
    assert validator_calls == 2
    assert constructor_calls == 1
    assert declaration_compile_calls >= 3
    assert declaration_exec_calls == 3
    assert compile_calls == declaration_compile_calls
    assert exec_calls == declaration_exec_calls


def test_repeated_construction_uses_no_reflection_or_compilation(monkeypatch: pytest.MonkeyPatch) -> None:
    class Retained(Spec):
        id: int
        payload: list[dict[str, int | None]]

    artifacts = vars(Retained)["__talea_artifacts__"]
    validator_ids = tuple(map(id, artifacts.validators))

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"instance construction invoked declaration work: {args!r}, {kwargs!r}")

    monkeypatch.setattr(spec_module, "resolve_annotation", forbidden)
    monkeypatch.setattr(spec_module, "compile_validator", forbidden)
    monkeypatch.setattr(spec_module._ConstructorCompiler, "compile", forbidden)
    monkeypatch.setattr(spec_module, "get_type_hints", forbidden)
    monkeypatch.setattr(talea.annotations, "get_origin", forbidden)
    monkeypatch.setattr(talea.annotations, "get_args", forbidden)
    monkeypatch.setattr(builtins, "compile", forbidden)
    monkeypatch.setattr(builtins, "exec", forbidden)

    for identifier in range(3):
        retained = Retained(id=identifier, payload=[{"value": identifier, "none": None}])
        assert retained.id == identifier

    assert tuple(map(id, artifacts.validators)) == validator_ids


def test_generated_constructor_binds_field_names_and_accepts_valid_unicode() -> None:
    class Localized(Spec):
        café: str

    localized = Localized(café="Kafi")
    globals_ = vars(Localized)["__init__"].__globals__

    assert localized.café == "Kafi"
    assert ("café",) in globals_.values()


def test_generated_constructor_names_cannot_collide_with_fields() -> None:
    names = (
        "self",
        "ValidationError",
        "_talea_instance",
        "_talea_validation_error",
        "_talea_field_names",
        "_talea_validator_0",
        "_talea_error_0",
    )
    collision = type("Collision", (Spec,), {"__annotations__": dict.fromkeys(names, int)})
    values = {name: index for index, name in enumerate(names)}

    instance = collision(**values)

    assert tuple(getattr(instance, name) for name in names) == tuple(values.values())


@pytest.mark.parametrize(
    "field_name",
    ["value); raise RuntimeError", "class", "K", "__private", 1],
)
def test_invalid_field_names_cannot_enter_generated_source(field_name: object) -> None:
    with pytest.raises(TypeError, match="invalid Spec field name"):
        type("Unsafe", (Spec,), {"__annotations__": {field_name: int}})


def test_declaration_name_is_not_executable_generated_source() -> None:
    unusual_name = "Injected'); raise RuntimeError('unsafe"
    unusual = type(unusual_name, (Spec,), {"__annotations__": {"value": int}})

    instance = unusual(value=1)

    assert instance.value == 1
    assert type(instance).__name__ == unusual_name


def test_malformed_annotation_protocols_are_rejected() -> None:
    with pytest.raises(TypeError, match="requires an annotations mapping"):
        type("InvalidAnnotations", (Spec,), {"__annotations__": 1})
    with pytest.raises(TypeError, match="requires a callable annotation function"):
        type("InvalidFunction", (Spec,), {"__annotate_func__": 1})
    with pytest.raises(TypeError, match="annotation function must return a mapping"):
        type("InvalidResult", (Spec,), {"__annotate_func__": lambda format: []})
