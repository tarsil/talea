import json
from dataclasses import dataclass, field
from typing import Annotated, Literal, cast

import pytest
from hypothesis import given, strategies as st
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi

from talea import (
    Alias,
    Contract,
    Description,
    Discriminator,
    Examples,
    Ge,
    ReadOnly,
    Sensitive,
    Spec,
    derive_spec,
)
from talea.introspection import inspect_contract
from talea.json_schema.projection import OPENAPI_DIALECT
from talea.schema import DataclassSchema
from talea.validation import ValidationError


def _definition(document: dict[str, object], name: str) -> dict[str, object]:
    definitions = document["$defs"]
    assert isinstance(definitions, dict)
    definition = definitions[name]
    assert isinstance(definition, dict)
    return definition


def _openapi_document(fragment: dict[str, object]) -> dict[str, object]:
    components = fragment["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    return {
        "openapi": "3.1.2",
        "jsonSchemaDialect": OPENAPI_DIALECT,
        "info": {"title": "Migration projection", "version": "1"},
        "paths": {},
        "components": {"schemas": {**schemas, "Root": fragment["schema"]}},
    }


class MigratedUser(Spec):
    user_id: Annotated[int, Alias("userId", legacy=("id", "user_id")), Ge(1), Description("User identifier")]
    nickname: Annotated[str, Alias("displayName", legacy=("name",)), Examples("Ada")] = "anonymous"
    stable: bool = True


@pytest.mark.parametrize("name", ["userId", "id", "user_id"])
def test_required_spec_input_accepts_each_name_and_projects_one_value_contract(name: str) -> None:
    document = MigratedUser.json_schema(mode="input")
    Draft202012Validator.check_schema(document)
    validator = Draft202012Validator(document)
    validator.validate({name: 1})
    schema = _definition(document, "MigratedUser")
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties[name]["minimum"] == 1
    assert properties[name]["description"] == "User identifier"
    assert "deprecated" not in properties[name]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"userId": 1, "id": 1},
        {"userId": 1, "id": 2},
        {"id": 1, "user_id": 1},
        {"id": 1, "user_id": 2},
        {"userId": 0},
        {"id": "1"},
        {"userId": 1, "unexpected": True},
    ],
)
def test_spec_input_schema_rejects_missing_conflicting_invalid_and_unknown_input(payload: dict[str, object]) -> None:
    assert not Draft202012Validator(MigratedUser.json_schema()).is_valid(payload)
    with pytest.raises(ValidationError):
        MigratedUser.from_mapping(payload)


def test_optional_migrated_field_allows_absence_or_one_name_and_keeps_default_canonical() -> None:
    document = MigratedUser.json_schema()
    validator = Draft202012Validator(document)
    for payload in ({"userId": 1}, {"userId": 1, "displayName": "Ada"}, {"userId": 1, "name": "Ada"}):
        validator.validate(payload)
    assert not validator.is_valid({"userId": 1, "displayName": "Ada", "name": "Ada"})

    properties = _definition(document, "MigratedUser")["properties"]
    assert properties["displayName"]["default"] == "anonymous"
    assert "default" not in properties["name"]
    assert properties["name"]["examples"] == ["Ada"]


def test_output_schema_is_current_only_and_zero_migration_shape_is_unchanged() -> None:
    output = _definition(MigratedUser.json_schema(mode="output"), "MigratedUser")
    assert list(output["properties"]) == ["userId", "displayName", "stable"]
    assert "allOf" not in output

    class Plain(Spec):
        value: int

    assert _definition(Plain.json_schema(), "Plain") == {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "additionalProperties": False,
        "required": ["value"],
    }


def test_multiple_migrated_fields_compose_linearly_without_whole_object_variants() -> None:
    class Pair(Spec):
        first: Annotated[int, Alias("firstNow", legacy=("firstOld", "firstOlder"))]
        second: Annotated[int, Alias("secondNow", legacy=("secondOld", "secondOlder"))]

    schema = _definition(Pair.json_schema(), "Pair")
    assert len(schema["properties"]) == 6
    assert len(schema["allOf"]) == 2
    assert [len(constraint["oneOf"]) for constraint in schema["allOf"]] == [3, 3]
    assert "required" not in schema


