"""Bounded property and adversarial proof for Campaign 12 utility surfaces."""

from concurrent.futures import ThreadPoolExecutor
from copy import replace
from typing import Annotated, NotRequired, Required, TypedDict

import pytest
from hypothesis import given, settings, strategies as st

from talea import Alias, Contract, Spec, ValidationError, create_spec


class PatchPayload(TypedDict, total=False):
    id: Required[int]
    label: NotRequired[str]


class RecursiveNode(Spec):
    value: int
    children: list["RecursiveNode"]


@given(identifier=st.integers(), label=st.one_of(st.none(), st.text(max_size=30)))
@settings(max_examples=40, deadline=None)
def test_typed_dict_required_and_optional_keys_share_one_contract(
    identifier: int,
    label: str | None,
) -> None:
    contract = Contract[PatchPayload](PatchPayload)
    value: PatchPayload = {"id": identifier}
    if label is not None:
        value["label"] = label

    assert contract.validate(value) is value
    assert contract.from_json(contract.to_json(value)) == value
    with pytest.raises(ValidationError):
        contract.validate({"label": label or "missing"})


@given(
    st.lists(
        st.dictionaries(
            st.text(min_size=1, max_size=8),
            st.lists(st.integers(), max_size=8),
            max_size=8,
        ),
        max_size=8,
    )
)
@settings(max_examples=40, deadline=None)
def test_contract_nested_containers_validate_and_detach(values: list[dict[str, list[int]]]) -> None:
    contract = Contract[list[dict[str, list[int]]]](list[dict[str, list[int]]])

    assert contract.validate(values) is values
    projected = contract.to_python(values)
    assert projected == values
    assert projected is not values
    assert contract.from_json(contract.to_json(values)) == values


@given(count=st.integers(min_value=0, max_value=30), seed=st.integers())
@settings(max_examples=30, deadline=None)
def test_dynamic_field_declarations_use_the_normal_spec_lifecycle(count: int, seed: int) -> None:
    fields = {f"field_{index}": int for index in range(count)}
    values = {name: seed + index for index, name in enumerate(fields)}
    Dynamic = create_spec("PropertySpec", fields)

    instance = Dynamic(**values)

    assert instance.to_dict() == values
    assert Dynamic.from_mapping(values).to_dict() == values


@given(x=st.integers(), y=st.integers(), replacement=st.integers())
@settings(max_examples=40, deadline=None)
def test_copy_replace_changes_only_selected_validated_fields(x: int, y: int, replacement: int) -> None:
    class Point(Spec):
        x: int
        y: int

    original = Point(x=x, y=y)
    changed = replace(original, x=replacement)

    assert (changed.x, changed.y) == (replacement, y)
    assert (original.x, original.y) == (x, y)


recursive_payloads = st.recursive(
    st.integers().map(lambda value: {"value": value, "children": []}),
    lambda children: st.builds(
        lambda value, items: {"value": value, "children": items},
        st.integers(),
        st.lists(children, max_size=3),
    ),
    max_leaves=12,
)


@given(recursive_payloads)
@settings(max_examples=30, deadline=None)
def test_recursive_arbitrary_contract_round_trip(payload: dict[str, object]) -> None:
    contract = Contract[RecursiveNode](RecursiveNode)
    node = contract.from_python(payload)

    assert contract.to_python(node) == payload
    assert contract.from_json(contract.to_json(node)).to_dict() == payload


def test_contract_first_use_compilation_is_safe_under_concurrency() -> None:
    contract = Contract[list[int]](list[int])

    def warm(index: int) -> object:
        match index % 4:
            case 0:
                return contract.from_python([index])
            case 1:
                return contract.from_json(f"[{index}]")
            case 2:
                return contract.to_python([index])
            case _:
                return contract.to_json([index])

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = tuple(executor.map(warm, range(128)))

    assert len(results) == 128
    artifacts = contract._artifacts
    assert all(
        artifact is not None
        for artifact in (
            artifacts.python_input,
            artifacts.json_input,
            artifacts.python_output,
            artifacts.json_output,
        )
    )


def test_dynamic_names_aliases_and_generated_source_names_are_data_not_code() -> None:
    alias = "x']; raise RuntimeError('executed'); #"
    Dynamic = create_spec(
        "SourceSafe",
        {
            "instance": int,
            "changes": int,
            "field_names": int,
            "unknown_names": int,
            "value_0": int,
        },
    )
    Aliased = create_spec("AliasSafe", {"value": int}, namespace={}, module="safe.generated")
    AliasedField = create_spec("AliasedField", {"value": Annotated[int, Alias(alias)]})

    value = Dynamic(instance=1, changes=2, field_names=3, unknown_names=4, value_0=5)
    changed = replace(value, changes=6)

    assert changed.changes == 6
    assert Aliased(value=1).value == 1
    assert AliasedField.from_mapping({alias: 1}).value == 1


def test_large_dynamic_declaration_and_weird_typed_dict_keys_remain_structural() -> None:
    Dynamic = create_spec("LargeDynamic", {f"field_{index}": int for index in range(500)})
    values = {f"field_{index}": index for index in range(500)}
    Weird = TypedDict("Weird", {"x-y": int, "class": str})

    assert Dynamic(**values).field_499 == 499
    assert Contract(Weird).from_json('{"x-y":1,"class":"safe"}') == {"x-y": 1, "class": "safe"}


def test_repeated_contract_creation_has_no_canonical_or_global_cache() -> None:
    contracts = [Contract[list[int]](list[int]) for _ in range(200)]

    assert len({id(contract._artifacts) for contract in contracts}) == len(contracts)
    assert len({id(contract._artifacts.validator) for contract in contracts}) == len(contracts)
