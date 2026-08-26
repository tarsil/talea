import dis
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Lock
from types import MappingProxyType
from typing import Annotated, cast
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

import talea
import talea.input
import talea.spec.lifecycle as spec_module
from talea import Ge, Spec, ValidationError, check, field, transform


class CustomMapping(Mapping[str, object]):
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def __getitem__(self, key: str) -> object:
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


def test_input_compiler_internals_are_not_public_api() -> None:
    assert talea.input.__all__ == ()
    assert not hasattr(talea.input, "compile_input")
    assert not hasattr(talea.input, "decode_json")


def test_from_mapping_accepts_mapping_implementations_and_returns_the_invoked_type() -> None:
    class User(Spec):
        identifier: int
        name: str

    class Employee(User):
        employee_id: int

    cases: tuple[Mapping[str, object], ...] = (
        {"identifier": 1, "name": "Ada"},
        MappingProxyType({"identifier": 1, "name": "Ada"}),
        CustomMapping({"identifier": 1, "name": "Ada"}),
    )

    for data in cases:
        user = User.from_mapping(data)
        assert type(user) is User
        assert (user.identifier, user.name) == (1, "Ada")
    employee = Employee.from_mapping({"identifier": 2, "name": "Grace", "employee_id": 3})
    assert type(employee) is Employee


def test_mapping_boundary_rejects_wrong_roots_non_string_keys_and_unexpected_fields() -> None:
    class User(Spec):
        identifier: int

    with pytest.raises(ValidationError) as wrong_root:
        User.from_mapping([("identifier", 1)])  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as wrong_keys:
        User.from_mapping(cast(Mapping[str, object], {"identifier": 1, 2: "two", "extra": True}))

    assert wrong_root.value.code == "type"
    assert wrong_root.value.location == ()
    assert [(error["code"], error["location"]) for error in wrong_keys.value.errors()] == [
        ("unexpected", [2]),
        ("unexpected", ["extra"]),
    ]


def test_mapping_boundary_is_strict_for_primitives_containers_and_standard_types() -> None:
    class Payload(Spec):
        count: int
        pair: tuple[int, ...]
        identifier: UUID
        day: date

    values = {
        "count": "1",
        "pair": [1, 2],
        "identifier": "00000000-0000-0000-0000-000000000000",
        "day": "2026-08-26",
    }
    with pytest.raises(ValidationError) as raised:
        Payload.from_mapping(values)

    assert [error["location"] for error in raised.value.errors()] == [
        ["count"],
        ["pair"],
        ["identifier"],
        ["day"],
    ]
    assert all(error["code"] == "type" for error in raised.value.errors())


def test_nested_mappings_construct_specs_compose_locations_and_preserve_existing_identity() -> None:
    calls = 0

    class Address(Spec):
        city: str
        numbers: list[int]

        @check("numbers")
        def nonempty(numbers: list[int]) -> None:
            nonlocal calls
            calls += 1
            if not numbers:
                raise ValueError("empty")

    class User(Spec):
        address: Address
        history: list[Address | None]
        lookup: dict[str, Address]

    address_artifacts = vars(Address)["__talea_artifacts__"]
    user_artifacts = vars(User)["__talea_artifacts__"]
    assert address_artifacts.inputs.mapping_input is None
    assert user_artifacts.inputs.mapping_input is None

    user = User.from_mapping(
        {
            "address": {"city": "Zurich", "numbers": [1]},
            "history": [{"city": "Geneva", "numbers": [2]}, None],
            "lookup": {"home": {"city": "Bern", "numbers": [3]}},
        }
    )

    assert type(user.address) is Address
    assert type(user.history[0]) is Address
    assert type(user.lookup["home"]) is Address
    assert calls == 3
    assert address_artifacts.inputs.mapping_input is not None
    assert user_artifacts.inputs.mapping_input is not None
    assert address_artifacts.inputs.json_input is None
    assert user_artifacts.inputs.json_input is None

    existing = Address(city="Basel", numbers=[4])
    reused = User.from_mapping({"address": existing, "history": [], "lookup": {}})
    assert reused.address is existing
    assert calls == 5
    existing.numbers.clear()
    with pytest.raises(ValidationError) as invalid_existing:
        User.from_mapping({"address": existing, "history": [], "lookup": {}})
    assert invalid_existing.value.location == ("address", "numbers")

    with pytest.raises(ValidationError) as nested:
        User.from_mapping(
            {
                "address": {"city": 1, "numbers": []},
                "history": [],
                "lookup": {},
            }
        )
    assert [(error["code"], error["location"]) for error in nested.value.errors()] == [
        ("type", ["address", "city"]),
        ("field_check", ["address", "numbers"]),
    ]


