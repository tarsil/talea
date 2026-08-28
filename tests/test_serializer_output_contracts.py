import json
from concurrent.futures import ThreadPoolExecutor
from copy import replace
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict, cast

import pytest

from talea import (
    Alias,
    Discriminator,
    Pattern,
    Representation,
    Sensitive,
    SerializationError,
    Spec,
    WriteOnly,
    apply_patch,
    create_spec,
    derive_spec,
    serialize,
)
from talea.introspection import inspect_spec
from talea.schema import PrimitiveSchema


class RecursiveSerializerPayload(TypedDict):
    name: str
    children: list["RecursiveSerializerPayload"]


def _definition(document: dict[str, object], name: str) -> dict[str, object]:
    definitions = cast(dict[str, object], document["$defs"])
    return cast(dict[str, object], definitions[name])


def test_declared_scalar_output_validates_projects_and_calls_once() -> None:
    calls: list[int] = []

    class Payload(Spec):
        value: int

        @serialize("value", output=str)
        def output(value: int) -> str:
            calls.append(value)
            return str(value)

    payload = Payload(value=3)
    assert payload.to_dict() == {"value": "3"}
    assert json.loads(payload.to_json()) == {"value": "3"}
    assert calls == [3, 3]


def test_invalid_declared_result_is_a_field_serialization_failure() -> None:
    calls = 0

    class Payload(Spec):
        value: int

        @serialize("value", output=str)
        def output(value: int) -> object:
            nonlocal calls
            calls += 1
            return value

    with pytest.raises(SerializationError, match="outside its declared output contract") as caught:
        Payload(value=3).to_dict()
    assert caught.value.location == ("value",)
    assert calls == 1
    with pytest.raises(SerializationError, match="outside its declared output contract"):
        Payload(value=3).to_json()
    assert calls == 2


def test_declared_callback_failure_preserves_ordinary_cause() -> None:
    failure = RuntimeError("application detail")

    class Payload(Spec):
        value: int

        @serialize("value", output=str)
        def output(value: int) -> str:
            raise failure

    with pytest.raises(SerializationError, match="hook 'output' failed") as caught:
        Payload(value=1).to_dict()
    assert caught.value.location == ("value",)
    assert caught.value.__cause__ is failure


def test_declared_constraints_and_sensitive_failures_do_not_leak() -> None:
    class Constrained(Spec):
        token: Annotated[str, Sensitive()]

        @serialize("token", output=Annotated[str, Pattern(r"^public$")])
        def output(token: str) -> str:
            return token

    with pytest.raises(SerializationError) as caught:
        Constrained(token="secret-value").to_dict()
    assert caught.value.__cause__ is None
    assert "secret-value" not in str(caught.value)

    class Crashing(Spec):
        token: Annotated[str, Sensitive()]

        @serialize("token", output=str)
        def output(token: str) -> str:
            raise RuntimeError(token)

    Crashing.output.__name__ = "secret-value"

    with pytest.raises(SerializationError) as callback:
        Crashing(token="secret-value").to_dict()
    assert callback.value.__cause__ is None
    assert "secret-value" not in str(callback.value)


def test_structural_output_supports_aliases_and_nested_include_exclude() -> None:
    calls = 0

    class Summary(Spec):
        display_name: Annotated[str, Alias("displayName")]
        email: str

    class Account(Spec):
        name: str

        @serialize("name", output=Summary)
        def summary(name: str) -> Summary:
            nonlocal calls
            calls += 1
            return Summary(display_name=name, email="hidden@example.test")

    account = Account(name="Ada")
    assert account.to_dict(include={"name": {"display_name": True}}) == {"name": {"displayName": "Ada"}}
    assert account.to_dict(exclude={"name": {"email": True}}) == {"name": {"displayName": "Ada"}}
    assert calls == 2


