"""Async execution proof for the canonical Talea callable owner."""

from __future__ import annotations

import asyncio
import dis
import inspect
from dataclasses import FrozenInstanceError, dataclass
from typing import Annotated, NotRequired, TypedDict, Unpack

import pytest
from hypothesis import given, strategies as st

from talea import Representation, Sensitive, Spec, ValidationError, check, validate_call
from talea.introspection import CallableInfo, ParameterInfo, inspect_callable
from talea.schema import PrimitiveSchema


def run[T](awaitable: object) -> T:
    """Run one test coroutine without requiring a pytest event-loop plugin."""

    return asyncio.run(awaitable)  # type: ignore[arg-type, no-any-return]


def test_async_arguments_awaited_return_and_none_are_strict() -> None:
    calls = 0

    @validate_call
    async def increment(value: int) -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return value + 1

    @validate_call
    async def invalid_return(value: int) -> int:
        nonlocal calls
        calls += 1
        return str(value)  # type: ignore[invalid-return-type]

    @validate_call
    async def nothing(valid: bool) -> None:
        if valid:
            return None
        return 1  # type: ignore[invalid-return-type]

    assert run(increment(1)) == 2
    assert calls == 1
    with pytest.raises(ValidationError) as argument:
        run(increment("1"))  # type: ignore[arg-type]
    assert argument.value.location == ("value",)
    assert calls == 1
    with pytest.raises(ValidationError) as returned:
        run(invalid_return(1))
    assert returned.value.location == ("return",)
    assert calls == 2
    assert run(nothing(True)) is None
    with pytest.raises(ValidationError) as returned_none:
        run(nothing(False))
    assert returned_none.value.location == ("return",)


def test_async_validation_runs_on_await_and_native_binding_runs_on_call() -> None:
    calls = 0

    @validate_call
    async def boundary(value: int, /, *, enabled: bool) -> int:
        nonlocal calls
        calls += 1
        return value if enabled else 0

    coroutine = boundary("bad", enabled=True)  # type: ignore[arg-type]
    assert calls == 0
    coroutine.close()
    assert calls == 0

    invalid_shapes = (
        lambda: boundary(enabled=True),
        lambda: boundary(value=1, enabled=True),
        lambda: boundary(1),
        lambda: boundary(1, enabled=True, unknown=False),
    )
    for call in invalid_shapes:
        with pytest.raises(TypeError) as captured:
            call()
        assert type(captured.value) is TypeError
    assert calls == 0


def test_async_complete_parameter_surface_and_defaults() -> None:
    @validate_call
    async def execute(
        identifier: int,
        /,
        value: str = "ready",
        *items: int,
        enabled: bool,
        **metadata: str,
    ) -> tuple[int, str, tuple[int, ...], bool, dict[str, str]]:
        return identifier, value, items, enabled, metadata

    assert inspect.signature(execute) == inspect.signature(execute.__wrapped__)
    assert run(execute(1, enabled=True)) == (1, "ready", (), True, {})
    assert run(execute(1, "go", 2, 3, enabled=False, source="test")) == (
        1,
        "go",
        (2, 3),
        False,
        {"source": "test"},
    )
    with pytest.raises(ValidationError) as positional:
        run(execute("1", enabled=True))  # type: ignore[arg-type]
    assert positional.value.location == ("identifier",)
    with pytest.raises(ValidationError) as items:
        run(execute(1, "go", 2, "bad", enabled=True))  # type: ignore[arg-type]
    assert items.value.location == ("items", 1)
    with pytest.raises(ValidationError) as keyword:
        run(execute(1, enabled=1))  # type: ignore[arg-type]
    assert keyword.value.location == ("enabled",)
    with pytest.raises(ValidationError) as metadata:
        run(execute(1, enabled=True, source=1))  # type: ignore[arg-type]
    assert metadata.value.location == ("metadata", "source")


def test_async_mutable_default_revalidates_when_awaited() -> None:
    @validate_call
    async def collect(values: list[int] = []) -> list[int]:  # noqa: B006
        return values

    assert run(collect()) == []
    default = collect.__defaults__[0]
    default.append("bad")
    with pytest.raises(ValidationError) as captured:
        run(collect())
    assert captured.value.location == ("values", 0)


class AsyncOptions(TypedDict):
    timeout: float
    trace_id: NotRequired[str]


class AsyncSecrets(TypedDict):
    token: Annotated[str, Sensitive()]


