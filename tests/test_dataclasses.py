from __future__ import annotations

import gc
import pickle
import weakref
from collections import UserDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import InitVar, dataclass, field, fields, make_dataclass
from typing import Annotated, ClassVar, Literal, TypedDict

import pytest
from hypothesis import given, strategies as st
from openapi_spec_validator import validate as validate_openapi

from talea import (
    Alias,
    Contract,
    Description,
    Discriminator,
    Ge,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
)
from talea.declaration.policies import (
    schema_contains_sensitive_metadata,
    schema_contains_tagged_union,
    schema_values_are_immutable,
)
from talea.introspection import inspect_contract
from talea.json_schema.projection import _StandardsProjector, project_schema
from talea.metadata import EMPTY_METADATA
from talea.schema import (
    DataclassField,
    DataclassSchema,
    NamedReferenceSchema,
    PrimitiveSchema,
    SequenceSchema,
)
from talea.schema.nodes import DATACLASS_MISSING
from talea.serialization import SerializationError
from talea.validation import ValidationError


@dataclass
class PropertyRecord:
    value: int
    labels: list[str]


PROPERTY_RECORD = Contract(PropertyRecord)


@dataclass
class RecursiveNode:
    value: int
    children: list[RecursiveNode] = field(default_factory=list)


@dataclass
class MutualParent:
    child: MutualChild | None = None


@dataclass
class MutualChild:
    parent: MutualParent | None = None


@dataclass(frozen=True)
class FrozenRecursiveNode:
    child: FrozenRecursiveNode | None = None


class CompositionFlags(TypedDict):
    active: bool


type CompositionIdentifier = int


class CompositionOwner(Spec):
    name: str


@dataclass
class CompositionPayload:
    id: CompositionIdentifier
    owner: CompositionOwner
    flags: CompositionFlags


class CompositionEnvelope(Spec):
    payload: CompositionPayload


@dataclass
class ErrorInstrument:
    symbol: Annotated[str, Alias("ticker")]


@dataclass
class ErrorOrder:
    instrument: ErrorInstrument


@dataclass
class ErrorBatch:
    orders: list[ErrorOrder]


@dataclass
class ResourceChild:
    value: int


@dataclass
class ResourceParent:
    child: ResourceChild


@dataclass(frozen=True)
class PickleUser:
    name: str


def _openapi_document(fragment: dict[str, object]) -> dict[str, object]:
    components = fragment["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    return {
        "openapi": "3.1.2",
        "info": {"title": "Dataclass contract", "version": "1"},
        "paths": {},
        "components": {"schemas": {**schemas, "Root": fragment["schema"]}},
    }


def test_dataclass_schema_is_the_canonical_immutable_structural_owner() -> None:
    @dataclass(frozen=True)
    class User:
        name: Annotated[str, Alias("fullName"), Description("Display name")]
        age: Annotated[int, Ge(0)] = 0
        derived: int = field(init=False, default=1)
        category: ClassVar[str] = "person"

    contract = Contract(User)
    schema = contract._artifacts.schema

    assert isinstance(schema, DataclassSchema)
    assert schema.dataclass_type is User
    assert schema.frozen is True
    assert tuple(item.name for item in schema.fields) == ("name", "age", "derived")
    assert tuple(item.external_name for item in schema.fields) == ("fullName", "age", "derived")
    assert schema.fields[0].metadata.description == "Display name"
    assert schema.fields[1].schema != PrimitiveSchema("int")
    assert schema.fields[2].init is False
    assert contract.annotation is User
    assert inspect_contract(contract).schema is schema


def test_strict_validation_is_exact_preserves_identity_and_revalidates_mutation() -> None:
    @dataclass
    class User:
        age: int

    @dataclass
    class Admin(User):
        level: int = 1

    contract = Contract(User)
    user = User(1)

    assert contract.validate(user) is user
    user.age = "1"  # type: ignore[assignment]
    with pytest.raises(ValidationError) as captured:
        contract.validate(user)
    assert captured.value.errors()[0]["location"] == ["age"]
    assert captured.value.errors()[0]["code"] == "type"

    with pytest.raises(ValidationError, match="Expected User"):
        contract.validate(Admin(1))


