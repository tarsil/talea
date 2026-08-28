import dis
import gc
import json
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Annotated, Literal, NotRequired, TypedDict, cast

import pytest
from hypothesis import given, strategies as st

import talea.serialization.api as serialization_api
import talea.serialization.artifacts as output_artifacts
from talea import (
    Alias,
    Discriminator,
    MinLength,
    Sensitive,
    Spec,
    WriteOnly,
    derive_spec,
    serialize,
)
from talea.introspection import inspect_spec
from talea.serialization.compilation import compile_selected_serialization
from talea.serialization.emission import _schema_accepts_selection
from talea.serialization.selection import _object_variants, normalize_selection


class Address(Spec):
    city: str
    country: str
    internal_code: str


class Profile(Spec):
    display_name: Annotated[str, Alias("displayName")]
    address: Address
    internal_note: str
    nickname: str | None = None


class Account(Spec):
    identifier: Annotated[int, Alias("id")]
    profile: Profile
    audit: str


def account() -> Account:
    return Account(
        identifier=1,
        profile=Profile(
            display_name="Ada",
            address=Address(city="Zurich", country="CH", internal_code="8001"),
            internal_note="private",
        ),
        audit="retained source state",
    )


def test_nested_spec_include_exclude_aliases_precedence_and_json() -> None:
    value = account()
    include = {
        "identifier": True,
        "profile": {
            "display_name": True,
            "address": {"city": True, "country": True},
            "internal_note": True,
        },
    }
    exclude = {
        "profile": {
            "address": {"country": True},
            "internal_note": True,
        }
    }

    assert value.to_dict(include=include, exclude=exclude) == {
        "id": 1,
        "profile": {"displayName": "Ada", "address": {"city": "Zurich"}},
    }
    assert value.to_dict(include=include, exclude=exclude, by_alias=False) == {
        "identifier": 1,
        "profile": {"display_name": "Ada", "address": {"city": "Zurich"}},
    }
    assert json.loads(value.to_json(include=include, exclude=exclude)) == {
        "id": 1,
        "profile": {"displayName": "Ada", "address": {"city": "Zurich"}},
    }
    assert value.to_dict(include={"profile": True}, exclude={"profile": {"internal_note": True}}) == {
        "profile": {
            "displayName": "Ada",
            "address": {"city": "Zurich", "country": "CH", "internal_code": "8001"},
            "nickname": None,
        }
    }


def test_legacy_sets_and_leaf_mappings_keep_top_level_semantics() -> None:
    value = account()
    expected = {"id": 1, "audit": "retained source state"}

    assert value.to_dict(include={"identifier", "audit"}) == expected
    assert value.to_dict(exclude={"profile"}) == expected
    assert value.to_dict(include={"identifier": True, "audit": True}) == expected
    with pytest.raises(ValueError, match="unknown field"):
        value.to_dict(include={"id": True})


class Member(Spec):
    name: str
    email: str


class Collections(Spec):
    listed: list[Member]
    variadic: tuple[Member, ...]
    unique: set[Member]
    frozen: frozenset[Member]
    indexed: dict[str, Member]


def test_uniform_container_and_mapping_value_selection_preserves_detachment() -> None:
    first = Member(name="Ada", email="ada@example.test")
    second = Member(name="Grace", email="grace@example.test")
    value = Collections(
        listed=[first, second],
        variadic=(first, second),
        unique={first},
        frozen=frozenset({second}),
        indexed={"first": first, "second": second},
    )
    selection = {
        "listed": {"name": True},
        "variadic": {"name": True},
        "indexed": {"name": True},
    }

    projected = value.to_dict(include=selection)
    assert projected == {
        "listed": [{"name": "Ada"}, {"name": "Grace"}],
        "variadic": ({"name": "Ada"}, {"name": "Grace"}),
        "indexed": {"first": {"name": "Ada"}, "second": {"name": "Grace"}},
    }
    cast(list[dict[str, str]], projected["listed"])[0]["name"] = "changed"
    assert first.name == "Ada"
    assert json.loads(value.to_json(include={"unique": {"name": True}, "frozen": {"name": True}})) == {
        "unique": [{"name": "Ada"}],
        "frozen": [{"name": "Grace"}],
    }
    with pytest.raises(ValueError, match="cannot preserve hashable"):
        value.to_dict(include={"unique": {"name": True}})
    with pytest.raises(ValueError, match="cannot preserve hashable"):
        value.to_dict(include={"frozen": {"name": True}})