def test_async_unpack_typed_dict_uses_canonical_validation() -> None:
    @validate_call
    async def configure(**kwargs: Unpack[AsyncOptions]) -> AsyncOptions:
        return kwargs

    assert run(configure(timeout=1.5)) == {"timeout": 1.5}
    assert run(configure(timeout=1.5, trace_id="abc")) == {"timeout": 1.5, "trace_id": "abc"}
    with pytest.raises(ValidationError) as missing:
        run(configure())
    assert missing.value.location == ("kwargs", "timeout")
    with pytest.raises(ValidationError) as unexpected:
        run(configure(timeout=1.0, unknown=True))  # type: ignore[call-arg]
    assert unexpected.value.location == ("kwargs", "unknown")
    with pytest.raises(ValidationError) as wrong:
        run(configure(timeout=1))  # type: ignore[typeddict-item]
    assert wrong.value.location == ("kwargs", "timeout")


def test_async_structured_values_are_strict_current_state() -> None:
    class Account(Spec):
        entries: list[int]

    @dataclass
    class Order:
        quantity: int

    class Payload(TypedDict):
        quantity: int

    @validate_call
    async def accept(account: Account, order: Order, payload: Payload) -> Account:
        return account

    account = Account(entries=[1])
    order = Order(2)
    payload: Payload = {"quantity": 3}
    assert run(accept(account, order, payload)) is account
    account.entries.append("bad")  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as spec_error:
        run(accept(account, order, payload))
    assert spec_error.value.location == ("account", "entries", 1)
    account = Account(entries=[1])
    order.quantity = "bad"  # type: ignore[assignment]
    with pytest.raises(ValidationError) as dataclass_error:
        run(accept(account, order, payload))
    assert dataclass_error.value.location == ("order", "quantity")
    order.quantity = 2
    payload["quantity"] = "bad"  # type: ignore[typeddict-item]
    with pytest.raises(ValidationError) as typed_dict_error:
        run(accept(account, order, payload))
    assert typed_dict_error.value.location == ("payload", "quantity")


def test_async_representation_stays_internal_and_sensitive_failures_redact() -> None:
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

    type MoneyValue = Annotated[Money, Representation(input=str, load=load, output=str, dump=dump)]
    type Secret = Annotated[str, Sensitive()]

    @validate_call
    async def identity(value: MoneyValue) -> MoneyValue:
        return value

    @validate_call
    async def secret_argument(value: Secret) -> int:
        return len(value)

    @validate_call
    async def secret_return() -> Secret:
        return 123  # type: ignore[invalid-return-type]

    @validate_call
    async def secret_unpack(**kwargs: Unpack[AsyncSecrets]) -> AsyncSecrets:
        return kwargs

    @validate_call
    async def secret_keywords(**kwargs: Secret) -> int:
        return len(kwargs)

    money = Money()
    assert run(identity(money)) is money
    with pytest.raises(ValidationError):
        run(identity("money"))  # type: ignore[arg-type]
    assert loaded == []
    assert dumped == []
    with pytest.raises(ValidationError) as argument:
        run(secret_argument(123))  # type: ignore[arg-type]
    assert argument.value.errors()[0]["input"] == "<redacted>"
    with pytest.raises(ValidationError) as returned:
        run(secret_return())
    assert returned.value.errors()[0]["input"] == "<redacted>"
    with pytest.raises(ValidationError) as unpacked:
        run(secret_unpack(token=123))  # type: ignore[typeddict-item]
    assert unpacked.value.errors()[0]["input"] == "<redacted>"
    with pytest.raises(ValidationError) as keywords:
        run(secret_keywords(**{"secret-key": 123}))  # type: ignore[arg-type]
    assert keywords.value.location == ("kwargs", "<redacted>")
    assert "secret-key" not in str(keywords.value)


def test_async_sensitive_spec_check_arguments_and_returns_are_redacted() -> None:
    secret = "async-callable-secret"

    class Checked(Spec):
        values: list[int]

        @check("values")
        def positive(values: list[int]) -> None:
            if values[0] < 0:
                raise ValueError(secret)

    type SecretChecked = Annotated[Checked, Sensitive()]
    checked = Checked(values=[1])
    checked.values[0] = -1

    @validate_call
    async def accept(value: SecretChecked) -> int:
        return value.values[0]

    @validate_call
    async def produce() -> SecretChecked:
        return checked

    for expected_location, operation in (
        (("value", "values"), lambda: accept(checked)),
        (("return", "values"), produce),
    ):
        with pytest.raises(ValidationError) as raised:
            run(operation())
        assert raised.value.location == expected_location
        assert raised.value.value == "<redacted>"
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert secret not in str(raised.value)
        assert secret not in repr(raised.value.errors())


def test_async_application_exceptions_and_mutation_are_application_owned() -> None:
    secret = "application-secret"
    values = [1]

    @validate_call
    async def mutate(items: list[int]) -> int:
        items.append(2)
        await asyncio.sleep(0)
        raise RuntimeError(secret)

    with pytest.raises(RuntimeError) as captured:
        run(mutate(values))
    assert captured.value.args == (secret,)
    assert values == [1, 2]