def test_unknown_nested_selection_rejects_before_callback() -> None:
    calls = 0

    class Summary(TypedDict):
        name: str

    class Account(Spec):
        value: int

        @serialize("value", output=Summary)
        def summary(value: int) -> Summary:
            nonlocal calls
            calls += 1
            return {"name": str(value)}

    with pytest.raises(ValueError, match="unknown field"):
        Account(value=1).to_dict(include={"value": {"missing": True}})
    assert calls == 0


def test_declared_containers_follow_uniform_nested_selection() -> None:
    class Summary(TypedDict):
        name: str
        private: str

    class Payload(Spec):
        values: int
        mapping: int

        @serialize("values", output=list[Summary])
        def serialize_values(value: int) -> list[Summary]:
            return [{"name": str(value), "private": "x"}]

        @serialize("mapping", output=dict[str, Summary])
        def serialize_mapping(value: int) -> dict[str, Summary]:
            return {"item": {"name": str(value), "private": "x"}}

    payload = Payload(values=1, mapping=2)
    assert payload.to_dict(include={"values": {"name": True}, "mapping": {"name": True}}) == {
        "values": [{"name": "1"}],
        "mapping": {"item": {"name": "2"}},
    }


def test_dataclass_typed_dict_spec_and_optional_outputs() -> None:
    @dataclass
    class DataclassSummary:
        name: str

    class DictionarySummary(TypedDict):
        name: str

    class SpecSummary(Spec):
        name: str

    class Payload(Spec):
        dataclass_value: int
        dictionary_value: int
        spec_value: int
        optional_value: int

        @serialize("dataclass_value", output=DataclassSummary)
        def dataclass_output(value: int) -> DataclassSummary:
            return DataclassSummary(str(value))

        @serialize("dictionary_value", output=DictionarySummary)
        def dictionary_output(value: int) -> DictionarySummary:
            return {"name": str(value)}

        @serialize("spec_value", output=SpecSummary)
        def spec_output(value: int) -> SpecSummary:
            return SpecSummary(name=str(value))

        @serialize("optional_value", output=str | None)
        def optional_output(value: int) -> str | None:
            return None if value == 0 else str(value)

    assert Payload(dataclass_value=1, dictionary_value=2, spec_value=3, optional_value=0).to_dict() == {
        "dataclass_value": {"name": "1"},
        "dictionary_value": {"name": "2"},
        "spec_value": {"name": "3"},
        "optional_value": None,
    }


def test_representation_output_composes_without_reusing_original_field_dump() -> None:
    class Identifier:
        def __init__(self, value: int) -> None:
            self.value = value

    dumps: list[int] = []

    def dump_identifier(value: Identifier) -> str:
        dumps.append(value.value)
        return f"id-{value.value}"

    type IdentifierValue = Annotated[
        Identifier,
        Representation(output=str, dump=dump_identifier),
    ]

    class Payload(Spec):
        value: IdentifierValue

        @serialize("value", output=IdentifierValue)
        def output(value: Identifier) -> Identifier:
            return Identifier(value.value + 1)

    payload = Payload(value=Identifier(4))
    assert payload.to_dict() == {"value": "id-5"}
    assert dumps == [5]


def test_declared_output_drives_output_schema_only_and_openapi() -> None:
    class Summary(Spec):
        name: str

    class Payload(Spec):
        value: int

        @serialize("value", output=Summary)
        def output(value: int) -> Summary:
            raise AssertionError("projection must not execute callbacks")

    input_definition = _definition(Payload.json_schema(mode="input"), "Payload")
    output_definition = _definition(Payload.json_schema(mode="output"), "Payload")
    assert input_definition["properties"] == {"value": {"type": "integer"}}
    output_properties = cast(dict[str, object], output_definition["properties"])
    assert output_properties["value"] == {"$ref": "#/$defs/Summary"}
    assert Payload.openapi_schema(mode="input")["schema"] == {"$ref": "#/components/schemas/Payload"}
    openapi_output = Payload.openapi_schema(mode="output")
    assert openapi_output["schema"] == {"$ref": "#/components/schemas/Payload"}
    components = cast(dict[str, object], openapi_output["components"])
    schemas = cast(dict[str, object], components["schemas"])
    payload_schema = cast(dict[str, object], schemas["Payload"])
    properties = cast(dict[str, object], payload_schema["properties"])
    assert properties["value"] == {"$ref": "#/components/schemas/Summary"}


