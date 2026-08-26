import json
import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, cast
from uuid import UUID

import pytest

from talea import Ge, Spec, ValidationError, check, transform
from talea.input.emission import _BoundaryValidationEmitter, schema_needs_conversion
from talea.schema import (
    ConstrainedSchema,
    EnumSchema,
    FixedTupleSchema,
    LiteralSchema,
    LiteralValue,
    MappingSchema,
    PrimitiveSchema,
    Schema,
    TypeSchema,
    UnionSchema,
)
from talea.validation.emission import _GeneratedNames


class State(Enum):
    OPEN = "open"


class Code(IntEnum):
    OK = 200


class Status(StrEnum):
    ACTIVE = "active"


class Ratio(Enum):
    HALF = 0.5


def test_default_json_boundary_accepts_text_bytes_and_bytearray() -> None:
    class User(Spec):
        identifier: int
        name: str

    payload = '{"identifier": 1, "name": "Ada"}'
    for data in (payload, payload.encode(), bytearray(payload.encode())):
        user = User.from_json(data)
        assert (user.identifier, user.name) == (1, "Ada")


def test_json_constructs_nested_specs_and_converts_all_array_container_shapes() -> None:
    class Address(Spec):
        city: str

    class Payload(Spec):
        address: Address
        addresses: list[Address | None]
        pair: tuple[int, str]
        values: tuple[int, ...]
        unique: set[int]
        frozen: frozenset[str]
        lookup: dict[str, Address]

    payload = Payload.from_json(
        """{
            "address": {"city": "Zurich"},
            "addresses": [{"city": "Bern"}, null],
            "pair": [1, "one"],
            "values": [1, 2],
            "unique": [1, 1, 2],
            "frozen": ["a", "b"],
            "lookup": {"home": {"city": "Basel"}}
        }"""
    )

    assert type(payload.address) is Address
    assert type(payload.addresses[0]) is Address
    assert payload.pair == (1, "one")
    assert payload.values == (1, 2)
    assert payload.unique == {1, 2}
    assert payload.frozen == frozenset({"a", "b"})
    assert type(payload.lookup["home"]) is Address


def test_json_standard_library_representations_are_schema_aware() -> None:
    class Payload(Spec):
        identifier: UUID
        timestamp: datetime
        day: date
        clock: time
        path: Path
        posix: PurePosixPath
        windows: PureWindowsPath
        address: IPv4Address
        network: IPv4Network
        interface: IPv4Interface
        state: State
        code: Code
        status: Status
        literal: Literal[State.OPEN]
        ratio: Ratio

    payload = Payload.from_json(
        """{
            "identifier": "00000000-0000-0000-0000-000000000000",
            "timestamp": "2026-08-26T12:30:00+00:00",
            "day": "2026-08-26",
            "clock": "12:30:00",
            "path": ".",
            "posix": "/tmp/input",
            "windows": "C:/input",
            "address": "127.0.0.1",
            "network": "10.0.0.0/24",
            "interface": "10.0.0.1/24",
            "state": "open",
            "code": 200,
            "status": "active",
            "literal": "open",
            "ratio": 0.5
        }"""
    )

    assert payload.identifier == UUID(int=0)
    assert payload.timestamp == datetime(2026, 8, 26, 12, 30, tzinfo=timezone.utc)
    assert payload.day == date(2026, 8, 26)
    assert payload.clock == time(12, 30)
    assert payload.path == Path(".")
    assert payload.posix == PurePosixPath("/tmp/input")
    assert payload.windows == PureWindowsPath("C:/input")
    assert payload.address == IPv4Address("127.0.0.1")
    assert payload.network == IPv4Network("10.0.0.0/24")
    assert payload.interface == IPv4Interface("10.0.0.1/24")
    assert (payload.state, payload.code, payload.status, payload.literal, payload.ratio) == (
        State.OPEN,
        Code.OK,
        Status.ACTIVE,
        State.OPEN,
        Ratio.HALF,
    )


def test_decimal_json_is_exact_and_custom_float_decoding_is_rejected() -> None:
    class Amount(Spec):
        value: Decimal
        whole: Decimal

    exact = "1234567890.12345678901234567890"
    amount = Amount.from_json(f'{{"value": {exact}, "whole": 2}}')
    assert amount.value == Decimal(exact)
    assert amount.whole == Decimal(2)

    with pytest.raises(ValidationError) as lossy:
        Amount.from_json('{"value": 1.25, "whole": 2}', loads=json.loads)
    assert lossy.value.location == ("value",)
    assert lossy.value.code == "type"


def test_json_float_accepts_json_numbers_and_rejects_non_finite_custom_values() -> None:
    class Measurement(Spec):
        value: float

    assert Measurement.from_json('{"value": 1}').value == 1.0
    assert Measurement.from_json('{"value": 1.25}').value == 1.25
    with pytest.raises(ValidationError) as non_finite:
        Measurement.from_json("ignored", loads=lambda data: {"value": math.nan})
    assert non_finite.value.code == "json_invalid"
    assert non_finite.value.location == ("value",)

    with pytest.raises(ValidationError) as overflow:
        Measurement.from_json("ignored", loads=lambda data: {"value": 10**1000})
    assert overflow.value.code == "type"


