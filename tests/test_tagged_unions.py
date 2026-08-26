from collections.abc import Iterator, Mapping
from dataclasses import replace
from enum import Enum, IntEnum
from typing import Annotated, ForwardRef, Literal, NotRequired, TypedDict

import pytest

from talea import (
    Alias,
    Contract,
    Discriminator,
    ErrorCode,
    Sensitive,
    Spec,
    ValidationError,
    create_spec,
    field,
    serialize,
    transform,
)
from talea.declaration.policies import (
    schema_contains_tagged_union,
    schema_is_covariant_override,
    schema_values_are_immutable,
)
from talea.introspection import inspect_contract
from talea.schema import (
    AliasSchema,
    SpecReferenceSchema,
    TaggedUnionDeclarationError,
    TaggedUnionSchema,
)
from talea.serialization.emission import compile_value_projector
from talea.spec.generics import retain_referenced_namespace, validate_annotation_expression


class Card(Spec):
    kind: Annotated[Literal["card"], Alias("type")]
    number: str


class Bank(Spec):
    kind: Annotated[Literal["bank"], Alias("type")]
    iban: str


type Payment = Annotated[Card | Bank, Discriminator("type")]


def test_tagged_spec_contract_supports_every_boundary_without_branch_trial() -> None:
    contract = Contract[Payment](Payment)
    card = contract.from_python({"type": "card", "number": "123"})
    bank = contract.from_json('{"type":"bank","iban":"CH1"}')

    assert type(card) is Card
    assert type(bank) is Bank
    assert contract.validate(card) is card
    assert contract.to_python(card) == {"type": "card", "number": "123"}
    assert contract.to_json(bank) == '{"type":"bank","iban":"CH1"}'
    round_trip = contract.from_json(contract.to_json(card))
    assert type(round_trip) is Card
    assert (round_trip.kind, round_trip.number) == (card.kind, card.number)


def test_selected_branch_reports_its_structural_error_directly() -> None:
    with pytest.raises(ValidationError) as captured:
        Contract(Payment).from_python({"type": "card", "number": 1})

    assert captured.value.code is ErrorCode.TYPE
    assert captured.value.location == ("number",)
    assert "branches" not in captured.value.errors()[0]


def test_mapping_dispatch_runs_only_the_selected_branch_lifecycle() -> None:
    calls: list[str] = []

    class Selected(Spec):
        kind: Literal["selected"]
        values: list[int] = field(default_factory=list)

        @transform("values")
        def selected(value: object) -> object:
            calls.append("selected")
            return value

    class Rejected(Spec):
        kind: Literal["rejected"]
        values: list[int]

        @transform("values")
        def rejected(value: object) -> object:
            calls.append("rejected")
            return value

    type Choice = Annotated[Selected | Rejected, Discriminator("kind")]
    result = Contract(Choice).from_python({"kind": "selected"})

    assert type(result) is Selected
    assert result.values == []
    assert calls == ["selected"]


def test_discriminator_failures_are_located_and_machine_readable() -> None:
    contract = Contract(Payment)

    with pytest.raises(ValidationError) as missing:
        contract.from_python({"number": "123"})
    assert missing.value.errors() == [
        {
            "code": "discriminator_missing",
            "location": ["type"],
            "message": "Required discriminator 'type' is missing",
            "discriminator": "type",
        }
    ]

    with pytest.raises(ValidationError) as unknown:
        contract.from_python({"type": "cash"})
    detail = unknown.value.errors()[0]
    assert detail["code"] == "discriminator_unknown"
    assert detail["location"] == ["type"]
    assert detail["input"] == "cash"
    assert detail["discriminator"] == "type"
    assert detail["expected_tags"] == ["bank", "card"]

    with pytest.raises(ValidationError) as invalid_type:
        contract.from_python({"type": 1})
    assert invalid_type.value.code is ErrorCode.TYPE
    assert invalid_type.value.location == ("type",)
    assert invalid_type.value.errors()[0]["context"] == {"discriminator": "type"}


def test_adversarial_tag_values_remain_bounded_and_duplicate_json_is_rejected() -> None:
    class Hostile:
        def __repr__(self) -> str:
            raise RuntimeError("repr must not escape")

    contract = Contract(Payment)
    with pytest.raises(ValidationError) as hostile:
        contract.from_python({"type": Hostile()})
    assert hostile.value.code is ErrorCode.TYPE
    assert len(str(hostile.value)) < 500

    with pytest.raises(ValidationError) as unicode_tag:
        contract.from_python({"type": "💳" * 10_000})
    assert unicode_tag.value.code is ErrorCode.DISCRIMINATOR_UNKNOWN
    assert len(str(unicode_tag.value)) < 1_000

    with pytest.raises(ValidationError) as duplicate:
        contract.from_json('{"type":"card","type":"bank","iban":"CH1"}')
    assert duplicate.value.code is ErrorCode.JSON_DUPLICATE
    assert duplicate.value.location == ()