def test_partial_directional_include_exclude_generic_and_recursive_specs_retain_actual_field_truth() -> None:
    class Source(Spec):
        migrated: Annotated[int, Alias("current", legacy=("old",))]
        removed: int

    Patch = derive_spec(Source, partial=True, mode="input", name="SourcePatch")
    Input = derive_spec(Source, mode="input", name="SourceInput")
    Output = derive_spec(Source, mode="output", name="SourceOutput")
    Included = derive_spec(Source, include=("migrated",), name="SourceIncluded")
    Excluded = derive_spec(Source, exclude=("migrated",), name="SourceExcluded")

    patch = _definition(Patch.json_schema(), "SourcePatch")
    Draft202012Validator(Patch.json_schema()).validate({"old": 1})
    Draft202012Validator(Patch.json_schema()).validate({})
    assert len(patch["allOf"][0]["oneOf"]) == 3
    assert "old" in _definition(Input.json_schema(mode="input"), "SourceInput")["properties"]
    assert "old" not in _definition(Output.json_schema(mode="output"), "SourceOutput")["properties"]
    assert "old" in _definition(Included.json_schema(), "SourceIncluded")["properties"]
    assert "old" not in _definition(Excluded.json_schema(), "SourceExcluded")["properties"]

    class Box[T](Spec):
        value: Annotated[T, Alias("valueNow", legacy=("valueOld",))]

    assert "valueOld" in _definition(Box[int].json_schema(), "Box[int]")["properties"]

    class Node(Spec):
        value: Annotated[int, Alias("valueNow", legacy=("valueOld",))]
        children: list[Node]

    recursive = Node.json_schema()
    assert tuple(recursive["$defs"]) == ("Node",)
    assert _definition(recursive, "Node")["properties"]["children"]["items"] == {"$ref": "#/$defs/Node"}


def test_dataclass_input_uses_retained_names_defaults_and_init_policy_while_output_is_current_only() -> None:
    factory_calls = 0

    def default_labels() -> list[str]:
        nonlocal factory_calls
        factory_calls += 1
        return []

    @dataclass
    class Record:
        identifier: Annotated[int, Alias("recordId", legacy=("id", "record_id"))]
        status: Annotated[str, Alias("statusNow", legacy=("statusOld",))] = "new"
        labels: list[str] = field(default_factory=default_labels)
        derived: int = field(init=False, default=1)

    contract = Contract(Record)
    document = contract.json_schema()
    validator = Draft202012Validator(document)
    for name in ("recordId", "id", "record_id"):
        validator.validate({name: 1})
    assert not validator.is_valid({"recordId": 1, "id": 1})
    schema = _definition(document, "Record")
    assert "derived" not in schema["properties"]
    assert schema["properties"]["statusNow"]["default"] == "new"
    assert "default" not in schema["properties"]["statusOld"]
    output = _definition(contract.json_schema(mode="output"), "Record")
    assert list(output["properties"]) == ["recordId", "statusNow", "labels", "derived"]
    assert output["properties"]["derived"]["readOnly"] is True
    retained = cast(DataclassSchema, inspect_contract(contract).schema).fields[0]
    assert retained.legacy_names == ("id", "record_id")
    assert retained.accepted_input_names == ("recordId", "id", "record_id")
    assert factory_calls == 0


@dataclass
class MigratedDataNode:
    value: Annotated[int, Alias("valueNow", legacy=("valueOld",))]
    children: list[MigratedDataNode] = field(default_factory=list)


def test_recursive_dataclass_migration_reuses_one_finite_definition() -> None:
    document = Contract(MigratedDataNode).json_schema()
    assert tuple(document["$defs"]) == ("MigratedDataNode",)
    schema = _definition(document, "MigratedDataNode")
    assert "valueOld" in schema["properties"]
    assert schema["properties"]["children"]["items"] == {"$ref": "#/$defs/MigratedDataNode"}
    Draft202012Validator(document).validate({"valueOld": 1, "children": []})


class CreatedEvent(Spec):
    kind: Annotated[Literal["created"], Alias("eventType", legacy=("type", "kind"))]
    value: int


class DeletedEvent(Spec):
    kind: Annotated[Literal["deleted"], Alias("eventType", legacy=("type", "kind"))]
    value: int