def test_frozen_trust_is_transitive_and_does_not_skip_initial_validation() -> None:
    @dataclass(frozen=True)
    class Point:
        x: int

    @dataclass(frozen=True)
    class Basket:
        items: list[int]

    point = Contract(Point)
    basket = Contract(Basket)

    assert isinstance(point._artifacts.schema, DataclassSchema)
    assert point._artifacts.schema.instances_are_permanently_trusted is True
    assert isinstance(basket._artifacts.schema, DataclassSchema)
    assert basket._artifacts.schema.instances_are_permanently_trusted is False
    with pytest.raises(ValidationError):
        point.validate(Point("1"))  # type: ignore[arg-type]

    value = Basket([1])
    basket.validate(value)
    value.items.append("2")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        basket.validate(value)


@pytest.mark.parametrize(
    "options",
    (
        {"slots": True},
        {"frozen": True, "slots": True},
    ),
)
def test_slots_and_frozen_slots_preserve_the_original_instance_shape(options: dict[str, bool]) -> None:
    User = make_dataclass("User", [("name", str)], **options)
    contract = Contract(User)
    user = contract.from_python({"name": "Ada"})

    assert type(user) is User
    assert contract.validate(user) is user
    assert not hasattr(user, "__dict__")
    assert not any(name.startswith("__talea") for name in dir(user))
    assert contract.to_python(user) == {"name": "Ada"}


def test_classvar_is_absent_from_every_contract_boundary() -> None:
    @dataclass
    class Record:
        value: int
        category: ClassVar[str] = "record"

    contract = Contract(Record)
    record = contract.from_python({"value": 1})

    assert tuple(item.name for item in fields(Record)) == ("value",)
    assert contract.validate(record) is record
    assert contract.to_python(record) == {"value": 1}
    assert "category" not in repr(contract.json_schema())
    with pytest.raises(ValidationError) as captured:
        contract.from_python({"value": 1, "category": "other"})
    assert captured.value.errors()[0]["code"] == "unexpected"


def test_initvar_and_incompatible_constructor_are_declaration_errors() -> None:
    @dataclass
    class WithInitVar:
        value: int
        context: InitVar[str]

    @dataclass
    class Incompatible:
        value: int

        def __init__(self, *, other: int) -> None:
            self.value = other

    @dataclass
    class IncompatibleKind:
        value: int

        def __init__(self, *, value: int) -> None:
            self.value = value

    @dataclass
    class UnsupportedField:
        value: object

    @dataclass
    class DuplicateAlias:
        value: Annotated[int, Alias("one"), Alias("two")]

    with pytest.raises(TypeError, match="unsupported InitVar"):
        Contract(WithInitVar)
    with pytest.raises(TypeError, match="incompatible constructor signature"):
        Contract(Incompatible)
    with pytest.raises(TypeError, match="incompatible constructor signature"):
        Contract(IncompatibleKind)
    with pytest.raises(TypeError, match="Unsupported annotation"):
        Contract(UnsupportedField)
    with pytest.raises(TypeError, match="only one Alias"):
        Contract(DuplicateAlias)


def test_dataclass_schema_invariants_and_policy_recursion_are_explicit() -> None:
    field_schema = DataclassField("value", PrimitiveSchema("int"), True, False)
    static = DataclassField("static", PrimitiveSchema("int"), True, False, 1)
    factory = DataclassField(
        "factory",
        PrimitiveSchema("int"),
        True,
        False,
        default_factory=lambda: 1,
    )

    assert field_schema.required is True
    assert static.has_static_default is True
    assert static.has_default_factory is False
    assert factory.has_static_default is False
    assert factory.has_default_factory is True
    with pytest.raises(ValueError, match="both a static default"):
        DataclassField("bad", PrimitiveSchema("int"), True, False, 1, lambda: 1)
    with pytest.raises(TypeError, match="non-empty string"):
        DataclassField("bad", PrimitiveSchema("int"), True, False, alias="")

    @dataclass
    class Manual:
        value: int

    with pytest.raises(ValueError, match="unique field names"):
        DataclassSchema(Manual, (field_schema, field_schema), False)
    with pytest.raises(ValueError, match="conflicts with a canonical"):
        DataclassSchema(
            Manual,
            (
                field_schema,
                DataclassField("other", PrimitiveSchema("int"), True, False, alias="value"),
            ),
            False,
        )
    with pytest.raises(ValueError, match="unique external field names"):
        DataclassSchema(
            Manual,
            (
                DataclassField("one", PrimitiveSchema("int"), True, False, alias="shared"),
                DataclassField("two", PrimitiveSchema("int"), True, False, alias="shared"),
            ),
            False,
        )

    recursive = Contract(FrozenRecursiveNode)._artifacts.schema
    assert isinstance(recursive, DataclassSchema)
    assert recursive.identity is not None
    assert schema_values_are_immutable(recursive) is True
    assert schema_values_are_immutable(recursive, frozenset({recursive.identity})) is True
    assert schema_contains_sensitive_metadata(recursive, frozenset({recursive.identity})) is False
    assert schema_contains_tagged_union(recursive) is False
    assert schema_contains_tagged_union(recursive, frozenset({recursive.identity})) is False

    manual = DataclassSchema(Manual, (field_schema,), False)
    projected = project_schema(
        manual,
        EMPTY_METADATA,
        mode="input",
        target="json_schema",
    )
    assert projected["type"] == "object"
    projector = _StandardsProjector("input", "json_schema")
    assert projector._contains_serializer(recursive) is False
    assert projector._contains_serializer(recursive, frozenset({recursive.identity})) is False
    assert static.default is not DATACLASS_MISSING