def test_introspection_exposes_declared_schema_but_not_callback() -> None:
    class Payload(Spec):
        value: int

        @serialize("value", output=str)
        def output(value: int) -> str:
            return str(value)

    serializer = inspect_spec(Payload).serializers[0]
    assert serializer.name == "output"
    assert serializer.field == "value"
    assert serializer.has_declared_output is True
    assert serializer.output_schema == PrimitiveSchema("str")
    assert not hasattr(serializer, "function")
    assert not hasattr(serializer, "callback")


def test_inheritance_dynamic_declaration_derivation_and_replacement() -> None:
    calls = 0

    class Base(Spec):
        value: int

        @serialize("value", output=str)
        def output(value: int) -> str:
            return str(value)

    class Child(Base):
        @serialize("value", output=int)
        def output(value: int) -> int:
            return value + 1

    class Shadowed(Base):
        def output(self) -> None:
            return None

    dynamic_output = serialize("value", output=str)(lambda value: str(value))
    Dynamic = create_spec("Dynamic", {"value": int}, namespace={"output": dynamic_output})
    Derived = derive_spec(Base, include={"value"})
    Omitted = derive_spec(Base, exclude={"value"})

    assert Child(value=1).to_dict() == {"value": 2}
    assert Shadowed(value=1).to_dict() == {"value": 1}
    assert Dynamic(value=1).to_dict() == {"value": "1"}
    assert Derived(value=1).to_dict() == {"value": "1"}
    assert inspect_spec(Omitted).serializers == ()

    class Counted(Spec):
        value: int

        @serialize("value", output=str)
        def output(value: int) -> str:
            nonlocal calls
            calls += 1
            return str(value)

    original = Counted(value=1)
    changed = replace(original, value=2)
    assert calls == 0
    assert changed.to_dict() == {"value": "2"}
    assert calls == 1


def test_partial_patch_retains_output_truth_without_executing_serializer() -> None:
    calls = 0

    class Source(Spec):
        value: int
        label: str

        @serialize("value", output=str)
        def output(value: int) -> str:
            nonlocal calls
            calls += 1
            return str(value)

    Patch = derive_spec(Source, include={"value"}, partial=True, mode="input")
    source = Source(value=1, label="kept")
    patch = Patch(value=2)
    changed = apply_patch(source, patch)
    assert calls == 0
    assert changed.to_dict() == {"value": "2", "label": "kept"}
    assert calls == 1


def test_generic_output_contract_specializes() -> None:
    class Box[T](Spec):
        value: T

        @serialize("value", output=T)
        def output(value: T) -> T:
            return value

    assert Box[int](value=1).to_dict() == {"value": 1}
    serializer = inspect_spec(Box[int]).serializers[0]
    assert serializer.output_schema == PrimitiveSchema("int")


def test_tagged_output_validates_and_preserves_discriminator_selection() -> None:
    class Success(TypedDict):
        kind: Literal["success"]
        value: int

    class Failure(TypedDict):
        kind: Literal["failure"]
        reason: str

    type Result = Annotated[Success | Failure, Discriminator("kind")]

    class Payload(Spec):
        code: int

        @serialize("code", output=Result)
        def output(code: int) -> Result:
            return {"kind": "success", "value": code}

    assert Payload(code=2).to_dict(include={"code": {"kind": True, "value": True}}) == {
        "code": {"kind": "success", "value": 2}
    }
    with pytest.raises(ValueError, match="discriminator"):
        Payload(code=2).to_dict(include={"code": {"value": True}})


def test_invalid_output_form_rejects_during_declaration() -> None:
    with pytest.raises(TypeError, match="Unsupported annotation"):

        class Invalid(Spec):
            value: int

            @serialize("value", output=object())
            def output(value: int) -> object:
                return value


