import json
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from typing import Annotated, cast

import pytest
from hypothesis import given, settings, strategies as st

from talea import (
    Alias,
    ErrorCode,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
    ValidationError,
    field,
)
from talea.introspection import inspect_spec


class ObservedMapping(Mapping[str, object]):
    """Expose Mapping method use without changing ordinary dictionary semantics."""

    def __init__(self, values: dict[str, object]) -> None:
        self.values = values
        self.lookups: list[str] = []

    def __getitem__(self, key: str) -> object:
        self.lookups.append(key)
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


def test_legacy_names_are_one_ordered_immutable_field_truth() -> None:
    alias = Alias("userId", legacy=("id", "user_id"))

    class User(Spec):
        user_id: Annotated[int, alias]

    field = vars(User)["__talea_artifacts__"].schema.fields[0]

    assert alias.legacy == ("id", "user_id")
    assert field.name == "user_id"
    assert field.alias == "userId"
    assert field.external_name == "userId"
    assert field.legacy_names == ("id", "user_id")
    assert field.accepted_input_names == ("userId", "id", "user_id")
    assert User.from_mapping({"userId": 1}).user_id == 1
    assert User.from_mapping({"id": 2}).user_id == 2
    assert User.from_mapping({"user_id": 3}).user_id == 3
    assert User.from_json('{"userId":4}').user_id == 4
    assert User.from_json('{"id":5}').user_id == 5
    assert User.from_json('{"user_id":6}').user_id == 6


def test_no_alias_and_existing_single_alias_semantics_are_unchanged() -> None:
    class Plain(Spec):
        value: int

    class Aliased(Spec):
        value: Annotated[int, Alias("external", legacy=())]

    assert Plain.from_mapping({"value": 1}).value == 1
    assert Plain(value=1).to_dict() == {"value": 1}
    assert Aliased.from_mapping({"external": 2}).value == 2
    assert Aliased.from_json('{"external":3}').value == 3
    assert Aliased(value=4).to_dict() == {"external": 4}
    assert Aliased(value=4).to_json() == '{"external":4}'

    with pytest.raises(ValidationError) as canonical_input:
        Aliased.from_mapping({"value": 2})
    assert [(item["code"], item["location"]) for item in canonical_input.value.errors()] == [
        ("missing", ["external"]),
        ("unexpected", ["value"]),
    ]


@pytest.mark.parametrize(
    ("payload", "names"),
    [
        ({"userId": 1, "id": 1}, ["userId", "id"]),
        ({"userId": 1, "id": 2}, ["userId", "id"]),
        ({"id": 1, "user_id": 1}, ["id", "user_id"]),
        ({"id": 1, "user_id": 2}, ["id", "user_id"]),
    ],
)
def test_multiple_accepted_mapping_names_always_conflict(payload: dict[str, object], names: list[str]) -> None:
    class User(Spec):
        user_id: Annotated[int, Alias("userId", legacy=("id", "user_id"))]

    with pytest.raises(ValidationError) as raised:
        User.from_mapping(payload)

    detail = raised.value.errors()[0]
    assert raised.value.code is ErrorCode.ALIAS_CONFLICT
    assert raised.value.location == ("userId",)
    assert detail == {
        "code": "alias_conflict",
        "location": ["userId"],
        "message": "Multiple accepted names were supplied for one field",
        "conflicting_names": names,
    }
    assert raised.value.__cause__ is None


def test_alias_conflict_never_compares_or_renders_values() -> None:
    equality_calls = 0
    repr_calls = 0

    class HostileValue:
        def __eq__(self, other: object) -> bool:
            nonlocal equality_calls
            equality_calls += 1
            raise RuntimeError("equality must not run")

        def __repr__(self) -> str:
            nonlocal repr_calls
            repr_calls += 1
            raise RuntimeError("repr must not run")

    class Payload(Spec):
        value: Annotated[int, Alias("current", legacy=("old",))]

    with pytest.raises(ValidationError) as raised:
        Payload.from_mapping({"current": HostileValue(), "old": HostileValue()})
    assert raised.value.code == "alias_conflict"
    assert equality_calls == 0
    assert repr_calls == 0
    assert "input" not in raised.value.errors()[0]


