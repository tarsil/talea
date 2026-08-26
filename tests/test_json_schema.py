import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from functools import reduce
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address
from operator import or_
from pathlib import Path
from typing import Annotated, Literal, NewType, NotRequired, ReadOnly as TypingReadOnly, TypedDict
from uuid import UUID

import pytest
from hypothesis import given, strategies as st
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from openapi_spec_validator import validate as validate_openapi

from talea import (
    Alias,
    Contract,
    Deprecated,
    Description,
    Discriminator,
    Examples,
    Ge,
    Gt,
    Le,
    Lt,
    MaxLength,
    MinLength,
    MultipleOf,
    Pattern,
    ReadOnly,
    SchemaProjectionError,
    Sensitive,
    Spec,
    Title,
    WriteOnly,
    check,
    create_spec,
    derive_spec,
    field,
    serialize,
    transform,
)
from talea.json_schema.projection import (
    JSON_SCHEMA_DIALECT,
    OPENAPI_DIALECT,
    _discriminator_key,
    _json_type,
    _length_keyword,
    _StandardsProjector,
    project_schema,
)
from talea.metadata import EMPTY_METADATA
from talea.schema import (
    AliasSchema,
    ConstrainedSchema,
    FixedTupleSchema,
    LiteralSchema,
    LiteralValue,
    MappingSchema,
    NamedReferenceSchema,
    NamedSchemaIdentity,
    PrimitiveSchema,
    SequenceSchema,
    SpecReferenceSchema,
    TaggedUnionSchema,
    TypedDictField,
    TypedDictSchema,
    TypeSchema,
    UnionSchema,
    VariadicTupleSchema,
    resolve_annotation,
)
from talea.schema.references import _NamedSchemaTarget


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
        "info": {"title": "Talea schema projection", "version": "1"},
        "paths": {},
        "components": {"schemas": {**schemas, "ProjectionRoot": fragment["schema"]}},
    }


def test_primitive_contracts_declare_draft_and_keep_bool_distinct_from_int() -> None:
    assert Contract(int).json_schema() == {"$schema": JSON_SCHEMA_DIALECT, "type": "integer"}
    assert Contract(float).json_schema() == {"$schema": JSON_SCHEMA_DIALECT, "type": "number"}
    assert Contract(str).json_schema() == {"$schema": JSON_SCHEMA_DIALECT, "type": "string"}
    assert Contract(bool).json_schema() == {"$schema": JSON_SCHEMA_DIALECT, "type": "boolean"}
    assert Contract(None).json_schema() == {"$schema": JSON_SCHEMA_DIALECT, "type": "null"}

    integer = Draft202012Validator(Contract(int).json_schema())
    integer.validate(1)
    with pytest.raises(JsonSchemaValidationError):
        integer.validate(True)


def test_constraints_project_to_the_keyword_owned_by_the_json_shape() -> None:
    numeric = Contract(Annotated[int, Gt(0), Le(10), MultipleOf(2)]).json_schema()
    text = Contract(Annotated[str, MinLength(2), MaxLength(8), Pattern(r"[a-z]+")]).json_schema()
    sequence = Contract(Annotated[list[int], MinLength(1), MaxLength(3)]).json_schema()
    mapping = Contract(Annotated[dict[str, int], MinLength(1), MaxLength(2)]).json_schema()

    assert numeric == {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "integer",
        "exclusiveMinimum": 0,
        "maximum": 10,
        "multipleOf": 2,
    }
    assert text["minLength"] == 2
    assert text["maxLength"] == 8
    assert text["pattern"] == r"[a-z]+"
    assert sequence["minItems"] == 1
    assert sequence["maxItems"] == 3
    assert mapping["minProperties"] == 1
    assert mapping["maxProperties"] == 2

    with pytest.raises(SchemaProjectionError, match="flags"):
        Contract(Annotated[str, Pattern(re.compile("value", re.IGNORECASE))]).json_schema()