def test_json_constrained_float_and_singleton_tuple_conversion() -> None:
    class Payload(Spec):
        ratio: Annotated[float, Ge(0.0)]
        single: tuple[UUID]

    payload = Payload.from_json('{"ratio":1,"single":["00000000-0000-0000-0000-000000000000"]}')
    assert payload.ratio == 1.0
    assert payload.single == (UUID(int=0),)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_default_json_decoder_rejects_non_standard_numeric_constants(token: str) -> None:
    class Measurement(Spec):
        value: float

    with pytest.raises(ValidationError) as raised:
        Measurement.from_json(f'{{"value": {token}}}')
    assert raised.value.code == "json_invalid"
    assert raised.value.errors()[0]["context"] == {"reason": "non_finite_number"}


def test_default_json_decoder_rejects_duplicate_keys_at_any_object_depth() -> None:
    class Nested(Spec):
        value: int

    class Payload(Spec):
        nested: Nested

    with pytest.raises(ValidationError) as raised:
        Payload.from_json('{"nested": {"value": 1, "value": 2}}')
    assert raised.value.code == "json_duplicate"
    assert raised.value.errors()[0]["context"] == {"key": "value"}

    huge = '{"padding":"' + "x" * 5000 + '","nested":{"value":1,"value":2}}'
    with pytest.raises(ValidationError) as large:
        Payload.from_json(huge)
    assert large.value.code == "json_duplicate"
    assert large.value.__cause__ is None


def test_malformed_json_has_parser_context_bounded_input_and_safe_cause_policy() -> None:
    class Payload(Spec):
        value: int

    with pytest.raises(ValidationError) as small:
        Payload.from_json('{"value": ]')
    detail = small.value.errors()[0]
    assert detail["code"] == "json_invalid"
    assert detail["context"] == {"line": 1, "column": 11, "position": 10}
    assert isinstance(small.value.__cause__, json.JSONDecodeError)

    huge = '{"value": ' + " " * 5000 + "]"
    with pytest.raises(ValidationError) as large:
        Payload.from_json(huge)
    assert large.value.__cause__ is None
    assert len(str(large.value.errors()[0]["input"])) <= 160

    with pytest.raises(ValidationError) as encoding:
        Payload.from_json(b'{"value":"\xff"}')
    assert encoding.value.code == "json_invalid"
    assert encoding.value.errors()[0]["context"]["reason"]

    huge_number = '{"value":' + "9" * 5_000 + "}"
    with pytest.raises(ValidationError) as pathological_number:
        Payload.from_json(huge_number)
    assert pathological_number.value.code == "json_invalid"
    assert pathological_number.value.__cause__ is None


def test_custom_decoder_contract_preserves_talea_semantics_and_exception_boundaries() -> None:
    calls: list[object] = []

    class Payload(Spec):
        identifier: int

    def loads(data: str | bytes | bytearray) -> object:
        calls.append(data)
        return {"identifier": "bad", "extra": True}

    with pytest.raises(ValidationError) as invalid:
        Payload.from_json("encoded", loads=loads)
    assert calls == ["encoded"]
    assert [error["code"] for error in invalid.value.errors()] == ["type", "unexpected"]

    with pytest.raises(ValidationError) as wrong_root:
        Payload.from_json("encoded", loads=lambda data: [1])
    assert wrong_root.value.expected == "JSON object"

    with pytest.raises(ValidationError) as syntax:
        Payload.from_json("encoded", loads=lambda data: (_ for _ in ()).throw(ValueError("bad syntax")))
    assert syntax.value.code == "json_invalid"
    assert syntax.value.errors()[0]["context"] == {"decoder": "function"}

    failure = RuntimeError("decoder bug")
    with pytest.raises(RuntimeError) as propagated:
        Payload.from_json("encoded", loads=lambda data: (_ for _ in ()).throw(failure))
    assert propagated.value is failure

    with pytest.raises(ValidationError) as large_custom_failure:
        Payload.from_json(
            "x" * 5_000,
            loads=lambda data: (_ for _ in ()).throw(ValueError("bad syntax")),
        )
    assert large_custom_failure.value.__cause__ is None

    text = "x" * 10_000

    class Text(Spec):
        value: str

    assert Text.from_json(f'{{"value":"{text}"}}').value == text


def test_custom_decoder_cannot_change_validation_or_recover_duplicate_evidence() -> None:
    class Payload(Spec):
        identifier: int

    assert Payload.from_json("encoded", loads=lambda data: {"identifier": 2}).identifier == 2
    with pytest.raises(ValidationError):
        Payload.from_json("encoded", loads=lambda data: {"identifier": "2"})


def test_json_transforms_receive_decoded_values_then_conversion_and_checks_run_once() -> None:
    events: list[str] = []

    class Payload(Spec):
        identifier: UUID

        @transform("identifier")
        def observe(identifier: object) -> object:
            events.append(f"transform:{type(identifier).__name__}")
            return identifier

        @check("identifier")
        def checked(identifier: UUID) -> None:
            events.append(f"check:{type(identifier).__name__}")

    payload = Payload.from_json('{"identifier":"00000000-0000-0000-0000-000000000000"}')
    assert payload.identifier == UUID(int=0)
    assert events == ["transform:str", "check:UUID"]


