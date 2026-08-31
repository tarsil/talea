"""Python descriptor integration for strict synchronous callable boundaries."""

from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import FunctionType, MethodType
from typing import TypedDict

import pytest

from talea import ValidationError, validate_call
from talea.introspection import inspect_callable


def test_instance_method_uses_native_descriptor_binding_and_receiver_exemption() -> None:
    class Ledger:
        """A service with one validated method."""

        @validate_call
        def transfer(self, amount: int, /, *, urgent: bool = False) -> int:
            """Transfer one amount."""

            return amount + urgent

    ledger = Ledger()
    assert ledger.transfer(2) == 2
    assert ledger.transfer(2, urgent=True) == 3
    assert type(Ledger.__dict__["transfer"]) is FunctionType
    assert type(ledger.transfer) is MethodType
    assert inspect.signature(Ledger.transfer) == inspect.Signature.from_callable(Ledger.transfer.__wrapped__)
    assert tuple(inspect.signature(ledger.transfer).parameters) == ("amount", "urgent")
    assert Ledger.transfer.__name__ == "transfer"
    assert Ledger.transfer.__doc__ == "Transfer one amount."
    assert Ledger.transfer.__annotations__ == Ledger.transfer.__wrapped__.__annotations__
    assert inspect_callable(Ledger.transfer).callable_kind == "instance_method"
    assert inspect_callable(ledger.transfer).parameters[0].receiver is True

    with pytest.raises(ValidationError) as captured:
        ledger.transfer("bad")  # type: ignore[arg-type]
    assert captured.value.location == ("amount",)


def test_receiver_exemption_does_not_accept_an_ordinary_unannotated_parameter() -> None:
    def ordinary(self, value: int) -> int:
        del self
        return value

    with pytest.raises(TypeError, match="parameter 'self'.*requires an annotation"):
        validate_call(ordinary)


def test_method_inheritance_override_and_super_follow_python_attribute_resolution() -> None:
    class Base:
        @validate_call
        def value(self, amount: int) -> int:
            return amount + 1

    class Inherited(Base):
        pass

    class ValidatedOverride(Base):
        @validate_call
        def value(self, amount: int) -> int:
            return super().value(amount) + 1

    class PlainOverride(Base):
        def value(self, amount: int) -> str:
            del amount
            return "plain"

    assert Inherited().value(1) == 2
    assert ValidatedOverride().value(1) == 3
    assert PlainOverride().value("not validated") == "plain"  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Inherited().value("bad")  # type: ignore[arg-type]


def test_classmethod_and_staticmethod_require_talea_as_outer_decorator() -> None:
    class Service:
        @validate_call
        @classmethod
        def identify(cls, value: int) -> tuple[str, int]:
            return cls.__name__, value

        @validate_call
        @staticmethod
        def normalize(value: int) -> int:
            return value

    class Child(Service):
        pass

    assert Service.identify(1) == ("Service", 1)
    assert Child.identify(2) == ("Child", 2)
    assert Service.normalize(3) == 3
    assert type(Service.__dict__["identify"]) is classmethod
    assert type(Service.__dict__["normalize"]) is staticmethod
    assert inspect_callable(Service.identify).callable_kind == "class_method"
    assert inspect_callable(Service.__dict__["identify"]).callable_kind == "class_method"
    assert inspect_callable(Service.identify).parameters[0].receiver is True
    assert inspect_callable(Service.normalize).callable_kind == "static_method"
    assert inspect_callable(Service.__dict__["normalize"]).callable_kind == "static_method"
    assert all(not parameter.receiver for parameter in inspect_callable(Service.normalize).parameters)
    assert tuple(inspect.signature(Service.identify).parameters) == ("value",)
    assert tuple(inspect.signature(Service.normalize).parameters) == ("value",)

    with pytest.raises(ValidationError):
        Service.identify("bad")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Service.normalize("bad")  # type: ignore[arg-type]

    class UnsupportedOrder:
        @classmethod
        @validate_call
        def class_method(cls, value: int) -> int:
            return value

        @staticmethod
        @validate_call
        def static_method(value: int) -> int:
            return value

    with pytest.raises(TypeError, match="outermost"):
        UnsupportedOrder.class_method(1)
    with pytest.raises(TypeError, match="outermost"):
        UnsupportedOrder.static_method(1)


def test_method_candidate_redecoration_is_idempotent_and_variadic_receiver_is_rejected() -> None:
    class Service:
        @validate_call
        @validate_call
        def execute(self, value: int) -> int:
            return value

    assert Service().execute(1) == 1

    with pytest.raises(TypeError, match="receiver 'self' must be positional"):

        class InvalidReceiver:
            @validate_call
            def execute(*self: object) -> int:
                return len(self)

    with pytest.raises(TypeError, match="receiver 'self' cannot have a default"):

        class DefaultReceiver:
            @validate_call
            def execute(self: object = None) -> int:
                del self
                return 1


def test_methods_validate_exactly_once_and_preserve_application_exceptions() -> None:
    calls = 0

    class Service:
        @validate_call
        def execute(self, value: int) -> int:
            nonlocal calls
            calls += 1
            return value

        @validate_call
        def invalid_return(self, value: int) -> int:
            nonlocal calls
            calls += 1
            return "bad"  # type: ignore[return-value]

        @validate_call
        def application_failure(self, value: int) -> int:
            nonlocal calls
            calls += 1
            raise LookupError(value)

    service = Service()
    with pytest.raises(ValidationError):
        service.execute("bad")  # type: ignore[arg-type]
    assert calls == 0
    assert service.execute(1) == 1
    assert calls == 1
    with pytest.raises(ValidationError) as returned:
        service.invalid_return(1)
    assert returned.value.location == ("return",)
    assert calls == 2
    with pytest.raises(LookupError, match="1"):
        service.application_failure(1)
    assert calls == 3


def test_method_reentrancy_recursion_and_concurrency_have_no_shared_state() -> None:
    class Calculator:
        @validate_call
        def increment(self, value: int) -> int:
            return value + 1

        @validate_call
        def twice(self, value: int) -> int:
            return self.increment(self.increment(value))

        @validate_call
        def factorial(self, value: int) -> int:
            return 1 if value < 2 else value * self.factorial(value - 1)

    calculator = Calculator()
    assert calculator.twice(1) == 3
    assert calculator.factorial(6) == 720
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(calculator.increment, range(20))) == list(range(1, 21))


def test_method_composes_with_dataclass_and_typed_dict_contracts() -> None:
    class Payload(TypedDict):
        value: int

    @dataclass(kw_only=True)
    class Handler:
        offset: int

        @validate_call
        def handle(self, payload: Payload, *, enabled: bool = True) -> int:
            return payload["value"] + self.offset if enabled else self.offset

    handler = Handler(offset=2)
    assert handler.handle({"value": 1}) == 3
    with pytest.raises(ValidationError) as captured:
        handler.handle({"value": "bad"})  # type: ignore[typeddict-item]
    assert captured.value.location == ("payload", "value")


def test_unsupported_method_execution_forms_remain_rejected() -> None:
    with pytest.raises(TypeError, match="async functions"):

        class AsyncService:
            @validate_call
            async def execute(self, value: int) -> int:
                return value

    with pytest.raises(TypeError, match="generator functions"):

        class GeneratorService:
            @validate_call
            def execute(self, value: int) -> int:
                yield value  # type: ignore[misc]

    class CallableObject:
        def __call__(self, value: int) -> int:
            return value

    with pytest.raises(TypeError, match="ordinary Python function"):
        validate_call(CallableObject())  # type: ignore[arg-type]