def test_container_projection_follows_json_representation_and_mode() -> None:
    assert Contract(list[int]).json_schema()["type"] == "array"
    assert "uniqueItems" not in Contract(set[int]).json_schema(mode="input")
    assert Contract(set[int]).json_schema(mode="output")["uniqueItems"] is True
    assert Contract(frozenset[str]).json_schema(mode="output")["uniqueItems"] is True
    assert Contract(tuple[int, ...]).json_schema()["items"] == {"type": "integer"}
    assert Contract(tuple[int, str]).json_schema() == {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "array",
        "prefixItems": [{"type": "integer"}, {"type": "string"}],
        "items": False,
        "minItems": 2,
        "maxItems": 2,
    }

    mapping = Contract(dict[Literal["left", "right"], int]).json_schema()
    assert mapping["propertyNames"] == {"enum": ["left", "right"]}
    with pytest.raises(SchemaProjectionError, match="exact string keys"):
        Contract(dict[int, str]).json_schema()


def test_standard_library_types_match_canonical_json_representations() -> None:
    formats = {
        UUID: "uuid",
        date: "date",
        datetime: "date-time",
        time: "time",
        IPv4Address: "ipv4",
        IPv6Address: "ipv6",
    }
    for annotation, schema_format in formats.items():
        assert Contract(annotation).json_schema()["format"] == schema_format

    for annotation in (Path, IPv4Network, IPv4Interface):
        assert Contract(annotation).json_schema() == {"$schema": JSON_SCHEMA_DIALECT, "type": "string"}

    decimal_input = Contract(Decimal).json_schema(mode="input")
    decimal_output = Contract(Decimal).json_schema(mode="output")
    assert decimal_input["anyOf"] == [{"type": "integer"}, {"type": "string"}]
    assert decimal_output == {"$schema": JSON_SCHEMA_DIALECT, "type": "string"}
    duration = Contract(timedelta).json_schema()
    assert duration["format"] == "duration"
    assert re.fullmatch(str(duration["pattern"]), "-P1DT2H3M4.000005S")


def test_bytes_use_canonical_padded_base64_schema() -> None:
    schema = Contract(bytes).json_schema()
    assert schema["contentEncoding"] == "base64"
    validator = Draft202012Validator(schema)
    validator.validate("YQ==")
    with pytest.raises(JsonSchemaValidationError):
        validator.validate("YQ")


class SchemaState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class SchemaCode(IntEnum):
    OK = 200
    ERROR = 500


class MixedEnum(Enum):
    ENABLED = True
    COUNT = 2


def test_enum_and_literal_values_preserve_json_scalar_types_and_order() -> None:
    assert Contract(SchemaState).json_schema()["enum"] == ["open", "closed"]
    assert Contract(SchemaCode).json_schema()["enum"] == [200, 500]
    mixed = Contract(MixedEnum).json_schema()
    assert mixed["anyOf"] == [
        {"type": "boolean", "const": True},
        {"type": "integer", "const": 2},
    ]
    assert Contract(Literal["one"]).json_schema()["const"] == "one"
    literal = Contract(Literal[True, 1, "one"]).json_schema()
    assert literal["anyOf"] == [
        {"type": "boolean", "const": True},
        {"type": "integer", "const": 1},
        {"type": "string", "const": "one"},
    ]
    encoded = Contract(Literal[b"a"]).json_schema()
    assert encoded == {"$schema": JSON_SCHEMA_DIALECT, "type": "string", "const": "YQ=="}


def test_spec_projection_uses_alias_requiredness_defaults_and_metadata() -> None:
    factory_calls = 0

    def make_tags() -> list[str]:
        nonlocal factory_calls
        factory_calls += 1
        return []

    class User(
        Spec,
        metadata=(
            Title("User record"),
            Description("External user."),
            Examples({"identifier": 1, "name": "Ada", "roles": ["admin"]}),
            Deprecated(),
        ),
    ):
        id: Annotated[int, Alias("identifier"), ReadOnly()]
        name: Annotated[str, Description("Display name."), WriteOnly()]
        active: bool = True
        tags: list[str] = field(default_factory=make_tags)
        secret: Annotated[str, Sensitive()] = "not-published"

    input_document = User.json_schema(mode="input")
    output_document = User.json_schema(mode="output")
    input_schema = _definition(input_document, "User")
    output_schema = _definition(output_document, "User")
    properties = input_schema["properties"]
    assert isinstance(properties, dict)

    assert input_schema["title"] == "User record"
    assert input_schema["description"] == "External user."
    assert input_schema["examples"] == [{"identifier": 1, "name": "Ada", "roles": ["admin"]}]
    assert input_schema["deprecated"] is True
    assert input_schema["required"] == ["identifier", "name"]
    assert output_schema["required"] == ["identifier", "name", "active", "tags", "secret"]
    assert properties["identifier"]["readOnly"] is True
    assert properties["name"]["writeOnly"] is True
    assert properties["active"]["default"] is True
    assert "default" not in properties["tags"]
    assert "default" not in properties["secret"]
    assert "sensitive" not in json.dumps(input_document).lower()
    assert "not-published" not in json.dumps(input_document)
    assert factory_calls == 0

    properties["active"]["default"] = False
    assert _definition(User.json_schema(), "User")["properties"]["active"]["default"] is True


