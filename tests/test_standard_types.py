from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from pathlib import Path, PosixPath, PurePath, PurePosixPath, PureWindowsPath, WindowsPath
from uuid import UUID, uuid4

import pytest

from talea import Spec
from talea.schema import EnumSchema, TypeSchema, resolve_annotation
from talea.validation import ValidationError, compile_validator


class State(Enum):
    OPEN = "open"
    CLOSED = "closed"


class Code(IntEnum):
    OK = 200
    ERROR = 500


class Status(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@pytest.mark.parametrize(
    ("annotation", "value", "wrong"),
    [
        (State, State.OPEN, "open"),
        (Code, Code.OK, 200),
        (Status, Status.ACTIVE, "active"),
        (UUID, uuid4(), "d9428888-122b-11e1-b85c-61cd3cbb3210"),
        (date, date.min, datetime.min),
        (datetime, datetime.min, date.min),
        (time, time.min, "00:00:00"),
        (timedelta, timedelta(days=-999_999_999), 0),
        (Decimal, Decimal("NaN"), "NaN"),
        (PurePath, PureWindowsPath("C:/data"), "C:/data"),
        (Path, Path("."), PurePath(".")),
        (PurePosixPath, PurePosixPath("/data"), PureWindowsPath("C:/data")),
        (PureWindowsPath, PureWindowsPath("C:/data"), PurePosixPath("/data")),
        (IPv4Address, IPv4Address("127.0.0.1"), IPv6Address("::1")),
        (IPv6Address, IPv6Address("::1"), IPv4Address("127.0.0.1")),
        (IPv4Network, IPv4Network("10.0.0.0/24"), IPv4Interface("10.0.0.1/24")),
        (IPv6Network, IPv6Network("2001:db8::/64"), IPv6Interface("2001:db8::1/64")),
        (IPv4Interface, IPv4Interface("10.0.0.1/24"), IPv4Address("10.0.0.1")),
        (IPv6Interface, IPv6Interface("2001:db8::1/64"), IPv6Address("2001:db8::1")),
    ],
)
def test_standard_type_families_are_strict(annotation: object, value: object, wrong: object) -> None:
    validator = compile_validator(resolve_annotation(annotation))

    assert validator(value) is value
    with pytest.raises(ValidationError) as raised:
        validator(wrong)
    assert raised.value.code == "type"


def test_enum_schema_retains_members_and_rejects_wrong_enum_classes() -> None:
    schema = resolve_annotation(Status)

    assert isinstance(schema, EnumSchema)
    assert schema.enum_type is Status
    assert tuple(member.value for member in schema.members) == (Status.ACTIVE, Status.DISABLED)
    validator = compile_validator(schema)
    with pytest.raises(ValidationError):
        validator(State.OPEN)


def test_temporal_values_accept_meaningful_edges_without_timezone_policy() -> None:
    datetimes = compile_validator(resolve_annotation(datetime))
    times = compile_validator(resolve_annotation(time))
    durations = compile_validator(resolve_annotation(timedelta))

    aware = datetime.max.replace(tzinfo=timezone.utc)
    naive = datetime.min
    assert datetimes(aware) is aware
    assert datetimes(naive) is naive
    assert times(time(23, 59, 59, 999_999, tzinfo=timezone.utc)).tzinfo is timezone.utc
    for value in (timedelta.min, timedelta(0), timedelta.max):
        assert durations(value) is value


def test_exact_and_nominal_subclass_policies_are_deliberate() -> None:
    class DateSubclass(date):
        pass

    class DateTimeSubclass(datetime):
        pass

    class UUIDSubclass(UUID):
        pass

    class AddressSubclass(IPv4Address):
        pass

    date_child = DateSubclass(2024, 1, 1)
    datetime_child = DateTimeSubclass(2024, 1, 1)
    uuid_child = UUIDSubclass(int=0)
    address_child = AddressSubclass("127.0.0.1")

    with pytest.raises(ValidationError):
        compile_validator(resolve_annotation(date))(date_child)
    assert compile_validator(resolve_annotation(datetime))(datetime_child) is datetime_child
    assert compile_validator(resolve_annotation(UUID))(uuid_child) is uuid_child
    with pytest.raises(ValidationError):
        compile_validator(resolve_annotation(IPv4Address))(address_child)


def test_path_schema_is_portable_and_preserves_python_nominal_relationships() -> None:
    platform_path_type = type(Path("."))
    platform_path = Path(".")

    assert compile_validator(resolve_annotation(platform_path_type))(platform_path) is platform_path
    assert compile_validator(resolve_annotation(PurePath))(PurePosixPath("/tmp")) == PurePosixPath("/tmp")
    assert isinstance(resolve_annotation(PosixPath), TypeSchema)
    assert isinstance(resolve_annotation(WindowsPath), TypeSchema)
    with pytest.raises(ValidationError):
        compile_validator(resolve_annotation(WindowsPath))(PureWindowsPath("C:/tmp"))


def test_standard_types_compose_in_specs_containers_unions_and_optionals() -> None:
    class Payload(Spec):
        identifier: UUID
        created: datetime
        states: list[Status]
        address: IPv4Address | IPv6Address
        expires: timedelta | None

    identifier = uuid4()
    created = datetime.now(timezone.utc)
    payload = Payload(
        identifier=identifier,
        created=created,
        states=[Status.ACTIVE],
        address=IPv6Address("::1"),
        expires=None,
    )

    assert payload.identifier is identifier
    assert payload.created is created
    assert payload.states == [Status.ACTIVE]
    with pytest.raises(ValidationError) as raised:
        Payload(
            identifier=identifier,
            created=created,
            states=["active"],  # type: ignore[list-item]
            address=IPv4Address("127.0.0.1"),
            expires=timedelta(),
        )
    assert raised.value.location == ("states", 0)


def test_enum_union_uses_exact_top_level_selection() -> None:
    validator = compile_validator(resolve_annotation(Status | int))

    assert validator(Status.ACTIVE) is Status.ACTIVE
    assert validator(1) == 1
    with pytest.raises(ValidationError):
        validator("active")
