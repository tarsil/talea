import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import Annotated, TypedDict, cast
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

from talea import (
    Alias,
    Contract,
    Deprecated,
    Description,
    Examples,
    ReadOnly,
    Sensitive,
    SerializationError,
    Spec,
    Title,
    ValidationError,
    WriteOnly,
    check,
    create_spec,
    field,
    serialize,
    transform,
)
from talea.declaration.policies import schema_contains_sensitive_metadata
from talea.introspection import inspect_contract, inspect_spec
from talea.metadata import normalize_metadata
from talea.schema import AliasSchema, Schema, SpecReferenceSchema, resolve_annotation

SECRET = "TALEA_SUPER_SECRET_SENTINEL"


def _assert_redacted(error: ValidationError) -> None:
    rendered = str(error)
    projected = error.errors()
    assert SECRET not in rendered
    assert SECRET not in repr(error)
    assert SECRET not in json.dumps(projected)
    assert error.value == "<redacted>"
    assert "<redacted>" in rendered
    assert projected[0]["input"] == "<redacted>"


def test_field_and_spec_metadata_have_one_immutable_introspection_projection() -> None:
    class Account(
        Spec,
        metadata=(
            Title("Account"),
            Description("Customer account."),
            Examples({"name": "Ada"}),
            Deprecated(),
        ),
    ):
        name: Annotated[
            str,
            Title("Display name"),
            Description("Customer-visible name."),
            Examples("Ada", "Grace"),
            Deprecated(),
            ReadOnly(),
            WriteOnly(),
        ]

    info = inspect_spec(Account)
    field_info = info.fields[0]

    assert (info.title, info.description, info.deprecated) == ("Account", "Customer account.", True)
    assert isinstance(info.examples[0], Mapping)
    assert info.examples[0] == {"name": "Ada"}
    assert info.examples[0]["name"] == "Ada"
    assert info.examples[0] != "not a mapping"
    assert list(info.examples[0]) == ["name"]
    assert len(info.examples[0]) == 1
    assert hash(info.examples[0])
    with pytest.raises(KeyError):
        _ = info.examples[0]["missing"]
    assert field_info.title == "Display name"
    assert field_info.description == "Customer-visible name."
    assert field_info.examples == ("Ada", "Grace")
    assert field_info.deprecated
    assert field_info.read_only
    assert field_info.write_only
    assert not field_info.sensitive
    with pytest.raises(TypeError):
        info.examples[0]["name"] = "changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        field_info.title = "changed"  # type: ignore[misc]


def test_spec_docstring_is_canonical_fallback_and_structured_description_wins() -> None:
    class Documented(Spec):
        """A documented declaration."""

        value: int

    class Explicit(Spec, metadata=(Description("Structured."),)):
        """Ignored fallback."""

        value: int

    assert inspect_spec(Documented).description == "A documented declaration."
    assert inspect_spec(Explicit).description == "Structured."


def test_dynamic_spec_and_contract_use_the_same_metadata_vocabulary() -> None:
    Dynamic = create_spec(
        "DynamicSecret",
        {"token": Annotated[str, Sensitive(), Description("Access token.")]},
        doc="Dynamic fallback.",
        metadata=(Title("Dynamic"), Deprecated()),
    )
    dynamic_info = inspect_spec(Dynamic)
    contract = Contract(
        Annotated[
            int,
            Title("Identifier"),
            Description("External identifier."),
            Examples(1, 2),
            Deprecated(),
            ReadOnly(),
            WriteOnly(),
            Sensitive(),
        ]
    )
    contract_info = inspect_contract(contract)

    assert dynamic_info.title == "Dynamic"
    assert dynamic_info.description == "Dynamic fallback."
    assert dynamic_info.deprecated
    assert dynamic_info.fields[0].description == "Access token."
    assert dynamic_info.fields[0].sensitive
    assert contract_info.title == "Identifier"
    assert contract_info.description == "External identifier."
    assert contract_info.examples == (1, 2)
    assert contract_info.deprecated
    assert contract_info.read_only
    assert contract_info.write_only
    assert contract_info.sensitive


