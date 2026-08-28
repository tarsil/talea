from __future__ import annotations

import dis
import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, dataclass, make_dataclass
from functools import partial
from typing import Annotated, Literal, NewType, NotRequired, TypedDict

import pytest
from hypothesis import given, strategies as st

import talea
from talea import (
    Alias,
    Contract,
    Discriminator,
    Pattern,
    Representation,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
    ValidationError,
    check,
    create_spec,
    derive_spec,
    serialize,
    transform,
)
from talea.declaration.policies import (
    schema_contains_representation,
    schema_contains_tagged_union,
    schema_input_directions_are_available,
    schema_output_directions_are_available,
)
from talea.introspection import RepresentationInfo, inspect_contract, inspect_spec
from talea.json_schema import SchemaProjectionError
from talea.schema import AliasSchema
from talea.schema.nodes import (
    DataclassSchema,
    NamedReferenceSchema,
    RepresentationSchema,
    SequenceSchema,
)
from talea.serialization import SerializationError
from talea.serialization.selection import _object_variants


class Money:
    def __init__(self, cents: int) -> None:
        self.cents = cents

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and self.cents == other.cents

    def __hash__(self) -> int:
        return hash(self.cents)


class MoneySubclass(Money):
    pass


@dataclass(frozen=True)
class MoneyOutput:
    amount: int
    currency: str


def load_money(value: str) -> Money:
    return Money(int(value))


def dump_money(value: Money) -> str:
    return str(value.cents)


def dump_money_output(value: Money) -> MoneyOutput:
    return MoneyOutput(value.cents, "CHF")


type MoneyValue = Annotated[Money, Representation(input=str, load=load_money)]
type FullMoneyValue = Annotated[
    Money,
    Representation(input=str, load=load_money, output=str, dump=dump_money),
]
type StructuredMoneyValue = Annotated[
    Money,
    Representation(input=str, load=load_money, output=MoneyOutput, dump=dump_money_output),
]


@dataclass
class RecursiveLedger:
    children: list[RecursiveLedger]
    amount: MoneyValue


def test_declaration_invariants_immutability_and_safe_identity() -> None:
    declaration = Representation(input=str, load=load_money)
    equivalent = Representation(input=str, load=load_money)

    assert declaration.input is str
    assert declaration.load is load_money
    assert declaration.output is declaration.dump is None
    assert declaration != equivalent
    assert hash(declaration) != hash(equivalent) or declaration is not equivalent
    assert repr(declaration) == "Representation(input=<type form>, load=<callback>)"
    full = Representation(input=str, load=load_money, output=str, dump=dump_money)
    assert full.dump is dump_money
    assert repr(full) == ("Representation(input=<type form>, load=<callback>, output=<type form>, dump=<callback>)")
    with pytest.raises(AttributeError, match="immutable"):
        declaration._input = int  # type: ignore[assignment]
    with pytest.raises(AttributeError, match="immutable"):
        del declaration._load

    for keywords, message in (
        ({}, "requires"),
        ({"input": str}, "together"),
        ({"load": load_money}, "together"),
        ({"output": str}, "together"),
        ({"dump": dump_money}, "together"),
    ):
        with pytest.raises(TypeError, match=message):
            Representation(**keywords)  # type: ignore[call-overload]

    none_input = Contract[Money](Annotated[Money, Representation(input=None, load=lambda value: Money(0))])
    assert none_input.from_python(None) == Money(0)


def test_callback_form_policy_accepts_sync_values_and_rejects_async_generators_and_descriptors() -> None:
    class Loader:
        def __call__(self, value: str) -> Money:
            return load_money(value)

    async def async_loader(value: str) -> Money:
        return load_money(value)

    def generator_loader(value: str):
        yield load_money(value)

    assert Representation(input=str, load=Loader()).load is not None
    assert Representation(input=str, load=partial(load_money)).load is not None
    assert Representation(input=str, load=int).load is int
    for callback, message in (
        (async_loader, "synchronous"),
        (generator_loader, "one value"),
        (staticmethod(load_money), "descriptor"),
        (classmethod(load_money), "descriptor"),
        (object(), "callable"),
    ):
        with pytest.raises(TypeError, match=message):
            Representation(input=str, load=callback)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match=message):
            Representation(output=str, dump=callback)  # type: ignore[arg-type]


def test_resolution_owns_one_schema_and_plain_opaque_classes_remain_unsupported() -> None:
    with pytest.raises(TypeError, match="Unsupported annotation"):
        Contract(Money)

    contract = Contract[Money](MoneyValue)
    schema = contract._artifacts.schema
    assert isinstance(schema, AliasSchema)
    assert isinstance(schema.schema, RepresentationSchema)
    assert schema.schema.opaque_internal is True
    assert schema.schema._declaration.load is load_money
    contract_info = inspect_contract(contract)
    assert contract_info.schema is schema
    assert contract_info.operations == ("strict_python", "external_python", "json_input")
    assert "load_money" not in repr(schema)
    assert talea.Representation is Representation

    marker = Representation(input=str, load=load_money)
    with pytest.raises(TypeError, match="only one Representation"):
        Contract[Money](Annotated[Money, marker, marker])
    with pytest.raises(TypeError, match="cannot annotate the same contract"):
        Contract[Money](Annotated[Money, marker, Discriminator("kind")])
    with pytest.raises(TypeError, match="Unsupported annotation"):
        Contract(Annotated[object(), marker])