def test_mapping_protocol_exceptions_are_not_reclassified() -> None:
    class Explosive(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise RuntimeError("mapping exploded")

        def __iter__(self) -> Iterator[str]:
            return iter(("type",))

        def __len__(self) -> int:
            return 1

    with pytest.raises(RuntimeError, match="mapping exploded"):
        Contract(Payment).from_python(Explosive())


def test_nested_locations_compose_through_lists_mappings_and_specs() -> None:
    class Envelope(Spec):
        payments: list[Payment]
        indexed: dict[str, Payment]

    with pytest.raises(ValidationError) as captured:
        Envelope.from_mapping(
            {
                "payments": [{"type": "card", "number": "1"}],
                "indexed": {"primary": {"type": "cash"}},
            }
        )

    assert captured.value.location == ("indexed", "primary", "type")


def test_optional_tagged_union_dispatches_none_without_mapping_work() -> None:
    type OptionalPayment = Annotated[Card | Bank | None, Discriminator("kind")]
    contract = Contract(OptionalPayment)

    assert contract.validate(None) is None
    assert contract.from_python(None) is None
    assert contract.from_json("null") is None
    assert type(contract.from_python({"type": "bank", "iban": "CH1"})) is Bank

    alias_optional = Contract(Payment | None)
    assert alias_optional.from_python(None) is None
    assert type(alias_optional.from_python({"type": "card", "number": "1"})) is Card

    with pytest.raises(TaggedUnionDeclarationError, match="unrelated outer alternatives"):
        Contract(Payment | str)


class EventA(TypedDict):
    kind: Literal["a"]
    value: int


class EventB(TypedDict):
    kind: Literal["b"]
    label: str


type Event = Annotated[EventA | EventB, Discriminator("kind")]


def test_typed_dict_branches_dispatch_and_detach_mapping_input() -> None:
    source = MappingProxy({"kind": "a", "value": 1})
    contract = Contract(Event)
    result = contract.from_python(source)

    assert result == {"kind": "a", "value": 1}
    assert type(result) is dict
    assert contract.validate(result) is result
    assert contract.to_python(result) == result
    assert contract.from_json(contract.to_json(result)) == result

    type OptionalEvent = Annotated[EventA | EventB | None, Discriminator("kind")]
    assert Contract(OptionalEvent).from_json('{"kind":"b","label":"ok"}') == {
        "kind": "b",
        "label": "ok",
    }


def test_concrete_generic_typed_dict_branches_dispatch() -> None:
    class Success[T](TypedDict):
        kind: Literal["success"]
        value: T

    class Failure[T](TypedDict):
        kind: Literal["failure"]
        error: T

    type Result = Annotated[Success[int] | Failure[str], Discriminator("kind")]
    assert Contract(Result).from_python({"kind": "success", "value": 1}) == {
        "kind": "success",
        "value": 1,
    }


class MappingProxy(Mapping[str, object]):
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def __getitem__(self, key: str) -> object:
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


def test_alias_identity_and_introspection_retain_immutable_tag_truth() -> None:
    info = inspect_contract(Contract(Payment))

    assert isinstance(info.schema, AliasSchema)
    assert isinstance(info.schema.schema, TaggedUnionSchema)
    tagged = info.schema.schema
    assert tagged.discriminator == "kind"
    assert tagged.external_name == "type"
    assert tuple(branch.tag.value for branch in tagged.branches) == ("bank", "card")


def test_dynamic_and_inherited_branches_use_normal_field_truth() -> None:
    Base = create_spec("TaggedBase", {"kind": Literal["base"]})
    Dynamic = create_spec("TaggedDynamic", {"kind": Literal["dynamic"], "value": int})

    type DynamicUnion = Annotated[Base | Dynamic, Discriminator("kind")]
    result = Contract(DynamicUnion).from_python({"kind": "dynamic", "value": 1})

    assert type(result) is Dynamic


def test_concrete_generic_branches_preserve_specialized_body_contracts() -> None:
    class Success[T](Spec):
        kind: Literal["success"]
        value: T

    class Failure[T](Spec):
        kind: Literal["failure"]
        error: T

    type Result = Annotated[Success[int] | Failure[str], Discriminator("kind")]
    contract = Contract(Result)

    success = contract.from_python({"kind": "success", "value": 1})
    assert type(success) is Success[int]
    assert success.value == 1
    with pytest.raises(ValidationError) as captured:
        contract.from_python({"kind": "success", "value": "1"})
    assert captured.value.location == ("value",)


def test_recursive_spec_graph_uses_finite_tagged_schema() -> None:
    class Leaf(Spec):
        kind: Literal["leaf"]
        value: int

    class Branch(Spec):
        children: list[Annotated[Leaf | ForwardRef("Branch"), Discriminator("kind")]]
        kind: Literal["branch"]

    type Node = Annotated[Leaf | Branch, Discriminator("kind")]
    contract = Contract(Node)
    node = contract.from_python({"kind": "branch", "children": [{"kind": "leaf", "value": 1}]})

    assert type(node) is Branch
    assert type(node.children[0]) is Leaf
    round_trip = contract.from_json(contract.to_json(node))
    assert type(round_trip) is Branch
    assert type(round_trip.children[0]) is Leaf


def test_recursive_branch_can_inherit_its_discriminator() -> None:
    class Leaf(Spec):
        kind: Literal["leaf"]

    class BranchBase(Spec):
        kind: Literal["branch"]

    class Branch(BranchBase):
        children: list[Annotated[Leaf | ForwardRef("Branch"), Discriminator("kind")]]

    node = Contract(Annotated[Leaf | Branch, Discriminator("kind")]).from_python({"kind": "branch", "children": []})
    assert type(node) is Branch


def test_recursive_resolution_sees_effective_inherited_and_declared_tag_serializers() -> None:
    class Leaf(Spec):
        kind: Literal["leaf"]

    class SerializedBase(Spec):
        kind: Literal["branch"]

        @serialize("kind")
        def inherited(value: str) -> str:
            return value

    class InheritedSerializer(SerializedBase):
        children: list[Annotated[Leaf | ForwardRef("InheritedSerializer"), Discriminator("kind")]]

    with pytest.raises(TaggedUnionDeclarationError, match="serialization hook"):
        Contract(Annotated[Leaf | InheritedSerializer, Discriminator("kind")])

    class DeclaredSerializer(Spec):
        kind: Literal["branch"]
        children: list[Annotated[Leaf | ForwardRef("DeclaredSerializer"), Discriminator("kind")]]

        @serialize("kind")
        def declared(value: str) -> str:
            return value

    with pytest.raises(TaggedUnionDeclarationError, match="serialization hook"):
        Contract(Annotated[Leaf | DeclaredSerializer, Discriminator("kind")])


def test_sensitive_discriminator_redacts_tag_failure_data() -> None:
    class SecretA(Spec):
        kind: Annotated[Literal["secret-a"], Sensitive()]

    class SecretB(Spec):
        kind: Annotated[Literal["secret-b"], Sensitive()]

    type Secret = Annotated[SecretA | SecretB, Discriminator("kind")]
    with pytest.raises(ValidationError) as captured:
        Contract(Secret).from_python({"kind": "not-secret"})

    detail = captured.value.errors()[0]
    assert detail["input"] == "<redacted>"
    assert detail["discriminator"] == "<redacted>"
    assert detail["expected_tags"] == ["<redacted>"]
    assert captured.value.value == "<redacted>"


class Tag(Enum):
    ALPHA = "alpha"
    BETA = "beta"


def test_enum_tags_use_members_for_python_and_values_for_json() -> None:
    class Alpha(Spec):
        kind: Literal[Tag.ALPHA]

    class Beta(Spec):
        kind: Literal[Tag.BETA]

    type Tagged = Annotated[Alpha | Beta, Discriminator("kind")]
    contract = Contract(Tagged)

    assert type(contract.from_python({"kind": Tag.ALPHA})) is Alpha
    assert type(contract.from_json('{"kind":"beta"}')) is Beta
    assert contract.to_json(Alpha(kind=Tag.ALPHA)) == '{"kind":"alpha"}'
    with pytest.raises(ValidationError) as captured:
        contract.from_python({"kind": "alpha"})
    assert captured.value.code is ErrorCode.TYPE


def test_bool_and_int_tags_remain_type_sensitive() -> None:
    class Enabled(Spec):
        kind: Literal[True]

    class VersionOne(Spec):
        kind: Literal[1]

    type Tagged = Annotated[Enabled | VersionOne, Discriminator("kind")]
    contract = Contract(Tagged)

    assert type(contract.from_python({"kind": True})) is Enabled
    assert type(contract.from_python({"kind": 1})) is VersionOne


def test_large_dispatch_table_selects_one_of_more_than_four_branches() -> None:
    branches = tuple(create_spec(f"Branch{index}", {"kind": Literal[index]}) for index in range(8))
    union = branches[0]
    for branch in branches[1:]:
        union = union | branch
    annotation = Annotated[union, Discriminator("kind")]

    contract = Contract(annotation)
    result = contract.from_python({"kind": 7})
    assert type(result) is branches[7]
    assert type(Contract(annotation).from_json('{"kind":7}')) is branches[7]
    assert contract.from_python(result) is result
    assert contract.validate(result) is result
    assert contract.to_python(result) == {"kind": 7}


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (Annotated[int, Discriminator("kind")], "requires a union"),
        (Annotated[Card | None, Discriminator("kind")], "at least two non-None"),
        (Annotated[Card | int, Discriminator("kind")], "Spec or TypedDict"),
        (Annotated[Card | EventA, Discriminator("kind")], "cannot mix"),
    ],
)
def test_invalid_tagged_union_shapes_fail_during_resolution(annotation: object, message: str) -> None:
    with pytest.raises(TaggedUnionDeclarationError, match=message):
        Contract(annotation)


