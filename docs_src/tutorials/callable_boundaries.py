"""Payment services exercising complete sync and async callable boundaries."""

import asyncio
import inspect
from dataclasses import dataclass
from typing import Annotated, Literal, NotRequired, TypedDict, Unpack

from talea import Ge, MinLength, Sensitive, Spec, ValidationError, validate_call
from talea.introspection import inspect_callable

type PositiveCents = Annotated[int, Ge(1)]
type Reference = Annotated[str, MinLength(8)]
type Secret = Annotated[str, Sensitive()]


class ExecutionOptions(TypedDict):
    """Strict keyword structure accepted by the execution operation."""

    timeout: float
    trace_id: NotRequired[str]
    authorization: NotRequired[Secret]


class PaymentReceipt(Spec):
    payment_id: str
    status: Literal["authorized", "simulated"]
    amount_cents: PositiveCents


class AuthorizationDeclined(RuntimeError):
    """Represent an application decision rather than a contract failure."""


@dataclass(frozen=True, slots=True)
class PaymentService:
    """Execute already-decoded, strictly typed payment commands."""

    gateway: str

    @validate_call
    @classmethod
    def connection_name(cls, gateway: str) -> str:
        """Build a class-qualified gateway name through a classmethod boundary."""

        return f"{cls.__name__}:{gateway}"

    @validate_call
    @staticmethod
    def normalize_reference(reference: Reference) -> Reference:
        """Normalize a reference without receiving an instance or class."""

        return reference.upper()

    @validate_call
    def execute(
        self,
        account_id: int,
        /,
        amount_cents: PositiveCents,
        *adjustments: int,
        dry_run: bool = False,
        **options: Unpack[ExecutionOptions],
    ) -> PaymentReceipt:
        """Execute a payment using every significant Python binding form."""

        del account_id, options
        settled = amount_cents + sum(adjustments)
        if settled <= 0:
            raise AuthorizationDeclined("non-positive settlement")
        return PaymentReceipt(
            payment_id=f"{self.gateway}-01JABCDE",
            status="simulated" if dry_run else "authorized",
            amount_cents=settled,
        )

    @validate_call
    def invalid_receipt(self, account_id: int, /) -> PaymentReceipt:
        """Model a dependency returning an invalid application value."""

        del account_id
        return "authorized"  # ty: ignore[invalid-return-type]


@dataclass(slots=True)
class AsyncAuthorizationService:
    """Authorize existing payment values through ordinary coroutine semantics."""

    entered: asyncio.Event
    cleanup_count: int = 0

    @validate_call
    async def authorize(
        self,
        receipt: PaymentReceipt,
        /,
        *,
        capture: bool = False,
        **options: Unpack[ExecutionOptions],
    ) -> PaymentReceipt:
        """Validate before I/O and validate the awaited service result."""

        del capture, options
        await asyncio.sleep(0)
        return receipt

    @validate_call
    async def decline(self, payment_id: str) -> PaymentReceipt:
        """Preserve an application-owned domain exception."""

        raise AuthorizationDeclined(payment_id)

    @validate_call
    async def invalid_receipt(self, payment_id: str) -> PaymentReceipt:
        """Model an async dependency returning invalid data."""

        del payment_id
        return "authorized"  # ty: ignore[invalid-return-type]

    @validate_call
    async def wait_for_settlement(self, receipt: PaymentReceipt) -> PaymentReceipt:
        """Expose normal cancellation and cleanup behavior."""

        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleanup_count += 1
        return receipt


assert PaymentService.connection_name("gateway") == "PaymentService:gateway"
service = PaymentService("gateway")
reference = PaymentService.normalize_reference("invoice-1843")
assert reference == "INVOICE-1843"

receipt = service.execute(
    7,
    1250,
    -50,
    dry_run=True,
    timeout=1.5,
    trace_id="trace-7",
)
assert receipt.status == "simulated"
assert receipt.amount_cents == 1200

# Python binds the positional-only identifier before Talea validates values.
try:
    service.execute(account_id=7, amount_cents=1250, timeout=1.5)  # ty: ignore[missing-argument]
