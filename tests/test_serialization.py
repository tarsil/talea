import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from inspect import signature
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Barrier, Lock
from typing import Annotated, Literal, cast
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

import talea.serialization.artifacts as output_module
from talea import Alias, Ge, SerializationError, Spec, serialize, transform
from talea.declaration import SpecField, SpecSchema
from talea.declaration.models import SerializationHook
from talea.input.emission import _BoundaryValidationEmitter
from talea.json.representations import decode_bytes, format_timedelta, parse_timedelta
from talea.schema import (
    LiteralSchema,
    LiteralValue,
    PrimitiveSchema,
    TypeSchema,
)
from talea.serialization.emission import _ValueProjectionCompiler, compile_value_projector
from talea.serialization.hooks import _SERIALIZER_MARKER
from talea.validation.emission import _GeneratedNames


class State(Enum):
    OPEN = "open"


class Code(IntEnum):
    OK = 200


class Status(StrEnum):
    ACTIVE = "active"


def test_to_dict_preserves_python_values_and_detaches_all_declared_structure() -> None:
    class Child(Spec):
        values: list[int]

    class Payload(Spec):
        child: Child
        children: list[Child]
        mapping: dict[str, list[int]]
        pair: tuple[Child, list[int]]
        unique: set[int]
        frozen: frozenset[int]

    child = Child(values=[1])
    payload = Payload(
        child=child,
        children=[child],
        mapping={"items": [2]},
        pair=(child, [3]),
        unique={4},
        frozen=frozenset({5}),
    )

    output = payload.to_dict()

    assert output == {
        "child": {"values": [1]},
        "children": [{"values": [1]}],
        "mapping": {"items": [2]},
        "pair": ({"values": [1]}, [3]),
        "unique": {4},
        "frozen": frozenset({5}),
    }
    assert type(output["pair"]) is tuple
    assert type(output["unique"]) is set
    assert type(output["frozen"]) is frozenset
    cast(dict[str, object], output["child"])["values"].append(9)  # type: ignore[union-attr]
    cast(list[dict[str, list[int]]], output["children"])[0]["values"].append(9)
    cast(dict[str, list[int]], output["mapping"])["items"].append(9)
    cast(tuple[dict[str, list[int]], list[int]], output["pair"])[1].append(9)
    assert child.values == [1]
    assert payload.mapping == {"items": [2]}
    assert payload.pair[1] == [3]


def test_python_projection_preserves_standard_library_enum_and_literal_values() -> None:
    class Payload(Spec):
        identifier: UUID
        timestamp: datetime
        day: date
        clock: time
        duration: timedelta
        amount: Decimal
        path: Path
        address: IPv4Address
        state: State
        literal: Literal[State.OPEN]
        raw: bytes

    values = {
        "identifier": UUID(int=0),
        "timestamp": datetime(2026, 8, 26, 12, 30, tzinfo=timezone.utc),
        "day": date(2026, 8, 26),
        "clock": time(12, 30),
        "duration": timedelta(days=-2, microseconds=7),
        "amount": Decimal("123.4500"),
        "path": Path("a/b"),
        "address": IPv4Address("127.0.0.1"),
        "state": State.OPEN,
        "literal": State.OPEN,
        "raw": b"payload",
    }
    payload = Payload(**values)

    output = payload.to_dict()

    assert output == values
    assert all(output[name] is value for name, value in values.items())


