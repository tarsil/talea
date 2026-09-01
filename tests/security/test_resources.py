import base64
import sys
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, dataclass
from decimal import Decimal
from typing import Annotated, Literal, TypedDict

import pytest
from hypothesis import given, strategies as st

from talea import (
    Contract,
    Discriminator,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
    ValidationError,
    create_spec,
)
from talea.input.value import compile_value_input
from talea.resources.policy import DEFAULT_RESOURCE_POLICY, resolve_policy
from talea.resources.state import UNLIMITED_RESOURCE_STATE, check_input_size, resource_state
from talea.schema import PrimitiveSchema

_INTEGER_LIST = Contract(list[int])


def test_resource_policy_is_immutable_validated_and_has_no_global_setter() -> None:
    policy = ResourcePolicy()

    assert policy is DEFAULT_RESOURCE_POLICY or policy == DEFAULT_RESOURCE_POLICY
    assert (policy.max_input_bytes, policy.max_depth, policy.max_nodes, policy.max_errors) == (
        8 * 1024 * 1024,
        64,
        100_000,
        100,
    )
    with pytest.raises(FrozenInstanceError):
        policy.max_depth = 1  # type: ignore[misc]
    for name in ("max_input_bytes", "max_depth", "max_nodes", "max_errors"):
        with pytest.raises(ValueError, match=name):
            ResourcePolicy(**{name: 0})  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=name):
            ResourcePolicy(**{name: True})  # type: ignore[arg-type]
    assert resolve_policy(None) is DEFAULT_RESOURCE_POLICY
    assert resolve_policy(policy) is policy
    with pytest.raises(TypeError, match="ResourcePolicy"):
        resolve_policy(object())  # type: ignore[arg-type]


def test_json_transport_size_is_checked_before_default_or_custom_decode() -> None:
    calls: list[object] = []

    class Payload(Spec):
        value: int

    policy = ResourcePolicy(max_input_bytes=5)
    for data in (b"123456", bytearray(b"123456"), "123456", "ééé"):
        with pytest.raises(ResourceLimitError) as raised:
            Payload.from_json(data, loads=lambda value: calls.append(value), policy=policy)
        assert raised.value.code == "input_size"
        assert raised.value.limit == 5
        assert raised.value.observed > raised.value.limit
        assert not hasattr(raised.value, "value")
    assert calls == []

    assert Payload.from_json("x", loads=lambda value: {"value": 1}, policy=policy).value == 1
    check_input_size("\ud800", ResourcePolicy(max_input_bytes=3))
    check_input_size(b"arbitrary", ResourcePolicy(max_input_bytes=None))


def test_scalar_sizes_remain_schema_or_python_owned() -> None:
    policy = ResourcePolicy(max_input_bytes=None, max_nodes=1)
    text = "x" * 100_000
    binary = b"x" * 100_000
    encoded = '"' + base64.b64encode(binary).decode() + '"'

    assert Contract(str).from_python(text, policy=policy) is text
    assert Contract(bytes).from_python(binary, policy=policy) is binary
    assert Contract(bytes).from_json(encoded, policy=policy) == binary

    digits = "9" * (sys.get_int_max_str_digits() + 1)
    with pytest.raises(ValidationError) as integer_failure:
        Contract(int).from_json(digits, policy=policy)
    assert integer_failure.value.code == "json_invalid"
    assert Contract(Decimal).from_json(f"{digits}.0", policy=policy) == Decimal(f"{digits}.0")


def test_contract_retains_a_policy_and_per_call_override_replaces_it() -> None:
    restrictive = ResourcePolicy(max_nodes=2)
    contract = Contract(list[int], policy=restrictive)

    with pytest.raises(ResourceLimitError) as retained:
        contract.from_python([1, 2])
    assert (retained.value.code, retained.value.limit, retained.value.observed) == ("nodes", 2, 3)
    assert contract.from_python([1, 2], policy=ResourcePolicy(max_nodes=3)) == [1, 2]
    assert contract.from_json("[1,2]", policy=ResourcePolicy(max_nodes=3)) == [1, 2]
    assert contract.validate([1, 2]) == [1, 2]