def test_strict_validation_uses_exact_internal_type_preserves_identity_and_never_loads() -> None:
    calls = 0

    def counted(value: str) -> Money:
        nonlocal calls
        calls += 1
        return load_money(value)

    contract = Contract[Money](Annotated[Money, Representation(input=str, load=counted)])
    money = Money(1)

    assert contract.validate(money) is money
    assert calls == 0
    for invalid in (MoneySubclass(1), "1"):
        with pytest.raises(ValidationError) as raised:
            contract.validate(invalid)
        assert raised.value.expected == "Money"
    assert counted not in contract.validate.__globals__.values()


def test_supported_internal_schema_is_preserved_instead_of_becoming_opaque() -> None:
    contract = Contract[list[int]](Annotated[list[int], Representation(input=int, load=lambda size: list(range(size)))])
    schema = contract._artifacts.schema
    assert isinstance(schema, RepresentationSchema)
    assert schema.opaque_internal is False
    value = [1, 2]
    assert contract.validate(value) is value
    with pytest.raises(ValidationError):
        contract.validate([1, "2"])


def test_python_and_json_input_validate_before_loading_call_once_and_validate_results() -> None:
    calls: list[object] = []

    def loader(value: str) -> Money:
        calls.append(value)
        return load_money(value)

    contract = Contract[Money](Annotated[Money, Representation(input=Annotated[str, Pattern(r"^\d+$")], load=loader)])

    assert contract.from_python("12") == Money(12)
    assert contract.from_json('"13"') == Money(13)
    assert calls == ["12", "13"]
    with pytest.raises(ValidationError) as input_error:
        contract.from_python("invalid")
    assert input_error.value.code.value == "pattern"
    assert calls == ["12", "13"]

    invalid_result = Contract[Money](Annotated[Money, Representation(input=str, load=lambda value: value)])
    with pytest.raises(ValidationError) as result_error:
        invalid_result.from_python("secret")
    assert result_error.value.code.value == "representation_result"
    assert result_error.value.expected == "Money"


def test_loader_error_policy_retains_ordinary_cause_and_propagates_other_exceptions() -> None:
    rejected = ValueError("rejected")

    def reject(value: str) -> Money:
        raise rejected

    contract = Contract[Money](Annotated[Money, Representation(input=str, load=reject)])
    with pytest.raises(ValidationError) as raised:
        contract.from_python("1")
    assert raised.value.code.value == "representation_load"
    assert raised.value.location == ()
    assert raised.value.__cause__ is rejected
    assert raised.value.errors()[0]["context"] == {"stage": "load"}

    def crash(value: str) -> Money:
        raise RuntimeError("application failure")

    with pytest.raises(RuntimeError, match="application failure"):
        Contract[Money](Annotated[Money, Representation(input=str, load=crash)]).from_python("1")


def test_sensitive_representation_failures_redact_values_messages_and_causes() -> None:
    def reject(value: str) -> Money:
        raise ValueError(f"secret:{value}")

    sensitive = Contract[Money](Annotated[Money, Representation(input=str, load=reject), Sensitive()])
    with pytest.raises(ValidationError) as loader_error:
        sensitive.from_python("token")
    assert loader_error.value.value == "<redacted>"
    assert loader_error.value.__cause__ is None
    assert "token" not in str(loader_error.value)

    def crash(value: str) -> Money:
        raise RuntimeError(f"secret:{value}")

    sensitive_crash = Contract[Money](Annotated[Money, Representation(input=str, load=crash), Sensitive()])
    with pytest.raises(ValidationError) as crash_error:
        sensitive_crash.from_python("token")
    assert crash_error.value.code.value == "representation_load"
    assert crash_error.value.value == "<redacted>"
    assert crash_error.value.__cause__ is None
    assert "token" not in str(crash_error.value)

    wrong = Contract[Money](Annotated[Money, Representation(input=str, load=lambda value: value), Sensitive()])
    with pytest.raises(ValidationError) as result_error:
        wrong.from_python("token")
    assert result_error.value.value == "<redacted>"
    assert "token" not in str(result_error.value)

    nested = Contract[Money](
        Annotated[Money, Representation(input=Annotated[str, Pattern("safe")], load=load_money), Sensitive()]
    )
    with pytest.raises(ValidationError) as input_error:
        nested.from_python("token")
    assert input_error.value.value == "<redacted>"