def test_json_projection_and_round_trip_cover_every_frozen_standard_representation() -> None:
    class Child(Spec):
        score: Annotated[int, Ge(0)]

    class Payload(Spec):
        identifier: UUID
        timestamp: datetime
        day: date
        clock: time
        duration: timedelta
        amount: Decimal
        path: PurePosixPath
        windows: PureWindowsPath
        address: IPv4Address
        network: IPv4Network
        interface: IPv4Interface
        state: State
        code: Code
        status: Status
        literal: Literal[State.OPEN]
        raw: bytes
        child: Child
        pair: tuple[int, str]
        unique: set[int]
        frozen: frozenset[str]
        mapping: dict[str, Child]

    original = Payload(
        identifier=UUID(int=0),
        timestamp=datetime(2026, 8, 26, 12, 30, 1, 2, tzinfo=timezone.utc),
        day=date(2026, 8, 26),
        clock=time(12, 30, 1, 2),
        duration=timedelta(days=-2, seconds=3, microseconds=4),
        amount=Decimal("1234567890.12345678901234567890"),
        path=PurePosixPath("/tmp/input"),
        windows=PureWindowsPath("C:/input"),
        address=IPv4Address("127.0.0.1"),
        network=IPv4Network("10.0.0.0/24"),
        interface=IPv4Interface("10.0.0.1/24"),
        state=State.OPEN,
        code=Code.OK,
        status=Status.ACTIVE,
        literal=State.OPEN,
        raw=b"\x00binary\xff",
        child=Child(score=1),
        pair=(1, "one"),
        unique={1, 2},
        frozen=frozenset({"a", "b"}),
        mapping={"nested": Child(score=2)},
    )

    encoded = original.to_json()
    tree = json.loads(encoded)
    restored = Payload.from_json(encoded)

    assert tree["amount"] == "1234567890.12345678901234567890"
    assert tree["duration"] == "-P1DT23H59M56.999996S"
    assert tree["raw"] == "AGJpbmFyef8="
    assert tree["pair"] == [1, "one"]
    assert set(tree["unique"]) == {1, 2}
    assert restored.identifier == original.identifier
    assert restored.timestamp == original.timestamp
    assert restored.day == original.day
    assert restored.clock == original.clock
    assert restored.duration == original.duration
    assert restored.amount == original.amount
    assert restored.path == original.path
    assert restored.windows == original.windows
    assert restored.address == original.address
    assert restored.network == original.network
    assert restored.interface == original.interface
    assert (restored.state, restored.code, restored.status, restored.literal) == (
        State.OPEN,
        Code.OK,
        Status.ACTIVE,
        State.OPEN,
    )
    assert restored.raw == original.raw
    assert restored.child.score == 1
    assert restored.pair == original.pair
    assert restored.unique == original.unique
    assert restored.frozen == original.frozen
    assert restored.mapping["nested"].score == 2


@given(
    text=st.text(max_size=80),
    integer=st.integers(min_value=-(2**63), max_value=2**63 - 1),
    decimal=st.decimals(allow_nan=False, allow_infinity=False, places=8),
    values=st.lists(st.integers(min_value=-100, max_value=100), max_size=20),
)
def test_json_round_trip_property_for_strings_numbers_decimal_and_containers(
    text: str,
    integer: int,
    decimal: Decimal,
    values: list[int],
) -> None:
    class Payload(Spec):
        text: str
        integer: int
        decimal: Decimal
        values: list[int]

    restored = Payload.from_json(Payload(text=text, integer=integer, decimal=decimal, values=values).to_json())

    assert (restored.text, restored.integer, restored.decimal, restored.values) == (
        text,
        integer,
        decimal,
        values,
    )


def test_custom_dumps_receives_only_json_native_talea_semantics_and_returns_text() -> None:
    seen: list[object] = []

    class Payload(Spec):
        amount: Decimal
        identifier: UUID

    payload = Payload(amount=Decimal("1.20"), identifier=UUID(int=0))

    def dumps(value: object) -> bytes:
        seen.append(value)
        return json.dumps(value, separators=(",", ":")).encode()

    encoded = payload.to_json(dumps=dumps)

    assert encoded == '{"amount":"1.20","identifier":"00000000-0000-0000-0000-000000000000"}'
    assert seen == [{"amount": "1.20", "identifier": "00000000-0000-0000-0000-000000000000"}]
    assert isinstance(encoded, str)
    assert payload.to_json(dumps=lambda value: bytearray(b"{}")) == "{}"


