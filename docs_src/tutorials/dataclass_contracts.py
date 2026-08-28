"""Stdlib dataclasses as a domain layer behind Talea Contract boundaries."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from decimal import Decimal
from typing import Annotated, Literal, cast

from talea import Alias, Contract, Ge, ResourcePolicy, Sensitive, ValidationError
from talea.introspection import inspect_contract
from talea.schema import DataclassSchema, NamedReferenceSchema, SequenceSchema


@dataclass(frozen=True, slots=True)
class Money:
    """Immutable monetary amount retained by the application domain."""

    amount: Decimal
    currency: Literal["CHF", "EUR", "USD"]


@dataclass(frozen=True, slots=True)
class Instrument:
    """Immutable traded instrument with an external protocol alias."""

    symbol: Annotated[str, Alias("ticker")]
    venue: str


@dataclass(slots=True)
class Address:
    """Mutable customer address used by the domain layer."""

    city: str
    postcode: str


@dataclass(slots=True)
class Customer:
    """Customer whose list state must be revalidated at every boundary."""

    customer_id: Annotated[int, Alias("customerId"), Ge(1)]
    name: str
    address: Address
    labels: list[str] = field(default_factory=list)
    api_token: Annotated[str, Sensitive()] = ""


@dataclass(slots=True)
class Trade:
    """Trade whose standard post-init lifecycle derives retained output state."""

    trade_id: Annotated[int, Alias("tradeId"), Ge(1)]
    customer: Customer
    instrument: Instrument
    price: Money
    quantity: Annotated[int, Ge(1)]
    notional: Decimal = field(init=False)

    def __post_init__(self) -> None:
        self.notional = self.price.amount * self.quantity


@dataclass(slots=True)
class AllocatedTrade(Trade):
    """Ordinary stdlib dataclass inheritance remains the field owner."""

    account: str


@dataclass(slots=True)
class Page[T]:
    """Concrete generic dataclass result page."""

    items: list[T]


@dataclass(slots=True)
class Referral:
    """Recursive dataclass graph using finite canonical references."""

    customer_id: int
    referrals: list[Referral] = field(default_factory=list)


trade_contract = Contract(
    Trade,
    policy=ResourcePolicy(max_depth=16, max_nodes=1_000),
)
trade = trade_contract.from_json(
    """{
      "tradeId": 17,
      "customer": {
        "customerId": 7,
        "name": "Ada",
        "address": {"city": "Zurich", "postcode": "8001"},
        "labels": ["professional"],
        "api_token": "token-7"
      },
      "instrument": {"ticker": "TALEA", "venue": "XSWX"},
      "price": {"amount": "12.50", "currency": "CHF"},
      "quantity": 4
    }"""
)

assert type(trade) is Trade
assert trade.notional == Decimal("50.00")
assert trade_contract.validate(trade) is trade
assert trade_contract.to_python(trade) == {
    "tradeId": 17,
    "customer": {
        "customerId": 7,
        "name": "Ada",
        "address": {"city": "Zurich", "postcode": "8001"},
        "labels": ["professional"],
        "api_token": "token-7",
    },
    "instrument": {"ticker": "TALEA", "venue": "XSWX"},
    "price": {"amount": Decimal("12.50"), "currency": "CHF"},
    "quantity": 4,
    "notional": Decimal("50.00"),
}
assert '"notional":"50.00"' in trade_contract.to_json(trade)

input_schema = cast(dict[str, object], trade_contract.json_schema()["$defs"])["Trade"]
output_schema = cast(dict[str, object], trade_contract.json_schema(mode="output")["$defs"])["Trade"]
input_properties = cast(dict[str, object], cast(dict[str, object], input_schema)["properties"])
output_properties = cast(dict[str, object], cast(dict[str, object], output_schema)["properties"])
assert "notional" not in input_properties
assert "notional" in output_properties
assert trade_contract.openapi_schema()["schema"] == {"$ref": "#/components/schemas/Trade"}

cast(list[object], trade.customer.labels).append(1)
try:
    trade_contract.validate(trade)
except ValidationError as error:
    assert error.errors()[0]["location"] == ["customer", "labels", 1]
else:
    raise AssertionError("mutable dataclass state was not revalidated")
trade.customer.labels.pop()

page_contract: Contract[Page[Instrument]] = Contract(Page[Instrument])
page = page_contract.from_python({"items": [{"ticker": "TALEA", "venue": "XSWX"}]})
assert type(page.items[0]) is Instrument

referrals = Contract(Referral)
referral = referrals.from_python({"customer_id": 7, "referrals": [{"customer_id": 8}]})
assert type(referral.referrals[0]) is Referral
referral_schema = referrals._artifacts.schema
assert isinstance(referral_schema, DataclassSchema)
recursive_field = referral_schema.fields[1].schema
assert isinstance(recursive_field, SequenceSchema)
assert isinstance(recursive_field.item, NamedReferenceSchema)

info = inspect_contract(trade_contract)
assert isinstance(info.schema, DataclassSchema)
assert info.schema.dataclass_type is Trade

secret = "do-not-log-this-token"
invalid = Customer(7, "Ada", Address("Zurich", "8001"), api_token=secret)
object.__setattr__(invalid, "api_token", 1)
try:
    Contract(Customer).validate(invalid)
except ValidationError as error:
    assert secret not in str(error)
    assert secret not in repr(error.errors())
else:
    raise AssertionError("invalid sensitive state was accepted")


@dataclass
class UnsupportedInitVar:
    """Demonstrate the deliberate retained-state boundary."""

    value: int
    context: InitVar[str]


try:
    Contract(UnsupportedInitVar)
except TypeError as error:
    assert "InitVar" in str(error)
else:
    raise AssertionError("InitVar dataclass contract was accepted")