def test_spec_mapping_node_budget_counts_actual_compiled_visits() -> None:
    class Payload(Spec):
        values: list[int]

    assert Payload.from_mapping(
        {"values": [1, 2]},
        policy=ResourcePolicy(max_nodes=4),
    ).values == [1, 2]
    with pytest.raises(ResourceLimitError) as raised:
        Payload.from_mapping({"values": [1, 2]}, policy=ResourcePolicy(max_nodes=3))
    assert raised.value.code == "nodes"
    assert raised.value.observed == 4


def test_spec_and_contract_boundaries_share_spec_node_semantics() -> None:
    class Payload(Spec):
        value: int

    policy = ResourcePolicy(max_nodes=2)
    assert Payload.from_mapping({"value": 1}, policy=policy).value == 1
    assert Contract(Payload).from_python({"value": 1}, policy=policy).value == 1
    existing = Payload(value=1)
    assert Contract(Payload).from_python(existing, policy=policy) is existing


def test_conversion_work_is_budgeted_before_constructing_the_full_sequence() -> None:
    constructed: list[int] = []

    @dataclass
    class Item:
        value: int

        def __post_init__(self) -> None:
            constructed.append(1)

    contract = Contract(list[Item])
    values = [{"value": index} for index in range(20)]

    with pytest.raises(ResourceLimitError) as captured:
        contract.from_python(values, policy=ResourcePolicy(max_nodes=5))

    assert (captured.value.code, captured.value.limit, captured.value.observed) == ("nodes", 5, 6)
    assert constructed == [1, 1]


def test_mapping_detachment_stops_at_the_node_budget() -> None:
    class Payload(TypedDict):
        value: int

    @dataclass
    class Record:
        value: int

    class CountingMapping(Mapping[str, object]):
        def __init__(self) -> None:
            self.data = {"value": 1, **{f"extra_{index}": index for index in range(20)}}
            self.reads = 0

        def __getitem__(self, key: str) -> object:
            self.reads += 1
            return self.data[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self.data)

        def __len__(self) -> int:
            return len(self.data)

    for annotation in (Payload, Record):
        value = CountingMapping()
        with pytest.raises(ResourceLimitError) as captured:
            Contract(annotation).from_python(value, policy=ResourcePolicy(max_nodes=3))
        assert (captured.value.code, captured.value.observed) == ("nodes", 4)
        assert value.reads == 3


@given(st.lists(st.integers(), max_size=50))
def test_node_budget_is_exact_for_arbitrary_integer_lists(values: list[int]) -> None:
    required_nodes = len(values) + 1

    assert _INTEGER_LIST.from_python(values, policy=ResourcePolicy(max_nodes=required_nodes)) == values
    if values:
        with pytest.raises(ResourceLimitError) as raised:
            _INTEGER_LIST.from_python(values, policy=ResourcePolicy(max_nodes=required_nodes - 1))
        assert (raised.value.code, raised.value.observed) == ("nodes", required_nodes)


def test_recursive_alias_and_typed_dict_depth_fail_before_python_recursion() -> None:
    type RecursiveValue = int | list[RecursiveValue]

    class RecursivePayload(TypedDict):
        value: int
        children: list[RecursivePayload]

    alias_value: object = 1
    typed_value: dict[str, object] = {"value": 1, "children": []}
    for _ in range(20):
        alias_value = [alias_value]
        typed_value = {"value": 1, "children": [typed_value]}

    alias_contract = Contract(RecursiveValue)
    typed_contract = Contract(RecursivePayload)
    assert alias_contract.from_python(alias_value, policy=ResourcePolicy(max_depth=20))
    with pytest.raises(ResourceLimitError) as alias_depth:
        alias_contract.from_python(alias_value, policy=ResourcePolicy(max_depth=19))
    assert alias_depth.value.code == "depth"
    assert alias_depth.value.observed == 20

    assert typed_contract.from_python(typed_value, policy=ResourcePolicy(max_depth=42))
    with pytest.raises(ResourceLimitError) as typed_depth:
        typed_contract.from_python(typed_value, policy=ResourcePolicy(max_depth=41))
    assert typed_depth.value.code == "depth"
    assert typed_depth.value.observed == 42


