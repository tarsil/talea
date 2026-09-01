import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Annotated, Literal

import pytest

from talea import (
    Alias,
    Contract,
    Discriminator,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
    ValidationError,
    apply_patch,
    create_spec,
    derive_spec,
)
from talea.introspection import inspect_contract, inspect_spec
from talea.schema import DataclassSchema, TaggedUnionSchema
from talea.schema.resolution import TaggedUnionDeclarationError


def test_inheritance_retains_replaces_and_removes_migration_truth() -> None:
    class Base(Spec):
        identifier: Annotated[int, Alias("accountId", legacy=("id", "account_id"))]

    class AnnotationOnly(Base):
        identifier: int

    class Replaced(Base):
        identifier: Annotated[int, Alias("customerId", legacy=("customer_id",))]

    class LegacyRemoved(Base):
        identifier: Annotated[int, Alias("accountId")]

    inherited = inspect_spec(AnnotationOnly).fields[0]
    assert inherited.accepted_input_names == ("accountId", "id", "account_id")
    assert AnnotationOnly.from_mapping({"id": 1}).identifier == 1
    assert Replaced.from_mapping({"customer_id": 2}).identifier == 2
    assert inspect_spec(Replaced).fields[0].accepted_input_names == ("customerId", "customer_id")
    assert LegacyRemoved.from_mapping({"accountId": 3}).identifier == 3
    assert inspect_spec(LegacyRemoved).fields[0].legacy_names == ()
    with pytest.raises(ValidationError):
        LegacyRemoved.from_mapping({"id": 3})


def test_inherited_override_collisions_are_rejected_when_effective_truth_finalizes() -> None:
    class Base(Spec):
        identifier: Annotated[int, Alias("accountId", legacy=("id",))]

    with pytest.raises(ValueError, match="unique accepted input"):

        class CurrentLegacy(Base):
            other: Annotated[int, Alias("id")]

    with pytest.raises(ValueError, match="unique accepted input"):

        class LegacyLegacy(Base):
            other: Annotated[int, Alias("otherId", legacy=("id",))]


def test_derivation_partial_directional_dynamic_generic_and_recursive_specs_retain_truth() -> None:
    class Account(Spec):
        identifier: Annotated[int, Alias("accountId", legacy=("id", "account_id"))]
        label: str

    Included = derive_spec(Account, include=("identifier",))
    Excluded = derive_spec(Account, exclude=("identifier",))
    Input = derive_spec(Account, mode="input")
    Output = derive_spec(Account, mode="output")
    Patch = derive_spec(Account, partial=True)
    Dynamic = create_spec(
        "DynamicAccount",
        {"identifier": Annotated[int, Alias("accountId", legacy=("id",))]},
    )

    assert Included.from_mapping({"account_id": 1}).identifier == 1
    assert tuple(field.name for field in inspect_spec(Excluded).fields) == ("label",)
    assert Input.from_mapping({"id": 2, "label": "input"}).identifier == 2
    assert Output.from_mapping({"id": 3, "label": "output"}).to_dict() == {
        "accountId": 3,
        "label": "output",
    }
    patch = Patch.from_mapping({"account_id": 4})
    assert patch.present_fields == frozenset({"identifier"})
    assert apply_patch(Account(identifier=1, label="before"), patch).identifier == 4
    with pytest.raises(ValidationError, match="Multiple accepted names"):
        Patch.from_mapping({"accountId": 1, "id": 1})
    assert Dynamic.from_json('{"id":5}').identifier == 5

    class Envelope[T](Spec):
        payload: Annotated[T, Alias("body", legacy=("payload",))]

    concrete = Envelope[Account].from_mapping({"payload": {"id": 6, "label": "nested"}})
    assert concrete.payload.identifier == 6

    class Node(Spec):
        value: Annotated[int, Alias("currentValue", legacy=("value",))]
        children: list[Node]

    recursive = Node.from_json('{"value":1,"children":[{"value":2,"children":[]}]}')
    assert recursive.children[0].value == 2
    assert recursive.to_json() == '{"currentValue":1,"children":[{"currentValue":2,"children":[]}]}'
    with pytest.raises(ValidationError) as conflict:
        Node.from_mapping({"value": 1, "children": [{"currentValue": 2, "value": 2, "children": []}]})
    assert conflict.value.location == ("children", 0, "currentValue")