def test_type_alias_identity_metadata_is_distinct_from_contract_use_site_metadata() -> None:
    type SecretId = Annotated[
        int,
        Title("Secret identifier"),
        Description("Alias identity."),
        Sensitive(),
    ]

    schema = resolve_annotation(SecretId)
    assert isinstance(schema, AliasSchema)
    assert schema.metadata.title == "Secret identifier"
    assert schema.metadata.description == "Alias identity."
    assert schema.metadata.sensitive

    contract = Contract(Annotated[SecretId, Title("Request identifier")])
    info = inspect_contract(contract)
    assert info.title == "Request identifier"
    assert info.description == "Alias identity."
    assert info.sensitive
    with pytest.raises(ValidationError) as raised:
        contract.validate(SECRET)
    _assert_redacted(raised.value)

    still_sensitive = inspect_contract(Contract(Annotated[SecretId, Sensitive(False)]))
    assert still_sensitive.sensitive


def test_metadata_normalization_rejects_duplicates_and_invalid_declarations() -> None:
    with pytest.raises(TypeError, match="only one Title"):

        class Duplicate(Spec):
            value: Annotated[int, Title("First"), Title("Second")]

    with pytest.raises(TypeError, match="not Specs"):

        class InvalidSpecMetadata(Spec, metadata=(Sensitive(),)):
            value: int

    with pytest.raises(TypeError, match="non-empty string"):
        Title("")
    with pytest.raises(TypeError, match="bool"):
        Sensitive(1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="bool"):
        Deprecated(1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="bool"):
        ReadOnly(1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="bool"):
        WriteOnly(1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="at least one"):
        Examples()
    with pytest.raises(ValueError, match="finite"):
        Examples(float("inf"))
    with pytest.raises(TypeError, match="exact strings"):
        Examples({1: "value"})
    with pytest.raises(TypeError, match="JSON-compatible"):
        Examples(object())
    assert Examples(1.5, [1, "two"]).values == (1.5, (1, "two"))
    with pytest.raises(TypeError, match="iterable"):
        normalize_metadata("invalid")

    class UnknownMetadata(Spec):
        value: Annotated[int, object()]

    assert inspect_spec(UnknownMetadata).fields[0].title is None

    with pytest.raises(TypeError, match="Spec metadata must be an iterable"):

        class InvalidMetadataContainer(Spec, metadata=1):  # type: ignore[arg-type]
            value: int

    with pytest.raises(AssertionError):
        schema_contains_sensitive_metadata(cast(Schema, object()))

    class Deferred(Spec):
        child: "NotYetDefined"  # noqa: F821

    assert not schema_contains_sensitive_metadata(SpecReferenceSchema(Deferred))


def test_read_write_and_sensitive_semantics_remain_distinct() -> None:
    class Credentials(Spec):
        password: Annotated[str, Sensitive(), WriteOnly()]
        identifier: Annotated[int, ReadOnly()]

    value = Credentials.from_mapping({"password": SECRET, "identifier": 1})

    assert value.to_dict() == {"password": SECRET, "identifier": 1}
    assert value.to_json() == f'{{"password":"{SECRET}","identifier":1}}'
    assert repr(value) == "Credentials(password='<redacted>', identifier=1)"


def test_sensitive_structural_union_alias_and_nested_boundary_failures_redact() -> None:
    class Inner(Spec):
        value: int

    class Payload(Spec):
        password: Annotated[int, Sensitive()]
        alternatives: Annotated[int | UUID, Sensitive()]
        nested: Annotated[Inner, Sensitive()]
        token: Annotated[int, Alias("access-token"), Sensitive()]

    cases = (
        lambda: Payload(password=SECRET, alternatives=1, nested=Inner(value=1), token=1),
        lambda: Payload(password=SECRET * 10_000, alternatives=1, nested=Inner(value=1), token=1),
        lambda: Payload(password=SECRET.encode(), alternatives=1, nested=Inner(value=1), token=1),
        lambda: Payload(password=1, alternatives=SECRET, nested=Inner(value=1), token=1),
        lambda: Payload.from_mapping(
            {"password": 1, "alternatives": 1, "nested": {"value": SECRET}, "access-token": 1}
        ),
        lambda: Payload.from_mapping(
            {"password": 1, "alternatives": 1, "nested": {"value": 1}, "access-token": SECRET}
        ),
    )
    for operation in cases:
        with pytest.raises(ValidationError) as raised:
            operation()
        _assert_redacted(raised.value)