class ProjectionPayload(TypedDict, total=False):
    required: Annotated[int, Description("Required key.")]
    optional: NotRequired[str]
    immutable: TypingReadOnly[int]


ProjectionPayload.__required_keys__ = frozenset({"required"})


def test_typed_dict_projection_uses_canonical_key_truth() -> None:
    document = Contract(ProjectionPayload).json_schema()
    schema = _definition(document, "ProjectionPayload")
    assert schema["required"] == ["required"]
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["required"]["description"] == "Required key."
    assert properties["immutable"]["readOnly"] is True


def test_partial_pick_and_omit_specs_project_retained_presence_truth() -> None:
    class User(Spec):
        id: int
        name: str
        active: bool = True

    Patch = derive_spec(User, partial=True, name="UserPatch")
    Picked = derive_spec(User, include=("id", "active"), name="UserPick")
    Omitted = derive_spec(User, exclude=("name",), name="UserOmit")

    patch_input = _definition(Patch.json_schema(mode="input"), "UserPatch")
    patch_output = _definition(Patch.json_schema(mode="output"), "UserPatch")
    assert "required" not in patch_input
    assert "required" not in patch_output
    assert list(patch_input["properties"]) == ["id", "name", "active"]
    assert list(_definition(Picked.json_schema(), "UserPick")["properties"]) == ["id", "active"]
    assert list(_definition(Omitted.json_schema(), "UserOmit")["properties"]) == ["id", "active"]


def test_transform_check_and_serializer_policies_are_mode_specific() -> None:
    class Transformed(Spec):
        value: int

        @transform("value")
        def parse(value: object) -> object:
            return value

        @check("value")
        def positive(value: int) -> None:
            if value < 0:
                raise ValueError

    class Serialized(Spec):
        value: int

        @serialize("value")
        def encode(value: int) -> str:
            return str(value)

    with pytest.raises(SchemaProjectionError, match="transform.*accepted domain"):
        Transformed.json_schema(mode="input")
    assert _definition(Transformed.json_schema(mode="output"), "Transformed")["type"] == "object"
    assert _definition(Serialized.json_schema(mode="input"), "Serialized")["type"] == "object"
    with pytest.raises(SchemaProjectionError, match="serializer.*return contract"):
        Serialized.json_schema(mode="output")


class NestedNode(Spec):
    value: int
    children: list[NestedNode]


class MutualLeft(Spec):
    right: MutualRight | None


class MutualRight(Spec):
    left: MutualLeft | None


type JsonValue = None | bool | int | str | list[JsonValue] | dict[str, JsonValue]


class RecursiveRecord(TypedDict):
    value: int
    children: list[RecursiveRecord]


def test_recursive_specs_aliases_and_typed_dicts_emit_finite_refs() -> None:
    node = NestedNode.json_schema()
    assert _definition(node, "NestedNode")["properties"]["children"]["items"] == {"$ref": "#/$defs/NestedNode"}
    mutual = MutualLeft.json_schema()
    assert tuple(mutual["$defs"]) == ("MutualLeft", "MutualRight")
    assert _definition(mutual, "MutualRight")["properties"]["left"]["anyOf"][1] == {"$ref": "#/$defs/MutualLeft"}
    alias = Contract(JsonValue).json_schema()
    alias_options = _definition(alias, "JsonValue")["anyOf"]
    mapping = next(option for option in alias_options if option.get("type") == "object")
    assert "$ref" in mapping["additionalProperties"]
    typed = Contract(RecursiveRecord).json_schema()
    assert _definition(typed, "RecursiveRecord")["properties"]["children"]["items"] == {
        "$ref": "#/$defs/RecursiveRecord"
    }


