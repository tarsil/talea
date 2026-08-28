from __future__ import annotations

import dis
import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Literal, NewType, NotRequired, TypedDict

import pytest

import talea
from talea import (
    Contract,
    Discriminator,
    Pattern,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
    ValidationError,
    check,
    create_spec,
    derive_spec,
    transform,
)
from talea.declaration.policies import (
    schema_contains_representation,
    schema_contains_tagged_union,
    schema_input_directions_are_available,
)
from talea.introspection import inspect_contract, inspect_spec
from talea.json_schema import SchemaProjectionError
from talea.representation import Representation
from talea.schema import AliasSchema
from talea.schema.nodes import (
    DataclassSchema,
    NamedReferenceSchema,
    RepresentationSchema,
    SequenceSchema,
)
from talea.serialization import SerializationError


class Money:
    def __init__(self, cents: int) -> None:
        self.cents = cents

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and self.cents == other.cents

    def __hash__(self) -> int:
        return hash(self.cents)


class MoneySubclass(Money):
    pass


def load_money(value: str) -> Money:
    return Money(int(value))


def dump_money(value: Money) -> str:
    return str(value.cents)


type MoneyValue = Annotated[Money, Representation(input=str, load=load_money)]
type FullMoneyValue = Annotated[
    Money,
    Representation(input=str, load=load_money, output=str, dump=dump_money),
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
    assert not hasattr(talea, "Representation")

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


def test_output_only_and_deferred_output_projection_fail_explicitly_without_calling_dump() -> None:
    dumps = 0

    def dump(value: Money) -> str:
        nonlocal dumps
        dumps += 1
        return str(value.cents)

    output_only = Contract[Money](Annotated[Money, Representation(output=str, dump=dump)])
    money = Money(1)
    assert output_only.validate(money) is money
    assert inspect_contract(output_only).operations == ("strict_python",)
    with pytest.raises(TypeError, match="no input direction"):
        output_only.from_python("1")
    with pytest.raises(SerializationError, match="not available"):
        output_only.to_python(money)
    with pytest.raises(SerializationError, match="not available"):
        Contract[Money](FullMoneyValue).to_json(money)
    with pytest.raises(SchemaProjectionError, match="not available"):
        Contract[Money](FullMoneyValue).json_schema()
    assert dumps == 0


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