def test_compatible_custom_constructor_uses_named_boundary_and_validates_result() -> None:
    calls = 0

    @dataclass(init=False)
    class Custom:
        value: int

        def __init__(self, value: int) -> None:
            nonlocal calls
            calls += 1
            self.value = value

    contract = Contract(Custom)
    value = contract.from_python({"value": 1})

    assert value == Custom(1)
    assert calls == 2


def test_init_false_is_stored_output_state_but_not_external_input() -> None:
    @dataclass
    class Derived:
        value: int
        doubled: int = field(init=False)

        def __post_init__(self) -> None:
            self.doubled = self.value * 2

    contract = Contract(Derived)
    value = contract.from_python({"value": 2})

    assert contract.validate(value) is value
    assert contract.to_python(value) == {"value": 2, "doubled": 4}
    with pytest.raises(ValidationError) as captured:
        contract.from_python({"value": 2, "doubled": 4})
    assert captured.value.errors()[0]["code"] == "unexpected"

    input_definition = contract.json_schema()["$defs"]["Derived"]
    output_definition = contract.json_schema(mode="output")["$defs"]["Derived"]
    assert "doubled" not in input_definition["properties"]
    assert output_definition["properties"]["doubled"]["readOnly"] is True
    assert output_definition["required"] == ["value", "doubled"]


def test_defaults_factory_and_post_init_keep_single_stdlib_lifecycle_ownership() -> None:
    factory_calls = 0
    post_init_calls = 0

    def make_labels() -> list[str]:
        nonlocal factory_calls
        factory_calls += 1
        return []

    @dataclass
    class Lifecycle:
        value: int = 1
        labels: list[str] = field(default_factory=make_labels)
        normalized: int = field(init=False)

        def __post_init__(self) -> None:
            nonlocal post_init_calls
            post_init_calls += 1
            self.value += 1
            self.normalized = self.value

    value = Contract(Lifecycle).from_python({})

    assert (value.value, value.labels, value.normalized) == (2, [], 2)
    assert factory_calls == 1
    assert post_init_calls == 1


def test_invalid_default_factory_and_post_init_state_fail_after_single_construction() -> None:
    factory_calls = 0
    post_init_calls = 0

    def invalid_factory() -> list[int]:
        nonlocal factory_calls
        factory_calls += 1
        return ["bad"]  # type: ignore[list-item]

    @dataclass
    class InvalidFactory:
        values: list[int] = field(default_factory=invalid_factory)

    @dataclass
    class InvalidPostInit:
        value: int

        def __post_init__(self) -> None:
            nonlocal post_init_calls
            post_init_calls += 1
            self.value = "bad"  # type: ignore[assignment]

    with pytest.raises(ValidationError) as factory_error:
        Contract(InvalidFactory).from_python({})
    assert factory_error.value.errors()[0]["location"] == ["values", 0]
    assert factory_calls == 1

    with pytest.raises(ValidationError) as post_error:
        Contract(InvalidPostInit).from_python({"value": 1})
    assert post_error.value.errors()[0]["location"] == ["value"]
    assert post_init_calls == 1


def test_post_init_application_exception_propagates_unchanged() -> None:
    failure = RuntimeError("application lifecycle failure")

    @dataclass
    class Failing:
        value: int

        def __post_init__(self) -> None:
            raise failure

    with pytest.raises(RuntimeError) as captured:
        Contract(Failing).from_python({"value": 1})
    assert captured.value is failure