def test_generic_specializations_get_distinct_definition_identity() -> None:
    class Box[T](Spec):
        value: T

    class Pair(Spec):
        integer: Box[int]
        text: Box[str]

    document = Pair.json_schema()
    assert tuple(document["$defs"]) == ("Pair", "Box[int]", "Box[str]")
    assert _definition(document, "Box[int]")["properties"]["value"] == {"type": "integer"}
    assert _definition(document, "Box[str]")["properties"]["value"] == {"type": "string"}
    with pytest.raises(TypeError, match="requires concrete specialization"):
        Box.json_schema()
    with pytest.raises(SchemaProjectionError, match="requires concrete specialization"):
        Box.openapi_schema()


def test_definition_collisions_and_json_pointer_escaping_are_deterministic() -> None:
    Left = create_spec("Duplicate", {"left": int}, module="left.module", qualname="Owner.Duplicate")
    Right = create_spec("Duplicate", {"right": str}, module="right.module", qualname="Owner.Duplicate")

    class Envelope(Spec):
        left: Left
        right: Right

    document = Envelope.json_schema()
    assert tuple(document["$defs"]) == ("Envelope", "Duplicate", "right.module.Owner.Duplicate")
    properties = _definition(document, "Envelope")["properties"]
    assert properties["left"] == {"$ref": "#/$defs/Duplicate"}
    assert properties["right"] == {"$ref": "#/$defs/right.module.Owner.Duplicate"}

    Odd = TypedDict("A/B~", {"value": int})
    odd = Contract(Odd).json_schema()
    assert odd["$ref"] == "#/$defs/A~1B~0"
    assert "A/B~" in odd["$defs"]
    assert json.dumps(document, sort_keys=False) == json.dumps(Envelope.json_schema(), sort_keys=False)


class CardPayment(Spec):
    kind: Literal["card"]
    number: str


class BankPayment(Spec):
    kind: Literal["bank"]
    iban: str


type Payment = Annotated[CardPayment | BankPayment, Discriminator("kind")]


def test_tagged_union_uses_json_one_of_and_openapi_discriminator_truth() -> None:
    json_document = Contract(Payment).json_schema()
    assert list(_definition(json_document, "Payment")) == ["oneOf"]
    fragment = Contract(Payment).openapi_schema()
    payment = fragment["components"]["schemas"]["Payment"]
    assert payment["discriminator"] == {
        "propertyName": "kind",
        "mapping": {
            "bank": "#/components/schemas/BankPayment",
            "card": "#/components/schemas/CardPayment",
        },
    }
    validate_openapi(_openapi_document(fragment))


@pytest.mark.parametrize("branch_count", [8, 32])
def test_large_tagged_union_maps_every_branch_to_one_definition(branch_count: int) -> None:
    branches = tuple(
        create_spec(
            f"Branch{branch_count}_{index}",
            {"kind": Literal[f"tag-{index}"], "value": int},
        )
        for index in range(branch_count)
    )
    annotation = Annotated[reduce(or_, branches), Discriminator("kind")]
    fragment = Contract(annotation).openapi_schema()
    schemas = fragment["components"]["schemas"]
    root = fragment["schema"]
    assert len(root["oneOf"]) == branch_count
    assert len(root["discriminator"]["mapping"]) == branch_count
    assert len(schemas) == branch_count


class LiteralExpression(TypedDict):
    kind: Literal["literal"]
    value: int


class AddExpression(TypedDict):
    kind: Literal["add"]
    left: Expression
    right: Expression


type Expression = Annotated[LiteralExpression | AddExpression, Discriminator("kind")]


def test_recursive_tagged_ast_reuses_alias_and_branch_definitions() -> None:
    document = Contract(Expression).json_schema()
    assert tuple(document["$defs"]) == ("Expression", "AddExpression", "LiteralExpression")
    add = _definition(document, "AddExpression")
    assert add["properties"]["left"] == {"$ref": "#/$defs/Expression"}
    assert add["properties"]["right"] == {"$ref": "#/$defs/Expression"}
    fragment = Contract(Expression).openapi_schema()
    expression = fragment["components"]["schemas"]["Expression"]
    assert expression["discriminator"]["propertyName"] == "kind"
    validate_openapi(_openapi_document(fragment))