def test_outer_constraints_do_not_move_to_the_external_contract() -> None:
    with pytest.raises(TypeError, match="Pattern does not apply"):
        Contract[Money](Annotated[Money, Representation(input=str, load=load_money), Pattern("x")])


def test_newtype_alias_and_container_composition() -> None:
    UserId = NewType("UserId", int)
    type UserIdValue = Annotated[UserId, Representation(input=str, load=int)]

    assert Contract[UserId](UserIdValue).from_python("3") == 3
    assert Contract[list[Money]](list[MoneyValue]).from_python(["1", "2"]) == [Money(1), Money(2)]
    assert Contract[tuple[Money, ...]](tuple[MoneyValue, ...]).from_json('["1","2"]') == (
        Money(1),
        Money(2),
    )
    assert Contract[set[Money]](set[MoneyValue]).from_python({"1", "2"}) == {Money(1), Money(2)}
    assert Contract[frozenset[Money]](frozenset[MoneyValue]).from_json('["1","2"]') == frozenset({Money(1), Money(2)})
    assert Contract[dict[str, Money]](dict[str, MoneyValue]).from_python({"a": "1"}) == {"a": Money(1)}
    assert Contract[Money | None](MoneyValue | None).from_python(None) is None


def test_union_execution_is_deterministic_and_valueerror_allows_the_next_branch() -> None:
    events: list[str] = []

    class Alpha:
        pass

    class Beta:
        pass

    def alpha(value: str) -> Alpha:
        events.append("alpha")
        raise ValueError("try beta")

    def beta(value: str) -> Beta:
        events.append("beta")
        return Beta()

    type AlphaValue = Annotated[Alpha, Representation(input=str, load=alpha)]
    type BetaValue = Annotated[Beta, Representation(input=str, load=beta)]

    result = Contract[Alpha | Beta](AlphaValue | BetaValue).from_python("value")
    assert isinstance(result, Beta)
    assert events == ["alpha", "beta"]

    events.clear()
    type TrackedMoney = Annotated[
        Money,
        Representation(input=str, load=lambda value: events.append("money") or load_money(value)),
    ]
    assert Contract[Money | str](TrackedMoney | str).from_python("7") == "7"
    assert events == []

    first = Representation(input=str, load=load_money)
    second = Representation(input=int, load=lambda value: Money(value))
    with pytest.raises(TypeError, match="same internal contract"):
        Contract[Money](Annotated[Money, first] | Annotated[Money, second])


def test_tagged_union_and_nested_error_locations_compose_without_representation_dispatch() -> None:
    class Card(TypedDict):
        kind: Literal["card"]
        amount: MoneyValue

    class Cash(TypedDict):
        kind: Literal["cash"]
        amount: MoneyValue

    type PaymentMethod = Annotated[Card | Cash, Discriminator("kind")]
    result = Contract[PaymentMethod](PaymentMethod).from_json('{"kind":"card","amount":"7"}')
    assert result == {"kind": "card", "amount": Money(7)}
    represented_tagged = Contract(
        Annotated[PaymentMethod, Representation(input=PaymentMethod, load=lambda value: value)]
    )
    assert schema_contains_tagged_union(represented_tagged._artifacts.schema)
    assert schema_input_directions_are_available(represented_tagged._artifacts.schema)

    def reject(value: str) -> Money:
        raise ValueError("invalid")

    type RejectedMoney = Annotated[Money, Representation(input=str, load=reject)]

    class Envelope(Spec):
        amounts: list[RejectedMoney]

    with pytest.raises(ValidationError) as raised:
        Envelope.from_mapping({"amounts": ["1"]})
    assert raised.value.location == ("amounts", 0)


def test_spec_transform_and_check_observe_loaded_internal_values() -> None:
    events: list[str] = []

    class Payment(Spec):
        amount: MoneyValue

        @transform("amount")
        def normalize(value: Money) -> Money:
            events.append(f"transform:{type(value).__name__}")
            return Money(value.cents + 1)

        @check("amount")
        def positive(amount: Money) -> None:
            events.append(f"check:{amount.cents}")

    internal = Money(1)
    assert Payment(amount=internal).amount == Money(2)
    assert events == ["transform:Money", "check:2"]
    events.clear()
    assert Payment.from_mapping({"amount": "2"}).amount == Money(3)
    assert events == ["transform:Money", "check:3"]
    events.clear()
    assert Payment.from_json('{"amount":"3"}').amount == Money(4)
    assert events == ["transform:Money", "check:4"]


