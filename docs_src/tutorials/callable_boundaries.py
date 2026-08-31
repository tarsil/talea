"""A strict payment-service boundary over already-constructed Python values."""

import inspect
from typing import Annotated, Literal

from talea import Ge, MinLength, Sensitive, Spec, ValidationError, validate_call
from talea.introspection import inspect_callable

type PositiveCents = Annotated[int, Ge(1)]
type Reference = Annotated[str, MinLength(8)]


class PaymentRequest(Spec):
    account_id: int
    merchant: str
    amount_cents: PositiveCents
    reference: Reference


class PaymentReceipt(Spec):
    payment_id: str
    status: Literal["authorized"]
    amount_cents: PositiveCents


class AuthorizationDeclined(RuntimeError):
    """Represent an application decision rather than a contract failure."""


@validate_call
def authorize_payment(request: PaymentRequest, risk_score: int = 0) -> PaymentReceipt:
    """Authorize one already-parsed request and enforce its return contract."""

    if risk_score > 90:
        raise AuthorizationDeclined(request.reference)
    return PaymentReceipt(
        payment_id="pay_01JABCDE",
        status="authorized",
        amount_cents=request.amount_cents,
    )


request = PaymentRequest.from_json(
    '{"account_id":7,"merchant":"Analytical Engines","amount_cents":1250,"reference":"invoice-1843"}'
)
receipt = authorize_payment(request)
assert receipt.amount_cents == 1250
assert inspect.unwrap(authorize_payment).__name__ == "authorize_payment"

info = inspect_callable(authorize_payment)
assert tuple(parameter.name for parameter in info.parameters) == ("request", "risk_score")
assert info.parameters[0].required is True
assert info.parameters[1].has_default is True
assert info.is_async is False

# Python binding owns invalid call shapes.
try:
    authorize_payment()  # ty: ignore[missing-argument]
except TypeError as error:
    assert type(error) is TypeError
else:
    raise AssertionError("missing required arguments must fail")

# Talea validation owns valid call shapes containing invalid values.
try:
    authorize_payment({"amount_cents": 1250})  # ty: ignore[invalid-argument-type]
except ValidationError as error:
    assert error.location == ("request",)
else:
    raise AssertionError("strict callable boundaries must not parse mappings")

# Application exceptions cross the boundary unchanged.
try:
    authorize_payment(request, risk_score=95)
except AuthorizationDeclined as error:
    assert error.args == ("invoice-1843",)
else:
    raise AssertionError("application rejection must propagate")


@validate_call
def broken_gateway(request: PaymentRequest) -> PaymentReceipt:
    """Model a dependency returning a value outside its declared contract."""

    del request
    return "authorized"  # ty: ignore[invalid-return-type]


try:
    broken_gateway(request)
except ValidationError as error:
    assert error.location == ("return",)
else:
    raise AssertionError("invalid returns must not escape")


type SecretToken = Annotated[str, Sensitive()]


@validate_call
def token_length(token: SecretToken) -> int:
    return len(token)


try:
    token_length(123)  # ty: ignore[invalid-argument-type]
except ValidationError as error:
    assert error.errors()[0]["input"] == "<redacted>"
else:
    raise AssertionError("sensitive failure must be redacted")