def test_json_alias_conflict_is_distinct_from_duplicate_textual_key() -> None:
    class User(Spec):
        user_id: Annotated[int, Alias("userId", legacy=("id",))]

    with pytest.raises(ValidationError) as conflict:
        User.from_json('{"userId":1,"id":2}')
    with pytest.raises(ValidationError) as duplicate:
        User.from_json('{"id":1,"id":2}')

    assert conflict.value.code == "alias_conflict"
    assert conflict.value.location == ("userId",)
    assert duplicate.value.code == "json_duplicate"
    assert duplicate.value.location == ()
    assert duplicate.value.errors()[0]["context"] == {"key": "id"}


def test_legacy_input_uses_the_ordinary_schema_missing_and_unexpected_contract() -> None:
    class User(Spec):
        user_id: Annotated[int, Alias("userId", legacy=("id", "user_id"))]

    for payload in ({"userId": "bad"}, {"id": "bad"}, {"user_id": "bad"}):
        with pytest.raises(ValidationError) as invalid:
            User.from_mapping(payload)
        assert invalid.value.code == "type"
        assert invalid.value.location == ("userId",)

    with pytest.raises(ValidationError) as absent:
        User.from_mapping({})
    with pytest.raises(ValidationError) as unrelated:
        User.from_mapping({"other": 1})
    assert [(item["code"], item["location"]) for item in absent.value.errors()] == [("missing", ["userId"])]
    assert [(item["code"], item["location"]) for item in unrelated.value.errors()] == [
        ("missing", ["userId"]),
        ("unexpected", ["other"]),
    ]


def test_legacy_names_preserve_static_and_factory_default_lifecycles() -> None:
    calls = 0

    def default_value() -> int:
        nonlocal calls
        calls += 1
        return 2

    class Defaults(Spec):
        static: Annotated[int, Alias("staticNow", legacy=("staticOld",))] = 1
        generated: Annotated[int, Alias("generatedNow", legacy=("generatedOld",))] = field(
            default_factory=default_value
        )

    omitted = Defaults.from_mapping({})
    assert (omitted.static, omitted.generated) == (1, 2)
    assert calls == 1
    supplied = Defaults.from_mapping({"staticOld": 3, "generatedOld": 4})
    assert (supplied.static, supplied.generated) == (3, 4)
    assert calls == 1


def test_nested_and_sensitive_legacy_failures_keep_field_locations_and_hide_values() -> None:
    class Credentials(Spec):
        token: Annotated[int, Alias("authToken", legacy=("token",)), Sensitive()]

    class Request(Spec):
        credentials: Credentials

    with pytest.raises(ValidationError) as invalid:
        Request.from_mapping({"credentials": {"token": "secret"}})
    assert invalid.value.location == ("credentials", "authToken")
    assert invalid.value.errors()[0]["input"] == "<redacted>"

    with pytest.raises(ValidationError) as conflict:
        Request.from_json('{"credentials":{"authToken":1,"token":2}}')
    detail = conflict.value.errors()[0]
    assert detail["code"] == "alias_conflict"
    assert detail["location"] == ["credentials", "authToken"]
    assert detail["conflicting_names"] == ["authToken", "token"]
    assert "input" not in detail
    assert "received" not in detail
    assert "1" not in str(conflict.value)
    assert "2" not in str(conflict.value)


@pytest.mark.parametrize(
    "operation",
    [
        lambda: Alias("current", legacy=cast(tuple[str, ...], ["old"])),
        lambda: Alias("current", legacy=("",)),
        lambda: Alias("current", legacy=cast(tuple[str, ...], (1,))),
        lambda: Alias("current", legacy=("old", "old")),
        lambda: Alias("current", legacy=("current",)),
        lambda: Alias("current", ("old",)),
    ],
)
def test_malformed_legacy_declarations_are_rejected_eagerly(operation: Callable[[], object]) -> None:
    with pytest.raises(TypeError):
        operation()


