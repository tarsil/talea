from __future__ import annotations

import builtins
import gc
import inspect
import sys
import weakref
from dataclasses import FrozenInstanceError

import pytest

import talea
import talea.schema.resolution as annotation_resolution
import talea.spec.declaration as spec_module
import talea.spec.fields as field_module
import talea.spec.metaclass as metaclass_module
from talea import Spec, field
from talea.declaration import MISSING_DEFAULT, SpecSchema
from talea.schema import (
    AnnotationResolutionError,
    MappingSchema,
    PrimitiveSchema,
    SequenceSchema,
    UnionSchema,
)
from talea.validation import ValidationError


class User(Spec):
    id: int
    name: str


class Payload(Spec):
    values: list[int]
    metadata: dict[str, int | None]


def test_root_package_exports_only_the_deliberate_public_api() -> None:
    assert talea.__all__ == [
        "Alias",
        "Contract",
        "Ge",
        "Gt",
        "ErrorCode",
        "ErrorData",
        "Le",
        "Lt",
        "MaxLength",
        "MinLength",
        "MultipleOf",
        "Pattern",
        "Spec",
        "SerializationError",
        "ValidationError",
        "check",
        "field",
        "serialize",
        "transform",
    ]
    assert talea.Spec is Spec
    assert talea.field is field
    assert not hasattr(talea, "SpecSchema")
    assert not hasattr(talea, "compile_validator")
    assert not hasattr(talea, "CustomValidationError")
    assert talea.ValidationError is ValidationError


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
    assert all(field.required for field in artifacts.schema.fields)
    assert all(field.default is MISSING_DEFAULT for field in artifacts.schema.fields)

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
    assert str(raised.value).startswith(
        "Payload\n  metadata.wrong\n    Expected one of: int | None\n    received: '2' (str)"
    )


def test_instances_are_compact_and_retain_only_declared_values() -> None:
    user = User(id=1, name="Tiago")

    assert User.__slots__ == ("id", "name")
    assert not hasattr(user, "__dict__")
    assert not hasattr(user, "__weakref__")
    assert not any(slot.startswith("__talea") for slot in User.__slots__)


def test_required_only_constructor_retains_no_default_runtime_artifacts() -> None:
    globals_ = vars(User)["__init__"].__globals__
    validators = vars(User)["__talea_artifacts__"].validators
    descriptors = {vars(User)[field] for field in User.__slots__}
    retained_setters = {
        value.__self__
        for value in globals_.values()
        if getattr(value, "__name__", None) == "__set__" and hasattr(value, "__self__")
    }

    assert not any(isinstance(value, field_module._FactorySentinel) for value in globals_.values())
    assert not any(isinstance(value, spec_module._FactoryDeclaration) for value in globals_.values())
    assert object.__setattr__ not in globals_.values()
    assert not any(validator in globals_.values() for validator in validators)
    assert retained_setters == descriptors


def test_compiled_slot_setters_do_not_keep_discarded_spec_classes_alive() -> None:
    def declare() -> weakref.ReferenceType[type[Spec]]:
        class Ephemeral(Spec):
            value: int

        return weakref.ref(Ephemeral)

    reference = declare()
    gc.collect()

    assert reference() is None


def test_spec_equality_and_hashing_use_object_identity() -> None:
    first = User(id=1, name="Tiago")
    second = User(id=1, name="Tiago")

    assert first == first
    assert first != second
    assert User.__eq__ is object.__eq__
    assert User.__hash__ is object.__hash__


def test_custom_construction_and_slots_are_rejected() -> None:
    with pytest.raises(TypeError, match="Spec manages construction"):

        class CustomInit(Spec):
            value: int

            def __init__(self, *, value: int) -> None:
                self.value = value

    with pytest.raises(TypeError, match="Spec manages instance slots"):

        class CustomSlots(Spec):
            __slots__ = ()


def test_static_default_is_retained_canonically_and_used_when_omitted() -> None:
    class Account(Spec):
        name: str
        active: bool = True
        roles: frozenset[str] = frozenset({"user"})

    omitted = Account(name="Ada")
    explicit = Account(name="Ada", active=False)
    artifacts = vars(Account)["__talea_artifacts__"]
    active = artifacts.schema.fields[1]

    assert str(inspect.signature(Account)) == "(*, name, active=True, roles=frozenset({'user'}))"
    assert omitted.active is True
    assert omitted.roles == frozenset({"user"})
    assert explicit.active is False
    assert not active.required
    assert active.has_static_default
    assert active.default is True
    assert vars(Account)["active"] is not True

    with pytest.raises(ValidationError) as raised:
        Account(name="Ada", active=1)  # type: ignore[invalid-argument-type]

    assert raised.value.location == ("active",)


