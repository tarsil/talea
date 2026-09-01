import json
from dataclasses import FrozenInstanceError, dataclass
from typing import Annotated, NamedTuple, TypedDict

import pytest

from talea import (
    Contract,
    ErrorTree,
    Representation,
    Sensitive,
    Spec,
    ValidationError,
    validate_call,
)
from talea.settings import Settings


def _at(tree: ErrorTree, *location: object) -> ErrorTree:
    node = tree
    for segment in location:
        node = node.children[segment]  # type: ignore[index]
    return node


def test_tree_represents_root_parent_leaf_multiplicity_and_hostile_names() -> None:
    failures = (
        ValidationError("root", "bad", ()),
        ValidationError("mapping", "bad", ("payload",)),
        ValidationError("str", 1, ("payload", "name")),
        ValidationError("minimum", "x", ("payload", "name")),
        ValidationError("int", "bad", ("items", 3, "price")),
        *(
            ValidationError("int", "bad", (name,))
            for name in (
                "errors",
                "children",
                "__errors__",
                "$root",
                "__proto__",
                "constructor",
                'quotes"',
                "new\nline",
            )
        ),
    )
    error = ValidationError._aggregate(failures, title="Payload")

    tree = error.error_tree()

    assert len(tree.errors) == 1
    assert tree.errors[0]["location"] == []
    assert len(_at(tree, "payload").errors) == 1
    assert [item["expected"] for item in _at(tree, "payload", "name").errors] == [
        "str",
        "minimum",
    ]
    assert _at(tree, "items", 3, "price").errors[0]["location"] == ["items", 3, "price"]
    assert list(tree.children) == [
        "payload",
        "items",
        "errors",
        "children",
        "__errors__",
        "$root",
        "__proto__",
        "constructor",
        'quotes"',
        "new\nline",
    ]

    data = tree.to_dict()
    assert json.loads(json.dumps(data)) == data
    items = next(child for child in data["children"] if child["key"] == "items")
    index = items["node"]["children"][0]
    assert index["key"] == 3
    assert type(index["key"]) is int


def test_tree_is_fresh_read_only_and_detached_from_error_data() -> None:
    error = ValidationError("int", "bad", ("value",))
    first = error.error_tree()
    second = error.error_tree()

    assert first is not second
    assert first.children["value"] is not second.children["value"]
    projected = first.children["value"].errors
    projected[0]["message"] = "changed"
    assert first.children["value"].errors[0]["message"] == "Expected int"
    assert error.errors()[0]["message"] == "Expected int"
    with pytest.raises(TypeError):
        first.children["other"] = second  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        first.children = {}  # type: ignore[misc]


def test_tree_handles_deep_paths_without_recursive_construction() -> None:
    location = tuple(range(2_000))
    tree = ValidationError("int", "bad", location).error_tree()

    node = tree
    for segment in location:
        node = node.children[segment]
    assert node.errors[0]["location"] == list(location)


def test_existing_structural_producers_need_no_tree_specific_logic() -> None:
    class Position(NamedTuple):
        latitude: float
        longitude: float

    class Payload(TypedDict):
        count: int

    @dataclass
    class Event:
        payload: Payload

    class Envelope(Spec):
        events: list[Event]

    invalid = Envelope(events=[])
    invalid.events.append(Event(payload={"count": "bad"}))  # type: ignore[typeddict-item]

    cases = (
        (Contract(Envelope), invalid, ("events", 0, "payload", "count")),
        (Contract(Position), Position(1.0, "bad"), (1,)),  # type: ignore[arg-type]
        (Contract(Payload), {"count": "bad"}, ("count",)),  # type: ignore[dict-item]
    )
    for contract, value, location in cases:
        with pytest.raises(ValidationError) as raised:
            contract.validate(value)  # type: ignore[arg-type]
        node = _at(raised.value.error_tree(), *location)
        assert node.errors[0]["location"] == list(location)