def test_openapi_component_names_are_normalized_and_fragment_is_fresh() -> None:
    class Box[T](Spec):
        value: T

    fragment = Box[list[int]].openapi_schema()
    schemas = fragment["components"]["schemas"]
    assert tuple(schemas) == ("Box_list_int__",)
    validate_openapi(_openapi_document(fragment))
    schemas.clear()
    assert Box[list[int]].openapi_schema()["components"]["schemas"]


def test_generated_documents_conform_to_draft_2020_12_and_runtime_examples() -> None:
    class Payload(Spec):
        identifier: UUID
        count: Annotated[int, Ge(1), Lt(10)]
        values: list[str]

    document = Payload.json_schema()
    Draft202012Validator.check_schema(document)
    validator = Draft202012Validator(document)
    accepted = {"identifier": str(UUID(int=0)), "count": 2, "values": ["a"]}
    assert Payload.from_json(json.dumps(accepted)).count == 2
    validator.validate(accepted)
    for rejected in (
        {"identifier": str(UUID(int=0)), "count": 0, "values": ["a"]},
        {"identifier": str(UUID(int=0)), "count": 2, "values": [1]},
        {"identifier": str(UUID(int=0)), "count": 2, "values": ["a"], "extra": True},
    ):
        with pytest.raises(JsonSchemaValidationError):
            validator.validate(rejected)


@given(st.integers(min_value=0, max_value=100).filter(lambda value: value % 5 == 0))
def test_constrained_integer_property_matches_runtime(value: int) -> None:
    annotation = Annotated[int, Ge(0), Le(100), MultipleOf(5)]
    document = Contract(annotation).json_schema()
    Draft202012Validator(document).validate(value)
    assert Contract(annotation).from_json(str(value)) == value


@given(st.sets(st.integers(), max_size=10))
def test_output_set_property_matches_runtime(values: set[int]) -> None:
    contract = Contract(set[int])
    projected = json.loads(contract.to_json(values))
    Draft202012Validator(contract.json_schema(mode="output")).validate(projected)


@given(st.dictionaries(st.sampled_from(("id", "name")), st.integers(), max_size=2))
def test_partial_requiredness_property_accepts_arbitrary_presence(values: dict[str, int]) -> None:
    class Source(Spec):
        id: int
        name: int

    Patch = derive_spec(Source, partial=True)
    document = Patch.json_schema()
    Draft202012Validator(document).validate(values)
    assert Patch.from_mapping(values).to_dict() == values


@given(
    st.fixed_dictionaries(
        {"required": st.integers()},
        optional={"optional": st.text(max_size=20), "immutable": st.integers()},
    )
)
def test_typed_dict_property_matches_runtime_requiredness(values: ProjectionPayload) -> None:
    contract = Contract(ProjectionPayload)
    Draft202012Validator(contract.json_schema()).validate(values)
    assert contract.from_json(json.dumps(values)) == values


@given(
    st.one_of(
        st.builds(lambda number: {"kind": "card", "number": number}, st.text(max_size=20)),
        st.builds(lambda iban: {"kind": "bank", "iban": iban}, st.text(max_size=20)),
    )
)
def test_tagged_union_property_matches_runtime_dispatch(values: dict[str, str]) -> None:
    contract = Contract(Payment)
    Draft202012Validator(contract.json_schema()).validate(values)
    assert contract.from_json(json.dumps(values)).kind == values["kind"]


def test_invalid_mode_is_rejected_without_projector_state_leakage() -> None:
    with pytest.raises(TypeError, match="schema mode"):
        Contract(int).json_schema(mode="invalid")  # type: ignore[arg-type]


def test_newtype_projects_underlying_contract_without_a_fake_definition() -> None:
    UserId = NewType("UserId", int)
    assert Contract(UserId).json_schema() == {"$schema": JSON_SCHEMA_DIALECT, "type": "integer"}