def test_dataclass_typeddict_dynamic_derived_generic_and_recursive_composition() -> None:
    @dataclass
    class Ledger:
        amount: MoneyValue

    class Payload(TypedDict):
        amount: MoneyValue
        note: NotRequired[str]

    ledger_contract = Contract[Ledger](Ledger)
    existing = Ledger(Money(1))
    assert ledger_contract.validate(existing) is existing
    assert ledger_contract.from_python({"amount": "2"}) == Ledger(Money(2))
    assert ledger_contract.from_json('{"amount":"3"}') == Ledger(Money(3))

    payload = Contract[Payload](Payload)
    assert payload.validate({"amount": Money(1)}) == {"amount": Money(1)}
    assert payload.from_python({"amount": "2"}) == {"amount": Money(2)}
    assert payload.from_json('{"amount":"3"}') == {"amount": Money(3)}

    recursive_contract = Contract(RecursiveLedger)
    recursive_schema = recursive_contract._artifacts.schema
    assert isinstance(recursive_schema, DataclassSchema)
    assert recursive_schema.identity is not None
    assert schema_contains_representation(recursive_schema)
    assert not schema_contains_representation(recursive_schema, frozenset({recursive_schema.identity}))
    assert schema_input_directions_are_available(recursive_schema)
    assert schema_input_directions_are_available(recursive_schema, frozenset({recursive_schema.identity}))
    children = recursive_schema.fields[0].schema
    assert isinstance(children, SequenceSchema)
    assert isinstance(children.item, NamedReferenceSchema)
    assert schema_input_directions_are_available(children.item)
    assert schema_input_directions_are_available(children.item, frozenset({children.item.identity}))
    recursive_ledger = recursive_contract.from_python({"children": [], "amount": "3"})
    assert recursive_ledger.amount == Money(3)
    assert len(inspect_contract(recursive_contract).representations) == 1

    Dynamic = create_spec("DynamicPayment", {"amount": MoneyValue})
    assert Dynamic.from_mapping({"amount": "4"}).amount == Money(4)
    Derived = derive_spec(Dynamic, partial=True, name="DynamicPaymentPatch")
    assert inspect_spec(Derived).fields[0].schema is inspect_spec(Dynamic).fields[0].schema
    assert Derived.from_mapping({"amount": "5"}).amount == Money(5)

    class Page[T](Spec):
        item: T

    assert Page[MoneyValue].from_mapping({"item": "6"}).item == Money(6)

    class Box[T](Spec):
        value: T

    assert Box[list[MoneyValue]].from_mapping({"value": ["6"]}).value == [Money(6)]

    for annotation in (
        list[MoneyValue],
        dict[str, MoneyValue],
        Payload,
        tuple[MoneyValue, ...],
        tuple[MoneyValue, int],
        MoneyValue | None,
    ):
        assert schema_input_directions_are_available(Contract(annotation)._artifacts.schema)

    class Node(Spec):
        identifier: MoneyValue
        children: list[Node]

    node = Node.from_mapping({"identifier": "1", "children": [{"identifier": "2", "children": []}]})
    assert node.identifier == Money(1)
    assert node.children[0].identifier == Money(2)


def test_resource_policy_counts_input_and_amplified_internal_validation_in_one_state() -> None:
    type Expanded = Annotated[list[int], Representation(input=int, load=lambda size: list(range(size)))]
    contract = Contract[list[int]](Expanded)

    assert contract.from_python(3, policy=ResourcePolicy(max_nodes=5)) == [0, 1, 2]
    with pytest.raises(ResourceLimitError) as nodes:
        contract.from_python(3, policy=ResourcePolicy(max_nodes=4))
    assert (nodes.value.code, nodes.value.limit, nodes.value.observed) == ("nodes", 4, 5)

    type Deep = Annotated[list[list[int]], Representation(input=int, load=lambda value: [[value]])]
    with pytest.raises(ResourceLimitError) as depth:
        Contract[list[list[int]]](Deep).from_python(1, policy=ResourcePolicy(max_depth=1))
    assert depth.value.code == "depth"


def test_output_only_bidirectional_and_missing_output_directions() -> None:
    dumps = 0

    def dump(value: Money) -> str:
        nonlocal dumps
        dumps += 1
        return str(value.cents)

    output_only = Contract[Money](Annotated[Money, Representation(output=str, dump=dump)])
    money = Money(1)
    assert output_only.validate(money) is money
    assert inspect_contract(output_only).operations == ("strict_python", "python_output", "json_output")
    with pytest.raises(TypeError, match="no input direction"):
        output_only.from_python("1")
    assert output_only.to_python(money) == "1"
    assert output_only.to_json(money) == '"1"'
    assert dumps == 2

    bidirectional = Contract[Money](FullMoneyValue)
    assert bidirectional.to_python(money) == "1"
    assert bidirectional.to_json(money) == '"1"'
    assert inspect_contract(bidirectional).operations == (
        "strict_python",
        "external_python",
        "json_input",
        "python_output",
        "json_output",
    )

    input_only = Contract[Money](MoneyValue)
    with pytest.raises(SerializationError, match="no output direction"):
        input_only.to_python(money)
    with pytest.raises(SerializationError, match="no output direction"):
        input_only.to_json(money)