def test_migrated_dataclass_mapping_json_conflicts_output_and_introspection() -> None:
    @dataclass
    class User:
        identifier: Annotated[int, Alias("userId", legacy=("id", "user_id"))]

    contract = Contract(User)
    assert contract.from_python({"userId": 1}) == User(1)
    assert contract.from_python({"id": 2}) == User(2)
    assert contract.from_json('{"user_id":3}') == User(3)
    for payload in ({"userId": 1, "id": 1}, {"id": 1, "user_id": 2}):
        with pytest.raises(ValidationError) as raised:
            contract.from_python(payload)
        assert raised.value.code == "alias_conflict"
        assert raised.value.location == ("userId",)
        assert "input" not in raised.value.errors()[0]
        assert raised.value.__cause__ is None
    with pytest.raises(ValidationError) as invalid:
        contract.from_python({"id": "secret"})
    assert invalid.value.location == ("userId",)
    assert contract.to_python(User(4)) == {"userId": 4}
    assert contract.to_json(User(5)) == '{"userId":5}'

    info = inspect_contract(contract)
    assert isinstance(info.schema, DataclassSchema)
    resolved = info.schema.fields[0]
    assert resolved.legacy_names == ("id", "user_id")
    assert resolved.accepted_input_names == ("userId", "id", "user_id")
    with pytest.raises(AttributeError):
        resolved.legacy_names = ()  # type: ignore[misc]


def test_dataclass_accepted_name_collisions_fail_during_schema_resolution() -> None:
    @dataclass
    class CanonicalCollision:
        first: Annotated[int, Alias("firstNow", legacy=("second",))]
        second: int

    @dataclass
    class AcceptedCollision:
        first: Annotated[int, Alias("firstNow", legacy=("old",))]
        second: Annotated[int, Alias("old")]

    with pytest.raises(ValueError, match="canonical field"):
        Contract(CanonicalCollision)
    with pytest.raises(ValueError, match="unique accepted input"):
        Contract(AcceptedCollision)


def test_dataclass_migration_preserves_defaults_factory_post_init_and_sensitive_safety() -> None:
    factory_calls = 0
    post_init_calls = 0

    def labels() -> list[str]:
        nonlocal factory_calls
        factory_calls += 1
        return []

    @dataclass
    class User:
        identifier: Annotated[int, Alias("userId", legacy=("id",))]
        tags: list[str] = field(default_factory=labels)

        def __post_init__(self) -> None:
            nonlocal post_init_calls
            post_init_calls += 1

    contract = Contract(User)
    result = contract.from_python({"id": 1})
    assert (result.identifier, result.tags) == (1, [])
    assert factory_calls == 1
    assert post_init_calls == 1

    @dataclass
    class Secret:
        token: Annotated[int, Alias("authToken", legacy=("token",)), Sensitive()]

    with pytest.raises(ValidationError) as conflict:
        Contract(Secret).from_python({"authToken": object(), "token": object()})
    assert conflict.value.code == "alias_conflict"
    assert conflict.value.errors()[0].get("input") is None
    assert conflict.value.__cause__ is None


def test_dataclass_and_spec_migration_compose_across_nesting_and_containers() -> None:
    class Account(Spec):
        identifier: Annotated[int, Alias("accountId", legacy=("id",))]

    @dataclass
    class Profile:
        account: Annotated[Account, Alias("accountData", legacy=("account",))]

    class Request(Spec):
        profiles: Annotated[list[Profile], Alias("items", legacy=("profiles",))]

    value = Request.from_json('{"profiles":[{"account":{"id":1}}]}')
    assert value.profiles[0].account.identifier == 1
    assert value.to_json() == '{"items":[{"accountData":{"accountId":1}}]}'

    @dataclass
    class Envelope:
        request: Annotated[Request, Alias("body", legacy=("request",))]

    nested = Contract(Envelope).from_python({"request": {"profiles": [{"account": {"id": 2}}]}})
    assert nested.request.profiles[0].account.identifier == 2

    for policy, code in (
        (ResourcePolicy(max_nodes=2), "nodes"),
        (ResourcePolicy(max_depth=2), "depth"),
    ):
        with pytest.raises(ResourceLimitError) as limited:
            Contract(Envelope).from_python(
                {"request": {"profiles": [{"account": {"id": 2}}]}},
                policy=policy,
            )
        assert limited.value.code == code


