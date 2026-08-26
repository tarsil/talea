from __future__ import annotations

from collections import UserDict
from copy import copy
from typing import Annotated, Required, TypedDict

import pytest

from talea import Contract, Ge, Spec
from talea.validation import ValidationError


class User(Spec):
    name: str


class UserPayload(TypedDict):
    user: Required[User]
    score: Annotated[int, Ge(0)]


class Page[T](Spec):
    items: list[T]


class Node(Spec):
    value: int
    children: list[Node]


def test_contract_validates_strict_root_values_with_retained_identity() -> None:
    contract = Contract[int](int)

    assert contract.annotation is int
    assert contract.validate(1) == 1
    with pytest.raises(ValidationError) as captured:
        contract.validate(True)

    assert captured.value.errors()[0]["location"] == []
    assert captured.value.errors()[0]["code"] == "type"


def test_contract_supports_containers_constraints_and_literal_roots() -> None:
    values = Contract[list[int]](list[int])
    positive = Contract[Annotated[int, Ge(0)]](Annotated[int, Ge(0)])

    source = [1, 2]
    assert values.validate(source) is source
    assert positive.validate(0) == 0

    with pytest.raises(ValidationError) as captured:
        positive.validate(-1)
    assert captured.value.errors()[0]["code"] == "greater_than_or_equal"


def test_contract_external_python_constructs_nested_specs_without_wrapper_models() -> None:
    contract = Contract[list[User]](list[User])
    source = [UserDict({"name": "Ada"})]

    users = contract.from_python(source)

    assert isinstance(users, list)
    assert isinstance(users[0], User)
    assert users[0].name == "Ada"
    assert users is not source


def test_contract_typed_dict_boundaries_are_strict_and_detached() -> None:
    contract = Contract[UserPayload](UserPayload)
    source = UserDict({"user": UserDict({"name": "Ada"}), "score": 1})

    payload = contract.from_python(source)

    assert isinstance(payload, dict)
    assert isinstance(payload["user"], User)
    assert payload is not source
    assert contract.validate(payload) is payload

    with pytest.raises(ValidationError) as captured:
        contract.from_python({"user": {"name": "Ada"}, "score": 1, "extra": True})
    assert captured.value.errors()[0]["code"] == "unexpected"


def test_contract_json_uses_canonical_input_and_per_call_codec_semantics() -> None:
    contract = Contract[list[User]](list[User])
    calls = []

    def loads(data: str | bytes | bytearray) -> object:
        calls.append(data)
        return [{"name": "Ada"}]

    users = contract.from_json("external", loads=loads)

    assert users[0].name == "Ada"
    assert calls == ["external"]
    assert Contract[list[int]](list[int]).from_json("[1,2]") == [1, 2]


def test_contract_python_projection_validates_and_detaches_mutable_output() -> None:
    contract = Contract[list[User]](list[User])
    users = [User(name="Ada")]

    projected = contract.to_python(users)

    assert projected == [{"name": "Ada"}]
    assert projected is not users

    users.append("invalid")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        contract.to_python(users)


def test_contract_json_output_uses_projection_and_per_call_encoder() -> None:
    contract = Contract[UserPayload](UserPayload)
    value = {"user": User(name="Ada"), "score": 1}
    projected = []

    def dumps(candidate: object) -> bytes:
        projected.append(candidate)
        return b'{"accepted":true}'

    assert contract.to_json(value) == '{"user":{"name":"Ada"},"score":1}'
    assert contract.to_json(value, dumps=dumps) == '{"accepted":true}'
    assert projected == [{"user": {"name": "Ada"}, "score": 1}]


def test_contract_supports_generic_and_recursive_spec_annotations() -> None:
    page_contract = Contract[Page[User]](Page[User])
    nodes = Contract[list[Node]](list[Node])
    page = page_contract.from_python({"items": [{"name": "Ada"}]})
    root = Node(value=1, children=[Node(value=2, children=[])])

    assert isinstance(page.items[0], User)
    assert nodes.validate([root])[0] is root
    assert nodes.from_json('[{"value":1,"children":[]}]')[0].value == 1
    assert nodes.to_python([root]) == [{"value": 1, "children": [{"value": 2, "children": []}]}]


def test_contract_artifacts_compile_lazily_and_reuse_per_instance() -> None:
    contract = Contract[list[int]](list[int])
    artifacts = contract._artifacts

    assert artifacts.python_input is None
    assert artifacts.json_input is None
    assert artifacts.python_output is None
    assert artifacts.json_output is None

    contract.from_python([1])
    python_input = artifacts.python_input
    contract.from_python([2])
    assert artifacts.python_input is python_input
    assert artifacts.input_for("mapping") is python_input

    contract.from_json("[1]")
    json_input = artifacts.json_input
    contract.from_json("[2]")
    assert artifacts.json_input is json_input
    assert artifacts.input_for("json") is json_input

    contract.to_python([1])
    python_output = artifacts.python_output
    contract.to_python([2])
    assert artifacts.python_output is python_output
    assert artifacts.output_for("python") is python_output

    contract.to_json([1])
    json_output = artifacts.json_output
    contract.to_json([2])
    assert artifacts.json_output is json_output
    assert artifacts.output_for("json") is json_output


def test_contract_has_no_copy_or_global_canonicalization_magic() -> None:
    first = Contract[int](int)
    second = Contract[int](int)

    assert first is not second
    assert first._artifacts.validator is not second._artifacts.validator
    assert copy(first) is not first


def test_contract_annotation_property_is_read_only() -> None:
    contract = Contract[int](int)

    with pytest.raises(AttributeError):
        contract.annotation = str  # type: ignore[misc]