def test_output_dumps_once_validates_result_and_uses_normal_projection() -> None:
    calls: list[Money] = []

    def dump(value: Money) -> MoneyOutput:
        calls.append(value)
        return MoneyOutput(value.cents, "CHF")

    type TrackedMoney = Annotated[Money, Representation(output=MoneyOutput, dump=dump)]
    contract = Contract[Money](TrackedMoney)
    money = Money(125)

    assert contract.to_python(money) == {"amount": 125, "currency": "CHF"}
    assert calls == [money]
    calls.clear()
    assert contract.to_json(money) == '{"amount":125,"currency":"CHF"}'
    assert calls == [money]

    mutable = Contract[list[int]](Annotated[list[int], Representation(output=list[int], dump=lambda value: value)])
    source = [1, 2]
    projected = mutable.to_python(source)
    assert projected == source
    assert projected is not source

    wrong = Contract[Money](Annotated[Money, Representation(output=str, dump=lambda value: value.cents)])
    with pytest.raises(SerializationError, match="outside its declared output contract") as raised:
        wrong.to_python(money)
    assert isinstance(raised.value.__cause__, ValidationError)


def test_output_callback_failures_and_sensitive_paths_follow_serialization_policy() -> None:
    rejected = RuntimeError("unsafe detail")

    def reject(value: Money) -> str:
        raise rejected

    ordinary = Contract[Money](Annotated[Money, Representation(output=str, dump=reject)])
    with pytest.raises(SerializationError, match="dumper failed") as raised:
        ordinary.to_python(Money(1))
    assert raised.value.__cause__ is rejected

    sensitive = Contract[Money](Annotated[Money, Representation(output=str, dump=reject), Sensitive()])
    with pytest.raises(SerializationError) as hidden:
        sensitive.to_json(Money(1))
    assert hidden.value.__cause__ is None
    assert "unsafe detail" not in str(hidden.value)

    invalid = Contract[Money](Annotated[Money, Representation(output=str, dump=lambda value: value.cents), Sensitive()])
    with pytest.raises(SerializationError) as invalid_error:
        invalid.to_python(Money(1))
    assert invalid_error.value.__cause__ is None


def test_representation_output_composes_through_specs_dataclasses_typed_dicts_and_containers() -> None:
    class Payment(Spec):
        amount: FullMoneyValue

    @dataclass
    class Ledger:
        amount: StructuredMoneyValue

    class Payload(TypedDict):
        amount: FullMoneyValue

    assert Payment(amount=Money(7)).to_dict() == {"amount": "7"}
    assert Payment(amount=Money(7)).to_json() == '{"amount":"7"}'
    assert Contract(Ledger).to_python(Ledger(Money(8))) == {"amount": {"amount": 8, "currency": "CHF"}}
    assert Contract(Ledger).to_json(Ledger(Money(8))) == '{"amount":{"amount":8,"currency":"CHF"}}'
    assert Contract[Payload](Payload).to_python({"amount": Money(9)}) == {"amount": "9"}
    assert Contract[Payload](Payload).to_json({"amount": Money(9)}) == '{"amount":"9"}'
    assert Contract[list[Money]](list[FullMoneyValue]).to_python([Money(1), Money(2)]) == ["1", "2"]
    assert Contract[tuple[Money, ...]](tuple[FullMoneyValue, ...]).to_python((Money(1), Money(2))) == (
        "1",
        "2",
    )
    assert Contract[set[Money]](set[FullMoneyValue]).to_python({Money(1)}) == {"1"}
    assert Contract[frozenset[Money]](frozenset[FullMoneyValue]).to_json(frozenset({Money(2)})) == '["2"]'
    assert Contract[dict[str, Money]](dict[str, FullMoneyValue]).to_json({"a": Money(3)}) == '{"a":"3"}'
    assert Contract[Money | None](FullMoneyValue | None).to_python(None) is None

    UserId = NewType("UserId", int)
    type RepresentedUserId = Annotated[UserId, Representation(output=str, dump=str)]
    assert Contract[UserId](RepresentedUserId).to_json(UserId(4)) == '"4"'


@given(st.integers(min_value=-1_000_000, max_value=1_000_000))
def test_scalar_output_property_preserves_every_bounded_integer(cents: int) -> None:
    contract = Contract[Money](FullMoneyValue)
    assert contract.to_python(Money(cents)) == str(cents)
    assert contract.to_json(Money(cents)) == f'"{cents}"'


