import pickle
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy, replace
from inspect import signature
from typing import Annotated, Literal

import pytest
from hypothesis import given, strategies as st

from talea import (
    Alias,
    Contract,
    Discriminator,
    Sensitive,
    Spec,
    ValidationError,
    apply_patch,
    check,
    create_spec,
    derive_spec,
    field,
    serialize,
    transform,
)
from talea.introspection import DerivationInfo, inspect_spec
from talea.schema import AliasSchema, SpecReferenceSchema, TaggedUnionSchema


class PickleSource(Spec):
    value: int
    label: str


PicklePatch = derive_spec(PickleSource, partial=True, name="PicklePatch", module=__name__)


def test_partial_distinguishes_absence_none_and_explicit_default_value() -> None:
    class User(Spec):
        age: int
        active: bool = True

    UserPatch = derive_spec(User, partial=True)
    absent = UserPatch()
    explicit = UserPatch(active=True)

    assert absent.present_fields == frozenset()
    assert absent.to_dict() == {}
    assert explicit.present_fields == frozenset({"active"})
    assert explicit.to_dict() == {"active": True}
    with pytest.raises(ValidationError) as raised:
        UserPatch(age=None)  # type: ignore[invalid-argument-type]
    assert raised.value.location == ("age",)


def test_partial_omission_never_materializes_source_defaults_or_factories() -> None:
    calls: list[str] = []

    def factory() -> list[str]:
        calls.append("factory")
        return []

    class Source(Spec):
        static: int = 1
        dynamic: list[str] = field(default_factory=factory)

    Patch = derive_spec(Source, partial=True)
    patch = Patch()

    assert calls == []
    assert repr(patch) == "SourcePartial()"
    assert not hasattr(patch, "static")
    assert not hasattr(patch, "dynamic")
    with pytest.raises(AttributeError, match="has no attribute 'static'"):
        _ = patch.static


def test_present_field_lifecycle_and_serializer_run_once_per_operation() -> None:
    events: list[str] = []

    class Source(Spec):
        value: int
        other: int

        @transform("value")
        def prepare(value: object) -> object:
            events.append("transform")
            return int(value) if isinstance(value, str) else value

        @check("value")
        def positive(value: int) -> None:
            events.append("check")
            if value < 0:
                raise ValueError

        @serialize("value")
        def render(value: int) -> str:
            events.append("serialize")
            return str(value)

    Patch = derive_spec(Source, partial=True)
    omitted = Patch()
    assert events == []
    assert omitted.to_dict() == {}
    assert events == []

    patch = Patch(value="2")  # type: ignore[invalid-argument-type]
    assert patch.value == 2
    assert events == ["transform", "check"]
    assert patch.to_dict() == {"value": "2"}
    assert events == ["transform", "check", "serialize"]


def test_partial_mapping_json_alias_and_error_aggregation_use_canonical_presence() -> None:
    class Source(Spec):
        age: int
        name: Annotated[str, Alias("full-name")]

    Patch = derive_spec(Source, partial=True)
    mapping = Patch.from_mapping({"full-name": "Ada"})
    decoded = Patch.from_json('{"age":1}')

    assert mapping.present_fields == frozenset({"name"})
    assert mapping.to_dict() == {"full-name": "Ada"}
    assert mapping.to_dict(by_alias=False) == {"name": "Ada"}
    assert decoded.present_fields == frozenset({"age"})
    assert decoded.to_json() == '{"age":1}'
    with pytest.raises(ValidationError) as raised:
        Patch.from_mapping({"age": "bad", "unexpected": 1})
    assert {error["code"] for error in raised.value.errors()} == {"type", "unexpected"}


def test_partial_serialization_obeys_presence_then_existing_filters() -> None:
    class Source(Spec):
        first: int | None
        second: int | None

    Patch = derive_spec(Source, partial=True)
    patch = Patch(first=None, second=2)

    assert patch.to_dict() == {"first": None, "second": 2}
    assert patch.to_dict(exclude_none=True) == {"second": 2}
    assert patch.to_dict(include={"first"}) == {"first": None}
    assert patch.to_json(exclude={"second"}) == '{"first":null}'


