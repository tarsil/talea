"""Behavioral and architectural proof for strict synchronous callables."""

from __future__ import annotations

import dis
import gc
import inspect
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, dataclass
from typing import Annotated, Literal, TypedDict, overload

import pytest

from talea import (
    Alias,
    Discriminator,
    Ge,
    Representation,
    Sensitive,
    Spec,
    ValidationError,
    validate_call,
)
from talea.callables.models import MISSING_DEFAULT, _CallableParameter, _CallableSchema
from talea.introspection import CallableInfo, ParameterInfo, inspect_callable
from talea.schema import AliasSchema, ConstrainedSchema, PrimitiveSchema


def test_validated_call_preserves_binding_metadata_and_static_default() -> None:
    """One wrapper retains ordinary function identity and call forms."""

    def transfer(amount: int, fee: int = 2) -> int:
        """Transfer a validated amount."""

        return amount - fee

    wrapped = validate_call(transfer)

    assert wrapped(10) == 8
    assert wrapped(amount=10, fee=3) == 7
    assert wrapped.__name__ == "transfer"
    assert wrapped.__qualname__ == transfer.__qualname__
    assert wrapped.__doc__ == transfer.__doc__
    assert wrapped.__module__ == transfer.__module__
    assert wrapped.__annotations__ == transfer.__annotations__
    assert wrapped.__wrapped__ is transfer
    assert inspect.signature(wrapped) == inspect.signature(transfer)
    assert wrapped.__defaults__ == (2,)


@pytest.mark.parametrize(
    "call",
    [
        lambda function: function(),
        lambda function: function(1, 2, 3),
        lambda function: function(unexpected=1),
        lambda function: function(1, left=2),
    ],
)
def test_python_rejects_invalid_call_shapes_with_plain_type_error(call: object) -> None:
    @validate_call
    def total(left: int, right: int) -> int:
        return left + right

    with pytest.raises(TypeError) as captured:
        call(total)  # type: ignore[operator]

    assert type(captured.value) is TypeError


def test_argument_and_return_failures_have_boundary_locations_and_exact_counts() -> None:
    calls = 0

    @validate_call
    def later_failure(first: int, payload: list[int]) -> int:
        nonlocal calls
        calls += 1
        return payload[0]

    with pytest.raises(ValidationError) as first:
        later_failure(True, ["bad"])  # type: ignore[arg-type, list-item]
    assert first.value.location == ("first",)
    assert len(first.value.errors()) == 1
    assert calls == 0

    with pytest.raises(ValidationError) as nested:
        later_failure(1, ["bad"])  # type: ignore[list-item]
    assert nested.value.location == ("payload", 0)
    assert calls == 0

    @validate_call
    def invalid_result(value: int) -> int:
        nonlocal calls
        calls += 1
        return "bad"  # type: ignore[invalid-return-type]

    with pytest.raises(ValidationError) as returned:
        invalid_result(1)
    assert returned.value.location == ("return",)
    assert calls == 1


def test_return_none_and_successful_identity_are_strict() -> None:
    @validate_call
    def nothing(valid: bool) -> None:
        if valid:
            return None
        return 1  # type: ignore[invalid-return-type]

    values = [1, 2]

    @validate_call
    def unchanged(value: list[int]) -> list[int]:
        return value

    assert nothing(True) is None
    with pytest.raises(ValidationError, match="return"):
        nothing(False)
    assert unchanged(values) is values


def test_application_exceptions_and_mutation_remain_application_owned() -> None:
    secret = "application-secret"

    @validate_call
    def mutate(values: list[int]) -> int:
        values.append(2)
        raise RuntimeError(secret)

    values = [1]
    with pytest.raises(RuntimeError) as captured:
        mutate(values)

    assert captured.value.args == (secret,)
    assert values == [1, 2]