def test_json_union_conversion_uses_the_first_canonical_alternative_that_validates() -> None:
    class Choice(Spec):
        address: IPv4Address | IPv6Address
        container: list[int] | tuple[str, ...]

    choice = Choice.from_json('{"address":"::1","container":["a","b"]}')
    assert choice.address == IPv6Address("::1")
    assert choice.container == ("a", "b")


def test_json_rejects_unfrozen_representations_and_malformed_standard_values() -> None:
    class Payload(Spec):
        duration: timedelta
        identifier: UUID
        status: Status

    with pytest.raises(ValidationError) as raised:
        Payload.from_json('{"duration": 1, "identifier": "bad", "status": "missing"}')
    assert [error["location"] for error in raised.value.errors()] == [
        ["duration"],
        ["identifier"],
        ["status"],
    ]
    assert all(error["code"] == "type" for error in raised.value.errors())

    class ImpossibleSet(Spec):
        values: set[list[int]]

    with pytest.raises(ValidationError) as impossible:
        ImpossibleSet.from_json('{"values":[[1]]}')
    assert impossible.value.code == "type"
    assert impossible.value.location == ("values",)


def test_json_nested_custom_decoder_spec_identity_uses_existing_trust_semantics() -> None:
    class Basket(Spec):
        items: list[int]

    class Order(Spec):
        basket: Basket

    basket = Basket(items=[1])
    order = Order.from_json("encoded", loads=lambda data: {"basket": basket})
    assert order.basket is basket
    basket.items.append("bad")  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as raised:
        Order.from_json("encoded", loads=lambda data: {"basket": basket})
    assert raised.value.location == ("basket", "items", 1)


def test_deep_json_uses_decoder_behavior_without_a_talea_schema_interpreter() -> None:
    class Payload(Spec):
        values: list[int]

    deeply_nested = '{"values":' + "[" * 2000 + "0" + "]" * 2000 + "}"
    with pytest.raises(ValidationError) as raised:
        Payload.from_json(deeply_nested)
    assert raised.value.location == ("values", 0)


def test_boundary_emitter_rejects_impossible_schema_values_and_covers_union_shapes() -> None:
    lines: list[str] = []
    emitter = _BoundaryValidationEmitter(
        lines,
        _GeneratedNames(),
        {},
        mode="json",
    )
    mapping_emitter = _BoundaryValidationEmitter([], _GeneratedNames(), {}, mode="mapping")
    constrained_float = ConstrainedSchema(PrimitiveSchema("float"), (Ge(0.0),))
    nested_union = UnionSchema(frozenset({PrimitiveSchema("int"), PrimitiveSchema("str")}))
    outer_union = UnionSchema(frozenset({nested_union, PrimitiveSchema("none")}))
    enum_schema = EnumSchema(State, (LiteralValue(State, State.OPEN),))
    enum_literal = LiteralSchema(frozenset({LiteralValue(State, State.OPEN)}))
    primitive_literal = LiteralSchema(frozenset({LiteralValue(str, "open")}))
    primitive_mapping = MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int"))
    primitive_tuple = FixedTupleSchema((PrimitiveSchema("int"), PrimitiveSchema("str")))

    assert "json_number_types" in emitter.boundary_condition(constrained_float, "value")
    assert schema_needs_conversion(constrained_float, "json")
    assert schema_needs_conversion(enum_schema, "json")
    assert schema_needs_conversion(enum_literal, "json")
    assert not schema_needs_conversion(primitive_mapping, "mapping")
    emitter.emit_conversion(constrained_float, "value", (), 0)
    emitter.emit_schema(nested_union, "value", (), 0)
    emitter.emit_mapping_conversion(primitive_mapping, "value", (), 0)
    mapping_emitter.emit_fixed_tuple_conversion(primitive_tuple, "value", (), 0)
    emitter.emit_enum_conversion(primitive_literal, "value", 0)
    assert "standard_type" in emitter.boundary_condition(TypeSchema(timedelta, "nominal"), "value")
    assert "dict" in emitter.boundary_condition(
        MappingSchema(PrimitiveSchema("str"), PrimitiveSchema("int")),
        "value",
    )
    assert " or " in emitter.boundary_condition(outer_union, "value")
    assert "int" in emitter.boundary_condition(TypeSchema(Decimal, "nominal"), "value")
    assert emitter.boundary_condition(enum_schema, "value") == "True"

    class Empty(Spec):
        pass

    assert type(Empty.from_json("{}")) is Empty

    with pytest.raises(AssertionError):
        schema_needs_conversion(cast(Schema, object()), "json")
    with pytest.raises(AssertionError):
        emitter.emit_conversion(cast(Schema, object()), "value", (), 0)
    with pytest.raises(AssertionError):
        emitter.boundary_condition(cast(Schema, object()), "value")