def test_pick_omit_and_partial_projection_preserve_order_and_field_truth() -> None:
    class Source(Spec):
        first: int
        second: Annotated[str, Alias("external")]
        third: list[int] = field(default_factory=list)

    Picked = derive_spec(Source, include=("third", "first"), name="Picked")
    Omitted = derive_spec(Source, exclude=("second",), name="Omitted")
    Patch = derive_spec(Source, include=("second", "first"), partial=True, name="Patch")

    assert not issubclass(Picked, Source)
    assert Picked.__slots__ == ("first", "third")
    assert Picked(first=1).to_dict() == {"first": 1, "third": []}
    assert Omitted(first=1).to_dict() == {"first": 1, "third": []}
    assert Patch.from_mapping({"external": "x"}).to_dict() == {"external": "x"}
    assert inspect_spec(Patch).derivation == DerivationInfo(
        Source,
        ("first", "second"),
        ("third",),
        "include",
        True,
        "Patch",
    )


@pytest.mark.parametrize("parameter", ["include", "exclude"])
def test_derivation_rejects_unknown_and_duplicate_selection(parameter: str) -> None:
    class Source(Spec):
        value: int

    with pytest.raises(ValueError, match="unknown field 'missing'"):
        derive_spec(Source, **{parameter: ("missing",)})
    with pytest.raises(ValueError, match="duplicate field names"):
        derive_spec(Source, **{parameter: ("value", "value")})
    with pytest.raises(TypeError, match="iterable of field names"):
        derive_spec(Source, **{parameter: "value"})


def test_derivation_rejects_invalid_policy_and_open_generic_sources() -> None:
    class Box[T](Spec):
        value: T

    with pytest.raises(TypeError, match="mutually exclusive"):
        derive_spec(Box[int], include=("value",), exclude=("value",))
    with pytest.raises(TypeError, match="requires concrete specialization"):
        derive_spec(Box, partial=True)
    with pytest.raises(TypeError, match="partial must be bool"):
        derive_spec(Box[int], partial=1)  # type: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="source must be a Spec class"):
        derive_spec(int)  # type: ignore[type-abstract]


def test_derived_contract_does_not_copy_application_methods_or_unsafe_whole_checks() -> None:
    events: list[tuple[int, int]] = []

    class Interval(Spec):
        start: int
        end: int

        def width(self) -> int:
            return self.end - self.start

        @check("start", "end")
        def ordered(start: int, end: int) -> None:
            events.append((start, end))
            if start > end:
                raise ValueError

    Patch = derive_spec(Interval, partial=True)
    patch = Patch(start=10)

    assert not hasattr(Patch, "width")
    assert events == []
    with pytest.raises(ValidationError):
        apply_patch(Interval(start=1, end=2), Patch(start=3))
    assert events == [(1, 2), (3, 2)]
    assert patch.start == 10


def test_nonpartial_projection_retains_only_checks_with_complete_targets() -> None:
    events: list[str] = []

    class Source(Spec):
        first: int
        second: int
        third: int

        @check("first", "second")
        def pair(first: int, second: int) -> None:
            events.append(f"{first}:{second}")

    Complete = derive_spec(Source, include=("first", "second"))
    Incomplete = derive_spec(Source, include=("first", "third"))

    Complete(first=1, second=2)
    Incomplete(first=1, third=3)
    assert events == ["1:2"]


def test_apply_patch_uses_source_replacement_and_rejects_unrelated_provenance() -> None:
    class User(Spec):
        name: str
        tags: list[str]

    class Account(Spec):
        name: str

    UserPatch = derive_spec(User, partial=True)
    AccountPatch = derive_spec(Account, partial=True)
    user = User(name="old", tags=[])
    updated = apply_patch(user, UserPatch(name="new"))

    assert type(updated) is User
    assert updated.name == "new"
    assert updated.tags is user.tags
    assert user.name == "old"
    with pytest.raises(TypeError, match="cannot apply"):
        apply_patch(user, AccountPatch(name="wrong"))
    with pytest.raises(TypeError, match="partial derived Spec"):
        apply_patch(user, User(name="x", tags=[]))
    with pytest.raises(TypeError, match="requires Spec instances"):
        apply_patch(user, object())  # type: ignore[invalid-argument-type]


