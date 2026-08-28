"""A realistic trading boundary without pretending validation is business logic."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, cast
from uuid import UUID

from talea import Alias, Gt, MaxLength, MinLength, Spec, ValidationError, check


class Currency(StrEnum):
    CHF = "CHF"
    EUR = "EUR"
    USD = "USD"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Instrument(Spec):
    isin: Annotated[str, MinLength(12), MaxLength(12)]
    symbol: Annotated[str, MinLength(1), MaxLength(16)]
    settlement_currency: Annotated[Currency, Alias("settlementCurrency")]


class Money(Spec):
    amount: Annotated[Decimal, Gt(Decimal("0"))]
    currency: Currency


class Order(Spec):
    order_id: Annotated[UUID, Alias("orderId")]
    instrument: Instrument
    side: Side
    quantity: Annotated[Decimal, Gt(Decimal("0"))]
    limit_price: Annotated[Money, Alias("limitPrice")]
    submitted_at: Annotated[datetime, Alias("submittedAt")]
    time_in_force: Annotated[Literal["day", "gtc"], Alias("timeInForce")] = "day"

    @check("limit_price", "instrument")
    def currencies_match(limit_price: Money, instrument: Instrument) -> None:
        if limit_price.currency is not instrument.settlement_currency:
            raise ValueError("limit currency differs from settlement currency")


class Trade(Spec):
    trade_id: Annotated[UUID, Alias("tradeId")]
    order_id: Annotated[UUID, Alias("orderId")]
    executed_quantity: Annotated[Decimal, Alias("executedQuantity"), Gt(Decimal("0"))]
    executed_price: Annotated[Money, Alias("executedPrice")]
    executed_at: Annotated[datetime, Alias("executedAt")]


class Counterparty(Spec):
    legal_name: Annotated[str, Alias("legalName")]
    lei: str
    internal_rating: str


class TradeReport(Spec):
    trade: Trade
    instrument: Instrument
    counterparty: Counterparty
    reconciliation_note: str


order = Order.from_json(
    """{
      "orderId": "12345678-1234-5678-1234-567812345678",
      "instrument": {
        "isin": "CH0000000001",
        "symbol": "TALEA",
        "settlementCurrency": "CHF"
      },
      "side": "buy",
      "quantity": "10.250",
      "limitPrice": {"amount": "42.50", "currency": "CHF"},
      "submittedAt": "2026-08-26T10:15:00Z",
      "timeInForce": "day"
    }"""
)
assert order.order_id == UUID("12345678-1234-5678-1234-567812345678")
assert order.quantity == Decimal("10.250")
assert order.limit_price.amount == Decimal("42.50")
assert order.submitted_at == datetime(2026, 8, 26, 10, 15, tzinfo=UTC)

encoded = order.to_json()
assert '"quantity":"10.250"' in encoded
assert '"settlementCurrency":"CHF"' in encoded

try:
    Order.from_json(encoded.replace('"currency":"CHF"', '"currency":"EUR"'))
except ValidationError as error:
    assert error.errors()[0]["code"] == "spec_check"
else:
    raise AssertionError("cross-field currency policy must reject the order")

trade = Trade(
    trade_id=UUID("87654321-4321-8765-4321-876543218765"),
    order_id=order.order_id,
    executed_quantity=Decimal("5.125"),
    executed_price=Money(amount=Decimal("42.40"), currency=Currency.CHF),
    executed_at=datetime(2026, 8, 26, 10, 16, tzinfo=UTC),
)
assert trade.to_dict()["executedQuantity"] == Decimal("5.125")
assert '"executedQuantity":"5.125"' in trade.to_json()
report = TradeReport(
    trade=trade,
    instrument=order.instrument,
    counterparty=Counterparty(
        legal_name="Analytical Engines AG",
        lei="529900EXAMPLE000001",
        internal_rating="A",
    ),
    reconciliation_note="operator-only",
)
assert report.to_dict(
    include={
        "trade": {"trade_id": True, "executed_quantity": True, "executed_price": {"amount": True}},
        "instrument": {"isin": True, "symbol": True},
        "counterparty": {"legal_name": True, "lei": True},
    }
) == {
    "trade": {
        "tradeId": UUID("87654321-4321-8765-4321-876543218765"),
        "executedQuantity": Decimal("5.125"),
        "executedPrice": {"amount": Decimal("42.40")},
    },
    "instrument": {"isin": "CH0000000001", "symbol": "TALEA"},
    "counterparty": {"legalName": "Analytical Engines AG", "lei": "529900EXAMPLE000001"},
}
order_schema = Order.json_schema()
assert order_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
order_definitions = cast(dict[str, object], order_schema["$defs"])
assert "Order" in order_definitions

# Talea establishes representation and structural invariants. Venue calendars,
# tick sizes, market permissions, credit limits, and settlement rules remain
# application/domain responsibilities.