def test_invalid_branch_tag_declarations_fail_during_resolution() -> None:
    class Missing(Spec):
        value: int

    class Multiple(Spec):
        kind: Literal["multiple", "other"]

    class OptionalTag(Spec):
        kind: Literal["optional"] = "optional"

    for branch, message in (
        (Missing, "has no discriminator"),
        (Multiple, "single-value Literal"),
        (OptionalTag, "must be required"),
    ):
        with pytest.raises(TaggedUnionDeclarationError, match=message):
            Contract(Annotated[Card | branch, Discriminator("kind")])


def test_duplicate_and_json_colliding_tags_fail_during_resolution() -> None:
    class Duplicate(Spec):
        kind: Annotated[Literal["card"], Alias("type")]

    with pytest.raises(TaggedUnionDeclarationError, match="unique Python tags"):
        Contract(Annotated[Card | Duplicate, Discriminator("kind")])

    class Numeric(IntEnum):
        ONE = 1

    class EnumOne(Spec):
        kind: Literal[Numeric.ONE]

    class IntOne(Spec):
        kind: Literal[1]

    with pytest.raises(TaggedUnionDeclarationError, match="unique JSON tags"):
        Contract(Annotated[EnumOne | IntOne, Discriminator("kind")])


def test_inconsistent_aliases_and_serializer_tag_corruption_are_rejected() -> None:
    class OtherAlias(Spec):
        kind: Annotated[Literal["other"], Alias("category")]

    with pytest.raises(TaggedUnionDeclarationError, match="external name"):
        Contract(Annotated[Card | OtherAlias, Discriminator("kind")])

    class Serialized(Spec):
        kind: Literal["serialized"]

        @serialize("kind")
        def corrupt(value: str) -> str:
            return "card"

    with pytest.raises(TaggedUnionDeclarationError, match="serialization hook"):
        Contract(Annotated[Serialized | Bank, Discriminator("kind")])

    with pytest.raises(TypeError, match="cannot replace tagged-union field"):

        class Envelope(Spec):
            payment: Payment

            @serialize("payment")
            def replace_payment(value: object) -> object:
                return value

    class NestedEnvelope(Spec):
        payment: Payment

    with pytest.raises(TypeError, match="cannot replace tagged-union field"):

        class OuterEnvelope(Spec):
            nested: NestedEnvelope

            @serialize("nested")
            def replace_nested(value: object) -> object:
                return value