def test_specs_dataclasses_typed_dicts_unions_and_constraints_use_strict_current_state() -> None:
    class Account(Spec):
        entries: list[int]

    @dataclass
    class Order:
        quantity: int

    class Payload(TypedDict):
        quantity: int

    type Positive = Annotated[int, Ge(1)]

    @validate_call
    def accept(
        account: Account,
        order: Order,
        payload: Payload,
        choice: int | str,
        quantity: Positive,
    ) -> Account:
        return account

    account = Account(entries=[3])
    order = Order(2)
    payload: Payload = {"quantity": 1}
    assert accept(account, order, payload, "sell", 1) is account

    account.entries.append("bad")  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as spec_error:
        accept(account, order, payload, 1, 1)
    assert spec_error.value.location == ("account", "entries", 1)

    account = Account(entries=[3])
    order.quantity = "bad"  # type: ignore[assignment]
    with pytest.raises(ValidationError) as dataclass_error:
        accept(account, order, payload, 1, 1)
    assert dataclass_error.value.location == ("order", "quantity")

    order.quantity = 2
    payload["quantity"] = "bad"  # type: ignore[typeddict-item]
    with pytest.raises(ValidationError) as typed_dict_error:
        accept(account, order, payload, 1, 1)
    assert typed_dict_error.value.location == ("payload", "quantity")

    payload["quantity"] = 1
    with pytest.raises(ValidationError) as constraint_error:
        accept(account, order, payload, 1, 0)
    assert constraint_error.value.location == ("quantity",)


def test_aliases_do_not_change_python_binding_names() -> None:
    type ExternalAmount = Annotated[int, Alias("externalAmount")]

    @validate_call
    def settle(amount: ExternalAmount) -> ExternalAmount:
        return amount

    assert settle(amount=1) == 1
    with pytest.raises(TypeError):
        settle(externalAmount=1)  # type: ignore[unknown-argument]
    with pytest.raises(ValidationError) as captured:
        settle(amount="1")  # type: ignore[invalid-argument-type]
    assert captured.value.location == ("amount",)


def test_tagged_union_parameter_uses_canonical_selected_branch_validation() -> None:
    class Card(Spec):
        kind: Literal["card"]
        number: str

    class Bank(Spec):
        kind: Literal["bank"]
        iban: str

    type Payment = Annotated[Card | Bank, Discriminator("kind")]

    @validate_call
    def payment_kind(payment: Payment) -> str:
        return payment.kind

    assert payment_kind(Card(kind="card", number="123")) == "card"
    assert payment_kind(Bank(kind="bank", iban="CH1")) == "bank"
    with pytest.raises(ValidationError) as captured:
        payment_kind(object())  # type: ignore[arg-type]
    assert captured.value.location == ("payment",)


def test_representation_uses_internal_contract_without_loader_or_dumper() -> None:
    class Money:
        pass

    loaded: list[str] = []
    dumped: list[Money] = []

    def load(value: str) -> Money:
        loaded.append(value)
        return Money()

    def dump(value: Money) -> str:
        dumped.append(value)
        return "money"

    representation = Representation(input=str, load=load, output=str, dump=dump)
    type MoneyValue = Annotated[Money, representation]

    @validate_call
    def identity(value: MoneyValue) -> MoneyValue:
        return value

    money = Money()
    assert identity(money) is money
    assert loaded == []
    assert dumped == []
    with pytest.raises(ValidationError):
        identity("money")  # type: ignore[invalid-argument-type]
    assert loaded == []

    @validate_call
    def wrong_return(value: MoneyValue) -> MoneyValue:
        return "money"  # type: ignore[invalid-return-type]

    with pytest.raises(ValidationError) as captured:
        wrong_return(money)
    assert captured.value.location == ("return",)
    assert dumped == []


def test_sensitive_argument_and_return_failures_are_redacted() -> None:
    type Secret = Annotated[str, Sensitive()]

    @validate_call
    def secret_argument(value: Secret) -> int:
        return len(value)

    with pytest.raises(ValidationError) as argument:
        secret_argument(123)  # type: ignore[invalid-argument-type]
    assert argument.value.errors()[0]["input"] == "<redacted>"
    assert argument.value.location == ("value",)

    @validate_call
    def secret_return() -> Secret:
        return 123  # type: ignore[invalid-return-type]

    with pytest.raises(ValidationError) as returned:
        secret_return()
    assert returned.value.errors()[0]["input"] == "<redacted>"
    assert returned.value.location == ("return",)


