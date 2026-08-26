from __future__ import annotations

from collections import UserDict
from typing import Annotated, NewType, NotRequired, ReadOnly, Required, TypedDict

import pytest

from talea import Ge, MinLength, Pattern, Spec
from talea.declaration.policies import schema_is_covariant_override
from talea.input.emission import schema_may_construct_spec, schema_needs_conversion
from talea.schema import (
    AliasSchema,
    AnnotationResolutionError,
    PrimitiveSchema,
    SequenceSchema,
    TypedDictField,
    TypedDictSchema,
    resolve_annotation,
)
from talea.serialization.emission import compile_value_projector
from talea.validation import ValidationError, compile_validator


class Identity(TypedDict):
    id: int


class Payload(Identity, total=False):
    score: Required[Annotated[int, Ge(0)]]
    trace_id: NotRequired[str]
    label: ReadOnly[str]


class Child(TypedDict):
    name: str


class ForwardPayload(TypedDict):
    child: Child


class Box[T](TypedDict):
    item: Required[T]


class RecursivePayload(TypedDict):
    child: NotRequired[RecursivePayload]


class IntegrationUser(Spec):
    name: str


class Message(TypedDict, total=False):
    user: Required[IntegrationUser]
    tags: list[str]


type PositiveId = Annotated[int, Ge(1)]
type Batch[T] = list[T]
type UserBatch = list[IntegrationUser]
type NamedText = Annotated[str, MinLength(1)]
UserId = NewType("UserId", int)


def test_typed_dict_resolution_retains_required_optional_and_read_only_truth() -> None:
    schema = resolve_annotation(Payload)

    assert schema == TypedDictSchema(
        "Payload",
        __name__,
        (
            TypedDictField("id", PrimitiveSchema("int"), True),
            TypedDictField("score", resolve_annotation(Annotated[int, Ge(0)]), True),
            TypedDictField("trace_id", PrimitiveSchema("str"), False),
            TypedDictField("label", PrimitiveSchema("str"), False, True),
        ),
    )


def test_typed_dict_strict_validation_preserves_exact_dict_identity() -> None:
    validator = compile_validator(resolve_annotation(Payload))
    value = {"id": 1, "score": 0}

    assert validator(value) is value

    with pytest.raises(ValidationError, match="Expected Payload"):
        validator(UserDict(value))


@pytest.mark.parametrize(
    ("value", "code", "location"),
    [
        ({"score": 1}, "missing", ["id"]),
        ({"id": 1}, "missing", ["score"]),
        ({"id": 1, "score": -1}, "greater_than_or_equal", ["score"]),
        ({"id": 1, "score": 1, "extra": True}, "unexpected", ["extra"]),
    ],
)
def test_typed_dict_validation_reports_structural_failures(
    value: object,
    code: str,
    location: list[object],
) -> None:
    validator = compile_validator(resolve_annotation(Payload))

    with pytest.raises(ValidationError) as captured:
        validator(value)

    assert captured.value.errors()[0]["code"] == code
    assert captured.value.errors()[0]["location"] == location


def test_typed_dict_supports_forward_nested_container_union_and_generics() -> None:
    forward = compile_validator(resolve_annotation(ForwardPayload))
    generic = compile_validator(resolve_annotation(Box[int]))
    collection = compile_validator(resolve_annotation(list[ForwardPayload | None]))

    assert forward({"child": {"name": "Ada"}})["child"]["name"] == "Ada"
    assert generic({"item": 1}) == {"item": 1}
    assert collection([{"child": {"name": "Ada"}}, None])[1] is None

    with pytest.raises(ValidationError):
        generic({"item": "1"})

    assert schema_needs_conversion(resolve_annotation(Payload), "mapping") is True


def test_typed_dict_projection_is_detached_and_json_shaped() -> None:
    schema = resolve_annotation(ForwardPayload)
    python_projector = compile_value_projector(schema, "python", True)
    json_projector = compile_value_projector(schema, "json", True)
    value = {"child": {"name": "Ada"}}

    python_value = python_projector(value, ())
    json_value = json_projector(value, ())

    assert python_value == value
    assert json_value == value
    assert python_value is not value
    assert python_value["child"] is not value["child"]