def test_apply_patch_preserves_changed_and_mutable_current_state_validation() -> None:
    class Source(Spec):
        value: int
        items: list[int]

    Patch = derive_spec(Source, partial=True)
    source = Source(value=1, items=[])
    source.items.append("invalid")  # type: ignore[arg-type]

    with pytest.raises(ValidationError) as raised:
        apply_patch(source, Patch(value=2))
    assert raised.value.location == ("items", 0)


def test_patch_source_compatibility_uses_exact_concrete_generic_identity() -> None:
    class Page[T](Spec):
        items: list[T]

    IntPatch = derive_spec(Page[int], partial=True)
    StrPatch = derive_spec(Page[str], partial=True)
    page = Page[int](items=[1])

    updated = apply_patch(page, IntPatch(items=[2]))
    assert type(updated) is Page[int]
    assert updated.items == [2]
    with pytest.raises(TypeError, match="cannot apply"):
        apply_patch(page, StrPatch(items=["wrong"]))


def test_inheritance_concrete_generics_recursion_and_tagged_fields_remain_canonical() -> None:
    class Base(Spec):
        identifier: int

    class Child(Base):
        label: str

    class Box[T](Spec):
        value: T

    class Node(Spec):
        value: int
        children: list[Node]

    class Add(Spec):
        kind: Literal["add"]
        value: int

    class Remove(Spec):
        kind: Literal["remove"]
        value: int

    type Action = Annotated[Add | Remove, Discriminator("kind")]

    class Envelope(Spec):
        action: Action

    ChildPatch = derive_spec(Child, partial=True)
    BoxPatch = derive_spec(Box[int], partial=True)
    NodePatch = derive_spec(Node, partial=True)
    EnvelopePatch = derive_spec(Envelope, partial=True)

    assert ChildPatch(label="x").present_fields == frozenset({"label"})
    assert BoxPatch(value=1).value == 1
    with pytest.raises(ValidationError):
        BoxPatch(value="1")  # type: ignore[invalid-argument-type]
    node_schema = inspect_spec(NodePatch).fields[1].schema
    assert node_schema == inspect_spec(Node).fields[1].schema
    action_schema = inspect_spec(EnvelopePatch).fields[0].schema
    assert action_schema == inspect_spec(Envelope).fields[0].schema
    assert isinstance(action_schema, AliasSchema)
    assert isinstance(action_schema.schema, TaggedUnionSchema)
    assert isinstance(action_schema.schema.branches[0].schema, SpecReferenceSchema)
    patch = EnvelopePatch.from_mapping({"action": {"kind": "add", "value": 1}})
    assert type(patch.action) is Add


def test_sensitive_present_values_redact_and_omitted_values_leak_nothing() -> None:
    class Credentials(Spec):
        password: Annotated[str, Sensitive()]

    Patch = derive_spec(Credentials, partial=True)

    assert repr(Patch()) == "CredentialsPartial()"
    assert repr(Patch(password="secret")) == "CredentialsPartial(password='<redacted>')"
    with pytest.raises(ValidationError) as raised:
        Patch(password=object())  # type: ignore[invalid-argument-type]
    assert "object" not in repr(raised.value.value)


def test_copy_deepcopy_replace_and_pickle_preserve_presence() -> None:
    patch = PicklePatch(value=1)
    copied = copy(patch)
    deep = deepcopy(patch)
    changed = replace(patch, label="x")
    restored = pickle.loads(pickle.dumps(patch))

    for candidate in (copied, deep, restored):
        assert type(candidate) is PicklePatch
        assert candidate.present_fields == frozenset({"value"})
        assert candidate.to_dict() == {"value": 1}
        assert not hasattr(candidate, "label")
    assert changed.present_fields == frozenset({"value", "label"})
    assert changed.to_dict() == {"value": 1, "label": "x"}
    assert repr(signature(PicklePatch).parameters["label"].default) == "<omitted>"


def test_partial_replace_runs_changed_hooks_and_revalidates_present_mutable_state() -> None:
    events: list[str] = []

    class Source(Spec):
        value: int
        items: list[int]

        @transform("value")
        def prepare(value: object) -> object:
            events.append("transform")
            return int(value) if isinstance(value, str) else value

        @check("value")
        def positive(value: int) -> None:
            events.append("check")

        @check("items")
        def checked(items: list[int]) -> None:
            events.append("items")

    Patch = derive_spec(Source, partial=True)
    patch = Patch(items=[])
    changed = replace(patch, value="2")  # type: ignore[invalid-argument-type]

    assert changed.to_dict() == {"value": 2, "items": []}
    assert events == ["items", "transform", "check", "items"]
    patch.items.append("invalid")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        replace(patch, value=3)