def test_custom_dumps_failure_and_return_type_contracts_are_separate_from_validation() -> None:
    class Payload(Spec):
        value: int

    payload = Payload(value=1)
    with pytest.raises(SerializationError, match="must return"):
        payload.to_json(dumps=lambda value: cast(str, 1))
    with pytest.raises(SerializationError, match="non-UTF-8"):
        payload.to_json(dumps=lambda value: b"\xff")
    with pytest.raises(SerializationError) as rejected:
        payload.to_json(dumps=lambda value: (_ for _ in ()).throw(ValueError("codec rejected")))
    assert isinstance(rejected.value.__cause__, ValueError)
    failure = RuntimeError("codec defect")
    with pytest.raises(RuntimeError) as propagated:
        payload.to_json(dumps=lambda value: (_ for _ in ()).throw(failure))
    assert propagated.value is failure
    with pytest.raises(TypeError):
        payload.to_json(dumps=lambda value: (_ for _ in ()).throw(TypeError("codec defect")))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_json_rejects_non_finite_float_output_before_codec(value: float) -> None:
    called = False

    class Payload(Spec):
        value: float

    def dumps(tree: object) -> str:
        nonlocal called
        called = True
        return "{}"

    with pytest.raises(SerializationError, match="non-finite") as raised:
        Payload(value=value).to_json(dumps=dumps)
    assert raised.value.location == ("value",)
    assert not called


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_json_rejects_non_finite_decimal_output(value: Decimal) -> None:
    class Payload(Spec):
        value: Decimal

    assert Payload(value=value).to_dict() == {"value": value}
    with pytest.raises(SerializationError, match="non-finite"):
        Payload(value=value).to_json()


def test_alias_is_one_canonical_external_truth_for_input_and_output() -> None:
    class User(Spec):
        first_name: Annotated[str, Alias("firstName")]
        age: int

    schema = vars(User)["__talea_artifacts__"].schema
    user = User(first_name="Ada", age=37)

    assert schema.fields[0].alias == "firstName"
    assert schema.fields[0].external_name == "firstName"
    assert user.to_dict() == {"firstName": "Ada", "age": 37}
    assert user.to_dict(by_alias=False) == {"first_name": "Ada", "age": 37}
    assert user.to_json(by_alias=False) == '{"first_name":"Ada","age":37}'
    assert User.from_mapping(user.to_dict()).first_name == "Ada"
    assert User.from_json(user.to_json()).first_name == "Ada"
    with pytest.raises(Exception) as canonical_input:
        User.from_mapping({"first_name": "Ada", "age": 37})
    assert [item["code"] for item in canonical_input.value.errors()] == ["missing", "unexpected"]


def test_alias_declaration_rejects_empty_duplicate_and_canonical_collisions() -> None:
    with pytest.raises(TypeError, match="non-empty"):
        Alias("")
    with pytest.raises(TypeError, match="only one Alias"):

        class DuplicateMetadata(Spec):
            value: Annotated[int, Alias("one"), Alias("two")]

    with pytest.raises(ValueError, match="unique external"):

        class DuplicateExternal(Spec):
            first: Annotated[int, Alias("same")]
            second: Annotated[int, Alias("same")]

    with pytest.raises(ValueError, match="canonical field"):

        class CanonicalCollision(Spec):
            first: Annotated[int, Alias("second")]
            second: int


def test_float_enum_and_bytes_union_json_projection_cover_specialized_alternatives() -> None:
    class Ratio(Enum):
        HALF = 0.5

    class Unsupported(Enum):
        VALUE = object()

    class Payload(Spec):
        ratio: Ratio
        value: bytes | int
        number: float

    encoded = Payload(ratio=Ratio.HALF, value=b"ok", number=1.25).to_json()
    restored = Payload.from_json(encoded)
    assert (restored.ratio, restored.value, restored.number) == (Ratio.HALF, b"ok", 1.25)

    class Broken(Spec):
        value: Unsupported

    with pytest.raises(SerializationError, match="Enum member"):
        Broken(value=Unsupported.VALUE).to_json()