def test_schema_projection_uses_declared_directions_without_executing_callbacks() -> None:
    calls: list[str] = []

    def load(value: int) -> Money:
        calls.append("load")
        return Money(value)

    def dump(value: Money) -> MoneyOutput:
        calls.append("dump")
        return dump_money_output(value)

    type BoundaryMoney = Annotated[
        Money,
        Representation(input=int, load=load, output=MoneyOutput, dump=dump),
    ]
    contract = Contract[Money](BoundaryMoney)

    input_schema = contract.json_schema(mode="input")
    output_schema = contract.json_schema(mode="output")
    assert input_schema["$defs"] == {"BoundaryMoney": {"type": "integer"}}
    assert output_schema["$defs"] == {
        "BoundaryMoney": {"$ref": "#/$defs/MoneyOutput"},
        "MoneyOutput": {
            "type": "object",
            "properties": {"amount": {"type": "integer"}, "currency": {"type": "string"}},
            "additionalProperties": False,
            "required": ["amount", "currency"],
        },
    }
    assert contract.openapi_schema(mode="input")["components"]["schemas"]["BoundaryMoney"] == {"type": "integer"}
    assert contract.openapi_schema(mode="output")["components"]["schemas"]["BoundaryMoney"] == {
        "$ref": "#/components/schemas/MoneyOutput"
    }

    class DefaultPayment(Spec):
        amount: BoundaryMoney = Money(1)

    assert "default" not in repr(DefaultPayment.json_schema(mode="output"))
    assert calls == []

    with pytest.raises(SchemaProjectionError, match="no output direction"):
        Contract[Money](MoneyValue).json_schema(mode="output")
    output_only = Annotated[Money, Representation(output=str, dump=dump_money)]
    with pytest.raises(SchemaProjectionError, match="no input direction"):
        Contract[Money](output_only).openapi_schema(mode="input")

    LeftPayload = make_dataclass("Payload", (("value", int),), module="left_contracts")
    RightPayload = make_dataclass("Payload", (("value", str),), module="right_contracts")

    def unused_collision_dump(value: Money) -> tuple[object, object]:
        raise AssertionError("schema projection executed a dumper")

    type CollisionBoundary = Annotated[
        Money,
        Representation(output=tuple[LeftPayload, RightPayload], dump=unused_collision_dump),
    ]
    collision = Contract[Money](CollisionBoundary)
    first_document = collision.json_schema(mode="output")
    second_document = collision.json_schema(mode="output")
    assert first_document == second_document
    definitions = first_document["$defs"]
    assert isinstance(definitions, dict)
    assert len([name for name in definitions if "Payload" in name]) == 2
    assert "unused_collision_dump" not in repr(first_document)


def test_representation_introspection_is_immutable_and_callback_free() -> None:
    info = inspect_contract(Contract[Money](FullMoneyValue))
    assert len(info.representations) == 1
    representation = info.representations[0]
    assert isinstance(representation, RepresentationInfo)
    assert representation.has_loader is representation.has_dumper is True
    assert representation.input is not None
    assert representation.output is not None
    assert not hasattr(representation, "load")
    assert not hasattr(representation, "dump")
    assert "load_money" not in repr(representation)
    assert "dump_money" not in repr(representation)
    with pytest.raises((AttributeError, FrozenInstanceError)):
        representation.has_dumper = False  # type: ignore[misc]

    class Payment(Spec):
        amount: FullMoneyValue
        fee: FullMoneyValue

    spec_info = inspect_spec(Payment)
    assert len(spec_info.representations) == 1
    assert spec_info.representations[0] == representation


def test_nested_selection_descends_through_declared_output_and_preserves_hook_opacity() -> None:
    calls = 0

    @dataclass(frozen=True)
    class AliasedOutput:
        amount: Annotated[int, Alias("minorUnits")]
        currency: str

    def dump(value: Money) -> AliasedOutput:
        nonlocal calls
        calls += 1
        return AliasedOutput(value.cents, "CHF")

    type AliasedMoney = Annotated[Money, Representation(output=AliasedOutput, dump=dump)]

    class Invoice(Spec):
        price: AliasedMoney
        history: list[AliasedMoney]
        indexed: dict[str, AliasedMoney]

    class OptionalInvoice(Spec):
        price: AliasedMoney | None

    invoice = Invoice(price=Money(1), history=[Money(2)], indexed={"paid": Money(3)})
    assert invoice.to_dict(include={"price": {"amount": True}}) == {"price": {"minorUnits": 1}}
    assert calls == 1
    assert invoice.to_dict(include={"history": {"currency": True}, "indexed": {"amount": True}}) == {
        "history": [{"currency": "CHF"}],
        "indexed": {"paid": {"minorUnits": 3}},
    }
    assert calls == 3
    assert invoice.to_dict(include={"price": True}, exclude={"price": {"currency": True}}) == {
        "price": {"minorUnits": 1}
    }
    assert calls == 4
    assert OptionalInvoice(price=Money(6)).to_dict(include={"price": {"amount": True}}) == {"price": {"minorUnits": 6}}
    assert calls == 5
    with pytest.raises(ValueError, match="unknown field"):
        invoice.to_dict(include={"price": {"missing": True}})
    assert calls == 5

    class Hooked(Spec):
        price: AliasedMoney

        @serialize("price")
        def replace_price(value: Money) -> dict[str, int]:
            return {"hook": value.cents}

    hooked = Hooked(price=Money(5))
    assert hooked.to_dict() == {"price": {"hook": 5}}
    assert calls == 5
    with pytest.raises(ValueError, match="cannot descend through serializer field"):
        hooked.to_dict(include={"price": {"amount": True}})
    assert calls == 5

    class InputOnlyInvoice(Spec):
        price: MoneyValue

    with pytest.raises(ValueError, match="without output"):
        InputOnlyInvoice(price=Money(1)).to_dict(include={"price": {"amount": True}})
    input_only_schema = Contract[Money](MoneyValue)._artifacts.schema
    assert _object_variants(input_only_schema) == ()
    assert schema_output_directions_are_available(input_only_schema) is False