def test_async_cancellation_is_transparent_and_exactly_once() -> None:
    async def scenario() -> None:
        starts = 0
        finishes = 0
        entered = asyncio.Event()

        @validate_call
        async def wait(value: int) -> int:
            nonlocal starts, finishes
            starts += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finishes += 1
            return value

        before = asyncio.create_task(wait(1))
        before.cancel()
        with pytest.raises(asyncio.CancelledError):
            await before
        assert starts == 0

        during = asyncio.create_task(wait(2))
        await entered.wait()
        during.cancel()
        with pytest.raises(asyncio.CancelledError):
            await during
        assert starts == 1
        assert finishes == 1

        entered.clear()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.001):
                await wait(3)
        assert starts == 2
        assert finishes == 2

    run(scenario())
    assert issubclass(asyncio.CancelledError, BaseException)


def test_async_create_task_gather_taskgroup_and_exception_group() -> None:
    @validate_call
    async def increment(value: int) -> int:
        await asyncio.sleep(0)
        return value + 1

    @validate_call
    async def grouped_failure(value: int) -> int:
        raise ExceptionGroup("application", [LookupError(value), RuntimeError(value)])

    async def scenario() -> None:
        assert await asyncio.create_task(increment(1)) == 2
        assert await asyncio.gather(*(increment(value) for value in range(10))) == list(range(1, 11))
        gathered = await asyncio.gather(increment(1), increment("bad"), return_exceptions=True)  # type: ignore[arg-type]
        assert gathered[0] == 2
        assert isinstance(gathered[1], ValidationError)

        tasks: list[asyncio.Task[int]] = []
        async with asyncio.TaskGroup() as group:
            tasks.extend(group.create_task(increment(value)) for value in range(4))
        assert [task.result() for task in tasks] == [1, 2, 3, 4]

        with pytest.raises(ExceptionGroup) as task_group_failure:
            async with asyncio.TaskGroup() as group:
                group.create_task(increment("bad"))  # type: ignore[arg-type]
                group.create_task(increment(1))
        assert any(isinstance(error, ValidationError) for error in task_group_failure.value.exceptions)

        with pytest.raises(ExceptionGroup) as captured:
            await grouped_failure(1)
        assert [type(error) for error in captured.value.exceptions] == [LookupError, RuntimeError]

    run(scenario())


def test_async_concurrency_reentrancy_recursion_and_sync_composition() -> None:
    @validate_call
    def sync_increment(value: int) -> int:
        return value + 1

    @validate_call
    async def increment(value: int) -> int:
        await asyncio.sleep(0)
        return sync_increment(value)

    @validate_call
    async def twice(value: int) -> int:
        return await increment(await increment(value))

    @validate_call
    async def factorial(value: int) -> int:
        return 1 if value < 2 else value * await factorial(value - 1)

    @validate_call
    def call_async(value: int) -> int:
        return asyncio.run(increment(value))

    async def scenario() -> None:
        assert await twice(1) == 3
        assert await factorial(6) == 720
        assert await asyncio.gather(*(increment(value) for value in range(50))) == list(range(1, 51))

    run(scenario())
    assert call_async(4) == 5