def test_optional_annotation_does_not_imply_a_default() -> None:
    class RequiredOptional(Spec):
        value: int | None

    class DefaultedOptional(Spec):
        value: int | None = None

    with pytest.raises(TypeError, match="required keyword-only argument: 'value'"):
        RequiredOptional()  # type: ignore[missing-argument]

    assert RequiredOptional(value=None).value is None
    assert DefaultedOptional().value is None


def test_required_fields_may_follow_defaults_without_losing_declaration_order() -> None:
    class Ordered(Spec):
        enabled: bool = True
        identifier: int

    ordered = Ordered(identifier=1)

    assert str(inspect.signature(Ordered)) == "(*, enabled=True, identifier)"
    assert repr(ordered) == "Ordered(enabled=True, identifier=1)"


@pytest.mark.parametrize(
    ("name", "annotation", "default", "expected", "location"),
    [
        ("count", int, "1", "int", ("count",)),
        ("values", list[int], (1, 2), "list[int]", ("values",)),
        ("pair", tuple[int, str], (1, 2), "str", ("pair", 1)),
    ],
)
def test_invalid_static_default_fails_during_declaration(
    name: str,
    annotation: object,
    default: object,
    expected: str,
    location: tuple[object, ...],
) -> None:
    with pytest.raises(ValidationError) as raised:
        type("InvalidDefault", (Spec,), {"__annotations__": {name: annotation}, name: default})

    assert raised.value.expected == expected
    assert raised.value.location == location


@pytest.mark.parametrize(
    ("annotation", "default"),
    [
        (list[int], [1]),
        (set[int], {1}),
        (dict[str, int], {"one": 1}),
        (tuple[list[int]], ([1],)),
    ],
)
def test_mutable_static_defaults_are_rejected(annotation: object, default: object) -> None:
    with pytest.raises(TypeError, match=r"mutable static default.*field\(default_factory=\.\.\.\)"):
        type("MutableDefault", (Spec,), {"__annotations__": {"value": annotation}, "value": default})


def test_default_factory_produces_independent_validated_values() -> None:
    calls = 0

    def new_items() -> list[int]:
        nonlocal calls
        calls += 1
        return []

    class Basket(Spec):
        owner: str
        items: list[int] = field(default_factory=new_items)

    first = Basket(owner="Ada")
    second = Basket(owner="Grace")
    first.items.append(1)
    artifacts = vars(Basket)["__talea_artifacts__"]
    items = artifacts.schema.fields[1]

    assert str(inspect.signature(Basket)) == "(*, owner, items=<factory>)"
    assert first.items == [1]
    assert second.items == []
    assert first.items is not second.items
    assert calls == 2
    assert not items.required
    assert not items.has_static_default
    assert items.default_factory is new_items


def test_factory_runs_once_only_when_omitted_and_interacts_with_multiple_fields() -> None:
    calls = 0

    def next_value() -> int:
        nonlocal calls
        calls += 1
        return calls

    class Generated(Spec):
        before: int = field(default_factory=next_value)
        required: str
        after: int = field(default_factory=next_value)

    generated = Generated(required="value", before=10)

    assert (generated.before, generated.required, generated.after) == (10, "value", 1)
    assert calls == 1


def test_factory_output_is_validated_at_the_field_boundary() -> None:
    class InvalidFactory(Spec):
        count: int = field(default_factory=lambda: "1")  # type: ignore[invalid-return-type]

    with pytest.raises(ValidationError) as raised:
        InvalidFactory()

    assert raised.value.expected == "int"
    assert raised.value.location == ("count",)


def test_factory_failure_has_a_clear_field_boundary_and_preserves_cause() -> None:
    failure = RuntimeError("unavailable")

    def fail() -> int:
        raise failure

    class FailedFactory(Spec):
        count: int = field(default_factory=fail)

    with pytest.raises(ValidationError, match="Default factory failed") as raised:
        FailedFactory()

    assert raised.value.code == "factory"
    assert raised.value.location == ("count",)
    assert raised.value.__cause__ is failure


def test_invalid_factory_declaration_syntax_is_rejected() -> None:
    with pytest.raises(TypeError, match="default_factory must be callable"):
        field(default_factory=1)  # type: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match=r"field\(\) requires an annotation: 'items'"):

        class MissingAnnotation(Spec):
            items = field(default_factory=list)


def test_specs_are_immutable_and_unknown_assignment_is_deterministic() -> None:
    user = User(id=1, name="Tiago")

    with pytest.raises(AttributeError, match="User instances are immutable"):
        user.id = "invalid"  # type: ignore[invalid-assignment]
    with pytest.raises(AttributeError, match="User instances are immutable"):
        user.email = "tiago@example.com"  # type: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match="User instances are immutable"):
        del user.name

    assert (user.id, user.name) == (1, "Tiago")