def _migrated_tagged_contract() -> Contract[object]:
    class Card(Spec):
        kind: Annotated[
            Literal["card"],
            Alias("type", legacy=("kind", "event_type")),
            Sensitive(),
        ]
        number: int

    class Bank(Spec):
        kind: Annotated[
            Literal["bank"],
            Alias("type", legacy=("kind", "event_type")),
            Sensitive(),
        ]
        account: int

    return Contract(Annotated[Card | Bank, Discriminator("kind")])


def test_tagged_discriminator_uses_one_migration_vocabulary_and_conflict_law() -> None:
    contract = _migrated_tagged_contract()
    assert type(contract.from_python({"type": "card", "number": 1})).__name__ == "Card"
    assert type(contract.from_python({"kind": "bank", "account": 2})).__name__ == "Bank"
    assert type(contract.from_json('{"event_type":"card","number":3}')).__name__ == "Card"

    for payload in (
        {"type": "card", "kind": "card", "number": 1},
        {"kind": "card", "event_type": "bank", "number": 1},
    ):
        with pytest.raises(ValidationError) as conflict:
            contract.from_python(payload)
        assert conflict.value.code == "alias_conflict"
        assert conflict.value.location == ("type",)
        assert conflict.value.__cause__ is None

    with pytest.raises(ValidationError) as missing:
        contract.from_python({"number": 1})
    with pytest.raises(ValidationError) as unknown:
        contract.from_python({"kind": "cash", "number": 1})
    assert missing.value.code == "discriminator_missing"
    assert unknown.value.code == "discriminator_unknown"
    assert unknown.value.location == ("type",)

    schema = inspect_contract(contract).schema
    assert isinstance(schema, TaggedUnionSchema)
    assert schema.accepted_input_names == ("type", "kind", "event_type")
    with pytest.raises(AttributeError):
        schema.accepted_input_names = ("changed",)  # type: ignore[misc]


def test_tagged_branches_require_identical_accepted_discriminator_names() -> None:
    class Card(Spec):
        kind: Annotated[Literal["card"], Alias("type", legacy=("kind",))]

    class Bank(Spec):
        kind: Annotated[Literal["bank"], Alias("type", legacy=("event_type",))]

    with pytest.raises(TaggedUnionDeclarationError, match="accepted input names"):
        Contract(Annotated[Card | Bank, Discriminator("kind")])


def test_recursive_tagged_migration_dispatches_each_level_directly() -> None:
    class Leaf(Spec):
        kind: Annotated[Literal["leaf"], Alias("type", legacy=("kind",))]
        value: int

    class Branch(Spec):
        kind: Annotated[Literal["branch"], Alias("type", legacy=("kind",))]
        children: list[Annotated[Leaf | Branch, Discriminator("kind")]]

    contract = Contract(Annotated[Leaf | Branch, Discriminator("kind")])
    value = contract.from_json('{"kind":"branch","children":[{"kind":"leaf","value":1}]}')
    assert type(value).__name__ == "Branch"
    assert type(value.children[0]).__name__ == "Leaf"
    assert contract.to_json(value) == '{"type":"branch","children":[{"type":"leaf","value":1}]}'


def test_migrated_compositions_are_thread_safe_on_first_and_retained_use() -> None:
    @dataclass
    class User:
        identifier: Annotated[int, Alias("userId", legacy=("id",))]

    class Payload(Spec):
        user: User

    dataclass_contract = Contract(User)
    tagged_contract = _migrated_tagged_contract()
    Patch = derive_spec(Payload, partial=True)

    def construct(index: int) -> int:
        user = dataclass_contract.from_python({"id": index})
        tagged = tagged_contract.from_python({"kind": "card", "number": index})
        patch = Patch.from_mapping({"user": {"id": index}})
        assert type(tagged).__name__ == "Card"
        return user.identifier + patch.user.identifier

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(construct, range(32))) == [index * 2 for index in range(32)]


