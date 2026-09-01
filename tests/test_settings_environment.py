"""Exercise schema-directed textual decoding and finite environment projection."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from ipaddress import IPv4Address, IPv6Network
from pathlib import Path
from typing import Annotated, Literal, TypedDict
from uuid import UUID

import pytest

from talea import Alias, Discriminator, Ge, Representation, Sensitive, Spec, ValidationError
from talea.settings import Settings


class ScalarSettings(Spec):
    text: str
    integer: int
    floating: float
    enabled: bool
    decimal: Decimal
    identifier: UUID
    day: date
    timestamp: datetime
    clock: time
    duration: timedelta
    payload: bytes
    address: IPv4Address
    network: IPv6Network
    path: Path


def test_all_standard_scalar_text_decoders() -> None:
    value = Settings(ScalarSettings, prefix="APP_").load(
        environment={
            "APP_TEXT": " null ",
            "APP_INTEGER": "-42",
            "APP_FLOATING": "1.25e2",
            "APP_ENABLED": "true",
            "APP_DECIMAL": "12.50",
            "APP_IDENTIFIER": "00000000-0000-0000-0000-000000000001",
            "APP_DAY": "2026-09-01",
            "APP_TIMESTAMP": "2026-09-01T10:11:12+02:00",
            "APP_CLOCK": "10:11:12",
            "APP_DURATION": "P2DT3H4M5.25S",
            "APP_PAYLOAD": "aGVsbG8=",
            "APP_ADDRESS": "192.0.2.1",
            "APP_NETWORK": "2001:db8::/32",
            "APP_PATH": "/srv/app",
        }
    )

    assert value.text == " null "
    assert value.integer == -42
    assert value.floating == 125.0
    assert value.enabled is True
    assert value.decimal == Decimal("12.50")
    assert value.identifier == UUID(int=1)
    assert value.day == date(2026, 9, 1)
    assert value.timestamp == datetime.fromisoformat("2026-09-01T10:11:12+02:00")
    assert value.clock == time(10, 11, 12)
    assert value.duration == timedelta(days=2, hours=3, minutes=4, seconds=5.25)
    assert value.payload == b"hello"
    assert value.address == IPv4Address("192.0.2.1")
    assert value.network == IPv6Network("2001:db8::/32")
    assert value.path == Path("/srv/app")


@pytest.mark.parametrize("text", ["TRUE", "yes", "on", "1", "false "])
def test_boolean_vocabulary_is_exact(text: str) -> None:
    class BooleanSettings(Spec):
        enabled: bool

    with pytest.raises(ValidationError) as raised:
        Settings(BooleanSettings, prefix="APP_").load(environment={"APP_ENABLED": text})
    assert raised.value.location == ("enabled",)


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("integer", "01"),
        ("floating", "nan"),
        ("decimal", "Infinity"),
        ("identifier", "not-a-uuid"),
        ("day", "2026-99-99"),
        ("timestamp", "yesterday"),
        ("clock", "25:00"),
        ("duration", "2 days"),
        ("payload", "***"),
        ("address", "999.0.0.1"),
        ("network", "bad"),
    ],
)
def test_malformed_scalar_text_flows_to_canonical_validation(field: str, text: str) -> None:
    valid = {
        "APP_TEXT": "x",
        "APP_INTEGER": "1",
        "APP_FLOATING": "1.0",
        "APP_ENABLED": "false",
        "APP_DECIMAL": "1",
        "APP_IDENTIFIER": str(UUID(int=0)),
        "APP_DAY": "2026-09-01",
        "APP_TIMESTAMP": "2026-09-01T00:00:00",
        "APP_CLOCK": "00:00:00",
        "APP_DURATION": "PT1S",
        "APP_PAYLOAD": "eA==",
        "APP_ADDRESS": "192.0.2.1",
        "APP_NETWORK": "2001:db8::/32",
        "APP_PATH": "/tmp",
    }
    valid[f"APP_{field.upper()}"] = text

    with pytest.raises(ValidationError) as raised:
        Settings(ScalarSettings, prefix="APP_").load(environment=valid)
    assert raised.value.location == (field,)


class Mode(StrEnum):
    SAFE = "safe"
    FAST = "fast"


class Code(Enum):
    OK = 200
    ERROR = 500


class ChoiceSettings(Spec):
    mode: Mode
    code: Code
    level: Literal["low", "high"]
    retries: Literal[1, 3]
    optional: int | None
    literal_null: str
    scalar_union: int | str


def test_enum_literal_optional_and_json_disambiguated_union() -> None:
    plan = Settings(ChoiceSettings, prefix="APP_")
    integer = plan.load(
        environment={
            "APP_MODE": "safe",
            "APP_CODE": "200",
            "APP_LEVEL": "high",
            "APP_RETRIES": "3",
            "APP_OPTIONAL": "null",
            "APP_LITERAL_NULL": "null",
            "APP_SCALAR_UNION": "4",
        }
    )
    text = plan.load(
        environment={
            "APP_MODE": "fast",
            "APP_CODE": "500",
            "APP_LEVEL": "low",
            "APP_RETRIES": "1",
            "APP_OPTIONAL": "5",
            "APP_LITERAL_NULL": "null",
            "APP_SCALAR_UNION": '"4"',
        }
    )

    assert (integer.mode, integer.code, integer.level, integer.retries) == (Mode.SAFE, Code.OK, "high", 3)
    assert integer.optional is None
    assert integer.literal_null == "null"
    assert type(integer.scalar_union) is int
    assert text.optional == 5
    assert type(text.scalar_union) is str


@pytest.mark.parametrize(
    ("name", "value"),
    [("APP_MODE", "unknown"), ("APP_LEVEL", "medium")],
)
def test_invalid_enum_and_literal_text_remain_validation_failures(name: str, value: str) -> None:
    environment = {
        "APP_MODE": "safe",
        "APP_CODE": "200",
        "APP_LEVEL": "high",
        "APP_RETRIES": "1",
        "APP_OPTIONAL": "null",
        "APP_LITERAL_NULL": "null",
        "APP_SCALAR_UNION": "1",
    }
    environment[name] = value
    with pytest.raises(ValidationError):
        Settings(ChoiceSettings, prefix="APP_").load(environment=environment)


class ContainerSettings(Spec):
    numbers: list[int]
    unique: set[str]
    frozen: frozenset[int]
    variable: tuple[int, ...]
    fixed: tuple[int, str]
    lookup: dict[str, int]
    decimals: list[Decimal]
    byte_values: list[bytes]
    float_values: list[float]
    modes: list[Mode]
    levels: list[Literal["low", "high"]]


def test_container_leaves_use_json_without_csv_grammar() -> None:
    value = Settings(ContainerSettings, prefix="APP_").load(
        environment={
            "APP_NUMBERS": "[1,2,3]",
            "APP_UNIQUE": '["a","b"]',
            "APP_FROZEN": "[1,2]",
            "APP_VARIABLE": "[4,5]",
            "APP_FIXED": '[6,"six"]',
            "APP_LOOKUP": '{"one":1,"two":2}',
            "APP_DECIMALS": '["1.25",2]',
            "APP_BYTE_VALUES": '["eA==","eQ=="]',
            "APP_FLOAT_VALUES": "[1,2.5]",
            "APP_MODES": '["safe","fast"]',
            "APP_LEVELS": '["low","high"]',
        }
    )
    assert value.numbers == [1, 2, 3]
    assert value.unique == {"a", "b"}
    assert value.frozen == frozenset({1, 2})
    assert value.variable == (4, 5)
    assert value.fixed == (6, "six")
    assert value.lookup == {"one": 1, "two": 2}
    assert value.decimals == [Decimal("1.25"), Decimal(2)]
    assert value.byte_values == [b"x", b"y"]
    assert value.float_values == [1.0, 2.5]
    assert value.modes == [Mode.SAFE, Mode.FAST]
    assert value.levels == ["low", "high"]


@pytest.mark.parametrize("text", ["1,2,3", '[1,"2"]', "{bad}"])
def test_malformed_or_weak_structured_text_rejects(text: str) -> None:
    class Lists(Spec):
        values: list[int]

    with pytest.raises(ValidationError):
        Settings(Lists, prefix="APP_").load(environment={"APP_VALUES": text})


@pytest.mark.parametrize(
    ("annotation", "text"),
    [(list[UUID], "[1]"), (list[Mode], '["unknown"]'), (list[Literal["low"]], '["high"]')],
)
def test_invalid_nested_textual_values_reach_normal_validation(annotation: object, text: str) -> None:
    from talea import create_spec

    Model = create_spec("NestedText", {"values": annotation}, module=__name__)
    with pytest.raises(ValidationError):
        Settings(Model, prefix="APP_").load(environment={"APP_VALUES": text})


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    port: int


class Credentials(TypedDict):
    user: str
    token: Annotated[str, Sensitive()]


class StructuralSettings(Spec):
    endpoint: Endpoint
    credentials: Credentials
    endpoints: list[Endpoint]
    credential_sets: list[Credentials]


def test_nested_dataclass_and_typed_dict_environment_names() -> None:
    value = Settings(StructuralSettings, prefix="APP_").load(
        environment={
            "APP_ENDPOINT__HOST": "service",
            "APP_ENDPOINT__PORT": "8443",
            "APP_CREDENTIALS__USER": "robot",
            "APP_CREDENTIALS__TOKEN": "secret",
            "APP_ENDPOINTS": '[{"host":"backup","port":9443}]',
            "APP_CREDENTIAL_SETS": '[{"user":"job","token":"batch"}]',
        }
    )
    assert value.endpoint == Endpoint("service", 8443)
    assert value.credentials == {"user": "robot", "token": "secret"}
    assert value.endpoints == [Endpoint("backup", 9443)]
    assert value.credential_sets == [{"user": "job", "token": "batch"}]


class Money:
    def __init__(self, cents: int) -> None:
        self.cents = cents


def test_representation_input_is_decoded_and_loader_runs_exactly_once() -> None:
    calls = 0

    def load(cents: int) -> Money:
        nonlocal calls
        calls += 1
        return Money(cents)

    class Billing(Spec):
        price: Annotated[Money, Representation(input=int, load=load)]

    value = Settings(Billing, prefix="APP_").load(environment={"APP_PRICE": "250"})
    assert value.price.cents == 250
    assert calls == 1


def test_output_only_representation_has_no_environment_text_conversion() -> None:
    class Identifier:
        pass

    class OutputOnly(Spec):
        identifier: Annotated[Identifier, Representation(output=str, dump=lambda _: "id")]

    with pytest.raises(TypeError, match="no input direction"):
        Settings(OutputOnly, prefix="APP_").load(environment={"APP_IDENTIFIER": "id"})


def test_union_shape_selection_uses_json_types_without_coercion_trials() -> None:
    calls = 0

    def load(value: int) -> Money:
        nonlocal calls
        calls += 1
        return Money(value)

    class UnionSettings(Spec):
        number_or_items: float | list[int]
        identifier_or_count: UUID | int
        endpoint_or_count: Endpoint | int
        items_or_count: list[int] | int
        mode_or_count: Mode | int
        level_or_count: Literal["low", "high"] | int
        money_or_text: Annotated[Money, Representation(input=int, load=load)] | str

    value = Settings(UnionSettings, prefix="APP_").load(
        environment={
            "APP_NUMBER_OR_ITEMS": "1.5",
            "APP_IDENTIFIER_OR_COUNT": f'"{UUID(int=2)}"',
            "APP_ENDPOINT_OR_COUNT": '{"host":"union","port":7443}',
            "APP_ITEMS_OR_COUNT": "[1,2]",
            "APP_MODE_OR_COUNT": '"safe"',
            "APP_LEVEL_OR_COUNT": '"high"',
            "APP_MONEY_OR_TEXT": "99",
        }
    )
    assert value.number_or_items == 1.5
    assert value.identifier_or_count == UUID(int=2)
    assert value.endpoint_or_count == Endpoint("union", 7443)
    assert value.items_or_count == [1, 2]
    assert value.mode_or_count is Mode.SAFE
    assert value.level_or_count == "high"
    assert isinstance(value.money_or_text, Money) and value.money_or_text.cents == 99
    assert calls == 1


def test_none_field_and_ambiguous_union_text_are_deterministic() -> None:
    class Nullable(Spec):
        absent: None
        ambiguous: Mode | Literal["safe"]

    # Both union options accept the same JSON string shape, so decoding does
    # not invent a branch priority; ordinary Talea union validation decides.
    value = Settings(Nullable, prefix="APP_").load(environment={"APP_ABSENT": "null", "APP_AMBIGUOUS": '"safe"'})
    assert value.absent is None
    assert value.ambiguous == "safe"


@pytest.mark.parametrize("text", ["NaN", '{"a":1,"a":2}'])
def test_non_finite_and_duplicate_structured_json_reject(text: str) -> None:
    class MappingSettings(Spec):
        values: dict[str, int]

    with pytest.raises(ValidationError):
        Settings(MappingSettings, prefix="APP_").load(environment={"APP_VALUES": text})


def test_constraints_remain_owned_by_normal_input_validation() -> None:
    class Limits(Spec):
        workers: Annotated[int, Ge(1)]

    with pytest.raises(ValidationError) as raised:
        Settings(Limits, prefix="APP_").load(environment={"APP_WORKERS": "0"})
    assert raised.value.code == "greater_than_or_equal"


def test_prefix_empty_and_case_policies() -> None:
    class Simple(Spec):
        value: int

    assert Settings(Simple).load(environment={"VALUE": "1"}).value == 1
    assert Settings(Simple, case_sensitive=True).load(environment={"value": "2"}).value == 2
    with pytest.raises(ValidationError):
        Settings(Simple, case_sensitive=True).load(environment={"VALUE": "2"})


def test_unicode_and_hostile_alias_names_are_inert_source_data() -> None:
    class Hostile(Spec):
        value: Annotated[int, Alias("λ\nvalue", legacy=("oldλ",))]

    plan = Settings(Hostile, prefix="APP_", case_sensitive=True)
    assert plan.load(environment={"APP_λ\nvalue": "1"}).value == 1
    assert plan.load(environment={"APP_oldλ": "2"}).value == 2


class Recursive(Spec):
    value: int
    child: "Recursive | None" = None


def test_recursive_projection_stops_at_back_edge_and_accepts_json_leaf() -> None:
    plan = Settings(Recursive, prefix="APP_")
    assert plan.info.environment_names == ("APP_value", "APP_child")
    value = plan.load(environment={"APP_VALUE": "1", "APP_CHILD": '{"value":2,"child":null}'})
    assert value.child is not None
    assert value.child.value == 2
    assert value.child.child is None


class RecursiveRecord(TypedDict):
    value: int
    child: "RecursiveRecord | None"


class RecursiveRecordSettings(Spec):
    record: RecursiveRecord


def test_recursive_typed_dict_projection_and_text_decoding_are_finite() -> None:
    plan = Settings(RecursiveRecordSettings, prefix="APP_")
    assert plan.info.environment_names == ("APP_record__value", "APP_record__child")
    value = plan.load(
        environment={
            "APP_RECORD__VALUE": "1",
            "APP_RECORD__CHILD": '{"value":2,"child":null}',
        }
    )
    assert value.record == {"value": 1, "child": {"value": 2, "child": None}}


class RightRecord(TypedDict):
    right: int
    left: "LeftRecord | None"


class LeftRecord(TypedDict):
    left: int
    right: RightRecord | None


class MutualRecordSettings(Spec):
    record: LeftRecord


def test_mutually_recursive_named_projection_stops_only_at_back_edge() -> None:
    names = Settings(MutualRecordSettings, prefix="APP_").info.environment_names
    assert names == ("APP_record__left", "APP_record__right__right", "APP_record__right__left")


class Created(Spec):
    kind: Literal["created"]
    value: int


class Deleted(Spec):
    kind: Literal["deleted"]
    reason: str


class Events(Spec):
    event: Annotated[Created | Deleted, Discriminator("kind")]


def test_tagged_union_is_a_json_textual_leaf() -> None:
    plan = Settings(Events, prefix="APP_")
    assert plan.info.environment_names == ("APP_event",)
    value = plan.load(environment={"APP_EVENT": '{"kind":"created","value":1}'})
    assert type(value.event) is Created
    assert value.event.value == 1