def test_output_constraints_unions_nested_representations_and_locations() -> None:
    constrained = Contract[Money](
        Annotated[
            Money,
            Representation(output=Annotated[str, Pattern(r"^CHF \d+$")], dump=lambda value: f"CHF {value.cents}"),
        ]
    )
    assert constrained.to_python(Money(7)) == "CHF 7"
    invalid_constraint = Contract[Money](
        Annotated[
            Money,
            Representation(output=Annotated[str, Pattern(r"^USD ")], dump=lambda value: f"CHF {value.cents}"),
        ]
    )
    with pytest.raises(SerializationError, match="outside its declared output contract"):
        invalid_constraint.to_python(Money(7))

    calls = 0

    def tracked(value: Money) -> str:
        nonlocal calls
        calls += 1
        return str(value.cents)

    type TrackedMoney = Annotated[Money, Representation(output=str, dump=tracked)]
    union = Contract[Money | str](TrackedMoney | str)
    assert union.to_python("already external") == "already external"
    assert calls == 0
    assert union.to_json(Money(8)) == '"8"'
    assert calls == 1

    type TwiceMoney = Annotated[
        Money,
        Representation(output=TrackedMoney, dump=lambda value: Money(value.cents * 2)),
    ]
    assert Contract[Money](TwiceMoney).to_python(Money(4)) == "8"
    assert calls == 2

    def dump_wrong(value: Money) -> int:
        return value.cents

    type BrokenMoney = Annotated[Money, Representation(output=str, dump=dump_wrong)]

    class BrokenPayment(Spec):
        amount: BrokenMoney

    with pytest.raises(SerializationError) as nested:
        BrokenPayment(amount=Money(1)).to_dict()
    assert nested.value.location == ("amount",)


def test_output_generics_dynamic_derivation_recursion_and_tagged_composition() -> None:
    class Page[T](Spec):
        item: T

    page = Page[FullMoneyValue](item=Money(1))
    assert page.to_dict() == {"item": "1"}
    assert page.to_json() == '{"item":"1"}'

    Dynamic = create_spec("RepresentedOutput", {"amount": FullMoneyValue})
    Derived = derive_spec(Dynamic, partial=True, name="RepresentedOutputPatch")
    assert Dynamic(amount=Money(2)).to_dict() == {"amount": "2"}
    assert Derived(amount=Money(3)).to_dict() == {"amount": "3"}

    class LedgerNode(Spec):
        amount: FullMoneyValue
        children: list[LedgerNode]

    root = LedgerNode(amount=Money(4), children=[LedgerNode(amount=Money(5), children=[])])
    assert Contract(LedgerNode).to_python(root) == {
        "amount": "4",
        "children": [{"amount": "5", "children": []}],
    }

    class Card(TypedDict):
        kind: Literal["card"]
        amount: FullMoneyValue

    class Cash(TypedDict):
        kind: Literal["cash"]
        amount: FullMoneyValue

    type Method = Annotated[Card | Cash, Discriminator("kind")]
    tagged = Contract[Method](Method)
    assert tagged.to_python({"kind": "card", "amount": Money(6)}) == {"kind": "card", "amount": "6"}
    assert tagged.to_json({"kind": "cash", "amount": Money(7)}) == '{"kind":"cash","amount":"7"}'

    class CardOutput(TypedDict):
        kind: Literal["card"]
        reference: str

    class CashOutput(TypedDict):
        kind: Literal["cash"]
        reference: str

    type TaggedOutput = Annotated[CardOutput | CashOutput, Discriminator("kind")]

    def dump_tagged(value: Money) -> TaggedOutput:
        return {"kind": "card", "reference": str(value.cents)}

    type TaggedMoney = Annotated[Money, Representation(output=TaggedOutput, dump=dump_tagged)]

    class TaggedEnvelope(Spec):
        method: TaggedMoney

    assert TaggedEnvelope(method=Money(8)).to_dict(include={"method": {"kind": True, "reference": True}}) == {
        "method": {"kind": "card", "reference": "8"}
    }


