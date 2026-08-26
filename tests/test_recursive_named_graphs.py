from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from typing import Annotated, Literal, NotRequired, TypedDict

import pytest
from hypothesis import given, strategies as st

from talea import Contract, Discriminator, Sensitive, Spec, create_spec
from talea.declaration.policies import (
    schema_contains_named_reference,
    schema_contains_tagged_union,
    schema_values_are_immutable,
)
from talea.input.value import compile_value_input
from talea.introspection import inspect_contract
from talea.schema import (
    AliasSchema,
    AnnotationResolutionError,
    NamedReferenceSchema,
    NamedSchemaIdentity,
    Schema,
    TypedDictSchema,
    resolve_annotation,
)
from talea.schema.references import _NamedSchemaTarget
from talea.serialization import SerializationError
from talea.serialization.emission import compile_value_projector
from talea.validation import ValidationError, compile_validator

type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
type MutualA = int | list[MutualB]
type MutualB = str | dict[str, MutualA]
type Tree[T] = T | list[Tree[T]]


class Node(TypedDict):
    value: int
    children: list[Node]


class OptionalNode(TypedDict):
    value: int
    child: NotRequired[OptionalNode]


class MutualNodeA(TypedDict):
    b: MutualNodeB | None


class MutualNodeB(TypedDict):
    a: MutualNodeA | None


class MixedNode(TypedDict):
    value: int
    children: NodeList


type NodeList = list[MixedNode]


class GenericNode[T](TypedDict):
    value: T
    children: list[GenericNode[T]]


class LiteralNode(TypedDict):
    kind: Literal["literal"]
    value: Annotated[int, Sensitive()]


class AddNode(TypedDict):
    kind: Literal["add"]
    left: Expr
    right: Expr


type Expr = Annotated[LiteralNode | AddNode, Discriminator("kind")]


class SecretNode(TypedDict):
    secret: Annotated[str, Sensitive()]
    children: list[SecretNode]


type SecretTree = Annotated[str | list[SecretTree], Sensitive()]
type BoundaryA = BoundaryB | int
type BoundaryB = str | BoundaryA
type RecursiveKey = int | tuple[RecursiveKey, ...]
type BrokenAlias = list[BrokenAlias] | object


class BrokenTypedDict(TypedDict):
    child: NotRequired[BrokenTypedDict]
    unsupported: object


_JSON_VALUES = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=12)),
    lambda child: st.one_of(
        st.lists(child, max_size=3),
        st.dictionaries(st.text(max_size=8), child, max_size=3),
    ),
    max_leaves=20,
)
_NODE_VALUES = st.recursive(
    st.integers().map(lambda value: {"value": value, "children": []}),
    lambda child: st.builds(
        lambda value, children: {"value": value, "children": children},
        st.integers(),
        st.lists(child, max_size=3),
    ),
    max_leaves=20,
)
_EXPRESSION_VALUES = st.recursive(
    st.integers().map(lambda value: {"kind": "literal", "value": value}),
    lambda child: st.builds(
        lambda left, right: {"kind": "add", "left": left, "right": right},
        child,
        child,
    ),
    max_leaves=20,
)


def _references(schema: Schema) -> tuple[NamedReferenceSchema, ...]:
    if isinstance(schema, NamedReferenceSchema):
        return (schema,)
    if isinstance(schema, AliasSchema):
        return _references(schema.schema)
    if isinstance(schema, TypedDictSchema):
        return tuple(reference for field in schema.fields for reference in _references(field.schema))
    item = getattr(schema, "item", None)
    if item is not None:
        return _references(item)
    key = getattr(schema, "key", None)
    value = getattr(schema, "value", None)
    if key is not None and value is not None:
        return (*_references(key), *_references(value))
    options = getattr(schema, "options", ())
    if options:
        return tuple(reference for option in options for reference in _references(option))
    branches = getattr(schema, "branches", ())
    return tuple(reference for branch in branches for reference in _references(branch.schema))


def test_self_and_mutually_recursive_aliases_use_finite_canonical_back_edges() -> None:
    json_schema = resolve_annotation(JSONValue)
    mutual_schema = resolve_annotation(MutualA)

    assert isinstance(json_schema, AliasSchema)
    assert len(_references(json_schema)) == 2
    assert all(reference.target is json_schema for reference in _references(json_schema))
    assert Contract(JSONValue).validate({"items": [1, "two", None]}) == {"items": [1, "two", None]}
    assert Contract(MutualA).validate([{"leaf": 1}]) == [{"leaf": 1}]
    assert _references(mutual_schema)
    assert Contract(BoundaryA).from_python("value") == "value"