def test_ordinary_union_uses_anyof_because_overlapping_branches_are_legal() -> None:
    schema = Contract(int | Annotated[int, Ge(0)]).json_schema()
    assert "anyOf" in schema
    assert "oneOf" not in schema


def test_schema_projection_adds_no_generated_constructor_or_instance_state() -> None:
    class Plain(Spec):
        value: int

    before = Plain(value=1)
    names = frozenset(Plain.__init__.__code__.co_names)
    assert not any("schema" in name or "openapi" in name for name in names)
    assert not hasattr(before, "__dict__")
    Plain.json_schema()
    after = Plain(value=2)
    assert frozenset(Plain.__init__.__code__.co_names) == names
    assert not hasattr(after, "__dict__")


def test_hostile_metadata_and_property_names_remain_inert_data() -> None:
    sentinel = "TALEA_SCHEMA_SECRET_SENTINEL"

    class Hostile(Spec, metadata=(Title("</script>\n雪"), Description("' OR 1=1 --"))):
        value: Annotated[str, Alias("a/b~\n雪"), Examples("quoted\nvalue")]
        secret: Annotated[str, Sensitive()] = sentinel

    document = Hostile.json_schema()
    schema = _definition(document, "Hostile")
    assert schema["title"] == "</script>\n雪"
    assert "a/b~\n雪" in schema["properties"]
    encoded = json.dumps(document, ensure_ascii=False)
    assert sentinel not in encoded
    assert "x-sensitive" not in encoded


def test_bytes_lengths_are_projected_in_encoded_units_and_float_multiple_is_runtime_only() -> None:
    bounded = Contract(Annotated[bytes, MinLength(1), MaxLength(4)]).json_schema()
    assert bounded["minLength"] == 4
    assert bounded["maxLength"] == 8
    floating = Contract(Annotated[float, MultipleOf(0.1)]).json_schema()
    assert "multipleOf" not in floating


def test_projection_errors_cover_open_generics_and_unrepresentable_values() -> None:
    class Box[T](Spec):
        value: T

    with pytest.raises(SchemaProjectionError, match="requires concrete specialization"):
        project_schema(SpecReferenceSchema(Box), EMPTY_METADATA, mode="input", target="json_schema")

    with pytest.raises(SchemaProjectionError, match="unsupported JSON representation"):
        project_schema(TypeSchema(complex, "exact"), EMPTY_METADATA, mode="input", target="json_schema")

    class UnsupportedEnum(Enum):
        VALUE = object()

    with pytest.raises(SchemaProjectionError, match="member without a JSON representation"):
        Contract(UnsupportedEnum).json_schema()

    unsupported = object()
    literal = LiteralSchema(frozenset({LiteralValue(object, unsupported)}))
    with pytest.raises(SchemaProjectionError, match="Literal contains"):
        project_schema(literal, EMPTY_METADATA, mode="input", target="json_schema")


def test_manual_canonical_nodes_cover_finite_named_and_anonymous_typed_dict_paths() -> None:
    anonymous = TypedDictSchema(
        "Anonymous",
        "tests",
        (TypedDictField("value", PrimitiveSchema("int"), False),),
    )
    assert project_schema(anonymous, EMPTY_METADATA, mode="input", target="json_schema")["type"] == "object"

    identity = NamedSchemaIdentity("alias", "Manual", "tests", object())
    target = _NamedSchemaTarget(identity)
    target.finalize(PrimitiveSchema("int"))
    reference = NamedReferenceSchema(identity, target)
    manual = project_schema(reference, EMPTY_METADATA, mode="input", target="json_schema")
    assert _definition(manual, "Manual") == {"type": "integer"}

    projector = _StandardsProjector("input", "json_schema")
    with pytest.raises(AssertionError):
        projector._project(object())  # type: ignore[arg-type]