class Employee(Spec):
    name: str
    employee_id: int


class Office(Spec):
    name: str
    city: str


class TupleEnvelope(Spec):
    compatible: tuple[Employee, Office]
    incompatible: tuple[Employee, Address]


def test_fixed_tuple_requires_one_subtree_valid_for_every_position() -> None:
    value = TupleEnvelope(
        compatible=(Employee(name="Ada", employee_id=1), Office(name="HQ", city="Zurich")),
        incompatible=(
            Employee(name="Grace", employee_id=2),
            Address(city="London", country="GB", internal_code="SW1"),
        ),
    )
    assert value.to_dict(include={"compatible": {"name": True}}) == {"compatible": ({"name": "Ada"}, {"name": "HQ"})}
    with pytest.raises(ValueError, match="unknown field"):
        value.to_dict(include={"incompatible": {"employee_id": True}})


class Person(Spec):
    name: str


class Administrator(Spec):
    level: int


class UnionEnvelope(Spec):
    subject: Person | Administrator | None


class PersonBranch(Spec):
    detail: Person


class AdministratorBranch(Spec):
    detail: Administrator


class NestedUnionEnvelope(Spec):
    subject: PersonBranch | AdministratorBranch


def test_ordinary_union_compiles_branch_specific_projections() -> None:
    selection = {"subject": {"name": True, "level": True}}
    assert UnionEnvelope(subject=Person(name="Ada")).to_dict(include=selection) == {"subject": {"name": "Ada"}}
    assert UnionEnvelope(subject=Administrator(level=3)).to_dict(include=selection) == {"subject": {"level": 3}}
    assert UnionEnvelope(subject=None).to_dict(include=selection) == {"subject": None}
    with pytest.raises(ValueError, match="unknown field"):
        UnionEnvelope(subject=None).to_dict(include={"subject": {"missing": True}})
    nested_selection = {"subject": {"detail": {"name": True, "level": True}}}
    assert NestedUnionEnvelope(subject=PersonBranch(detail=Person(name="Ada"))).to_dict(include=nested_selection) == {
        "subject": {"detail": {"name": "Ada"}}
    }
    assert NestedUnionEnvelope(subject=AdministratorBranch(detail=Administrator(level=3))).to_dict(
        include=nested_selection
    ) == {"subject": {"detail": {"level": 3}}}


type NamedPerson = Person
type RecursiveMap = int | dict[str, RecursiveMap]


class ContainerUnionEnvelope(Spec):
    subject: NamedPerson | Annotated[list[Person], MinLength(1)] | dict[str, Administrator] | tuple[Person, Office]


def test_union_selection_traverses_named_constrained_and_container_branches() -> None:
    selection = {"subject": {"name": True, "level": True}}
    assert ContainerUnionEnvelope(subject=Person(name="Ada")).to_dict(include=selection) == {"subject": {"name": "Ada"}}
    assert ContainerUnionEnvelope(subject=[Person(name="Grace")]).to_dict(include=selection) == {
        "subject": [{"name": "Grace"}]
    }
    assert ContainerUnionEnvelope(subject={"root": Administrator(level=3)}).to_dict(include=selection) == {
        "subject": {"root": {"level": 3}}
    }
    assert ContainerUnionEnvelope(subject=(Person(name="Ada"), Office(name="HQ", city="Zurich"))).to_dict(
        include=selection
    ) == {"subject": ({"name": "Ada"}, {"name": "HQ"})}