def test_existing_mutable_recursive_spec_obeys_depth_policy() -> None:
    class Node(Spec):
        value: int
        children: list[Node]

    node = Node(value=0, children=[])
    for value in range(20):
        node = Node(value=value, children=[node])

    with pytest.raises(ResourceLimitError) as raised:
        Contract(Node).from_python(node, policy=ResourcePolicy(max_depth=3))
    assert (raised.value.code, raised.value.limit, raised.value.observed) == ("depth", 3, 4)


def test_existing_spec_named_graph_uses_resource_validation_and_preserves_cycles() -> None:
    type RecursiveValue = int | list[RecursiveValue]

    class Holder(Spec):
        value: RecursiveValue
        children: list[Holder]

    nested: object = 0
    for _ in range(10):
        nested = [nested]
    holder = Holder(value=nested, children=[])
    contract = Contract(Holder)

    with pytest.raises(ResourceLimitError) as depth_failure:
        contract.from_python(holder, policy=ResourcePolicy(max_depth=3))
    assert depth_failure.value.code == "depth"

    holder.value[0] = "bad"  # type: ignore[index]
    with pytest.raises(ValidationError) as validation_failure:
        contract.from_python(holder)
    assert validation_failure.value.location == ("value",)

    cyclic: list[object] = []
    cyclic.append(cyclic)
    cycle_holder = Holder(value=[], children=[])
    cycle_holder.value.append(cycle_holder.value)  # type: ignore[union-attr]
    assert contract.from_python(cycle_holder) is cycle_holder


def test_existing_recursive_spec_validates_selected_tagged_branch_with_policy() -> None:
    branches = tuple(
        create_spec(
            f"CurrentResourceBranch{index}",
            {"kind": Literal[index], "values": list[int]},
        )
        for index in range(8)
    )
    union = branches[0]
    for branch in branches[1:]:
        union |= branch
    type Item = Annotated[union, Discriminator("kind")]  # type: ignore[valid-type]

    class Container(Spec):
        item: Item
        children: list[Container]

    value = Container(item=branches[7](kind=7, values=[1]), children=[])
    assert Contract(Container).from_python(value, policy=ResourcePolicy(max_nodes=10)) is value


def test_error_budget_stops_in_canonical_order_and_exposes_truncation() -> None:
    class Payload(Spec):
        value: int

    data: dict[str, object] = {"value": "bad"}
    data.update({f"extra_{index}": True for index in range(20)})

    with pytest.raises(ValidationError) as raised:
        Payload.from_mapping(data, policy=ResourcePolicy(max_errors=3))
    assert raised.value.truncated is True
    assert [error["location"] for error in raised.value.errors()] == [
        ["value"],
        ["extra_0"],
        ["extra_1"],
    ]
    assert str(raised.value).splitlines()[0] == "Payload (3 errors) [truncated]"

    with pytest.raises(ValidationError) as complete:
        Payload.from_mapping({"value": "bad", "extra": True}, policy=ResourcePolicy(max_errors=3))
    assert complete.value.truncated is False


def test_nested_error_truncation_terminates_the_root_operation() -> None:
    class Inner(Spec):
        value: int

    class Outer(Spec):
        inner: Inner
        later: int

    with pytest.raises(ValidationError) as raised:
        Outer.from_mapping(
            {"inner": {"value": "bad", "x": 1, "y": 2}, "later": "bad"},
            policy=ResourcePolicy(max_errors=2),
        )
    assert raised.value.truncated is True
    assert [error["location"] for error in raised.value.errors()] == [["inner", "value"], ["inner", "x"]]


def test_tagged_union_budget_visits_only_the_selected_branch() -> None:
    branches = tuple(create_spec(f"ResourceBranch{index}", {"kind": Literal[index]}) for index in range(8))
    union = branches[0]
    for branch in branches[1:]:
        union |= branch
    contract = Contract(Annotated[union, Discriminator("kind")])

    selected = contract.from_python({"kind": 7}, policy=ResourcePolicy(max_nodes=4))
    assert type(selected) is branches[7]