def test_sensitive_mapping_and_set_members_do_not_leak_through_locations() -> None:
    class Payload(Spec):
        mapping: Annotated[dict[str, int], Sensitive()]
        members: Annotated[set[int], Sensitive()]

    with pytest.raises(ValidationError) as mapping_error:
        Payload(mapping={SECRET: "bad"}, members={1})  # type: ignore[dict-item]
    _assert_redacted(mapping_error.value)
    assert mapping_error.value.location == ("mapping", "<redacted>")

    with pytest.raises(ValidationError) as set_error:
        Payload(mapping={}, members={SECRET})  # type: ignore[arg-type]
    _assert_redacted(set_error.value)
    assert set_error.value.location == ("members", "<redacted>")


def test_outer_sensitive_prefix_redacts_already_sensitive_nested_detail() -> None:
    class Inner(Spec):
        mapping: Annotated[dict[str, int], Sensitive()]

    class Outer(Spec):
        inner: Annotated[Inner, Sensitive()]

    with pytest.raises(ValidationError) as raised:
        Outer.from_mapping({"inner": {"mapping": {SECRET: "bad"}}})

    _assert_redacted(raised.value)
    assert raised.value.location == ("inner", "mapping", "<redacted>")

    class InnerPublic(Spec):
        mapping: dict[str, int]

    class OuterSensitive(Spec):
        inner: Annotated[InnerPublic, Sensitive()]

    with pytest.raises(ValidationError) as prefixed:
        OuterSensitive.from_mapping({"inner": {"mapping": {SECRET: "bad"}}})
    _assert_redacted(prefixed.value)
    assert prefixed.value.location == ("inner", "mapping", "<redacted>")


class SensitivePayload(TypedDict):
    secret: Annotated[int, Sensitive(), Description("Sensitive child.")]


def test_sensitive_typed_dict_contract_python_and_json_failures_redact() -> None:
    contract = Contract(SensitivePayload)

    for operation in (
        lambda: contract.validate({"secret": SECRET}),
        lambda: contract.from_python({"secret": SECRET}),
        lambda: contract.from_json(f'{{"secret":"{SECRET}"}}'),
    ):
        with pytest.raises(ValidationError) as raised:
            operation()
        _assert_redacted(raised.value)
        assert raised.value.location == ("secret",)


def test_sensitive_transform_field_check_and_whole_check_drop_callback_causes() -> None:
    class Hooks(Spec):
        secret: Annotated[int, Sensitive()]
        other: int

        @transform("secret")
        def parse(secret: object) -> object:
            if secret == SECRET:
                raise ValueError(SECRET)
            return secret

        @check("secret")
        def positive(secret: int) -> None:
            if secret < 0:
                raise ValueError(SECRET)

        @check("secret", "other")
        def ordered(secret: int, other: int) -> None:
            if secret > other:
                raise ValueError(SECRET)

    for values in (
        {"secret": SECRET, "other": 1},
        {"secret": -1, "other": 1},
        {"secret": 2, "other": 1},
    ):
        with pytest.raises(ValidationError) as raised:
            Hooks(**values)  # type: ignore[arg-type]
        _assert_redacted(raised.value)
        assert raised.value.__cause__ is None


def test_sensitive_factory_and_json_syntax_failures_discard_raw_evidence() -> None:
    def fail() -> str:
        raise RuntimeError(SECRET)

    class Factory(Spec):
        secret: Annotated[str, Sensitive()] = field(default_factory=fail)

    with pytest.raises(ValidationError) as factory_error:
        Factory()
    _assert_redacted(factory_error.value)
    assert factory_error.value.__cause__ is None

    with pytest.raises(ValidationError) as json_error:
        Factory.from_json(f'{{"secret":"{SECRET}"')
    _assert_redacted(json_error.value)
    assert json_error.value.__cause__ is None