def test_recursive_schema_classification_stops_at_named_back_edges() -> None:
    from talea.schema.resolution import resolve_annotation

    recursive_schema = resolve_annotation(RecursiveMap)
    assert _object_variants(recursive_schema) == ()
    assert _schema_accepts_selection(recursive_schema) is False

    structural_schema = vars(StructuralEnvelope)["__talea_artifacts__"].schema
    contact_schema = next(field.schema for field in structural_schema.fields if field.name == "contact")
    payload_schema = next(field.schema for field in structural_schema.fields if field.name == "payload")
    assert len(_object_variants(contact_schema)) == 1
    assert len(_object_variants(payload_schema)) == 1


class HookUnionBranch(Spec):
    shared: int

    @serialize("shared")
    def output(shared: int) -> dict[str, int]:
        return {"value": shared}


class StructuralUnionBranch(Spec):
    shared: Person


class HookUnionEnvelope(Spec):
    subject: HookUnionBranch | StructuralUnionBranch


def test_union_selection_rejects_descent_when_one_branch_uses_a_hook() -> None:
    value = HookUnionEnvelope(subject=StructuralUnionBranch(shared=Person(name="Ada")))
    with pytest.raises(ValueError, match="cannot descend through serializer"):
        value.to_dict(include={"subject": {"shared": {"name": True}}})


class Card(Spec):
    kind: Annotated[Literal["card"], Alias("type")]
    number: str
    network_note: str


class Bank(Spec):
    kind: Annotated[Literal["bank"], Alias("type")]
    iban: str
    routing_note: str


type Payment = Annotated[Card | Bank, Discriminator("kind")]


class PaymentEnvelope(Spec):
    payment: Payment


class OptionalPaymentEnvelope(Spec):
    payment: Payment | None


def test_tagged_union_keeps_direct_dispatch_and_requires_discriminator() -> None:
    selection = {"payment": {"kind": True, "number": True, "iban": True}}
    card = PaymentEnvelope(payment=Card(kind="card", number="4111", network_note="private"))
    bank = PaymentEnvelope(payment=Bank(kind="bank", iban="CH1", routing_note="private"))

    assert card.to_dict(include=selection) == {"payment": {"type": "card", "number": "4111"}}
    assert bank.to_dict(include=selection) == {"payment": {"type": "bank", "iban": "CH1"}}
    with pytest.raises(ValueError, match="must retain tagged-union discriminator"):
        card.to_dict(include={"payment": {"number": True}})
    with pytest.raises(ValueError, match="cannot remove tagged-union discriminator"):
        card.to_dict(exclude={"payment": {"kind": True}})
    with pytest.raises(ValueError, match="unknown field"):
        card.to_dict(include={"payment": {"type": True}})
    assert OptionalPaymentEnvelope(payment=None).to_dict(
        include={"payment": {"kind": True, "number": True, "iban": True}}
    ) == {"payment": None}


@dataclass
class DataclassContact:
    display_name: Annotated[str, Alias("displayName")]
    email: str


class ContactData(TypedDict):
    name: str
    email: str
    note: NotRequired[str]


class StructuralEnvelope(Spec):
    contact: DataclassContact
    payload: ContactData