def test_partial_is_a_normal_spec_contract_and_nested_mutable_state_is_validated() -> None:
    class Source(Spec):
        items: list[int]

    Patch = derive_spec(Source, partial=True)
    patch = Patch(items=[])

    assert Contract(Patch).validate(patch) is patch

    class Envelope(Spec):
        patch: Patch

    assert Envelope(patch=patch).patch is patch
    patch.items.append("invalid")  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as raised:
        Envelope(patch=patch)
    assert raised.value.location == ("patch", "items", 0)


def test_introspection_exposes_requiredness_without_runtime_heuristics() -> None:
    class Source(Spec):
        required: int
        defaulted: int = 1

    normal = inspect_spec(Source)
    partial = inspect_spec(derive_spec(Source, partial=True, name="SourcePatch"))

    assert not normal.presence_aware
    assert normal.derivation is None
    assert [field.required for field in normal.fields] == [True, False]
    assert partial.presence_aware
    assert [field.required for field in partial.fields] == [False, False]
    assert [field.omittable for field in partial.fields] == [True, True]
    assert partial.derivation is not None
    assert partial.derivation.source is Source


def test_normal_spec_layout_and_repeated_derivation_identity_remain_deliberate() -> None:
    class Normal(Spec):
        value: int

    first = derive_spec(Normal, partial=True)
    second = derive_spec(Normal, partial=True)

    assert Normal.__slots__ == ("value",)
    normal = Normal(value=1)
    assert not hasattr(normal, "__talea_presence__")
    assert normal.present_fields == frozenset({"value"})
    assert sys.getsizeof(Normal(value=1)) < sys.getsizeof(first())
    assert first is not second


def test_concurrent_uncached_derivation_produces_consistent_distinct_classes() -> None:
    class Source(Spec):
        value: int

    with ThreadPoolExecutor(max_workers=8) as executor:
        derived = tuple(executor.map(lambda _: derive_spec(Source, partial=True), range(32)))

    assert len({id(spec) for spec in derived}) == 32
    assert all(spec(value=1).to_dict() == {"value": 1} for spec in derived)
    assert all(inspect_spec(spec).derivation.source is Source for spec in derived)


def test_zero_and_large_field_partial_contracts_use_unbounded_integer_presence() -> None:
    Empty = create_spec("EmptySource", {})
    EmptyPatch = derive_spec(Empty, partial=True)
    Large = create_spec("LargeSource", {f"field_{index}": int for index in range(1_000)})
    LargePatch = derive_spec(Large, partial=True)

    assert EmptyPatch().present_fields == frozenset()
    last = LargePatch(field_999=999)
    assert last.present_fields == frozenset({"field_999"})
    assert last.to_dict() == {"field_999": 999}


def test_selection_requires_string_members() -> None:
    class Source(Spec):
        value: int

    with pytest.raises(TypeError, match="must contain str field names"):
        derive_spec(Source, include=(1,))  # type: ignore[arg-type]


@given(st.sets(st.sampled_from(("first", "second", "third"))))
def test_presence_property_tracks_arbitrary_supplied_subsets(names: set[str]) -> None:
    class Source(Spec):
        first: int
        second: int
        third: int

    Patch = derive_spec(Source, partial=True)
    values = {name: index for index, name in enumerate(sorted(names))}
    patch = Patch(**values)

    assert patch.present_fields == frozenset(names)
    assert patch.to_dict() == values


@given(st.sets(st.sampled_from(("first", "second", "third"))))
def test_pick_and_omit_property_preserve_source_order(names: set[str]) -> None:
    class Source(Spec):
        first: int = 1
        second: int = 2
        third: int = 3

    source_order = ("first", "second", "third")
    picked = derive_spec(Source, include=names)
    omitted = derive_spec(Source, exclude=names)

    assert tuple(field.name for field in inspect_spec(picked).fields) == tuple(
        name for name in source_order if name in names
    )
    assert tuple(field.name for field in inspect_spec(omitted).fields) == tuple(
        name for name in source_order if name not in names
    )