def test_union_work_budget_counts_attempted_plausible_branches() -> None:
    contract = Contract(list[int] | list[str])

    with pytest.raises(ResourceLimitError) as raised:
        contract.from_python(["one", "two"], policy=ResourcePolicy(max_nodes=4))
    assert raised.value.code == "nodes"
    assert contract.from_python(["one", "two"], policy=ResourcePolicy(max_nodes=6)) == ["one", "two"]


def test_cycle_errors_remain_validation_failures_under_a_policy() -> None:
    type RecursiveValue = int | list[RecursiveValue]
    value: list[object] = []
    value.append(value)

    with pytest.raises(ValidationError) as raised:
        Contract(RecursiveValue).from_python(value, policy=ResourcePolicy(max_depth=10))
    assert raised.value.code == "cycle"


def test_shared_policy_has_operation_local_counters_across_threads() -> None:
    policy = ResourcePolicy(max_nodes=4)

    class Payload(Spec):
        values: list[int]

    def convert(index: int) -> int:
        return Payload.from_mapping({"values": [index, index + 1]}, policy=policy).values[0]

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(convert, range(40))) == list(range(40))


def test_resource_failure_does_not_retain_sensitive_or_hostile_values() -> None:
    secret = "campaign-18-secret"

    class Credentials(Spec):
        tokens: Annotated[list[str], Sensitive()]

    with pytest.raises(ResourceLimitError) as raised:
        Credentials.from_mapping(
            {"tokens": [secret] * 10},
            policy=ResourcePolicy(max_nodes=2),
        )
    assert secret not in str(raised.value)
    assert vars(raised.value) == {"code": "nodes", "limit": 2, "observed": 3}


def test_hostile_mapping_and_repr_cannot_corrupt_later_policy_state() -> None:
    class HostileValue:
        def __repr__(self) -> str:
            raise RuntimeError("repr must not run")

    class HostileMapping(dict[str, object]):
        def __getitem__(self, key: str) -> object:
            if key == "values":
                raise RuntimeError("mapping failure")
            return super().__getitem__(key)

    class Payload(Spec):
        values: list[str]

    policy = ResourcePolicy(max_nodes=2)
    with pytest.raises(RuntimeError, match="mapping failure"):
        Payload.from_mapping(HostileMapping(values=[]), policy=policy)
    with pytest.raises(ResourceLimitError) as raised:
        Payload.from_mapping({"values": [HostileValue()]}, policy=policy)
    assert (raised.value.code, raised.value.observed) == ("nodes", 3)
    assert Payload.from_mapping({"values": []}, policy=policy).values == []


def test_unlimited_internal_state_preserves_direct_compiled_artifact_contract() -> None:
    compiled = compile_value_input(PrimitiveSchema("int"), "mapping", "int")

    assert compiled(1) == 1
    marker = UNLIMITED_RESOURCE_STATE.begin_reservations()
    UNLIMITED_RESOURCE_STATE.reserve_node(1_000_000)
    UNLIMITED_RESOURCE_STATE.end_reservations(marker)
    assert UNLIMITED_RESOURCE_STATE.call_nested(compiled, 1, 99) == 1
    assert UNLIMITED_RESOURCE_STATE.error_limit_reached(1_000_000) is False
    state = resource_state(ResourcePolicy(max_errors=None, max_nodes=None, max_depth=None))
    state.consume_node(1_000_000)
    assert state.error_limit_reached(1_000_000) is False


def test_resource_reservation_scopes_enforce_depth_and_lifo_closure() -> None:
    state = resource_state(ResourcePolicy(max_depth=1))
    outer = state.begin_reservations()
    inner = state.begin_reservations()

    with pytest.raises(ResourceLimitError) as captured:
        state.reserve_node(2)
    assert (captured.value.code, captured.value.limit, captured.value.observed) == ("depth", 1, 2)
    with pytest.raises(RuntimeError, match="out of order"):
        state.end_reservations(outer)

    state.end_reservations(inner)
    state.end_reservations(outer)