def test_related_nominal_branches_are_rejected_as_ambiguous() -> None:
    class Base(Spec):
        kind: Literal["base"]

    class Child(Base):
        pass

    with pytest.raises(TaggedUnionDeclarationError, match="non-overlapping nominal"):
        Contract(Annotated[Base | Child, Discriminator("kind")])


def test_typed_dict_requires_a_required_single_literal_key() -> None:
    class OptionalEvent(TypedDict):
        kind: NotRequired[Literal["optional"]]

    with pytest.raises(TaggedUnionDeclarationError, match="must be required"):
        Contract(Annotated[EventA | OptionalEvent, Discriminator("kind")])

    class NoTag(TypedDict):
        value: int

    with pytest.raises(TaggedUnionDeclarationError, match="has no discriminator key"):
        Contract(Annotated[EventA | NoTag, Discriminator("kind")])


def test_discriminator_marker_and_duplicate_metadata_are_validated() -> None:
    for value in ("", 1):
        with pytest.raises(TypeError, match="non-empty string"):
            Discriminator(value)  # type: ignore[arg-type]

    with pytest.raises(TaggedUnionDeclarationError, match="only one Discriminator"):
        Contract(Annotated[Card | Bank, Discriminator("kind"), Discriminator("kind")])


def test_unsupported_tag_representation_is_rejected() -> None:
    class BytesA(Spec):
        kind: Literal[b"a"]

    class BytesB(Spec):
        kind: Literal[b"b"]

    with pytest.raises(TaggedUnionDeclarationError, match="require str, int, bool"):
        Contract(Annotated[BytesA | BytesB, Discriminator("kind")])