def test_three_way_definition_collision_is_stable_in_json_schema_and_openapi() -> None:
    classes = tuple(
        create_spec("Same", {f"value_{index}": int}, module="same.module", qualname="Owner.Same") for index in range(3)
    )
    Envelope = create_spec(
        "CollisionEnvelope",
        {f"item_{index}": spec for index, spec in enumerate(classes)},
    )
    json_document = Envelope.json_schema()
    assert tuple(json_document["$defs"]) == (
        "CollisionEnvelope",
        "Same",
        "same.module.Owner.Same",
        "same.module.Owner.Same_2",
    )
    openapi = Envelope.openapi_schema()
    assert tuple(openapi["components"]["schemas"]) == (
        "CollisionEnvelope",
        "Same",
        "same.module.Owner.Same",
        "same.module.Owner.Same_2",
    )

    projector = _StandardsProjector("input", "json_schema")
    assert projector._definition_key("", "tests", "Anonymous") == "Schema"


def test_alias_key_constraints_and_invalid_default_projection_remain_safe() -> None:
    type Key = Annotated[str, MinLength(1)]

    key_schema = Contract(dict[Key, int]).json_schema()
    assert key_schema["type"] == "object"

    class NonFiniteDefault(Spec):
        value: float = float("inf")

    properties = _definition(NonFiniteDefault.json_schema(), "NonFiniteDefault")["properties"]
    assert "default" not in properties["value"]


def test_serializer_scan_traverses_each_canonical_container_shape_cycle_safely() -> None:
    class Serialized(Spec):
        value: int

        @serialize("value")
        def output(value: int) -> str:
            return str(value)

    projector = _StandardsProjector("input", "json_schema")
    serialized = SpecReferenceSchema(Serialized)
    assert projector._contains_serializer(serialized)
    assert not projector._contains_serializer(SpecReferenceSchema(NestedNode))

    identity = NamedSchemaIdentity("alias", "Number", "tests", object())
    target = _NamedSchemaTarget(identity)
    alias = AliasSchema("Number", "tests", PrimitiveSchema("int"), identity=identity)
    target.finalize(alias)
    named = NamedReferenceSchema(identity, target)

    assert not projector._contains_serializer(ConstrainedSchema(alias, (Ge(0),)))
    assert not projector._contains_serializer(named)
    assert not projector._contains_serializer(named, frozenset({identity}))
    assert not projector._contains_serializer(SequenceSchema("frozenset", PrimitiveSchema("int")))
    assert not projector._contains_serializer(MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")))
    assert not projector._contains_serializer(
        TypedDictSchema("Data", "tests", (TypedDictField("value", PrimitiveSchema("int"), True),))
    )
    payment = resolve_annotation(Payment)
    assert isinstance(payment, AliasSchema)
    assert isinstance(payment.schema, TaggedUnionSchema)
    assert not projector._contains_serializer(payment.schema)
    assert not projector._contains_serializer(VariadicTupleSchema(PrimitiveSchema("int")))
    assert not projector._contains_serializer(FixedTupleSchema((PrimitiveSchema("int"),)))
    assert not projector._contains_serializer(UnionSchema(frozenset({PrimitiveSchema("int"), PrimitiveSchema("none")})))


def test_internal_projection_guards_reject_impossible_canonical_values() -> None:
    projector = _StandardsProjector("input", "json_schema")
    constrained_alias = AliasSchema("Count", "tests", PrimitiveSchema("int"))
    projected = {"type": "integer"}
    projector._apply_constraints(projected, constrained_alias, (Gt(0),))
    assert projected["exclusiveMinimum"] == 0
    with pytest.raises(AssertionError, match="unsupported canonical constraint"):
        projector._apply_constraints({}, PrimitiveSchema("int"), (object(),))

    assert _json_type(None) == "null"
    assert _json_type(1.5) == "number"
    with pytest.raises(SchemaProjectionError, match="no JSON scalar"):
        _json_type(object())
    with pytest.raises(SchemaProjectionError, match="length constraint"):
        _length_keyword(TypeSchema(UUID, "nominal"), minimum=True)


def test_openapi_discriminator_mapping_stringifies_supported_tag_families() -> None:
    class Status(StrEnum):
        ACTIVE = "active"

    assert _discriminator_key(LiteralValue(Status, Status.ACTIVE)) == "active"
    assert _discriminator_key(LiteralValue(bool, True)) == "true"
    assert _discriminator_key(LiteralValue(bool, False)) == "false"
    assert _discriminator_key(LiteralValue(int, 2)) == "2"
    with pytest.raises(SchemaProjectionError, match="discriminator mappings"):
        _discriminator_key(LiteralValue(bytes, b"tag"))