def test_write_only_derivation_removes_serializer_and_callback() -> None:
    calls = 0

    class Payload(Spec):
        secret: Annotated[str, WriteOnly()]
        visible: str

        @serialize("secret", output=str)
        def output(value: str) -> str:
            nonlocal calls
            calls += 1
            return value

    Output = derive_spec(Payload, mode="output")
    assert Output(visible="ok").to_dict() == {"visible": "ok"}
    assert calls == 0


def test_recursive_output_and_cyclic_result_follow_existing_cycle_policy() -> None:
    class Node(Spec):
        name: str
        children: list["Node"]

    class Tree(Spec):
        depth: int

        @serialize("depth", output=Node)
        def output(depth: int) -> Node:
            return Node(name="root", children=[] if depth == 0 else [Node(name="leaf", children=[])])

    assert Tree(depth=1).to_dict() == {"depth": {"name": "root", "children": [{"name": "leaf", "children": []}]}}

    class Cyclic(Spec):
        value: int

        @serialize("value", output=RecursiveSerializerPayload)
        def output(value: int) -> RecursiveSerializerPayload:
            result: RecursiveSerializerPayload = {"name": str(value), "children": []}
            result["children"].append(result)
            return result

    with pytest.raises(SerializationError, match="cyclic object graphs"):
        Cyclic(value=1).to_dict()


def test_callback_reentrancy_and_mutation_execute_once_without_locking() -> None:
    calls = 0

    class Inner(Spec):
        value: int

    class Outer(Spec):
        values: list[int]

        @serialize("values", output=list[int])
        def output(values: list[int]) -> list[int]:
            nonlocal calls
            calls += 1
            values.append(Inner(value=2).to_dict()["value"])  # type: ignore[arg-type]
            return values

    outer = Outer(values=[1])
    assert outer.to_dict() == {"values": [1, 2]}
    assert outer.values == [1, 2]
    assert calls == 1


def test_declared_serializer_compiles_once_under_concurrent_first_use() -> None:
    calls = 0

    class Payload(Spec):
        value: int

        @serialize("value", output=str)
        def output(value: int) -> str:
            nonlocal calls
            calls += 1
            return str(value)

    payload = Payload(value=1)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(lambda _: payload.to_dict(), range(24)))
    assert results == ({"value": "1"},) * 24
    assert calls == 24
    artifacts = vars(Payload)["__talea_artifacts__"]
    assert artifacts.outputs.python_alias is not None


def test_fixed_tuple_and_union_declared_outputs_reuse_selection_policy() -> None:
    class Common(TypedDict):
        name: str
        hidden: str

    class Alternate(TypedDict):
        name: str
        count: int

    class Payload(Spec):
        pair: int
        choice: int

        @serialize("pair", output=tuple[Common, Common])
        def pair_output(value: int) -> tuple[Common, Common]:
            item: Common = {"name": str(value), "hidden": "x"}
            return item, item

        @serialize("choice", output=Common | Alternate)
        def choice_output(value: int) -> Common | Alternate:
            return {"name": str(value), "count": value}

    assert Payload(pair=1, choice=2).to_dict(include={"pair": {"name": True}, "choice": {"name": True}}) == {
        "pair": ({"name": "1"}, {"name": "1"}),
        "choice": {"name": "2"},
    }


def test_plain_hook_remains_opaque_when_declared_hooks_are_available() -> None:
    class Mixed(Spec):
        plain: int
        declared: int

        @serialize("plain")
        def plain_output(value: int) -> dict[str, int]:
            return {"nested": value}

        @serialize("declared", output=dict[str, int])
        def declared_output(value: int) -> dict[str, int]:
            return {"nested": value}

    value = Mixed(plain=1, declared=2)
    assert value.to_dict(include={"plain": True}) == {"plain": {"nested": 1}}
    with pytest.raises(ValueError, match="cannot descend through serializer"):
        value.to_dict(include={"plain": {"nested": True}})
    with pytest.raises(ValueError, match="scalar field"):
        value.to_dict(include={"declared": {"nested": True}})