def test_kw_only_aliases_mapping_json_and_detached_output_share_field_truth() -> None:
    @dataclass(kw_only=True)
    class User:
        name: Annotated[str, Alias("fullName")]
        tags: list[str] = field(default_factory=list)

    contract = Contract(User)
    source = UserDict({"fullName": "Ada", "tags": ["admin"]})
    user = contract.from_python(source)
    decoded = contract.from_json('{"fullName":"Grace"}')
    projected = contract.to_python(user)

    assert user == User(name="Ada", tags=["admin"])
    assert decoded == User(name="Grace")
    assert projected == {"fullName": "Ada", "tags": ["admin"]}
    assert projected["tags"] is not user.tags
    assert contract.to_json(user) == '{"fullName":"Ada","tags":["admin"]}'


def test_inheritance_overrides_and_supported_multiple_inheritance_follow_stdlib_fields() -> None:
    @dataclass
    class Right:
        right: int

    @dataclass
    class Left:
        value: int

    @dataclass
    class Combined(Left, Right):
        value: str
        own: bool

    contract = Contract(Combined)
    combined = contract.from_python({"right": 1, "value": "ok", "own": True})

    assert combined == Combined(right=1, value="ok", own=True)
    assert contract.to_python(combined) == {"right": 1, "value": "ok", "own": True}
    with pytest.raises(ValidationError):
        contract.from_python({"right": 1, "value": 2, "own": True})


def test_concrete_generic_and_open_generic_policy_are_explicit() -> None:
    @dataclass
    class Page[T]:
        items: list[T]

    contract = Contract[Page[int]](Page[int])
    page = contract.from_python({"items": [1, 2]})

    assert page == Page([1, 2])
    assert contract.validate(page) is page
    assert contract.to_python(page) == {"items": [1, 2]}
    with pytest.raises(TypeError, match="Unsupported annotation"):
        Contract(Page)


def test_recursive_and_mutually_recursive_dataclasses_use_finite_named_graphs() -> None:
    nodes = Contract(RecursiveNode)
    node = nodes.from_python({"value": 1, "children": [{"value": 2}]})
    parent = Contract(MutualParent).from_python({"child": {"parent": None}})

    schema = nodes._artifacts.schema
    assert isinstance(schema, DataclassSchema)
    children = schema.fields[1].schema
    assert isinstance(children, SequenceSchema)
    assert isinstance(children.item, NamedReferenceSchema)
    assert type(node.children[0]) is RecursiveNode
    assert type(parent.child) is MutualChild
    assert nodes.to_python(node) == {"value": 1, "children": [{"value": 2, "children": []}]}


def test_runtime_cycle_policy_separates_strict_input_and_output() -> None:
    contract = Contract(RecursiveNode)
    node = RecursiveNode(1)
    node.children.append(node)

    assert contract.validate(node) is node
    with pytest.raises(SerializationError, match="cyclic"):
        contract.to_python(node)

    cyclic: dict[str, object] = {"value": 1}
    cyclic["children"] = [cyclic]
    with pytest.raises(ValidationError) as captured:
        contract.from_python(cyclic)
    assert captured.value.errors()[0]["code"] == "cycle"
    assert captured.value.errors()[0]["location"] == ["children", 0]


def test_spec_typed_dict_alias_union_and_dataclass_composition_is_bidirectional() -> None:
    envelope = CompositionEnvelope.from_mapping(
        {"payload": {"id": 1, "owner": {"name": "Ada"}, "flags": {"active": True}}}
    )
    collection = Contract(list[CompositionPayload | None]).from_json(
        '[{"id":1,"owner":{"name":"Ada"},"flags":{"active":true}},null]'
    )

    assert type(envelope.payload) is CompositionPayload
    assert type(envelope.payload.owner) is CompositionOwner
    assert envelope.to_dict() == {"payload": {"id": 1, "owner": {"name": "Ada"}, "flags": {"active": True}}}
    assert type(collection[0]) is CompositionPayload
    assert collection[1] is None


def test_tagged_dataclass_unions_remain_a_deliberate_non_goal() -> None:
    @dataclass
    class Cat:
        kind: Literal["cat"]

    @dataclass
    class Dog:
        kind: Literal["dog"]

    with pytest.raises(TypeError, match="tagged unions support Spec or TypedDict"):
        Contract(Annotated[Cat | Dog, Discriminator("kind")])


def test_nested_errors_compose_external_alias_locations() -> None:
    with pytest.raises(ValidationError) as captured:
        Contract(ErrorBatch).from_python({"orders": [{"instrument": {"ticker": "A"}}, {"instrument": {"ticker": 2}}]})
    assert captured.value.errors()[0]["location"] == ["orders", 1, "instrument", "ticker"]