except TypeError as error:
    assert type(error) is TypeError
else:
    raise AssertionError("a positional-only identifier passed by keyword must fail")

# A required Unpack key is structure validation, not a named-parameter bind.
try:
    service.execute(7, 1250)  # ty: ignore[missing-argument]
except ValidationError as error:
    assert error.location == ("options", "timeout")
else:
    raise AssertionError("a missing TypedDict option must fail")

# Valid call shape plus an invalid value is a Talea ValidationError.
try:
    service.execute(7, "1250", timeout=1.5)  # ty: ignore[invalid-argument-type]
except ValidationError as error:
    assert error.location == ("amount_cents",)
else:
    raise AssertionError("strict callable boundaries must not coerce values")

# Sensitive TypedDict fields redact Talea-owned evidence.
try:
    service.execute(7, 1250, timeout=1.5, authorization=123)  # ty: ignore[invalid-argument-type]
except ValidationError as error:
    assert error.location == ("options", "authorization")
    assert error.errors()[0]["input"] == "<redacted>"
else:
    raise AssertionError("sensitive keyword failure must be redacted")

# The application body still owns domain exceptions.
try:
    service.execute(7, 1, -2, timeout=1.5)
except AuthorizationDeclined as error:
    assert error.args == ("non-positive settlement",)
else:
    raise AssertionError("application rejection must propagate")

# Return validation runs after exactly one successful application call.
try:
    service.invalid_receipt(7)
except ValidationError as error:
    assert error.location == ("return",)
else:
    raise AssertionError("invalid returns must not escape")

unbound_signature = inspect.signature(PaymentService.execute)
bound_signature = inspect.signature(service.execute)
assert "self" in unbound_signature.parameters
assert "self" not in bound_signature.parameters
assert inspect.unwrap(PaymentService.execute).__name__ == "execute"

method_info = inspect_callable(service.execute)
assert method_info.callable_kind == "instance_method"
assert method_info.parameters[0].receiver is True
assert method_info.parameters[3].variadic_semantics == "items"
assert method_info.parameters[-1].variadic_semantics == "unpack_typed_dict"
assert inspect_callable(PaymentService.connection_name).callable_kind == "class_method"
assert inspect_callable(PaymentService.normalize_reference).callable_kind == "static_method"


async def exercise_async_boundaries() -> None:
    """Exercise awaiting, task composition, failures, and cancellation."""

    entered = asyncio.Event()
    authorizer = AsyncAuthorizationService(entered)
    async_info = inspect_callable(authorizer.authorize)
    assert async_info.is_async is True
    assert async_info.callable_kind == "instance_method"
    assert inspect.iscoroutinefunction(authorizer.authorize)
    assert inspect.unwrap(AsyncAuthorizationService.authorize).__name__ == "authorize"

    authorized, repeated = await asyncio.gather(
        authorizer.authorize(receipt, capture=True, timeout=1.5),
        authorizer.authorize(receipt, timeout=2.0, trace_id="trace-8"),
    )
    assert authorized is receipt
    assert repeated is receipt

    # Value validation begins when the normal wrapper coroutine is awaited.
    invalid = authorizer.authorize("not-a-receipt", timeout=1.5)  # ty: ignore[invalid-argument-type]
    try:
        await invalid
    except ValidationError as error:
        assert error.location == ("receipt",)
    else:
        raise AssertionError("an invalid async argument must fail before application I/O")

    try:
        await authorizer.invalid_receipt("payment-1")
    except ValidationError as error:
        assert error.location == ("return",)
    else:
        raise AssertionError("an invalid awaited result must not escape")

    try:
        await authorizer.decline("payment-1")
    except AuthorizationDeclined as error:
        assert error.args == ("payment-1",)
    else:
        raise AssertionError("application exceptions must propagate unchanged")

    task = asyncio.create_task(authorizer.wait_for_settlement(receipt))
    await entered.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancellation must remain visible to the caller")
    assert authorizer.cleanup_count == 1


asyncio.run(exercise_async_boundaries())