def test_open_generic_branch_is_rejected() -> None:
    class Open[T](Spec):
        kind: Literal["open"]
        value: T

    with pytest.raises(TaggedUnionDeclarationError, match="requires concrete specialization"):
        Contract(Annotated[Open | Bank, Discriminator("kind")])


def test_named_literal_alias_is_unwrapped_as_existing_tag_truth() -> None:
    type FirstTag = Literal["first"]
    type SecondTag = Literal["second"]

    class First(Spec):
        kind: FirstTag

    class Second(Spec):
        kind: SecondTag

    result = Contract(Annotated[First | Second, Discriminator("kind")]).from_python({"kind": "second"})
    assert type(result) is Second


def test_tagged_schema_policy_and_constructor_invariants() -> None:
    tagged = inspect_contract(Contract(Payment)).schema
    assert isinstance(tagged, AliasSchema)
    assert isinstance(tagged.schema, TaggedUnionSchema)
    assert schema_values_are_immutable(tagged.schema) is True
    assert schema_is_covariant_override(replace(tagged.schema, sensitive=True), tagged.schema) is False

    branch = tagged.schema.branches[0]
    with pytest.raises(ValueError, match="at least two branches"):
        TaggedUnionSchema("kind", "type", (branch,))


def test_tagged_serializer_policy_traverses_structural_containers() -> None:
    class Payload(TypedDict):
        payment: Payment

    annotations = (
        dict[str, Payment],
        Payload,
        tuple[Payment, ...],
        tuple[int, Payment],
        Payment | None,
    )
    assert all(
        schema_contains_tagged_union(inspect_contract(Contract(annotation)).schema) for annotation in annotations
    )

    class PlainNode(Spec):
        children: list[PlainNode]

    class OpenNode[T](Spec):
        value: T

    PlainNode(children=[])
    assert schema_contains_tagged_union(SpecReferenceSchema(PlainNode)) is False
    assert schema_contains_tagged_union(SpecReferenceSchema(OpenNode)) is False


def test_invalid_projector_state_reports_serialization_failure() -> None:
    tagged = inspect_contract(Contract(Event)).schema
    assert isinstance(tagged, AliasSchema)
    projector = compile_value_projector(tagged.schema, "python", True)

    with pytest.raises(Exception, match="no longer identifies"):
        projector({"kind": "other"}, ())

    payment = inspect_contract(Contract(Payment)).schema
    assert isinstance(payment, AliasSchema)
    projector = compile_value_projector(payment.schema, "python", True)
    with pytest.raises(Exception, match="no longer identifies"):
        projector(object(), ())


def test_tagged_union_is_not_a_supported_projected_mapping_key() -> None:
    card = Card(kind="card", number="1")
    contract = Contract(dict[Payment, int])

    with pytest.raises(Exception, match="losing hashability"):
        contract.to_python({card: 1})


def test_forward_annotation_parser_rejects_non_discriminator_calls() -> None:
    validate_annotation_expression('Annotated[A | B, Discriminator("kind")]')
    with pytest.raises(Exception, match="Unsupported annotation"):

        class Unsafe(Spec):
            value: "list[factory()]"  # noqa: F821

    with pytest.raises(Exception, match="Unsupported annotation"):
        validate_annotation_expression("[item for item in values]")

    annotation = tuple[Literal["not-a-name"], ForwardRef("Target")]
    assert retain_referenced_namespace({"value": annotation}, {"Target": Card, "unused": Bank}) == {"Target": Card}