def test_dump_side_effects_reentrancy_concurrent_compilation_cycles_and_lifetime() -> None:
    integer = Contract(int)
    calls = 0

    class HostileDumper:
        def __call__(self, value: Money) -> list[int]:
            nonlocal calls
            calls += 1
            value.cents += 1
            return [integer.to_python(value.cents)]

        def __repr__(self) -> str:
            raise RuntimeError("repr must not execute")

    dumper = HostileDumper()
    declaration = Representation(output=list[int], dump=dumper)
    contract = Contract[Money](Annotated[Money, declaration])
    values = [Money(index) for index in range(32)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        projected = tuple(executor.map(contract.to_python, values))
    assert projected == tuple([index + 1] for index in range(32))
    assert calls == 32
    assert [value.cents for value in values] == list(range(1, 33))
    compiled = contract._artifacts.python_output
    assert compiled is not None
    assert dumper in compiled.__globals__.values()
    assert any("representation_dump" in name for name in compiled.__globals__)
    assert any("representation_output_validator" in name for name in compiled.__globals__)
    assert any("representation_output_projector" in name for name in compiled.__globals__)
    assert all("registry" not in name for name in compiled.__globals__)
    assert repr(declaration) == "Representation(output=<type form>, dump=<callback>)"

    type RecursiveOutput = int | list[RecursiveOutput]

    def cyclic(value: Money) -> RecursiveOutput:
        result: list[RecursiveOutput] = []
        result.append(result)
        return result

    cyclic_contract = Contract[Money](Annotated[Money, Representation(output=RecursiveOutput, dump=cyclic)])
    with pytest.raises(SerializationError, match="cyclic object graphs"):
        cyclic_contract.to_json(Money(1))

    amplified = Contract[Money](
        Annotated[Money, Representation(output=list[int], dump=lambda value: list(range(value.cents)))]
    )
    assert len(amplified.to_python(Money(1_000))) == 1_000

    class CollectableDumper:
        def __call__(self, value: Money) -> str:
            return str(value.cents)

    collectable = CollectableDumper()
    reference = weakref.ref(collectable)
    local_declaration = Representation(output=str, dump=collectable)
    local = Contract[Money](Annotated[Money, local_declaration])
    assert local.to_python(Money(1)) == "1"
    del local, local_declaration, collectable
    for _index in range(256):
        callback = CollectableDumper()
        Contract[Money](Annotated[Money, Representation(output=str, dump=callback)])
    del callback
    gc.collect()
    assert reference() is None


def test_callback_reentrancy_concurrent_first_use_source_safety_and_lifetime() -> None:
    integer = Contract(int)

    class HostileLoader:
        def __call__(self, value: str) -> Money:
            return Money(integer.from_python(int(value)))

        def __repr__(self) -> str:
            raise RuntimeError("repr must not execute")

    HostileLoader.__qualname__ = "hostile 'loader'\\n\\\\\u96ea"
    loader = HostileLoader()
    declaration = Representation(input=str, load=loader)
    contract = Contract[Money](Annotated[Money, declaration])
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = tuple(executor.map(contract.from_python, (str(index) for index in range(32))))
    assert [value.cents for value in values] == list(range(32))
    compiled = contract._artifacts.python_input
    assert compiled is not None
    assert loader in compiled.__globals__.values()
    assert any(instruction.opname == "CALL" for instruction in dis.get_instructions(compiled))
    assert repr(declaration) == "Representation(input=<type form>, load=<callback>)"

    class ConcurrentPayment(Spec):
        amount: MoneyValue

    with ThreadPoolExecutor(max_workers=8) as executor:
        mapped = tuple(executor.map(ConcurrentPayment.from_mapping, ({"amount": str(i)} for i in range(16))))
        decoded = tuple(executor.map(ConcurrentPayment.from_json, (f'{{"amount":"{i}"}}' for i in range(16))))
    assert [item.amount.cents for item in mapped] == list(range(16))
    assert [item.amount.cents for item in decoded] == list(range(16))

    class CollectableLoader:
        def __call__(self, value: str) -> Money:
            return load_money(value)

    collectable = CollectableLoader()
    reference = weakref.ref(collectable)
    local_declaration = Representation(input=str, load=collectable)
    local_contract = Contract[Money](Annotated[Money, local_declaration])
    assert local_contract.from_python("1") == Money(1)
    del local_contract, local_declaration, collectable
    for _index in range(256):
        callback = CollectableLoader()
        Contract[Money](Annotated[Money, Representation(input=str, load=callback)])
    gc.collect()
    assert reference() is None


def test_loader_mutation_is_visible_without_corrupting_extracted_sibling_values() -> None:
    def load_mutating(value: dict[str, int]) -> Money:
        cents = value.pop("cents")
        return Money(cents)

    type MutatingMoney = Annotated[
        Money,
        Representation(input=dict[str, int], load=load_mutating),
    ]

    class Payment(Spec):
        amount: MutatingMoney
        sequence: int

    source = {"amount": {"cents": 125}, "sequence": 7}
    payment = Payment.from_mapping(source)

    assert payment.amount == Money(125)
    assert payment.sequence == 7
    assert source == {"amount": {}, "sequence": 7}