def test_defaults_fail_at_declaration_and_mutable_defaults_revalidate() -> None:
    with pytest.raises(ValidationError) as invalid:

        @validate_call
        def bad_default(value: int = "bad") -> int:  # type: ignore[assignment]
            return value

    assert invalid.value.location == ("value",)

    @validate_call
    def mutable_default(values: list[int] = []) -> list[int]:  # noqa: B006
        return values

    assert mutable_default() == []
    default = mutable_default.__defaults__[0]
    default.append("bad")
    with pytest.raises(ValidationError) as current:
        mutable_default()
    assert current.value.location == ("values", 0)


def test_immutable_default_is_declaration_trusted_but_explicit_values_validate() -> None:
    @validate_call
    def with_default(value: int = 1) -> int:
        return value

    assert with_default() == 1
    with pytest.raises(ValidationError) as captured:
        with_default("1")  # type: ignore[arg-type]
    assert captured.value.location == ("value",)


def test_introspection_projects_one_immutable_contract_without_artifacts() -> None:
    @validate_call
    def transfer(amount: int, reason: str = "trade") -> bool:
        return bool(amount and reason)

    info = inspect_callable(transfer)

    assert isinstance(info, CallableInfo)
    assert info.signature == inspect.signature(transfer)
    assert info.parameters == (
        ParameterInfo("amount", "POSITIONAL_OR_KEYWORD", PrimitiveSchema("int"), True, False),
        ParameterInfo("reason", "POSITIONAL_OR_KEYWORD", PrimitiveSchema("str"), False, True),
    )
    assert info.return_schema == PrimitiveSchema("bool")
    assert info.is_async is False
    assert not hasattr(info, "validator")
    assert not hasattr(info, "source")
    assert not hasattr(info, "globals")
    with pytest.raises(FrozenInstanceError):
        info.parameters = ()  # type: ignore[misc]
    with pytest.raises(TypeError, match="decorated with validate_call"):
        inspect_callable(lambda: None)

    class HostileCallable:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(name)

        def __call__(self) -> None:
            return None

    with pytest.raises(TypeError, match="decorated with validate_call"):
        inspect_callable(HostileCallable())  # type: ignore[arg-type]


def test_redecoration_is_idempotent() -> None:
    @validate_call
    def identity(value: int) -> int:
        return value

    assert validate_call(identity) is identity


def test_missing_return_annotation_is_rejected() -> None:
    def missing(value: int):
        return value

    with pytest.raises(TypeError, match="return annotation"):
        validate_call(missing)


def test_missing_parameter_annotation_is_rejected() -> None:
    def missing(value) -> int:
        return value

    with pytest.raises(TypeError, match="parameter 'value'.*requires an annotation"):
        validate_call(missing)


def test_unsupported_targets_are_rejected_explicitly() -> None:
    async def coroutine(value: int) -> int:
        return value

    def generator(value: int) -> int:
        yield value  # type: ignore[misc]

    async def async_generator(value: int) -> int:
        yield value  # type: ignore[misc]

    def plain(value: int) -> int:
        return value

    class CallableObject:
        def __call__(self, value: int) -> int:
            return value

    class HostileCallable:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(name)

        def __call__(self, value: int) -> int:
            return value

    targets = (
        (coroutine, "async functions"),
        (generator, "generator functions"),
        (async_generator, "async generator functions"),
        (CallableObject(), "ordinary Python function"),
        (HostileCallable(), "ordinary Python function"),
        (staticmethod(plain), "staticmethod"),
        (classmethod(plain), "classmethod"),
    )
    for target, message in targets:
        with pytest.raises(TypeError, match=message):
            validate_call(target)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "function",
    [
        lambda: exec("def positional(value: int, /) -> int:\n    return value", globals()),
        lambda: exec("def keyword(*, value: int) -> int:\n    return value", globals()),
        lambda: exec("def variadic(*values: int) -> int:\n    return len(values)", globals()),
        lambda: exec("def keywords(**values: int) -> int:\n    return len(values)", globals()),
    ],
)
def test_deferred_parameter_kinds_are_rejected(function: object) -> None:
    function()
    name = {"positional", "keyword", "variadic", "keywords"}.intersection(globals()).pop()
    candidate = globals().pop(name)
    with pytest.raises(TypeError, match="does not yet support"):
        validate_call(candidate)


