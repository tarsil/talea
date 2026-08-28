"""Executable custom-domain Representation examples."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, TypedDict, cast

from talea import Contract, Representation, Sensitive, Spec
from talea.serialization import SerializationError


class Currency(StrEnum):
    """Currencies accepted by the payment boundary."""

    CHF = "CHF"
    EUR = "EUR"


class Money:
    """Application-owned financial value with normalized minor precision."""

    __slots__ = ("amount", "currency")

    def __init__(self, amount: Decimal, currency: Currency) -> None:
        self.amount = amount.quantize(Decimal("0.01"))
        self.currency = currency

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and (self.amount, self.currency) == (other.amount, other.currency)


class MoneyInput(TypedDict):
    """Accepted request representation."""

    amount: str
    currency: Currency


class MoneyOutput(TypedDict):
    """Canonical response representation."""

    amount: str
    currency: Currency


def load_money(value: MoneyInput) -> Money:
    """Construct the domain value from accepted boundary data."""

    return Money(Decimal(value["amount"]), value["currency"])


def dump_money(value: Money) -> MoneyOutput:
    """Produce the canonical structured response value."""

    return {"amount": str(value.amount), "currency": value.currency}


type MoneyValue = Annotated[
    Money,
    Representation(
        input=MoneyInput,
        load=load_money,
        output=MoneyOutput,
        dump=dump_money,
    ),
]


class Payment(Spec):
    """Payment with represented values at scalar and container positions."""

    order_id: str
    amount: MoneyValue
    fees: list[MoneyValue]


payment = Payment.from_json(
    '{"order_id":"ord-7","amount":{"amount":"12.500","currency":"CHF"},"fees":[{"amount":"0.20","currency":"CHF"}]}'
)
assert payment.amount == Money(Decimal("12.50"), Currency.CHF)
assert payment.to_dict() == {
    "order_id": "ord-7",
    "amount": {"amount": "12.50", "currency": Currency.CHF},
    "fees": [{"amount": "0.20", "currency": Currency.CHF}],
}
assert payment.to_json() == (
    '{"order_id":"ord-7","amount":{"amount":"12.50","currency":"CHF"},"fees":[{"amount":"0.20","currency":"CHF"}]}'
)
assert payment.to_dict(include={"amount": {"amount": True}}) == {"amount": {"amount": "12.50"}}


@dataclass(slots=True)
class LedgerLine:
    """Stdlib dataclass containing the same reusable contract."""

    amount: MoneyValue


class Settlement(TypedDict):
    """TypedDict containing the same reusable contract."""

    amount: MoneyValue


line = LedgerLine(Money(Decimal("3"), Currency.EUR))
assert Contract(LedgerLine).to_python(line) == {"amount": {"amount": "3.00", "currency": Currency.EUR}}
settlement: Settlement = {"amount": Money(Decimal("4"), Currency.CHF)}
assert Contract[Settlement](Settlement).to_json(settlement) == ('{"amount":{"amount":"4.00","currency":"CHF"}}')


class Ulid:
    """Small stand-in for an immutable third-party identifier type."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text.upper()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Ulid) and self.text == other.text


def load_ulid(value: str) -> Ulid:
    """Load a normalized identifier."""

    return Ulid(value)


def dump_ulid(value: Ulid) -> str:
    """Return canonical identifier text."""

    return value.text


type UlidValue = Annotated[
    Ulid,
    Representation(input=str, load=load_ulid, output=str, dump=dump_ulid),
]
ulids = Contract[list[Ulid]](list[UlidValue])
identifier_values = ulids.from_json('["01jabc"]')
assert identifier_values == [Ulid("01JABC")]
assert ulids.to_json(identifier_values) == '["01JABC"]'


schema_calls: list[str] = []


def counted_load(value: int) -> Money:
    schema_calls.append("load")
    return Money(Decimal(value), Currency.CHF)


def counted_dump(value: Money) -> MoneyOutput:
    schema_calls.append("dump")
    return dump_money(value)


type AsymmetricMoney = Annotated[
    Money,
    Representation(input=int, load=counted_load, output=MoneyOutput, dump=counted_dump),
]
asymmetric = Contract[Money](AsymmetricMoney)
input_schema = asymmetric.json_schema(mode="input")
output_schema = asymmetric.openapi_schema(mode="output")
input_definitions = cast(dict[str, object], input_schema["$defs"])
assert input_definitions["AsymmetricMoney"] == {"type": "integer"}
output_components = cast(dict[str, object], output_schema["components"])
output_definitions = cast(dict[str, object], output_components["schemas"])
assert "MoneyOutput" in output_definitions
assert schema_calls == []


type InputOnlyMoney = Annotated[
    Money,
    Representation(input=MoneyInput, load=load_money),
]
type OutputOnlyMoney = Annotated[
    Money,
    Representation(output=MoneyOutput, dump=dump_money),
]
assert Contract[Money](InputOnlyMoney).from_python({"amount": "1", "currency": Currency.CHF}) == Money(
    Decimal("1"), Currency.CHF
)
assert Contract[Money](OutputOnlyMoney).to_python(Money(Decimal("2"), Currency.EUR)) == {
    "amount": "2.00",
    "currency": Currency.EUR,
}
try:
    Contract[Money](InputOnlyMoney).to_python(Money(Decimal("1"), Currency.CHF))
except SerializationError as error:
    assert "no output direction" in str(error)
else:
    raise AssertionError("input-only Representation unexpectedly supported output")


class SecretToken:
    """Opaque application secret."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


def reject_secret(value: SecretToken) -> str:
    """Demonstrate a hostile application-owned callback failure."""

    raise RuntimeError(f"unsafe callback detail: {value.value}")


type SecretValue = Annotated[
    SecretToken,
    Representation(output=str, dump=reject_secret),
    Sensitive(),
]
try:
    Contract[SecretToken](SecretValue).to_python(SecretToken("token-123"))
except SerializationError as error:
    assert error.__cause__ is None
    assert "token-123" not in str(error)
else:
    raise AssertionError("failing secret dumper unexpectedly succeeded")