def test_recursive_alias_contract_supports_every_boundary_and_exact_errors() -> None:
    contract = Contract(JSONValue)
    value = {"items": [1, {"nested": [True, None]}]}

    assert contract.validate(value) is value
    assert contract.from_python(value) == value
    assert contract.from_json('{"items":[1,{"nested":[true,null]}]}') == value
    assert contract.to_python(value) == value
    assert contract.to_python(value) is not value
    assert contract.to_json(value) == '{"items":[1,{"nested":[true,null]}]}'

    with pytest.raises(ValidationError) as captured:
        contract.validate({"items": [1, {"nested": [object()]}]})
    assert captured.value.location == ()
    assert captured.value.errors()[0]["code"] == "union"


@given(_JSON_VALUES, _NODE_VALUES, _EXPRESSION_VALUES)
def test_bounded_recursive_named_values_round_trip(
    json_value: object,
    node: object,
    expression: object,
) -> None:
    for annotation, value in ((JSONValue, json_value), (Node, node), (Expr, expression)):
        contract = Contract(annotation)
        assert contract.validate(value) is value
        assert contract.from_json(contract.to_json(value)) == value


def test_recursive_typed_dict_and_mutual_typed_dict_round_trip() -> None:
    contract = Contract(Node)
    value = {"value": 1, "children": [{"value": 2, "children": []}]}

    assert contract.validate(value) is value
    assert contract.from_python(value) == value
    assert contract.from_json('{"value":1,"children":[{"value":2,"children":[]}]}') == value
    assert contract.to_python(value) == value
    assert contract.to_python(value)["children"] is not value["children"]
    assert contract.to_json(value) == '{"value":1,"children":[{"value":2,"children":[]}]}'
    assert Contract(OptionalNode).validate({"value": 1, "child": {"value": 2}})["value"] == 1
    assert Contract(MutualNodeA).validate({"b": {"a": None}}) == {"b": {"a": None}}

    with pytest.raises(ValidationError) as captured:
        contract.validate({"value": 1, "children": [{"value": 2, "children": [{"value": "bad", "children": []}]}]})
    assert captured.value.location == ("children", 0, "children", 0, "value")


def test_mixed_and_generic_recursive_specializations_are_concrete() -> None:
    mixed = Contract(NodeList)
    generic = Contract(Tree[int])
    generic_typed_dict = Contract(GenericNode[int])

    assert mixed.from_json('[{"value":1,"children":[]}]') == [{"value": 1, "children": []}]
    assert generic.from_json("[1,[2,[3]]]") == [1, [2, [3]]]
    assert generic_typed_dict.from_python({"value": 1, "children": [{"value": 2, "children": []}]})["value"] == 1
    with pytest.raises(ValidationError):
        generic.validate(["wrong"])
    with pytest.raises(ValidationError):
        generic_typed_dict.validate({"value": "wrong", "children": []})


def test_recursive_tagged_typed_dict_ast_selects_direct_branches() -> None:
    contract = Contract(Expr)
    value = {
        "kind": "add",
        "left": {"kind": "literal", "value": 1},
        "right": {"kind": "literal", "value": 2},
    }

    assert contract.validate(value) is value
    assert contract.from_python(value) == value
    assert contract.from_json(contract.to_json(value)) == value
    with pytest.raises(ValidationError) as captured:
        contract.from_python({"kind": "add", "left": {"kind": "unknown"}, "right": value["right"]})
    assert captured.value.errors()[0]["location"] == ["left", "kind"]
    assert captured.value.errors()[0]["code"] == "discriminator_unknown"


def test_specs_and_dynamic_specs_consume_recursive_graph_truth_normally() -> None:
    class Document(Spec):
        root: Expr

    DynamicDocument = create_spec("DynamicDocument", {"root": JSONValue})
    expression = {"kind": "literal", "value": 1}

    assert Document.from_mapping({"root": expression}).to_json() == '{"root":{"kind":"literal","value":1}}'
    assert DynamicDocument.from_json('{"root":{"value":[1,2]}}').to_dict() == {"root": {"value": [1, 2]}}


def test_runtime_cycle_policy_matches_recursive_spec_policy() -> None:
    contract = Contract(JSONValue)
    value: list[object] = []
    value.append(value)

    assert contract.validate(value) is value
    with pytest.raises(ValidationError) as input_error:
        contract.from_python(value)
    assert input_error.value.code == "cycle"
    assert input_error.value.location == (0,)
    with pytest.raises(SerializationError) as output_error:
        contract.to_python(value)
    assert output_error.value.location == (0,)