def test_local_alias_forward_resolution_and_overload_runtime_owner() -> None:
    type Identifier = Annotated[int, Ge(1)]

    @overload
    def normalize(value: int) -> int: ...

    @overload
    def normalize(value: str) -> str: ...

    def normalize(value: int | str) -> int | str:
        return value

    validated = validate_call(normalize)

    @validate_call
    def local(value: Identifier) -> Identifier:
        return value

    assert validated(1) == 1
    assert validated("one") == "one"
    assert local(1) == 1
    with pytest.raises(ValidationError):
        local(0)


def test_lost_function_local_annotation_name_fails_explicitly() -> None:
    def make_function():
        type Local = int

        def local(value: Local) -> Local:
            return value

        return local

    local = make_function()
    with pytest.raises(TypeError, match="cannot be resolved"):
        validate_call(local)


def test_generic_functions_are_rejected_without_runtime_inference() -> None:
    def identity[T](value: T) -> T:
        return value

    with pytest.raises(TypeError, match="generic functions"):
        validate_call(identity)


def test_reentrant_recursive_and_concurrent_calls_need_no_shared_state() -> None:
    @validate_call
    def inner(value: int) -> int:
        return value + 1

    @validate_call
    def outer(value: int) -> int:
        return inner(value)

    @validate_call
    def factorial(value: int) -> int:
        return 1 if value < 2 else value * factorial(value - 1)

    assert outer(1) == 2
    assert factorial(6) == 720
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(outer, range(20))) == list(range(1, 21))


def test_hostile_metadata_and_function_names_remain_inert() -> None:
    marker = '"\nraise RuntimeError("injected")\n#'
    type Marked = Annotated[int, Alias(marker)]

    def original(value: Marked = 1) -> Marked:
        return value

    original.__name__ = marker
    original.__qualname__ = marker
    wrapped = validate_call(original)

    assert wrapped(1) == 1
    assert wrapped.__name__ == marker
    with pytest.raises(ValidationError):
        wrapped("bad")  # type: ignore[arg-type]


def test_local_wrapper_and_contract_collect_without_a_global_registry() -> None:
    def make() -> weakref.ReferenceType[object]:
        @validate_call
        def local(value: int) -> int:
            return value

        return weakref.ref(local)

    reference = make()
    gc.collect()
    assert reference() is None


def test_generated_warm_path_has_no_generic_binding_or_schema_interpreter() -> None:
    @validate_call
    def total(left: int, right: int) -> int:
        return left + right

    instructions = tuple(dis.get_instructions(total))
    loaded_names = {
        instruction.argval
        for instruction in instructions
        if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME", "LOAD_ATTR", "LOAD_METHOD"}
    }

    assert "bind" not in loaded_names
    assert "Signature" not in loaded_names
    assert "Parameter" not in loaded_names
    assert "BoundArguments" not in loaded_names
    assert all("schema" not in str(name).lower() for name in loaded_names)
    assert all(instruction.opname != "FOR_ITER" for instruction in instructions)
    assert total(1, 2) == 3


def test_introspection_retains_alias_and_constraint_schema_truth() -> None:
    type Positive = Annotated[int, Alias("positive"), Ge(1)]

    @validate_call
    def positive(value: Positive) -> Positive:
        return value

    info = inspect_callable(positive)
    parameter_schema = info.parameters[0].schema
    assert isinstance(parameter_schema, AliasSchema)
    assert isinstance(parameter_schema.schema, ConstrainedSchema)
    assert info.return_schema == parameter_schema


def test_callable_ir_represents_future_binding_and_receiver_forms() -> None:
    """The single IR can express accepted later binding work without a new owner."""

    def method(self: object, value: int) -> int:
        del self
        return value

    receiver = _CallableParameter(
        "self",
        "POSITIONAL_ONLY",
        None,
        MISSING_DEFAULT,
        False,
        False,
        "receiver",
    )
    variadic = _CallableParameter(
        "values",
        "VAR_KEYWORD",
        PrimitiveSchema("int"),
        MISSING_DEFAULT,
        False,
        False,
        unpack_typed_dict=False,
    )
    contract = _CallableSchema(
        method,
        inspect.signature(method),
        (receiver, variadic),
        PrimitiveSchema("int"),
        False,
        False,
        "instance_method",
    )

    assert contract.parameters[0].role == "receiver"
    assert contract.parameters[1].kind == "VAR_KEYWORD"
    assert contract.callable_kind == "instance_method"