def test_async_methods_descriptors_inheritance_override_and_super() -> None:
    class Base:
        @validate_call
        async def value(self, amount: int) -> int:
            return amount + 1

        @validate_call
        @classmethod
        async def identify(cls, amount: int) -> tuple[str, int]:
            return cls.__name__, amount

        @validate_call
        @staticmethod
        async def normalize(amount: int) -> int:
            return amount

        @validate_call
        async def invalid_return(self, amount: int) -> int:
            del amount
            return "bad"  # type: ignore[invalid-return-type]

    class Inherited(Base):
        pass

    class Override(Base):
        @validate_call
        async def value(self, amount: int) -> int:
            return await super().value(amount) + 1

    assert run(Base().value(1)) == 2
    assert run(Inherited().value(1)) == 2
    assert run(Override().value(1)) == 3
    assert run(Base.identify(2)) == ("Base", 2)
    assert run(Inherited.identify(3)) == ("Inherited", 3)
    assert run(Base.normalize(4)) == 4
    assert inspect.iscoroutinefunction(Base.value)
    assert inspect.iscoroutinefunction(Base().value)
    assert inspect.iscoroutinefunction(Base.identify)
    assert inspect.iscoroutinefunction(Base.normalize)
    assert inspect_callable(Base.value).callable_kind == "instance_method"
    assert inspect_callable(Base.value).parameters[0].receiver is True
    assert inspect_callable(Base.identify).callable_kind == "class_method"
    assert inspect_callable(Base.identify).parameters[0].receiver is True
    assert inspect_callable(Base.normalize).callable_kind == "static_method"
    with pytest.raises(ValidationError):
        run(Base().value("bad"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        run(Base.identify("bad"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        run(Base.normalize("bad"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as returned:
        run(Base().invalid_return(1))
    assert returned.value.location == ("return",)


def test_async_descriptor_order_policy_matches_sync() -> None:
    class Unsupported:
        @classmethod
        @validate_call
        async def class_method(cls, value: int) -> int:
            return value

        @staticmethod
        @validate_call
        async def static_method(value: int) -> int:
            return value

    with pytest.raises(TypeError, match="outermost"):
        Unsupported.class_method(1)
    with pytest.raises(TypeError, match="outermost"):
        Unsupported.static_method(1)


def test_async_signature_metadata_wrapped_and_introspection_are_preserved() -> None:
    async def original(value: int, /, *, enabled: bool = True) -> int:
        """Return a validated async value."""

        return value if enabled else 0

    wrapped = validate_call(original)
    assert inspect.iscoroutinefunction(wrapped)
    assert inspect.signature(wrapped) == inspect.signature(original)
    assert inspect.signature(wrapped, follow_wrapped=False) == inspect.signature(original)
    assert wrapped.__name__ == original.__name__
    assert wrapped.__qualname__ == original.__qualname__
    assert wrapped.__doc__ == original.__doc__
    assert wrapped.__module__ == original.__module__
    assert wrapped.__annotations__ == original.__annotations__
    assert wrapped.__wrapped__ is original

    info = inspect_callable(wrapped)
    assert info == CallableInfo(
        inspect.signature(original),
        (
            ParameterInfo("value", "POSITIONAL_ONLY", PrimitiveSchema("int"), True, False),
            ParameterInfo("enabled", "KEYWORD_ONLY", PrimitiveSchema("bool"), False, True),
        ),
        PrimitiveSchema("int"),
        True,
    )
    assert not hasattr(info, "compiler")
    assert not hasattr(info, "function")
    assert not hasattr(info, "globals")
    with pytest.raises(FrozenInstanceError):
        info.is_async = False  # type: ignore[misc]


def test_async_generated_code_has_direct_await_and_no_runtime_dispatch() -> None:
    @validate_call
    async def boundary(head: int, /, *items: int, flag: bool, **values: int) -> int:
        return head + sum(items) + flag + sum(values.values())

    instructions = tuple(dis.get_instructions(boundary))
    loaded = {
        instruction.argval
        for instruction in instructions
        if instruction.opname in {"LOAD_GLOBAL", "LOAD_ATTR", "LOAD_METHOD"}
    }
    assert "bind" not in loaded
    assert "Signature" not in loaded
    assert "Parameter" not in loaded
    assert "BoundArguments" not in loaded
    assert "iscoroutinefunction" not in loaded
    assert "isawaitable" not in loaded
    assert "lock" not in {str(name).lower() for name in loaded}
    assert all("schema" not in str(name).lower() for name in loaded)
    assert sum(instruction.opname == "FOR_ITER" for instruction in instructions) == 2
    assert any(instruction.opname == "GET_AWAITABLE" for instruction in instructions)
    assert any(instruction.opname == "SEND" for instruction in instructions)


@given(
    st.lists(st.integers(), max_size=30),
    st.dictionaries(st.text(min_size=1, max_size=12), st.integers(), max_size=30),
)
def test_async_variadic_property_matches_python_values(args: list[int], kwargs: dict[str, int]) -> None:
    @validate_call
    async def boundary(*values: int, **metadata: int) -> tuple[tuple[int, ...], dict[str, int]]:
        return values, metadata

    assert run(boundary(*args, **kwargs)) == (tuple(args), kwargs)


def test_async_hostile_metadata_and_function_name_remain_inert() -> None:
    namespace: dict[str, object] = {"__name__": __name__}
    exec("async def operation_π(value: int) -> int:\n    return value", namespace)
    original = namespace["operation_π"]
    original.__doc__ = "hostile ' metadata \\ source"  # type: ignore[attr-defined]
    wrapped = validate_call(original)  # type: ignore[arg-type]
    assert run(wrapped(1)) == 1  # type: ignore[call-arg]
    assert wrapped.__name__ == "operation_π"  # type: ignore[attr-defined]
    assert wrapped.__doc__ == "hostile ' metadata \\ source"  # type: ignore[attr-defined]


def test_generators_async_generators_and_callable_instances_remain_rejected() -> None:
    def generator(value: int) -> int:
        yield value  # type: ignore[misc]

    async def async_generator(value: int) -> int:
        yield value  # type: ignore[misc]

    class CallableObject:
        async def __call__(self, value: int) -> int:
            return value

    with pytest.raises(TypeError, match="generator functions"):
        validate_call(generator)
    with pytest.raises(TypeError, match="async generator functions"):
        validate_call(async_generator)
    with pytest.raises(TypeError, match="ordinary Python function"):
        validate_call(CallableObject())  # type: ignore[arg-type]