type MigratedEvent = Annotated[CreatedEvent | DeletedEvent, Discriminator("kind")]


class TreeLeaf(Spec):
    kind: Annotated[Literal["leaf"], Alias("nodeType", legacy=("type",))]
    value: int


class TreeBranch(Spec):
    kind: Annotated[Literal["branch"], Alias("nodeType", legacy=("type",))]
    children: list[MigratedTree]


type MigratedTree = Annotated[TreeLeaf | TreeBranch, Discriminator("kind")]


def test_tagged_input_projects_legacy_discriminator_keys_and_openapi_keeps_canonical_hint() -> None:
    contract = Contract(MigratedEvent)
    input_document = contract.json_schema()
    validator = Draft202012Validator(input_document)
    for name in ("eventType", "type", "kind"):
        validator.validate({name: "created", "value": 1})
    assert not validator.is_valid({"eventType": "created", "type": "created", "value": 1})
    assert not validator.is_valid({"kind": "unknown", "value": 1})
    assert not validator.is_valid({"value": 1})

    fragment = contract.openapi_schema()
    tagged = fragment["components"]["schemas"]["MigratedEvent"]
    assert tagged["discriminator"]["propertyName"] == "eventType"
    assert tagged["discriminator"]["mapping"] == {
        "created": "#/components/schemas/CreatedEvent",
        "deleted": "#/components/schemas/DeletedEvent",
    }
    validate_openapi(_openapi_document(fragment))

    output = contract.json_schema(mode="output")
    for branch_name in ("CreatedEvent", "DeletedEvent"):
        assert list(_definition(output, branch_name)["properties"]) == ["eventType", "value"]


def test_recursive_tagged_migration_keeps_finite_branch_definitions() -> None:
    document = Contract(MigratedTree).json_schema()
    assert tuple(document["$defs"]) == ("MigratedTree", "TreeBranch", "TreeLeaf")
    branch = _definition(document, "TreeBranch")
    assert "type" in branch["properties"]
    assert branch["properties"]["children"]["items"] == {"$ref": "#/$defs/MigratedTree"}
    Draft202012Validator(document).validate({"type": "branch", "children": [{"nodeType": "leaf", "value": 1}]})


@given(
    st.dictionaries(
        st.sampled_from(["userId", "id", "user_id", "displayName", "name", "stable", "unexpected"]),
        st.one_of(st.integers(min_value=-1, max_value=3), st.text(max_size=3), st.booleans()),
        max_size=5,
    )
)
def test_runtime_and_draft_2020_12_agree_for_bounded_migrated_payloads(payload: dict[str, object]) -> None:
    schema_accepts = Draft202012Validator(MigratedUser.json_schema()).is_valid(payload)
    try:
        MigratedUser.from_mapping(payload)
    except ValidationError:
        runtime_accepts = False
    else:
        runtime_accepts = True
    assert runtime_accepts is schema_accepts
    try:
        MigratedUser.from_json(json.dumps(payload))
    except ValidationError:
        json_accepts = False
    else:
        json_accepts = True
    assert json_accepts is schema_accepts


def test_hostile_legacy_names_are_inert_deterministic_and_returns_are_fresh() -> None:
    hostile = ("雪", "quote'\nname", "a/b", "a~b", "__import__('os').system('false')")

    class Hostile(Spec):
        value: Annotated[int, Alias("current", legacy=hostile), ReadOnly()]
        secret: Annotated[
            str,
            Alias("token", legacy=("oldToken",)),
            Sensitive(),
            Examples("example-hidden"),
        ] = "hidden"

    first = Hostile.json_schema()
    second = Hostile.json_schema()
    assert json.dumps(first, ensure_ascii=False, sort_keys=False) == json.dumps(
        second, ensure_ascii=False, sort_keys=False
    )
    properties = _definition(first, "Hostile")["properties"]
    assert all(name in properties for name in hostile)
    assert all(properties[name]["readOnly"] is True for name in ("current", *hostile))
    assert "hidden" not in json.dumps(first)
    assert "example-hidden" not in json.dumps(first)
    Draft202012Validator(first).validate({"a/b": 1})

    properties["雪"]["type"] = "string"
    fresh_properties = _definition(Hostile.json_schema(), "Hostile")["properties"]
    assert fresh_properties["雪"]["type"] == "integer"