def test_sensitive_serialization_hook_failure_drops_secret_cause() -> None:
    class Credentials(Spec):
        password: Annotated[str, Sensitive()]

        @serialize("password")
        def encode(password: str) -> str:
            raise ValueError(f"cannot encode {password}")

    value = Credentials(password=SECRET)
    with pytest.raises(SerializationError) as raised:
        value.to_dict()

    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)
    assert raised.value.location == ("password",)
    assert raised.value.sensitive
    assert raised.value.__cause__ is None


def test_sensitive_json_codec_failure_drops_secret_cause() -> None:
    class Credentials(Spec):
        password: Annotated[str, Sensitive()]

    def reject(projected: object) -> str:
        raise ValueError(repr(projected))

    with pytest.raises(SerializationError) as raised:
        Credentials(password=SECRET).to_json(dumps=reject)

    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)
    assert raised.value.sensitive
    assert raised.value.__cause__ is None


def test_metadata_free_constructor_contains_no_metadata_runtime_branch() -> None:
    class Point(Spec):
        x: int
        y: int

    initializer = vars(Point)["__init__"]
    assert "metadata" not in initializer.__code__.co_names
    assert "sensitive" not in initializer.__code__.co_names


def test_sensitive_inheritance_is_sticky_without_and_clear_with_explicit_opt_out() -> None:
    class Base(Spec):
        value: Annotated[int | str, Sensitive(), Description("Inherited.")]

    class Narrowed(Base):
        value: int

    class ExplicitlyPublic(Base):
        value: Annotated[int, Sensitive(False)]

    inherited = inspect_spec(Narrowed).fields[0]
    public = inspect_spec(ExplicitlyPublic).fields[0]
    assert inherited.sensitive
    assert inherited.description == "Inherited."
    assert not public.sensitive
    assert public.description == "Inherited."

    with pytest.raises(ValidationError) as redacted:
        Narrowed(value=SECRET)
    _assert_redacted(redacted.value)
    with pytest.raises(ValidationError) as visible:
        ExplicitlyPublic(value=SECRET)
    assert visible.value.value == SECRET


def test_metadata_survives_generic_specialization_and_recursive_finalization() -> None:
    class Box[T](Spec, metadata=(Title("Box"),)):
        value: Annotated[T, Sensitive(), Description("Boxed value.")]

    class Node(Spec):
        secret: Annotated[int, Sensitive()]
        children: list["Node"]

    box = inspect_spec(Box[int])
    node = inspect_spec(Node)
    assert box.title == "Box"
    assert box.fields[0].description == "Boxed value."
    assert box.fields[0].sensitive
    assert node.recursive
    assert node.fields[0].sensitive

    with pytest.raises(ValidationError) as raised:
        Node.from_mapping({"secret": 1, "children": [{"secret": SECRET, "children": []}]})
    _assert_redacted(raised.value)
    assert raised.value.location == ("children", 0, "secret")


class _ExplodingRepr:
    calls = 0

    def __repr__(self) -> str:
        type(self).calls += 1
        raise RuntimeError(SECRET)


def test_sensitive_failure_never_calls_hostile_repr() -> None:
    class Payload(Spec):
        secret: Annotated[int, Sensitive()]

    _ExplodingRepr.calls = 0
    with pytest.raises(ValidationError) as raised:
        Payload(secret=_ExplodingRepr())  # type: ignore[arg-type]

    _assert_redacted(raised.value)
    assert _ExplodingRepr.calls == 0
    assert raised.value.received_type is _ExplodingRepr


@given(st.text(max_size=500))
def test_arbitrary_sensitive_text_never_appears_in_public_error_surfaces(secret: str) -> None:
    class Payload(Spec):
        secret: Annotated[int, Sensitive()]

    payload = f"{SECRET}:{secret}"
    with pytest.raises(ValidationError) as raised:
        Payload(secret=payload)  # type: ignore[arg-type]

    assert raised.value.value == "<redacted>"
    assert raised.value.errors()[0]["input"] == "<redacted>"
    assert payload not in str(raised.value)