def test_permanent_trust_requires_a_transitively_immutable_schema() -> None:
    class Stable(Spec):
        pair: tuple[int, str]

    class MutablePayload(Spec):
        values: list[int]

    Stable(pair=(1, "one"))
    mutable = MutablePayload(values=[1])
    mutable.values.append("invalid")  # type: ignore[invalid-argument-type]

    assert vars(Stable)["__talea_artifacts__"].schema.instances_are_permanently_trusted
    assert not vars(MutablePayload)["__talea_artifacts__"].schema.instances_are_permanently_trusted
    assert mutable.values == [1, "invalid"]


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

    def counted_constructor(
        compiler: object,
        schema: object,
        slot_setters: object,
    ) -> object:
        nonlocal constructor_calls
        constructor_calls += 1
        return original_constructor(
            compiler,
            schema,
            slot_setters,
        )  # type: ignore[invalid-argument-type]

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
    artifacts = vars(Lifecycle)["__talea_artifacts__"]
    assert artifacts.inputs.mapping_input is None
    assert artifacts.inputs.json_input is None
    assert Lifecycle(id=1, labels=["one"]).id == 1
    assert Lifecycle(id=2, labels=["two"]).id == 2
    assert resolution_calls == 2
    assert validator_calls == 2
    assert constructor_calls == 1
    assert declaration_compile_calls >= 3
    assert declaration_exec_calls == 3
    assert compile_calls == declaration_compile_calls
    assert exec_calls == declaration_exec_calls

    assert Lifecycle.from_mapping({"id": 3, "labels": ["three"]}).id == 3
    mapping_compile_calls = compile_calls
    mapping_exec_calls = exec_calls
    assert artifacts.inputs.mapping_input is not None
    assert artifacts.inputs.json_input is None
    assert Lifecycle.from_mapping({"id": 4, "labels": ["four"]}).id == 4
    assert compile_calls == mapping_compile_calls
    assert exec_calls == mapping_exec_calls

    assert Lifecycle.from_json('{"id":5,"labels":["five"]}').id == 5
    json_compile_calls = compile_calls
    json_exec_calls = exec_calls
    assert artifacts.inputs.json_input is not None
    assert Lifecycle.from_json('{"id":6,"labels":["six"]}').id == 6
    assert compile_calls == json_compile_calls
    assert exec_calls == json_exec_calls


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
    monkeypatch.setattr(annotation_resolution, "get_origin", forbidden)
    monkeypatch.setattr(annotation_resolution, "get_args", forbidden)
    monkeypatch.setattr(builtins, "compile", forbidden)
    monkeypatch.setattr(builtins, "exec", forbidden)

    for identifier in range(3):
        retained = Retained(id=identifier, payload=[{"value": identifier, "none": None}])
        assert retained.id == identifier

    assert tuple(map(id, artifacts.validators)) == validator_ids


def test_defaults_use_only_retained_declaration_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def make_identifier() -> int:
        nonlocal calls
        calls += 1
        return calls

    class RetainedDefaults(Spec):
        active: bool = True
        identifier: int = field(default_factory=make_identifier)

    artifacts = vars(RetainedDefaults)["__talea_artifacts__"]
    validator_ids = tuple(map(id, artifacts.validators))

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"default construction invoked declaration work: {args!r}, {kwargs!r}")

    monkeypatch.setattr(spec_module, "resolve_annotation", forbidden)
    monkeypatch.setattr(spec_module, "compile_validator", forbidden)
    monkeypatch.setattr(spec_module._ConstructorCompiler, "compile", forbidden)
    monkeypatch.setattr(spec_module, "get_type_hints", forbidden)
    monkeypatch.setattr(annotation_resolution, "get_origin", forbidden)
    monkeypatch.setattr(annotation_resolution, "get_args", forbidden)
    monkeypatch.setattr(builtins, "compile", forbidden)
    monkeypatch.setattr(builtins, "exec", forbidden)

    first = RetainedDefaults()
    second = RetainedDefaults(active=False)

    assert (first.active, first.identifier) == (True, 1)
    assert (second.active, second.identifier) == (False, 2)
    assert tuple(map(id, artifacts.validators)) == validator_ids


def test_defaulted_instances_retain_values_only() -> None:
    class Required(Spec):
        count: int

    class Compact(Spec):
        count: int = 1
        items: list[int] = field(default_factory=list)

    class Static(Spec):
        count: int = 1

    class Factory(Spec):
        items: list[int] = field(default_factory=list)

    compact = Compact()

    assert Compact.__slots__ == ("count", "items")
    assert not hasattr(compact, "__dict__")
    assert not any(slot.startswith("__talea") for slot in Compact.__slots__)
    assert compact.items == []
    assert sys.getsizeof(Required(count=1)) == sys.getsizeof(Static()) == sys.getsizeof(Factory())