def test_mapping_aggregates_declared_fields_then_unexpected_keys_in_encounter_order() -> None:
    class User(Spec):
        identifier: int
        name: str
        age: Annotated[int, Ge(18)]

    with pytest.raises(ValidationError) as raised:
        User.from_mapping(
            CustomMapping(
                {
                    "identifier": "bad",
                    "age": 15,
                    "z_extra": True,
                    "a_extra": False,
                }
            )
        )

    assert [(error["code"], error["location"]) for error in raised.value.errors()] == [
        ("type", ["identifier"]),
        ("missing", ["name"]),
        ("greater_than_or_equal", ["age"]),
        ("unexpected", ["z_extra"]),
        ("unexpected", ["a_extra"]),
    ]
    assert str(raised.value).startswith("User (5 errors)\n")


def test_factories_wait_for_external_field_and_key_success_then_run_once() -> None:
    calls: list[str] = []

    def first() -> list[int]:
        calls.append("first")
        return []

    def second() -> int:
        calls.append("second")
        return 2

    class Payload(Spec):
        required: int
        static: str = "default"
        items: list[int] = field(default_factory=first)
        generated: int = field(default_factory=second)

    for invalid in ({}, {"required": "bad"}, {"required": 1, "extra": True}):
        with pytest.raises(ValidationError):
            Payload.from_mapping(invalid)
    assert calls == []

    payload = Payload.from_mapping({"required": 1})
    assert (payload.static, payload.items, payload.generated) == ("default", [], 2)
    assert calls == ["first", "second"]


def test_factory_failures_and_invalid_outputs_use_normal_boundary_errors() -> None:
    failure = RuntimeError("unavailable")

    def fail() -> int:
        raise failure

    class Payload(Spec):
        failed: int = field(default_factory=fail)
        invalid: int = field(default_factory=lambda: "1")  # type: ignore[arg-type]

    with pytest.raises(ValidationError) as raised:
        Payload.from_mapping({})

    assert [error["code"] for error in raised.value.errors()] == ["factory", "type"]
    assert raised.value.__cause__ is None


def test_transforms_and_checks_run_once_and_whole_spec_checks_require_valid_fields() -> None:
    events: list[str] = []

    class Interval(Spec):
        start: int
        end: int

        @transform("start")
        def parse(start: object) -> object:
            events.append(f"transform:{start}")
            return int(start) if isinstance(start, str) else start

        @check("start")
        def positive(start: int) -> None:
            events.append(f"field:{start}")

        @check("start", "end")
        def ordered(start: int, end: int) -> None:
            events.append(f"spec:{start}:{end}")
            if end < start:
                raise ValueError("unordered")

    assert Interval.from_mapping({"start": "1", "end": 2}).start == 1
    assert events == ["transform:1", "field:1", "spec:1:2"]
    events.clear()
    with pytest.raises(ValidationError):
        Interval.from_mapping({"start": "bad", "end": "bad"})
    assert events == ["transform:bad"]

    source: dict[str, object] = {"value": 1}
    mutation_calls = 0

    class MutatingTransform(Spec):
        value: int

        @transform("value")
        def mutate(value: object) -> object:
            nonlocal mutation_calls
            mutation_calls += 1
            source["extra"] = True
            return value

    with pytest.raises(ValidationError) as mutated:
        MutatingTransform.from_mapping(source)
    assert mutated.value.location == ("extra",)
    assert mutation_calls == 1