def test_sensitive_errors_redact_but_dataclass_repr_remains_application_owned() -> None:
    secret = "campaign-21b-secret"

    @dataclass
    class Credentials:
        token: Annotated[int, Sensitive()]

    credentials = Credentials(secret)  # type: ignore[arg-type]
    contract = Contract(Credentials)

    with pytest.raises(ValidationError) as captured:
        contract.validate(credentials)
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value.errors())
    assert secret in repr(credentials)


def test_dataclass_metadata_is_ignored_and_hostile_names_never_enter_generated_source() -> None:
    Hostile = make_dataclass(
        "user;raise RuntimeError",
        [("value", Annotated[int, Alias("x-y")], field(metadata={"talea": Sensitive()}))],
    )
    contract = Contract(Hostile)
    value = contract.from_python({"x-y": 1})

    assert contract.to_python(value) == {"x-y": 1}
    assert contract._artifacts.contains_sensitive is False


def test_declared_attribute_execution_is_trusted_application_behavior() -> None:
    @dataclass
    class HostileAccess:
        value: int

        def __getattribute__(self, name: str) -> object:
            if name == "value":
                raise RuntimeError("application descriptor failure")
            return object.__getattribute__(self, name)

    with pytest.raises(RuntimeError, match="application descriptor failure"):
        Contract(HostileAccess).validate(HostileAccess(1))


def test_json_schema_and_openapi_share_directional_dataclass_truth() -> None:
    @dataclass
    class User:
        name: Annotated[str, Alias("fullName")]
        active: bool = True
        normalized: str = field(init=False, default="yes")

    contract = Contract(User)
    input_schema = contract.json_schema()
    output_schema = contract.json_schema(mode="output")
    user_input = input_schema["$defs"]["User"]
    user_output = output_schema["$defs"]["User"]

    assert user_input["required"] == ["fullName"]
    assert user_input["properties"]["active"]["default"] is True
    assert "normalized" not in user_input["properties"]
    assert user_output["required"] == ["fullName", "active", "normalized"]
    assert user_output["properties"]["normalized"]["readOnly"] is True
    validate_openapi(_openapi_document(contract.openapi_schema()))


def test_resource_policy_counts_nested_dataclass_work() -> None:
    contract = Contract(ResourceParent)
    with pytest.raises(ResourceLimitError) as captured:
        contract.from_python({"child": {"value": 1}}, policy=ResourcePolicy(max_nodes=1))
    assert captured.value.code == "nodes"


def test_concurrent_lazy_publication_reuses_contract_artifacts() -> None:
    @dataclass
    class User:
        name: str

    contract = Contract(User)

    def exercise(index: int) -> tuple[str, dict[str, object]]:
        user = contract.from_json(f'{{"name":"user-{index}"}}')
        return contract.to_json(user), contract.to_python(user)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(exercise, range(32)))

    assert results[0] == ('{"name":"user-0"}', {"name": "user-0"})
    assert contract._artifacts.json_input is not None
    assert contract._artifacts.python_output is not None
    assert contract._artifacts.json_output is not None


def test_ordinary_contract_and_spec_paths_do_not_discover_dataclasses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from talea.schema import resolution

    def unexpected_discovery(value: object) -> bool:
        raise AssertionError(f"unexpected dataclass discovery for {value!r}")

    monkeypatch.setattr(resolution, "is_dataclass", unexpected_discovery)

    integers = Contract(int)

    class Ordinary(Spec):
        value: int

    assert integers.validate(1) == 1
    assert Ordinary(value=1).value == 1


def test_contract_graphs_are_collectible_and_instances_keep_stdlib_behavior() -> None:
    def build() -> weakref.ReferenceType[type[object]]:
        @dataclass(frozen=True)
        class User:
            name: str

        contract = Contract(User)
        user = contract.from_python({"name": "Ada"})
        assert hash(user) == hash(User("Ada"))
        return weakref.ref(User)

    class_reference = build()
    gc.collect()
    pickled = pickle.dumps(PickleUser("Ada"))

    assert pickle.loads(pickled) == PickleUser("Ada")
    assert class_reference() is None


@given(st.integers(), st.lists(st.text(max_size=12), max_size=8))
def test_mapping_json_and_projection_round_trip_property(value: int, labels: list[str]) -> None:
    record = PROPERTY_RECORD.from_python({"value": value, "labels": labels})
    encoded = PROPERTY_RECORD.to_json(record)
    decoded = PROPERTY_RECORD.from_json(encoded)

    assert decoded == record
    assert PROPERTY_RECORD.to_python(decoded) == {"value": value, "labels": labels}