def test_generated_zero_migration_paths_remain_specialized_and_names_are_bound_data() -> None:
    @dataclass
    class PlainDataclass:
        value: int

    @dataclass
    class MigratedDataclass:
        value: Annotated[int, Alias('current"\nname', legacy=("old\nname",))]

    plain_dataclass = Contract(PlainDataclass)
    migrated_dataclass = Contract(MigratedDataclass)
    plain_dataclass.from_python({"value": 1})
    migrated_dataclass.from_python({"old\nname": 1})
    plain_input = plain_dataclass._artifacts.python_input
    migrated_input = migrated_dataclass._artifacts.python_input
    assert plain_input is not None and migrated_input is not None
    assert "_alias_conflict" not in plain_input.__code__.co_names
    assert not any("accepted_names" in name for name in plain_input.__globals__)
    assert "_alias_conflict" in migrated_input.__code__.co_names
    assert 'current"\nname' not in migrated_input.__code__.co_consts
    assert "old\nname" not in migrated_input.__code__.co_consts
    MigratedDataclass.__annotations__["value"] = str
    assert migrated_dataclass.from_python({"old\nname": 2}).value == 2

    class PlainA(Spec):
        kind: Literal["a"]

    class PlainB(Spec):
        kind: Literal["b"]

    plain_tagged = Contract(Annotated[PlainA | PlainB, Discriminator("kind")])
    migrated_tagged = _migrated_tagged_contract()
    plain_tagged.from_python({"kind": "a"})
    migrated_tagged.from_python({"event_type": "card", "number": 1})
    plain_tagged_input = plain_tagged._artifacts.python_input
    migrated_tagged_input = migrated_tagged._artifacts.python_input
    assert plain_tagged_input is not None and migrated_tagged_input is not None
    assert "_alias_conflict" not in plain_tagged_input.__code__.co_names
    assert not any("accepted_names" in name for name in plain_tagged_input.__globals__)
    assert "_alias_conflict" in migrated_tagged_input.__code__.co_names
    schema = migrated_tagged._artifacts.schema
    assert isinstance(schema, TaggedUnionSchema)
    for branch in schema.branches:
        branch.schema.spec_type.__annotations__["kind"] = str
    assert type(migrated_tagged.from_python({"kind": "bank", "account": 2})).__name__ == "Bank"
    retained = [
        value for name, value in migrated_tagged_input.__globals__.items() if "discriminator_accepted_names" in name
    ]
    assert retained == [schema.accepted_input_names]
    assert retained[0] is schema.accepted_input_names


def test_distinct_dataclass_and_tagged_json_names_conflict_but_textual_duplicates_do_not() -> None:
    @dataclass
    class User:
        identifier: Annotated[int, Alias("userId", legacy=("id",))]

    dataclass_contract = Contract(User)
    tagged_contract = _migrated_tagged_contract()
    for contract, conflict_json, duplicate_json in (
        (dataclass_contract, '{"userId":1,"id":2}', '{"id":1,"id":2}'),
        (
            tagged_contract,
            '{"type":"card","kind":"card","number":1}',
            '{"kind":"card","kind":"card","number":1}',
        ),
    ):
        with pytest.raises(ValidationError) as conflict:
            contract.from_json(conflict_json)
        with pytest.raises(ValidationError) as duplicate:
            contract.from_json(duplicate_json)
        assert conflict.value.code == "alias_conflict"
        assert duplicate.value.code == "json_duplicate"


def test_migration_name_strings_remain_data_under_json_encoding() -> None:
    current = 'type"\n雪'
    legacy = "kind\nλ"

    class First(Spec):
        discriminator: Annotated[Literal["first"], Alias(current, legacy=(legacy,))]

    class Second(Spec):
        discriminator: Annotated[Literal["second"], Alias(current, legacy=(legacy,))]

    contract = Contract(Annotated[First | Second, Discriminator("discriminator")])
    value = contract.from_json(json.dumps({legacy: "second"}))
    assert type(value) is Second
    operation = contract._artifacts.json_input
    assert operation is not None
    assert current not in operation.__code__.co_consts
    assert legacy not in operation.__code__.co_consts
