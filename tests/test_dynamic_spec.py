from __future__ import annotations

import pickle
from typing import Annotated

import pytest

import talea
from talea import Alias, Ge, Spec, ValidationError, check, create_spec, serialize, transform


def test_create_spec_returns_a_normal_compiled_spec_class() -> None:
    DynamicUser = create_spec("DynamicUser", {"id": int, "name": str})

    user = DynamicUser(id=1, name="Ada")

    assert issubclass(DynamicUser, Spec)
    assert type(DynamicUser) is type(Spec)
    assert user.id == 1
    assert user.name == "Ada"
    assert DynamicUser.from_mapping({"id": 2, "name": "Grace"}).id == 2
    assert DynamicUser.from_json('{"id":3,"name":"Lin"}').id == 3
    assert user.to_dict() == {"id": 1, "name": "Ada"}
    assert user.to_json() == '{"id":1,"name":"Ada"}'


def test_create_spec_supports_static_defaults_and_factories() -> None:
    calls = 0

    def make_tags() -> list[str]:
        nonlocal calls
        calls += 1
        return []

    Dynamic = create_spec(
        "DynamicDefaults",
        {"active": bool, "tags": list[str]},
        defaults={"active": True},
        factories={"tags": make_tags},
    )

    first = Dynamic()
    second = Dynamic(active=False)

    assert first.active is True
    assert second.active is False
    assert first.tags == second.tags == []
    assert first.tags is not second.tags
    assert calls == 2


def test_create_spec_preserves_aliases_and_constraints_in_annotations() -> None:
    Dynamic = create_spec(
        "DynamicAlias",
        {"identifier": Annotated[int, Alias("id"), Ge(1)]},
    )

    assert Dynamic.from_mapping({"id": 1}).identifier == 1
    assert Dynamic(identifier=1).to_dict() == {"id": 1}
    with pytest.raises(ValidationError):
        Dynamic(identifier=0)


def test_create_spec_uses_normal_inheritance_and_concrete_generic_bases() -> None:
    class Person(Spec):
        name: str

    class Box[T](Spec):
        value: T

    Employee = create_spec("Employee", {"employee_id": int}, base=Person)
    IntBox = create_spec("IntBox", {"label": str}, base=Box[int])

    employee = Employee(name="Ada", employee_id=1)
    box = IntBox(value=1, label="one")

    assert isinstance(employee, Person)
    assert employee.name == "Ada"
    assert isinstance(box, Box[int])
    assert box.value == 1


def test_create_spec_namespace_uses_the_existing_hook_and_method_lifecycle() -> None:
    @transform("value")
    def normalize(value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @check("value")
    def nonempty(value: str) -> None:
        if not value:
            raise ValueError

    @serialize("value")
    def upper(value: str) -> str:
        return value.upper()

    def greeting(self: object) -> str:
        return f"hello {self.value}"  # type: ignore[unresolved-attribute]

    Dynamic = create_spec(
        "DynamicHooks",
        {"value": str},
        namespace={
            "normalize": normalize,
            "nonempty": nonempty,
            "upper": upper,
            "greeting": greeting,
        },
    )

    instance = Dynamic(value=" Ada ")

    assert instance.value == "Ada"
    assert instance.greeting() == "hello Ada"
    assert instance.to_dict() == {"value": "ADA"}
    with pytest.raises(ValidationError):
        Dynamic(value="")


def test_create_spec_sets_explicit_identity_without_binding_modules() -> None:
    Dynamic = create_spec(
        "Generated",
        {"value": int},
        module="generated.contracts",
        qualname="Registry.Generated",
        doc="Generated application contract.",
    )

    assert Dynamic.__name__ == "Generated"
    assert Dynamic.__module__ == "generated.contracts"
    assert Dynamic.__qualname__ == "Registry.Generated"
    assert Dynamic.__doc__ == "Generated application contract."
    assert "generated.contracts" not in __import__("sys").modules
    with pytest.raises(pickle.PicklingError):
        pickle.dumps(Dynamic(value=1))


@pytest.mark.parametrize("name", ["", "class", "not-valid", "＿hidden"])
def test_create_spec_rejects_unsafe_class_names(name: str) -> None:
    with pytest.raises(TypeError, match="invalid create_spec class name"):
        create_spec(name, {"value": int})


@pytest.mark.parametrize("metadata", ["", "bad-name", "pkg.class", "pkg..name", 1])
def test_create_spec_rejects_unsafe_module_identity(metadata: object) -> None:
    with pytest.raises(TypeError, match="module must be a dotted Python name"):
        create_spec("Safe", {"value": int}, module=metadata)  # type: ignore[invalid-argument-type]


@pytest.mark.parametrize("metadata", ["", "bad-name", "Owner.<locals>.Safe", 1])
def test_create_spec_rejects_unsafe_qualified_identity(metadata: object) -> None:
    with pytest.raises(TypeError, match="qualname must be a dotted Python name"):
        create_spec("Safe", {"value": int}, qualname=metadata)  # type: ignore[invalid-argument-type]


def test_create_spec_rejects_ambiguous_or_invalid_field_configuration() -> None:
    with pytest.raises(TypeError, match="fields must be a mapping"):
        create_spec("Invalid", [])  # type: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="defaults must be a mapping"):
        create_spec("Invalid", {"value": int}, defaults=[])  # type: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="factories must be a mapping"):
        create_spec("Invalid", {"value": int}, factories=[])  # type: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="unknown field"):
        create_spec("Invalid", {"value": int}, defaults={"other": 1})
    with pytest.raises(TypeError, match="both a default and factory"):
        create_spec("Invalid", {"value": int}, defaults={"value": 1}, factories={"value": int})
    with pytest.raises(TypeError, match="must be callable"):
        create_spec("Invalid", {"value": int}, factories={"value": 1})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="evaluated field annotations"):
        create_spec("Invalid", {"value": "int"})


def test_create_spec_rejects_namespace_and_identity_collisions() -> None:
    with pytest.raises(TypeError, match="namespace must be a mapping"):
        create_spec("Invalid", {"value": int}, namespace=[])  # type: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="conflicts with namespace"):
        create_spec("Invalid", {"value": int}, namespace={"value": object()})
    with pytest.raises(TypeError, match="manages namespace key"):
        create_spec("Invalid", {"value": int}, namespace={"__module__": "other"})
    with pytest.raises(TypeError, match="manages construction"):
        create_spec("Invalid", {"value": int}, namespace={"__init__": lambda self: None})


def test_create_spec_validates_base_doc_and_field_names_through_normal_owners() -> None:
    with pytest.raises(TypeError, match="base must be a Spec class"):
        create_spec("Invalid", {"value": int}, base=object)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="doc must be str or None"):
        create_spec("Invalid", {"value": int}, doc=1)  # type: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="invalid Spec field name"):
        create_spec("Invalid", {"not-valid": int})


def test_create_spec_is_the_only_new_dynamic_root_vocabulary() -> None:
    assert talea.create_spec is create_spec
    assert "DynamicSpec" not in talea.__all__
    assert "create_model" not in talea.__all__