def test_callable_incremental_jsonl_and_settings_prefixes_project_directly() -> None:
    calls = 0

    @validate_call
    def calculate(values: list[int]) -> int:
        nonlocal calls
        calls += 1
        return "bad"  # type: ignore[invalid-return-type]

    with pytest.raises(ValidationError) as argument:
        calculate(["bad"])  # type: ignore[list-item]
    assert _at(argument.value.error_tree(), "values", 0).errors[0]["location"] == ["values", 0]
    assert calls == 0

    with pytest.raises(ValidationError) as returned:
        calculate([1])
    assert _at(returned.value.error_tree(), "return").errors[0]["location"] == ["return"]
    assert calls == 1

    with pytest.raises(ValidationError) as incremental:
        next(Contract(int).iter_validate(("bad",)))  # type: ignore[arg-type]
    assert _at(incremental.value.error_tree(), 0).errors[0]["location"] == [0]

    with pytest.raises(ValidationError) as jsonl:
        next(Contract(int).iter_jsonl(('"bad"',)))
    assert _at(jsonl.value.error_tree(), 0).errors[0]["location"] == [0]

    class Application(Spec):
        port: int

    with pytest.raises(ValidationError) as settings:
        Settings(Application, prefix="APP_").load(environment={"APP_PORT": "bad"})
    assert _at(settings.value.error_tree(), "port").errors[0]["location"] == ["port"]


def test_sensitive_projection_uses_only_pre_redacted_facts() -> None:
    secret = "never-project-this-secret"

    class Credentials(Spec):
        token: Annotated[int, Sensitive()]

    with pytest.raises(ValidationError) as raised:
        Credentials.from_mapping({"token": secret})

    tree = raised.value.error_tree()
    detail = tree.children["token"].errors[0]
    assert detail == raised.value.errors()[0]
    assert detail["input"] == "<redacted>"
    assert secret not in repr(tree.to_dict())


def test_sensitive_producers_all_share_the_same_redacted_tree_boundary() -> None:
    secret = "sensitive-tree-evidence"
    type SecretInt = Annotated[int, Sensitive()]

    class SecretSpec(Spec):
        token: SecretInt

    @dataclass
    class SecretDataclass:
        token: SecretInt

    class SecretTuple(NamedTuple):
        token: SecretInt

    @dataclass
    class Represented:
        value: int

    def reject(value: str) -> Represented:
        raise ValueError(f"rejected {value}")

    represented = Contract[Represented](Annotated[Represented, Representation(input=str, load=reject), Sensitive()])

    @validate_call
    def accept(token: SecretInt) -> int:
        return token

    @validate_call
    def produce() -> SecretInt:
        return secret  # type: ignore[invalid-return-type]

    class SecretSettings(Spec):
        token: SecretInt

    operations = (
        lambda: SecretSpec.from_mapping({"token": secret}),
        lambda: Contract(SecretDataclass).from_python({"token": secret}),
        lambda: Contract(SecretTuple).from_python([secret]),
        lambda: represented.from_python(secret),
        lambda: accept(secret),  # type: ignore[invalid-argument-type]
        produce,
        lambda: Settings(SecretSettings).load({"token": secret}),
        lambda: next(Contract[int](SecretInt).iter_validate((secret,))),
        lambda: next(Contract[int](SecretInt).iter_jsonl((json.dumps(secret),))),
    )

    for operation in operations:
        with pytest.raises(ValidationError) as raised:
            operation()
        error = raised.value
        flat = error.errors()
        node = _at(error.error_tree(), *flat[0]["location"])
        assert node.errors == tuple(flat)
        assert flat[0]["input"] == "<redacted>"
        assert secret not in repr(error.error_tree().to_dict())


def test_union_and_truncation_remain_canonical_node_facts() -> None:
    union = ValidationError.union("int | str", object(), ("choice",), ("int", "str"))
    truncated = ValidationError._aggregate(
        (union, ValidationError("int", "bad", ("later",))),
        title="Payload",
        truncated=True,
    )

    tree = truncated.error_tree()
    union_detail = tree.children["choice"].errors[0]
    assert union_detail["code"] == "union"
    assert [branch["label"] for branch in union_detail["branches"]] == ["int", "str"]
    assert list(tree.children) == ["choice", "later"]
    assert truncated.truncated is True
    assert "truncated" not in tree.to_dict()