def test_typed_dict_composes_with_spec_python_json_and_output_boundaries() -> None:
    class Envelope(Spec):
        message: Message

    source = UserDict({"user": UserDict({"name": "Ada"}), "tags": ["admin"]})
    envelope = Envelope.from_mapping({"message": source})

    assert isinstance(envelope.message, dict)
    assert isinstance(envelope.message["user"], IntegrationUser)
    assert envelope.message is not source
    assert Envelope.from_json('{"message":{"user":{"name":"Ada"}}}').message["user"].name == "Ada"
    assert envelope.to_dict() == {"message": {"user": {"name": "Ada"}, "tags": ["admin"]}}
    assert envelope.to_json() == '{"message":{"user":{"name":"Ada"},"tags":["admin"]}}'


def test_modern_aliases_and_newtype_preserve_identity_without_runtime_dispatch() -> None:
    positive = resolve_annotation(PositiveId)
    batch = resolve_annotation(Batch[PositiveId])
    user_id = resolve_annotation(UserId)

    assert isinstance(positive, AliasSchema)
    assert positive.name == "PositiveId"
    assert isinstance(batch, AliasSchema)
    assert batch.schema == SequenceSchema("list", positive)
    assert user_id == AliasSchema("UserId", __name__, PrimitiveSchema("int"))

    assert compile_validator(positive)(1) == 1
    with pytest.raises(ValidationError) as captured:
        compile_validator(positive)(0)
    assert captured.value.errors()[0]["code"] == "greater_than_or_equal"


def test_aliases_and_typed_dicts_compose_as_spec_fields() -> None:
    class Record(Spec):
        id: UserId
        values: Batch[PositiveId]
        payload: Payload

    record = Record(id=1, values=[1, 2], payload={"id": 1, "score": 0})

    assert record.id == 1
    assert record.values == [1, 2]
    assert record.to_dict() == {
        "id": 1,
        "values": [1, 2],
        "payload": {"id": 1, "score": 0},
    }


def test_aliases_are_unwrapped_by_every_compile_time_policy() -> None:
    batch = resolve_annotation(Batch[int])
    positive = resolve_annotation(PositiveId)

    assert schema_needs_conversion(batch, "mapping") is False
    assert schema_may_construct_spec(batch) is False
    assert schema_is_covariant_override(positive, PrimitiveSchema("int"))
    assert compile_validator(resolve_annotation(PositiveId | str))(1) == 1

    class AliasBoundaries(Spec):
        labels: dict[PositiveId, str]

    class UnionBoundary(Spec):
        value: UserBatch | int

    users = UnionBoundary.from_json('{"value":[{"name":"Ada"}]}').value
    assert isinstance(users, list)
    assert users[0].name == "Ada"
    assert AliasBoundaries(labels={1: "one"}).to_dict() == {"labels": {1: "one"}}


def test_alias_constraints_apply_to_underlying_numeric_and_sized_truth() -> None:
    stricter = resolve_annotation(Annotated[PositiveId, Ge(2)])
    sized = resolve_annotation(Annotated[Batch[int], MinLength(1)])
    nested_sized = resolve_annotation(Annotated[NamedText, MinLength(2)])

    assert compile_validator(stricter)(2) == 2
    assert compile_validator(sized)([1]) == [1]
    assert compile_validator(nested_sized)("ok") == "ok"
    with pytest.raises(TypeError, match="Pattern does not apply to PositiveId"):
        resolve_annotation(Annotated[PositiveId, Pattern("x")])


def test_unspecialized_generic_alias_and_typed_dict_are_rejected() -> None:
    with pytest.raises(AnnotationResolutionError):
        resolve_annotation(Batch)
    with pytest.raises(AnnotationResolutionError):
        resolve_annotation(Box)


def test_typed_dict_union_uses_the_json_boundary_shape() -> None:
    class Boundary(Spec):
        value: Payload | int

    value = Boundary.from_json('{"value":{"id":1,"score":0}}').value

    assert value == {"id": 1, "score": 0}


def test_recursive_alias_and_typed_dict_cycles_fail_explicitly() -> None:
    type RecursiveAlias = int | list[RecursiveAlias]

    with pytest.raises(AnnotationResolutionError, match="RecursiveAlias"):
        resolve_annotation(RecursiveAlias)
    with pytest.raises(AnnotationResolutionError, match="RecursivePayload"):
        resolve_annotation(RecursivePayload)


def test_typed_dict_schema_rejects_duplicate_canonical_keys() -> None:
    field = TypedDictField("id", PrimitiveSchema("int"), True)

    with pytest.raises(ValueError, match="unique field names"):
        TypedDictSchema("Duplicate", __name__, (field, field))