def test_dataclass_and_typed_dict_consume_the_same_selection_owner() -> None:
    value = StructuralEnvelope(
        contact=DataclassContact(display_name="Ada", email="ada@example.test"),
        payload={"name": "Grace", "email": "grace@example.test", "note": "internal"},
    )
    selection = {"contact": {"display_name": True}, "payload": {"name": True}}

    assert value.to_dict(include=selection) == {
        "contact": {"displayName": "Ada"},
        "payload": {"name": "Grace"},
    }
    assert json.loads(value.to_json(include=selection)) == {
        "contact": {"displayName": "Ada"},
        "payload": {"name": "Grace"},
    }
    with pytest.raises(ValueError, match="unknown field"):
        value.to_dict(include={"contact": {"displayName": True}})

    @dataclass
    class OptionalDataclass:
        name: str
        note: str | None

    class OptionalPayload(TypedDict):
        name: str
        note: str | None

    class OptionalEnvelope(Spec):
        dataclass_value: OptionalDataclass
        typed_value: OptionalPayload

    optional = OptionalEnvelope(
        dataclass_value=OptionalDataclass(name="Ada", note=None),
        typed_value={"name": "Grace", "note": None},
    )
    optional_selection = {
        "dataclass_value": {"name": True, "note": True},
        "typed_value": {"name": True, "note": True},
    }
    assert optional.to_dict(include=optional_selection, exclude_none=True) == {
        "dataclass_value": {"name": "Ada"},
        "typed_value": {"name": "Grace"},
    }
    assert optional.to_dict(
        include={"dataclass_value": True, "typed_value": True},
        exclude={"dataclass_value": {"note": True}, "typed_value": {"note": True}},
    ) == {
        "dataclass_value": {"name": "Ada"},
        "typed_value": {"name": "Grace"},
    }


class RecursivePayload(TypedDict):
    value: int
    child: NotRequired["RecursivePayload"]


class RecursivePayloadEnvelope(Spec):
    payload: RecursivePayload


def test_recursive_typed_dict_selection_follows_finite_named_back_edges() -> None:
    value = RecursivePayloadEnvelope(payload={"value": 1, "child": {"value": 2}})
    selection = {"payload": {"value": True, "child": {"value": True}}}

    assert value.to_dict(include=selection) == {"payload": {"value": 1, "child": {"value": 2}}}


def test_partial_presence_and_directional_shape_remain_canonical() -> None:
    class Source(Spec):
        public: Profile
        secret: Annotated[str, WriteOnly()]

    Patch = derive_spec(Source, partial=True)
    Output = derive_spec(Source, mode="output")

    patch = Patch.from_mapping({"secret": "present"})
    assert patch.to_dict(include={"public": {"display_name": True}}) == {}
    output = Output(public=account().profile)
    assert output.to_dict(include={"public": {"display_name": True}}) == {"public": {"displayName": "Ada"}}
    with pytest.raises(ValueError, match="unknown field"):
        output.to_dict(include={"secret": True})


class RecursiveNode(Spec):
    value: int
    child: "RecursiveNode | None" = None


def test_recursive_selection_is_bounded_by_the_finite_input_tree() -> None:
    value = RecursiveNode(value=1, child=RecursiveNode(value=2, child=RecursiveNode(value=3)))
    selection = {"value": True, "child": {"value": True, "child": {"value": True}}}

    assert value.to_dict(include=selection) == {
        "value": 1,
        "child": {"value": 2, "child": {"value": 3}},
    }
    assert json.loads(value.to_json(include=selection)) == {
        "value": 1,
        "child": {"value": 2, "child": {"value": 3}},
    }


def test_invalid_shapes_constraints_and_empty_nested_trees_fail_before_reads() -> None:
    value = account()
    with pytest.raises(ValueError, match="unknown field"):
        value.to_dict(include={"profile": {"missing": True}})
    with pytest.raises(ValueError, match="scalar field"):
        value.to_dict(include={"identifier": {"anything": True}})
    with pytest.raises(ValueError, match="cannot be empty"):
        value.to_dict(include={"profile": {}})
    with pytest.raises(TypeError, match="must map to True"):
        value.to_dict(include={"profile": False})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="must map to True"):
        value.to_dict(include={"profile": "display_name"})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="exact strings"):
        value.to_dict(include=cast(object, {1: True}))  # type: ignore[arg-type]

    class ScalarUnion(Spec):
        value: int | str

    with pytest.raises(ValueError, match="scalar field"):
        ScalarUnion(value=1).to_dict(include={"value": {"anything": True}})

    class SizedEnvelope(Spec):
        members: Annotated[list[Member], MinLength(0)]

    sized = SizedEnvelope(members=[Member(name="Ada", email="a@example.test")])
    assert sized.to_dict(include={"members": {"name": True}}) == {"members": [{"name": "Ada"}]}