def test_unexpected_mapping_implementation_exceptions_propagate() -> None:
    class ExplodingMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise RuntimeError("mapping failed")

        def __iter__(self) -> Iterator[str]:
            return iter(("value",))

        def __len__(self) -> int:
            return 1

    class Payload(Spec):
        value: int

    with pytest.raises(RuntimeError, match="mapping failed"):
        Payload.from_mapping(ExplodingMapping())

    class InconsistentMapping(CustomMapping):
        def __len__(self) -> int:
            return 1

    with pytest.raises(ValidationError) as inconsistent:
        Payload.from_mapping(InconsistentMapping({"value": 1, "extra": True}))
    assert inconsistent.value.location == ("extra",)


def test_huge_mapping_is_bounded_to_individual_error_inputs() -> None:
    class Payload(Spec):
        value: int

    data: dict[str, object] = {"value": 1}
    data.update({f"extra_{index}": True for index in range(2_000)})

    with pytest.raises(ValidationError) as raised:
        Payload.from_mapping(data)

    errors = raised.value.errors()
    assert len(errors) == 2_000
    assert errors[0]["location"] == ["extra_0"]
    assert errors[-1]["location"] == ["extra_1999"]
    assert all(error["input"] is True for error in errors)


@given(
    required=st.booleans(),
    unexpected=st.lists(
        st.text(min_size=1, max_size=8).filter(lambda value: value != "value"), max_size=4, unique=True
    ),
)
def test_generated_mapping_keysets_have_stable_missing_and_unexpected_semantics(
    required: bool,
    unexpected: list[str],
) -> None:
    class Payload(Spec):
        value: int

    data: dict[str, object] = dict.fromkeys(unexpected, True)
    if required:
        data["value"] = 1
    if required and not unexpected:
        assert Payload.from_mapping(data).value == 1
        return
    with pytest.raises(ValidationError) as raised:
        Payload.from_mapping(data)
    codes = [error["code"] for error in raised.value.errors()]
    assert codes == ([] if required else ["missing"]) + ["unexpected"] * len(unexpected)


def test_boundary_compilation_does_not_enter_the_normal_constructor_or_instances() -> None:
    class Point(Spec):
        x: int
        y: int

    initializer = vars(Point)["__init__"]
    names = set(initializer.__code__.co_names)
    operations = {instruction.opname for instruction in dis.get_instructions(initializer)}
    point = Point(x=1, y=2)

    assert names.isdisjoint({"Mapping", "from_mapping", "from_json", "loads", "errors", "unexpected"})
    assert "FOR_ITER" not in operations
    assert not hasattr(point, "__dict__")
    assert Point.from_mapping.__func__ is Spec.from_mapping.__func__
    assert not hasattr(talea, "compile_input")

    boundary_names = set(Spec.from_mapping.__func__.__code__.co_names)
    assert boundary_names.isdisjoint({"vars", "cast"})


def test_boundary_compilation_is_lazy_independent_and_thread_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Payload(Spec):
        value: int

    artifacts = vars(Payload)["__talea_artifacts__"]
    assert artifacts.inputs.mapping_input is None
    assert artifacts.inputs.json_input is None

    compile_calls: list[str] = []
    calls_lock = Lock()
    original_compile = spec_module.compile_input

    def counted_compile(*args: object, **kwargs: object) -> object:
        with calls_lock:
            compile_calls.append(args[3])  # type: ignore[arg-type]
        return original_compile(*args, **kwargs)  # type: ignore[invalid-argument-type]

    monkeypatch.setattr(spec_module, "compile_input", counted_compile)
    workers = 8
    barrier = Barrier(workers)

    def construct(index: int) -> int:
        barrier.wait()
        return Payload.from_mapping({"value": index}).value

    with ThreadPoolExecutor(max_workers=workers) as executor:
        assert sorted(executor.map(construct, range(workers))) == list(range(workers))

    assert compile_calls == ["mapping"]
    assert artifacts.inputs.mapping_input is not None
    assert artifacts.inputs.json_input is None
    assert Payload.from_json('{"value":8}').value == 8
    assert compile_calls == ["mapping", "json"]


def test_spec_rejects_a_custom_new_constructor() -> None:
    with pytest.raises(TypeError, match="manages construction"):

        class CustomNew(Spec):
            value: int

            def __new__(cls, **values: object) -> "CustomNew":
                return super().__new__(cls)