def test_include_exclude_and_exclude_none_are_top_level_canonical_field_policies() -> None:
    class Payload(Spec):
        first: Annotated[int, Alias("one")]
        second: int | None
        third: int

    payload = Payload(first=1, second=None, third=3)

    assert payload.to_dict(include={"first", "second"}) == {"one": 1, "second": None}
    assert payload.to_dict(include={"first", "third"}, exclude={"third"}) == {"one": 1}
    assert payload.to_dict(exclude_none=True) == {"one": 1, "third": 3}
    assert payload.to_json(include={"first"}) == '{"one":1}'
    with pytest.raises(TypeError, match="must be a set"):
        payload.to_dict(include=["first"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact strings"):
        payload.to_dict(include=cast(set[str], {1}))
    with pytest.raises(ValueError, match="unknown field"):
        payload.to_dict(exclude={"missing"})
    with pytest.raises(TypeError, match="must be bool"):
        payload.to_json(by_alias=cast(bool, 1))
    with pytest.raises(TypeError, match="must be bool"):
        payload.to_dict(exclude_none=cast(bool, 1))
    with pytest.raises(TypeError, match="must be bool"):
        payload.to_dict(by_alias=cast(bool, 1))
    with pytest.raises(TypeError, match="must be bool"):
        payload.to_json(include={"first"}, exclude_none=cast(bool, 1))


def test_serialization_hook_replaces_projection_once_and_copies_returned_containers() -> None:
    calls: list[bytes] = []

    class Payload(Spec):
        token: bytes
        values: list[int]

        @serialize("token")
        def token_output(token: bytes) -> str:
            calls.append(token)
            return token.hex()

        @serialize("values")
        def values_output(values: list[int]) -> list[int]:
            return values

    payload = Payload(token=b"\x00\xff", values=[1])
    python_output = payload.to_dict()
    json_output = payload.to_json()

    assert python_output == {"token": "00ff", "values": [1]}
    assert json_output == '{"token":"00ff","values":[1]}'
    assert calls == [b"\x00\xff", b"\x00\xff"]
    cast(list[int], python_output["values"]).append(2)
    assert payload.values == [1]
    assert payload.to_dict(include={"token"}) == {"token": "00ff"}


def test_hook_replacement_projects_nested_python_and_every_supported_json_value() -> None:
    class Child(Spec):
        value: int

    child = Child(value=1)

    class Payload(Spec):
        value: int

        @serialize("value")
        def output(value: int) -> object:
            return {
                "child": child,
                "none": None,
                "float": 1.5,
                "bytes": b"ok",
                "decimal": Decimal("1.20"),
                "duration": timedelta(microseconds=1),
                "datetime": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "date": date(2026, 1, 1),
                "time": time(1, 2),
                "uuid": UUID(int=0),
                "path": PurePosixPath("/tmp"),
                "ip": IPv6Address("::1"),
                "enum": State.OPEN,
                "containers": ([1], (2,), {3}, frozenset({4})),
            }

    python_output = cast(dict[str, object], Payload(value=1).to_dict()["value"])
    json_output = json.loads(Payload(value=1).to_json())["value"]

    assert python_output["child"] == {"value": 1}
    assert cast(dict[str, list[int]], python_output)["containers"] is not None
    assert json_output["child"] == {"value": 1}
    assert json_output["bytes"] == "b2s="
    assert json_output["decimal"] == "1.20"
    assert json_output["duration"] == "PT0.000001S"
    assert json_output["enum"] == "open"


def test_serialization_hook_inheritance_override_addition_and_shadowing_follow_python() -> None:
    class Parent(Spec):
        first: int
        second: int

        @serialize("first")
        def project_first(first: int) -> str:
            return f"parent:{first}"

    class Child(Parent):
        @serialize("first")
        def project_first(first: int) -> str:
            return f"child:{first}"

        @serialize("second")
        def project_second(second: int) -> str:
            return f"second:{second}"

    class Shadow(Parent):
        def project_first(first: int) -> str:
            return f"ordinary:{first}"

    assert Parent(first=1, second=2).to_dict() == {"first": "parent:1", "second": 2}
    assert Child(first=1, second=2).to_dict() == {"first": "child:1", "second": "second:2"}
    assert Shadow(first=1, second=2).to_dict() == {"first": 1, "second": 2}


def test_serialization_hook_failures_preserve_cause_and_reject_unsupported_json_output() -> None:
    failure = RuntimeError("hook failed")

    class Broken(Spec):
        value: int

        @serialize("value")
        def broken(value: int) -> object:
            raise failure

    with pytest.raises(SerializationError) as raised:
        Broken(value=1).to_dict()
    assert raised.value.location == ("value",)
    assert raised.value.__cause__ is failure

    class Unsupported(Spec):
        value: int

        @serialize("value")
        def unsupported(value: int) -> object:
            return object()

    assert type(Unsupported(value=1).to_dict()["value"]) is object
    with pytest.raises(SerializationError, match="unsupported JSON"):
        Unsupported(value=1).to_json()

    class Nested(Spec):
        value: int

        @serialize("value")
        def fail(value: int) -> int:
            raise ValueError("nested")

    class Parent(Spec):
        nested: Nested

    with pytest.raises(SerializationError) as nested:
        Parent(nested=Nested(value=1)).to_dict()
    assert nested.value.location == ("nested", "value")
    assert "nested" in str(nested.value)


def test_serialization_hook_declaration_rejects_ambiguous_or_async_lifecycles() -> None:
    with pytest.raises(TypeError, match="non-empty"):
        serialize("")
    with pytest.raises(TypeError, match="plain function"):
        serialize("value")(cast(object, object()))  # type: ignore[arg-type]

    def already_marked(value: int) -> int:
        return value

    serialize("value")(already_marked)
    with pytest.raises(TypeError, match="only one"):
        serialize("value")(already_marked)

    with pytest.raises(TypeError, match="unknown field"):

        class Unknown(Spec):
            value: int

            @serialize("missing")
            def output(value: int) -> int:
                return value

    with pytest.raises(ValueError, match="only one serialization hook"):

        class Duplicate(Spec):
            value: int

            @serialize("value")
            def first(value: int) -> int:
                return value

            @serialize("value")
            def second(value: int) -> int:
                return value

    with pytest.raises(TypeError, match="must be synchronous"):

        class Async(Spec):
            value: int

            @serialize("value")
            async def output(value: int) -> int:
                return value

    with pytest.raises(TypeError, match="cannot be a generator"):

        class Generator(Spec):
            value: int

            @serialize("value")
            def output(value: int):
                yield value

    with pytest.raises(TypeError, match="exactly one positional"):

        class Signature(Spec):
            value: int

            @serialize("value")
            def output(value: int, extra: int) -> int:
                return value + extra

    with pytest.raises(TypeError, match="both a validation and serialization"):

        class Both(Spec):
            value: int

            @serialize("value")
            @transform("value")
            def output(value: int) -> int:
                return value

    with pytest.raises(TypeError, match="cannot combine"):

        class Descriptor(Spec):
            value: int

            @staticmethod
            @serialize("value")
            def output(value: int) -> int:
                return value

    with pytest.raises(TypeError, match="conflicts with Spec field"):

        class FieldConflict(Spec):
            value: int

            @serialize("value")
            def value(value: int) -> int:
                return value

    def malformed(value: int) -> int:
        return value

    setattr(malformed, _SERIALIZER_MARKER, object())
    with pytest.raises(TypeError, match="metadata requires"):

        class Malformed(Spec):
            value: int
            output = malformed


def test_lazy_serializers_compile_independently_once_under_concurrent_first_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Payload(Spec):
        value: int

    artifacts = vars(Payload)["__talea_artifacts__"]
    calls: list[tuple[str, bool, bool]] = []
    lock = Lock()
    original = output_module.compile_serialization
    original_plain = output_module.compile_plain_to_dict

    def counted(
        schema: SpecSchema,
        mode: str,
        by_alias: bool,
        filtered: bool,
    ) -> object:
        with lock:
            calls.append((mode, by_alias, filtered))
        return original(schema, mode, by_alias, filtered)  # type: ignore[arg-type]

    def counted_plain(schema: SpecSchema, fallback: object) -> object:
        with lock:
            calls.append(("python", True, False))
        return original_plain(schema, fallback)  # type: ignore[arg-type]

    monkeypatch.setattr(output_module, "compile_serialization", counted)
    monkeypatch.setattr(output_module, "compile_plain_to_dict", counted_plain)
    workers = 8
    barrier = Barrier(workers)

    def project(index: int) -> dict[str, object]:
        barrier.wait()
        return Payload(value=index).to_dict()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        assert sorted(item["value"] for item in executor.map(project, range(workers))) == list(range(workers))

    assert calls == [("python", True, False)]
    assert artifacts.outputs.python_alias is not None
    assert artifacts.outputs.json_alias is None
    assert Payload(value=9).to_json() == '{"value":9}'
    assert calls == [("python", True, False), ("json", True, False)]


def test_plain_serializer_publication_preserves_options_subclasses_and_overrides() -> None:
    class Parent(Spec):
        first: int

    fallback = vars(Parent)["to_dict"]
    parent = Parent(first=1)
    assert parent.to_dict() == {"first": 1}

    installed = vars(Parent)["to_dict"]
    artifacts = vars(Parent)["__talea_artifacts__"]
    assert installed is artifacts.outputs.python_alias
    assert installed is not fallback
    assert signature(installed) == signature(fallback)
    assert parent.to_dict(include={"first"}) == {"first": 1}
    assert parent.to_dict(by_alias=True) == {"first": 1}

    class Child(Parent):
        second: int

    assert vars(Child)["to_dict"] is fallback
    assert Child(first=1, second=2).to_dict() == {"first": 1, "second": 2}

    class Custom(Spec):
        value: int

        def to_dict(self) -> dict[str, object]:
            return {"custom": self.value}

    assert Custom(value=3).to_dict() == {"custom": 3}


def test_mapping_key_policies_reject_non_roundtrippable_projection_without_aliasing() -> None:
    class Key(Spec):
        values: list[int]

    class PythonMapping(Spec):
        mapping: dict[Key, int]

    key = Key(values=[1])
    with pytest.raises(SerializationError, match="hashability"):
        PythonMapping(mapping={key: 1}).to_dict()
    assert key.values == [1]

    class JsonMapping(Spec):
        mapping: dict[int, str]

    with pytest.raises(SerializationError, match="exact strings"):
        JsonMapping(mapping={1: "one"}).to_json()


def test_hashable_python_mapping_keys_use_compiled_structural_projection() -> None:
    class Payload(Spec):
        constrained: dict[Annotated[int, Ge(0)], str]
        frozen: dict[frozenset[int], str]
        variadic: dict[tuple[int, ...], str]
        fixed: dict[tuple[int, str], str]
        union: dict[int | str, str]

    payload = Payload(
        constrained={1: "one"},
        frozen={frozenset({2}): "two"},
        variadic={(3, 4): "three"},
        fixed={(5, "five"): "five"},
        union={"six": "six"},
    )
    assert payload.to_dict() == {
        "constrained": {1: "one"},
        "frozen": {frozenset({2}): "two"},
        "variadic": {(3, 4): "three"},
        "fixed": {(5, "five"): "five"},
        "union": {"six": "six"},
    }


def test_mutated_union_current_state_fails_in_compiled_branch_selection() -> None:
    class Payload(Spec):
        values: list[int] | tuple[str, ...]

    payload = Payload(values=[1])
    cast(list[object], payload.values).append("bad")
    with pytest.raises(SerializationError, match="union alternative"):
        payload.to_dict()


def test_empty_spec_unusual_unicode_and_literal_bytes_are_strict_json() -> None:
    class Empty(Spec):
        pass

    class Payload(Spec):
        text: str
        raw: Literal[b"ok"]

    text = "snowman ☃ control\u0000 surrogate-like text"
    payload = Payload(text=text, raw=b"ok")

    assert Empty().to_dict() == {}
    assert Empty().to_json() == "{}"
    assert Payload.from_json(payload.to_json()).text == text
    assert Payload.from_json(payload.to_json()).raw == b"ok"


@pytest.mark.parametrize(
    "value",
    [
        timedelta(0),
        timedelta(microseconds=1),
        timedelta(microseconds=-1),
        timedelta.min,
        timedelta.max,
    ],
)
def test_timedelta_representation_is_exact_at_all_boundaries(value: timedelta) -> None:
    encoded = format_timedelta(value)
    assert parse_timedelta(encoded) == value


def test_invalid_shared_scalar_representations_remain_field_validation_failures() -> None:
    class Payload(Spec):
        raw: bytes
        duration: timedelta
        amount: Decimal

    with pytest.raises(Exception) as raised:
        Payload.from_json('{"raw":"***","duration":"tomorrow","amount":"NaN"}')
    assert [item["location"] for item in raised.value.errors()] == [["raw"], ["duration"], ["amount"]]
    assert decode_bytes("YQ==") == b"a"
    with pytest.raises(ValueError):
        decode_bytes("YQ")
    with pytest.raises(ValueError):
        parse_timedelta("P")


def test_declaration_models_cover_alias_and_serializer_invariants_directly() -> None:
    field = SpecField("value", PrimitiveSchema("int"), alias="external")
    assert field.external_name == "external"
    with pytest.raises(TypeError, match="non-empty"):
        SpecField("value", PrimitiveSchema("int"), alias="")
    with pytest.raises(ValueError, match="unique external"):
        SpecSchema(
            (
                SpecField("one", PrimitiveSchema("int"), alias="same"),
                SpecField("two", PrimitiveSchema("int"), alias="same"),
            )
        )
    first = SerializationHook("same", "value", lambda value: value)
    second = SerializationHook("same", "other", lambda value: value)
    with pytest.raises(ValueError, match="unique serializer names"):
        SpecSchema(
            (SpecField("value", PrimitiveSchema("int")), SpecField("other", PrimitiveSchema("int"))),
            serializers=(first, second),
        )


def test_internal_projection_fallbacks_remain_explicit_and_specialized() -> None:
    location = ("value",)
    assert compile_value_projector(TypeSchema(int, "exact"), "json", True)(1, location) == 1
    primitive_literal = LiteralSchema(frozenset({LiteralValue(str, "ok")}))
    assert compile_value_projector(primitive_literal, "json", True)("ok", location) == "ok"
    literal = LiteralSchema(frozenset({LiteralValue(float, 1.5)}))
    with pytest.raises(SerializationError, match="Literal value"):
        compile_value_projector(literal, "json", True)(1.5, location)

    lines: list[str] = []
    emitter = _BoundaryValidationEmitter(lines, _GeneratedNames(), {}, mode="json")
    emitter.emit_json_type_conversion(TypeSchema(int, "exact"), "value", (), 0)
    assert "standard_type" in emitter.boundary_condition(TypeSchema(int, "exact"), "value")


def test_serializer_shadowing_across_mro_owners_is_deterministic() -> None:
    class Parent(Spec):
        value: int

        @serialize("value")
        def output(value: int) -> str:
            return str(value)

    class Mixin:
        __slots__ = ()

        def output(self) -> None:
            pass

    class Mixed(Mixin, Parent):
        pass

    class Shadow(Parent):
        def output(value: int) -> int:
            return value

    class Combined(Shadow, Parent):
        pass

    assert Mixed(value=1).to_dict() == {"value": 1}
    assert Combined(value=1).to_dict() == {"value": 1}


def test_impossible_projection_schema_reaches_assert_never_guard() -> None:
    compiler = _ValueProjectionCompiler("json", True)
    with pytest.raises(AssertionError):
        compiler.compile(cast(object, object()))  # type: ignore[arg-type]