def test_cross_field_accepted_name_collisions_fail_at_declaration() -> None:
    with pytest.raises(ValueError, match="unique external"):

        class CurrentCurrent(Spec):
            first: Annotated[int, Alias("same")]
            second: Annotated[int, Alias("same")]

    with pytest.raises(ValueError, match="unique accepted input"):

        class CurrentLegacy(Spec):
            first: Annotated[int, Alias("currentFirst", legacy=("old",))]
            second: Annotated[int, Alias("old")]

    with pytest.raises(ValueError, match="unique accepted input"):

        class LegacyLegacy(Spec):
            first: Annotated[int, Alias("currentFirst", legacy=("old",))]
            second: Annotated[int, Alias("currentSecond", legacy=("old",))]

    with pytest.raises(ValueError, match="canonical field"):

        class OtherCanonical(Spec):
            first: Annotated[int, Alias("currentFirst", legacy=("second",))]
            second: Annotated[int, Alias("externalSecond")]


def test_inherited_effective_fields_retain_legacy_names_and_reject_collisions() -> None:
    class Base(Spec):
        user_id: Annotated[int, Alias("userId", legacy=("id",))]

    class Child(Base):
        active: bool

    assert Child.from_mapping({"id": 1, "active": True}).user_id == 1

    with pytest.raises(ValueError, match="unique accepted input"):

        class Ambiguous(Base):
            other: Annotated[int, Alias("id")]


def test_alias_strings_are_bound_as_data_and_cannot_change_generated_source() -> None:
    current = 'current"\n雪; raise RuntimeError("source")'
    legacy = ("old'\nname", "λ\u2028legacy")

    class Payload(Spec):
        value: Annotated[int, Alias(current, legacy=legacy)]

    assert Payload.from_mapping({legacy[0]: 1}).value == 1
    assert Payload.from_json(json.dumps({legacy[1]: 2})).value == 2
    boundary = vars(Payload)["__talea_artifacts__"].inputs.mapping_input
    assert boundary is not None
    assert current not in boundary.__code__.co_consts
    assert all(name not in boundary.__code__.co_consts for name in legacy)


def test_custom_and_large_mappings_use_declaration_bounded_name_probes() -> None:
    class Payload(Spec):
        value: Annotated[int, Alias("valueNow", legacy=("value", "oldValue"))]

    observed = ObservedMapping({"oldValue": 1})
    assert Payload.from_mapping(observed).value == 1
    assert observed.lookups == ["valueNow", "value", "oldValue"]

    class ExplodingMapping(ObservedMapping):
        def __getitem__(self, key: str) -> object:
            raise RuntimeError("mapping lookup failed")

    with pytest.raises(RuntimeError, match="mapping lookup failed"):
        Payload.from_mapping(ExplodingMapping({"oldValue": 1}))

    class ExplodingIteration(ObservedMapping):
        def __iter__(self) -> Iterator[str]:
            raise RuntimeError("mapping iteration failed")

    with pytest.raises(RuntimeError, match="mapping iteration failed"):
        Payload.from_mapping(ExplodingIteration({"oldValue": 1}))

    class HostileKey:
        def __repr__(self) -> str:
            raise RuntimeError("key repr failed")

    hostile_key = HostileKey()
    hostile_keys = cast(Mapping[str, object], {"value": 1, hostile_key: 2})
    with pytest.raises(ValidationError) as hostile:
        Payload.from_mapping(hostile_keys)
    assert hostile.value.errors()[0]["code"] == "unexpected"

    large: dict[str, object] = {"value": 1}
    large.update({f"extra_{index}": index for index in range(2_000)})
    with pytest.raises(ValidationError) as unrelated:
        Payload.from_mapping(large)
    assert unrelated.value.truncated is True
    assert unrelated.value.errors()[0]["location"] == ["extra_0"]


def test_alias_input_preserves_resource_policy_accounting() -> None:
    class Nested(Spec):
        value: Annotated[int, Alias("valueNow", legacy=("value",))]

    class Payload(Spec):
        nested: Nested

    with pytest.raises(ResourceLimitError) as raised:
        Payload.from_mapping({"nested": {"value": 1}}, policy=ResourcePolicy(max_nodes=1))
    assert raised.value.code == "nodes"