def test_generated_constructor_binds_field_names_and_accepts_valid_unicode() -> None:
    class Localized(Spec):
        café: str

    localized = Localized(café="Kafi")
    globals_ = vars(Localized)["__init__"].__globals__

    assert localized.café == "Kafi"
    assert ("café",) in globals_.values()


def test_generated_constructor_names_cannot_collide_with_fields() -> None:
    annotations = {
        "_talea_instance_1": list[dict[str, int | None]],
        "_talea_item_1": list[int] | dict[str, int],
        "_talea_key_1": dict[str, int],
        "_talea_matched_1": list[int] | dict[str, int],
        "_talea_best_error_1": tuple[int, ...],
        "_talea_error_1": int,
        "_talea_validation_error_1": int,
        "_talea_identity_index_1": list[int],
        "_talea_slot_0_1": int,
        "_talea_validator_0": int,
        "under___score": int,
    }
    values = {
        "_talea_instance_1": [{"one": 1, "none": None}],
        "_talea_item_1": [1, 2],
        "_talea_key_1": {"one": 1},
        "_talea_matched_1": {"two": 2},
        "_talea_best_error_1": (1, 2),
        "_talea_error_1": 3,
        "_talea_validation_error_1": 4,
        "_talea_identity_index_1": [5],
        "_talea_slot_0_1": 6,
        "_talea_validator_0": 7,
        "under___score": 8,
    }
    collision = type("Collision", (Spec,), {"__annotations__": annotations})

    instance = collision(**values)
    initializer = vars(collision)["__init__"]
    parameter_names = set(inspect.signature(initializer).parameters)
    generated_locals = set(initializer.__code__.co_varnames) - parameter_names

    assert tuple(getattr(instance, name) for name in annotations) == tuple(values.values())
    assert not set(annotations) & generated_locals
    assert not set(annotations) & set(initializer.__globals__)

    with pytest.raises(ValidationError) as raised:
        collision(**{**values, "_talea_instance_1": [{"wrong": "value"}]})

    assert raised.value.location == ("_talea_instance_1", 0, "wrong")


def test_inline_runtime_bindings_cannot_be_shadowed_by_fields() -> None:
    annotations = {
        "type": int,
        "int": int,
        "list": list[int],
        "dict": dict[str, int],
        "tuple": tuple[int, str],
        "len": int,
        "Exception": str,
        "TypeError": bytes,
        "factory": int,
    }
    runtime_names = type(
        "RuntimeNames",
        (Spec,),
        {
            "__annotations__": annotations,
            "factory": field(default_factory=lambda: 9),
        },
    )
    values = {
        "type": 1,
        "int": 2,
        "list": [3],
        "dict": {"four": 4},
        "tuple": (5, "six"),
        "len": 7,
        "Exception": "eight",
        "TypeError": b"nine",
    }

    instance = runtime_names(**values)

    assert tuple(getattr(instance, name) for name in annotations) == (*values.values(), 9)


def test_constructor_inlines_validation_while_standalone_validators_remain_independent() -> None:
    class Inline(Spec):
        identifier: int
        label: str = "default"
        values: list[int] = field(default_factory=lambda: [1])

    artifacts = vars(Inline)["__talea_artifacts__"]
    initializer = vars(Inline)["__init__"]

    assert artifacts.validators[0](1) == 1
    assert artifacts.validators[1]("explicit") == "explicit"
    assert artifacts.validators[2]([2]) == [2]
    assert not any(validator in initializer.__globals__.values() for validator in artifacts.validators)

    def forbidden_validator(value: object) -> object:
        raise AssertionError(f"constructor called standalone validator with {value!r}")

    for validator in artifacts.validators:
        validator.__code__ = forbidden_validator.__code__

    omitted = Inline(identifier=1)
    explicit = Inline(identifier=2, label="explicit", values=[3])

    assert (omitted.identifier, omitted.label, omitted.values) == (1, "default", [1])
    assert (explicit.identifier, explicit.label, explicit.values) == (2, "explicit", [3])


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
    with pytest.raises(TypeError, match="requires at least one Spec base"):
        metaclass_module._SpecMeta("Detached", (object,), {})
    with pytest.raises(TypeError, match="requires an annotations mapping"):
        type("InvalidAnnotations", (Spec,), {"__annotations__": 1})
    with pytest.raises(TypeError, match="requires a callable annotation function"):
        type("InvalidFunction", (Spec,), {"__annotate_func__": 1})
    with pytest.raises(TypeError, match="annotation function must return a mapping"):
        type("InvalidResult", (Spec,), {"__annotate_func__": lambda format: []})