@pytest.mark.parametrize("annotation", [SecretTree, SecretNode, Expr])
def test_sensitive_recursive_failures_do_not_retain_or_render_secrets(annotation: object) -> None:
    sentinel = {"CAMPAIGN_15_SENTINEL": object()}
    if annotation is SecretTree:
        value = [[sentinel]]
    elif annotation is SecretNode:
        value = {"secret": "ok", "children": [{"secret": sentinel, "children": []}]}
    else:
        value = {
            "kind": "add",
            "left": {"kind": "literal", "value": sentinel},
            "right": {"kind": "literal", "value": 1},
        }

    with pytest.raises(ValidationError) as captured:
        Contract(annotation).validate(value)
    rendered = f"{captured.value!s}{captured.value.errors()!r}{captured.value.__cause__!r}"
    assert "CAMPAIGN_15_SENTINEL" not in rendered


def test_introspection_is_finite_immutable_and_retains_declaration_identity() -> None:
    schema = inspect_contract(Contract(JSONValue)).schema
    references = _references(schema)

    assert isinstance(schema, AliasSchema)
    assert schema.identity is not None
    assert schema.identity.declaration is JSONValue
    assert references[0].identity is schema.identity
    with pytest.raises(FrozenInstanceError):
        references[0].identity.name = "changed"  # type: ignore[misc]


def test_same_display_names_have_distinct_declaration_identity() -> None:
    def declarations() -> tuple[object, object]:
        type SameName = int | list[SameName]
        first = SameName
        type SameName = str | list[SameName]
        return first, SameName

    first, second = declarations()
    first_schema = resolve_annotation(first)
    second_schema = resolve_annotation(second)

    assert isinstance(first_schema, AliasSchema)
    assert isinstance(second_schema, AliasSchema)
    assert first_schema.identity != second_schema.identity


def test_concurrent_first_resolution_publishes_only_complete_local_graphs() -> None:
    def construct(_: int) -> tuple[object, object]:
        contract = Contract(Tree[int])
        return contract.validate([1, [2]]), inspect_contract(contract).schema

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(construct, range(32)))

    assert all(value == [1, [2]] for value, _ in results)
    assert all(_references(schema) for _, schema in results)


def test_recursive_trust_classification_terminates_and_preserves_mutability_truth() -> None:
    type ImmutableTree = int | tuple[ImmutableTree, ...]

    assert schema_values_are_immutable(resolve_annotation(ImmutableTree))
    assert not schema_values_are_immutable(resolve_annotation(JSONValue))
    assert not schema_values_are_immutable(resolve_annotation(Node))
    assert not schema_values_are_immutable(_references(resolve_annotation(Node))[0])

    class ImmutableDocument(Spec):
        root: ImmutableTree

    class MutableDocument(Spec):
        root: Node

    assert inspect_contract(Contract(ImmutableTree)).schema.identity is not None  # type: ignore[union-attr]
    assert vars(ImmutableDocument)["__talea_declaration__"].artifacts().schema.instances_are_permanently_trusted
    assert not vars(MutableDocument)["__talea_declaration__"].artifacts().schema.instances_are_permanently_trusted


def test_nonrecursive_alias_and_typed_dict_bind_no_recursive_runtime_helpers() -> None:
    type Identifiers = list[int]

    class Payload(TypedDict):
        identifiers: list[int]

    for schema in (resolve_annotation(Identifiers), resolve_annotation(Payload)):
        operations = (
            compile_validator(schema),
            compile_value_input(schema, "mapping", "Payload"),
            compile_value_projector(schema, "python", True),
        )
        assert not _references(schema)
        assert all(
            not any(type(value).__name__.startswith("_Named") for value in operation.__globals__.values())
            for operation in operations
        )


def test_named_graph_policy_branches_are_finite_for_every_container_shape() -> None:
    type FixedRecursive = int | tuple[FixedRecursive, str]

    expression_reference = _references(resolve_annotation(Expr))[0]
    assert schema_contains_tagged_union(expression_reference)
    assert not schema_contains_tagged_union(expression_reference, frozenset({expression_reference.identity}))
    assert schema_contains_named_reference(resolve_annotation(RecursiveKey))
    assert schema_contains_named_reference(resolve_annotation(FixedRecursive))
    compile_value_projector(resolve_annotation(dict[RecursiveKey, str]), "python", True)


def test_named_target_rejects_stale_access_and_conflicting_publication() -> None:
    identity = NamedSchemaIdentity("alias", "Target", __name__, object())
    target = _NamedSchemaTarget(identity)
    schema = resolve_annotation(int)

    with pytest.raises(RuntimeError, match="not finalized"):
        _ = target.schema
    target.finalize(schema)
    target.finalize(schema)
    with pytest.raises(RuntimeError, match="finalized twice"):
        target.finalize(resolve_annotation(str))


@pytest.mark.parametrize("annotation", [BrokenAlias, BrokenTypedDict])
def test_failed_named_resolution_discards_its_incomplete_local_target(annotation: object) -> None:
    with pytest.raises(AnnotationResolutionError):
        resolve_annotation(annotation)