def test_exclude_none_applies_at_explicitly_selected_object_levels() -> None:
    value = account()
    assert value.to_dict(
        include={"profile": {"display_name": True, "nickname": True}},
        exclude_none=True,
    ) == {"profile": {"displayName": "Ada"}}
    assert account().to_dict(include={"profile": True}, exclude_none=True)["profile"] == {
        "displayName": "Ada",
        "address": {"city": "Zurich", "country": "CH", "internal_code": "8001"},
        "internal_note": "private",
        "nickname": None,
    }


def test_serializer_hook_is_a_selectable_leaf_but_not_declared_structure() -> None:
    calls: list[int] = []

    class Hooked(Spec):
        value: int

        @serialize("value")
        def output(value: int) -> dict[str, int]:
            calls.append(value)
            return {"nested": value}

    value = Hooked(value=1)
    assert value.to_dict(include={"value": True}) == {"value": {"nested": 1}}
    with pytest.raises(ValueError, match="cannot descend through serializer"):
        value.to_dict(include={"value": {"nested": True}})
    assert calls == [1]


def test_sensitive_fields_keep_ordinary_output_semantics() -> None:
    class Secret(Spec):
        token: Annotated[str, Sensitive()]
        label: str

    class SecretEnvelope(Spec):
        secret: Secret

    assert SecretEnvelope(secret=Secret(token="visible-by-policy", label="x")).to_dict(
        include={"secret": {"token": True}}
    ) == {"secret": {"token": "visible-by-policy"}}


def test_selector_is_frozen_before_field_access_and_hostile_names_are_data() -> None:
    selection: dict[str, object] = {"profile": {"display_name": True}}

    class MutatingAccount(Account):
        def __getattribute__(self, name: str) -> object:
            if name == "profile":
                selection["profile"] = {"internal_note": True}
            return super().__getattribute__(name)

    value = MutatingAccount(
        identifier=1,
        profile=account().profile,
        audit="unchanged",
    )
    assert value.to_dict(include=cast(object, selection)) == {  # type: ignore[arg-type]
        "profile": {"displayName": "Ada"}
    }
    assert selection == {"profile": {"internal_note": True}}
    with pytest.raises(ValueError, match="unknown field"):
        value.to_dict(include={"profile": {"x'\n__import__('os')": True}})


def test_deep_broad_and_concurrent_selection_is_finite_and_operation_local() -> None:
    depth = 40
    node = RecursiveNode(value=depth)
    selection: dict[str, object] = {"value": True}
    for index in range(depth - 1, -1, -1):
        node = RecursiveNode(value=index, child=node)
        selection = {"value": True, "child": selection}
    output = node.to_dict(include=cast(object, selection))  # type: ignore[arg-type]
    for expected in range(depth + 1):
        assert output["value"] == expected
        if expected < depth:
            output = cast(dict[str, object], output["child"])

    field_count = 1_000
    Broad = type(
        "Broad",
        (Spec,),
        {"__annotations__": {f"field_{index}": int for index in range(field_count)}},
    )
    broad = Broad(**{f"field_{index}": index for index in range(field_count)})
    broad_selection = {f"field_{index}": True for index in range(field_count)}
    assert len(broad.to_dict(include=broad_selection)) == field_count

    shared = {"profile": {"display_name": True}}
    with ThreadPoolExecutor(max_workers=8) as executor:
        outputs = tuple(executor.map(lambda _: account().to_dict(include=shared), range(32)))
    assert all(output == {"profile": {"displayName": "Ada"}} for output in outputs)