def test_field_info_projects_immutable_current_legacy_and_accepted_names() -> None:
    class User(Spec):
        user_id: Annotated[int, Alias("userId", legacy=("id", "user_id"))]
        active: Annotated[bool, Alias("isActive")]

    info = inspect_spec(User)
    migrated, single = info.fields
    assert migrated.alias == "userId"
    assert migrated.external_name == "userId"
    assert migrated.legacy_names == ("id", "user_id")
    assert migrated.accepted_input_names == ("userId", "id", "user_id")
    assert single.external_name == "isActive"
    assert single.legacy_names == ()
    assert single.accepted_input_names == ("isActive",)
    with pytest.raises(FrozenInstanceError):
        migrated.legacy_names = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        migrated.legacy_names[0] = "changed"  # type: ignore[index]
    assert User.from_mapping({"id": 1, "isActive": True}).user_id == 1


def test_retained_mapping_and_json_artifacts_are_thread_safe() -> None:
    class User(Spec):
        user_id: Annotated[int, Alias("userId", legacy=("id", "user_id"))]

    User.from_mapping({"id": 0})
    User.from_json('{"user_id":0}')
    artifacts = vars(User)["__talea_artifacts__"].inputs
    mapping_input = artifacts.mapping_input
    json_input = artifacts.json_input

    def construct(index: int) -> int:
        if index % 2:
            return User.from_mapping({"id": index}).user_id
        return User.from_json(json.dumps({"user_id": index})).user_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(construct, range(100))) == list(range(100))
    assert artifacts.mapping_input is mapping_input
    assert artifacts.json_input is json_input


def test_generated_no_legacy_path_has_no_migration_lookup_machinery() -> None:
    class Plain(Spec):
        value: int

    class SingleAlias(Spec):
        value: Annotated[int, Alias("external")]

    class Migrated(Spec):
        value: Annotated[int, Alias("external", legacy=("old",))]

    for spec, payload in ((Plain, {"value": 1}), (SingleAlias, {"external": 1}), (Migrated, {"old": 1})):
        spec.from_mapping(payload)
    plain_boundary = vars(Plain)["__talea_artifacts__"].inputs.mapping_input
    single_boundary = vars(SingleAlias)["__talea_artifacts__"].inputs.mapping_input
    migrated_artifacts = vars(Migrated)["__talea_artifacts__"]
    migrated_boundary = migrated_artifacts.inputs.mapping_input
    assert plain_boundary is not None and single_boundary is not None and migrated_boundary is not None
    assert "_alias_conflict" not in plain_boundary.__code__.co_names
    assert "_alias_conflict" not in single_boundary.__code__.co_names
    assert not any("accepted_names" in name for name in plain_boundary.__globals__)
    assert not any("accepted_names" in name for name in single_boundary.__globals__)
    assert "_alias_conflict" in migrated_boundary.__code__.co_names
    retained = [value for name, value in migrated_boundary.__globals__.items() if "accepted_names" in name]
    assert retained == [migrated_artifacts.schema.fields[0].accepted_input_names]
    assert retained[0] is migrated_artifacts.schema.fields[0].accepted_input_names

    type.__setattr__(Migrated, "__annotations__", {"value": str})
    assert Migrated.from_mapping({"old": 2}).value == 2


@settings(max_examples=30)
@given(
    st.lists(
        st.text(min_size=1, max_size=8).filter(lambda value: value != "current"),
        min_size=1,
        max_size=5,
        unique=True,
    )
)
def test_unique_legacy_sets_accept_each_name_and_reject_every_pair(legacy: list[str]) -> None:
    class Payload(Spec):
        value: Annotated[int, Alias("current", legacy=tuple(legacy))]

    accepted = ("current", *legacy)
    for index, name in enumerate(accepted):
        assert Payload.from_mapping({name: index}).value == index
    for first, second in zip(accepted, accepted[1:], strict=False):
        with pytest.raises(ValidationError) as raised:
            Payload.from_mapping({first: 1, second: 1})
        assert raised.value.code == "alias_conflict"
