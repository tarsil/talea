from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Literal, NamedTuple, TypedDict

import pytest

from talea import (
    Contract,
    Discriminator,
    Representation,
    ResourceLimitError,
    ResourcePolicy,
    Spec,
    ValidationError,
    create_spec,
    derive_spec,
    validate_call,
)
from talea.settings import Settings


class Coordinate(NamedTuple):
    latitude: float
    longitude: float


class SpecEnvelope(Spec):
    coordinate: Coordinate


@dataclass
class DataclassEnvelope:
    coordinate: Coordinate


class TypedEnvelope(TypedDict):
    coordinate: Coordinate


def _load_integer(value: str) -> int:
    return int(value)


def _dump_integer(value: int) -> str:
    return str(value)


type EncodedInteger = Annotated[
    int,
    Representation(input=str, load=_load_integer, output=str, dump=_dump_integer),
]


class EncodedRecord(NamedTuple):
    value: EncodedInteger


class RecursiveRecord(NamedTuple):
    children: list[RecursiveRecord]


def test_structured_and_container_composition_uses_normal_schema_dispatch() -> None:
    spec = SpecEnvelope.from_mapping({"coordinate": [47.37, 8.54]})
    dataclass_value = Contract(DataclassEnvelope).from_python({"coordinate": (47.37, 8.54)})
    typed = Contract(TypedEnvelope).from_python({"coordinate": [47.37, 8.54]})
    sequence = Contract[list[Coordinate]](list[Coordinate]).from_python([[47.37, 8.54]])
    mapping = Contract[dict[str, Coordinate]](dict[str, Coordinate]).from_python({"zurich": [47.37, 8.54]})

    expected = Coordinate(47.37, 8.54)
    assert spec.coordinate == expected
    assert dataclass_value.coordinate == expected
    assert typed == {"coordinate": expected}
    assert sequence == [expected]
    assert mapping == {"zurich": expected}
    assert spec.to_dict() == {"coordinate": (47.37, 8.54)}
    assert spec.to_json() == '{"coordinate":[47.37,8.54]}'


def test_union_and_optional_composition_preserve_positional_identity() -> None:
    contract = Contract[Coordinate | None](Coordinate | None)

    assert contract.from_python([1.0, 2.0]) == Coordinate(1.0, 2.0)
    assert contract.from_python(None) is None
    with pytest.raises(ValidationError):
        contract.from_python({"latitude": 1.0, "longitude": 2.0})


def test_tagged_branch_may_contain_namedtuple_without_becoming_a_positional_discriminator() -> None:
    class Located(Spec):
        kind: Literal["located"]
        coordinate: Coordinate

    class Unknown(Spec):
        kind: Literal["unknown"]

    type Event = Annotated[Located | Unknown, Discriminator("kind")]
    event = Contract[Event](Event).from_python({"kind": "located", "coordinate": [47.37, 8.54]})

    assert type(event) is Located
    assert event.coordinate == Coordinate(47.37, 8.54)
    with pytest.raises(TypeError, match="Spec or TypedDict"):
        Contract(Annotated[Coordinate | TypedEnvelope, Discriminator("kind")])


def test_dynamic_and_derived_specs_retain_the_whole_namedtuple_field() -> None:
    Dynamic = create_spec("DynamicCoordinate", {"coordinate": Coordinate}, module=__name__)
    Derived = derive_spec(SpecEnvelope, include=("coordinate",))

    expected = Coordinate(1.0, 2.0)
    assert Dynamic.from_mapping({"coordinate": [1.0, 2.0]}).coordinate == expected
    assert Derived.from_mapping({"coordinate": (1.0, 2.0)}).coordinate == expected


def test_representation_inside_namedtuple_composes_in_both_directions() -> None:
    contract = Contract(EncodedRecord)
    value = contract.from_python(["4"])

    assert value == EncodedRecord(4)
    assert contract.to_python(value) == ("4",)
    assert contract.to_json(value) == '["4"]'


def test_sync_and_async_callable_boundaries_remain_strict() -> None:
    @validate_call
    def sync(value: Coordinate) -> Coordinate:
        return value

    @validate_call
    async def asynchronous(value: Coordinate) -> Coordinate:
        return value

    coordinate = Coordinate(1.0, 2.0)
    assert sync(coordinate) is coordinate
    assert asyncio.run(asynchronous(coordinate)) is coordinate
    with pytest.raises(ValidationError):
        sync((1.0, 2.0))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        asyncio.run(asynchronous((1.0, 2.0)))  # type: ignore[arg-type]


def test_settings_text_decoding_uses_json_array_semantics() -> None:
    class Application(Spec):
        coordinate: Coordinate

    value = Settings(Application, prefix="APP_").load(environment={"APP_COORDINATE": "[47.37,8.54]"})

    assert value.coordinate == Coordinate(47.37, 8.54)


def test_incremental_and_jsonl_boundaries_reuse_retained_namedtuple_input() -> None:
    contract = Contract(Coordinate)

    assert list(contract.iter_validate((Coordinate(1.0, 2.0),))) == [Coordinate(1.0, 2.0)]
    assert list(contract.iter_python(([1.0, 2.0], (3.0, 4.0)))) == [
        Coordinate(1.0, 2.0),
        Coordinate(3.0, 4.0),
    ]
    assert list(contract.iter_jsonl(("[1.0,2.0]", "[3.0,4.0]"))) == [
        Coordinate(1.0, 2.0),
        Coordinate(3.0, 4.0),
    ]


def test_resource_policy_counts_namedtuple_and_nested_slots() -> None:
    class Batch(NamedTuple):
        values: list[int]

    contract = Contract(Batch)

    with pytest.raises(ResourceLimitError) as nodes:
        contract.from_python([[1, 2]], policy=ResourcePolicy(max_nodes=2))
    assert nodes.value.code == "nodes"
    with pytest.raises(ResourceLimitError) as depth:
        contract.from_python([[1]], policy=ResourcePolicy(max_depth=1))
    assert depth.value.code == "depth"


def test_recursive_external_cycles_follow_existing_named_graph_policy() -> None:
    source: list[object] = []
    source.append(source)

    with pytest.raises(ValidationError) as captured:
        Contract(RecursiveRecord).from_python([source])
    assert captured.value.errors()[0]["code"] == "cycle"


def test_namedtuple_is_a_leaf_for_nested_serialization_selection() -> None:
    value = SpecEnvelope(coordinate=Coordinate(1.0, 2.0))

    assert value.to_dict(include={"coordinate": True}) == {"coordinate": (1.0, 2.0)}
    with pytest.raises(ValueError, match="cannot descend into scalar field"):
        value.to_dict(include={"coordinate": {"latitude": True}})