def test_selected_cache_is_concurrent_bounded_and_does_not_retain_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = output_artifacts.compile_selected_serialization
    calls = 0

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(output_artifacts, "compile_selected_serialization", counted)

    class CachedChild(Spec):
        first: int
        second: int

    class CachedParent(Spec):
        child: CachedChild

    value = CachedParent(child=CachedChild(first=1, second=2))
    shared = {"child": {"first": True}}
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(
            item == {"child": {"first": 1}} for item in executor.map(lambda _: value.to_dict(include=shared), range(32))
        )
    assert calls == 1

    field_count = 40
    Many = type(
        "Many",
        (Spec,),
        {"__annotations__": {f"field_{index}": int for index in range(field_count)}},
    )
    Holder = type("Holder", (Spec,), {"__annotations__": {"value": Many}})
    holder = Holder.from_mapping({"value": Many(**{f"field_{index}": index for index in range(field_count)})})
    for index in range(field_count):
        assert holder.to_dict(include={"value": {f"field_{index}": True}}) == {"value": {f"field_{index}": index}}
    variants = vars(Holder)["__talea_artifacts__"].outputs.variants
    assert variants is not None
    assert sum(key[0] == "selected" for key in variants) == 32

    def collectible() -> tuple[weakref.ReferenceType[type[Spec]], weakref.ReferenceType[type[Spec]]]:
        class LocalChild(Spec):
            value: int

        class LocalParent(Spec):
            child: LocalChild

        LocalParent(child=LocalChild(value=1)).to_dict(include={"child": {"value": True}})
        return weakref.ref(LocalParent), weakref.ref(LocalChild)

    parent_reference, child_reference = collectible()
    gc.collect()
    assert parent_reference() is None
    assert child_reference() is None


def test_plain_and_top_level_paths_do_not_enter_nested_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Canary(Spec):
        first: int
        second: int

    value = Canary(first=1, second=2)
    assert value.to_dict() == {"first": 1, "second": 2}

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("nested selection machinery executed")

    monkeypatch.setattr(serialization_api, "normalize_selection", forbidden)
    monkeypatch.setattr(type(vars(Canary)["__talea_artifacts__"].outputs), "selected_for", forbidden)
    assert value.to_dict() == {"first": 1, "second": 2}
    assert value.to_json() == '{"first":1,"second":2}'


def test_selected_output_does_not_mutate_schema_introspection_or_source() -> None:
    value = account()
    source = (
        value.identifier,
        value.profile.display_name,
        value.profile.address.city,
        value.profile.internal_note,
        value.audit,
    )
    json_schema = Account.json_schema()
    openapi = Account.openapi_schema()
    info = inspect_spec(Account)

    assert value.to_dict(include={"profile": {"display_name": True}}) == {"profile": {"displayName": "Ada"}}
    assert Account.json_schema() == json_schema
    assert Account.openapi_schema() == openapi
    assert inspect_spec(Account) is info
    assert (
        value.identifier,
        value.profile.display_name,
        value.profile.address.city,
        value.profile.internal_note,
        value.audit,
    ) == source


def test_compiled_selection_reads_only_selected_top_level_siblings() -> None:
    schema = vars(Account)["__talea_artifacts__"].schema
    normalized = normalize_selection(
        {"identifier": True, "profile": {"display_name": True}},
        schema,
        "include",
    )
    assert normalized is not None
    compiled = compile_selected_serialization(schema, "python", True, normalized, None, False)
    attributes = {
        instruction.argval for instruction in dis.get_instructions(compiled) if instruction.opname == "LOAD_ATTR"
    }

    assert attributes == {"identifier", "profile"}
    assert "audit" not in attributes
    assert compiled(account()) == {"id": 1, "profile": {"displayName": "Ada"}}


@given(st.sets(st.sampled_from(("display_name", "internal_note", "nickname")), min_size=1))
def test_generated_nested_include_contains_only_selected_present_fields(fields: set[str]) -> None:
    value = account()
    projected = cast(dict[str, object], value.to_dict(include={"profile": fields})["profile"])
    emitted = {
        "display_name": "displayName",
        "internal_note": "internal_note",
        "nickname": "nickname",
    }
    expected = {emitted[name] for name in fields if name != "nickname"}
    if "nickname" in fields:
        expected.add("nickname")
    assert set(projected) == expected
