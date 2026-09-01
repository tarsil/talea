"""Compact market records with positional NamedTuple boundaries."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Generic, NamedTuple, TypeVar, cast

from talea import (
    Contract,
    Ge,
    Representation,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
    ValidationError,
    validate_call,
)
from talea.introspection import inspect_contract
from talea.schema import NamedReferenceSchema, NamedTupleSchema, VariadicTupleSchema


def _load_decimal(value: str) -> Decimal:
    return Decimal(value)


def _dump_decimal(value: Decimal) -> str:
    return str(value)


type WireDecimal = Annotated[
    Decimal,
    Representation(input=str, load=_load_decimal, output=str, dump=_dump_decimal),
]


class PriceLevel(NamedTuple):
    """One price and its non-negative displayed quantity."""

    price: WireDecimal
    quantity: Annotated[int, Ge(0)]


class Quote(NamedTuple):
    """Compact positional quote used by the market-data protocol."""

    symbol: str
    bid: PriceLevel
    ask: PriceLevel
    venue: str = "XNAS"


class AuthenticatedQuote(NamedTuple):
    """Quote paired with a credential that Talea-owned failures redact."""

    quote: Quote
    token: Annotated[str, Sensitive()]


T = TypeVar("T")


class Packet(NamedTuple, Generic[T]):
    """Generic sequence-numbered market-data packet."""

    sequence: int
    payload: T


class Chain(NamedTuple):
    """Recursive positional record used for linked venue snapshots."""

    venue: str
    children: tuple[Chain, ...] = ()


class Snapshot(Spec):
    """Object-shaped envelope containing positional quote records."""

    source: str
    quote: Quote


quote_contract = Contract(Quote, policy=ResourcePolicy(max_depth=16, max_nodes=1_000))
quote = Quote(
    "AAPL",
    PriceLevel(Decimal("198.10"), 120),
    PriceLevel(Decimal("198.14"), 90),
)

assert quote_contract.validate(quote) is quote
try:
    quote_contract.validate(tuple(quote))  # type: ignore[arg-type]
except ValidationError as error:
    assert error.errors()[0]["location"] == []
else:
    raise AssertionError("strict validation accepted a plain tuple")

wire_quote: list[object] = ["AAPL", ["198.10", 120], ["198.14", 90]]
converted = quote_contract.from_python(wire_quote)
assert converted == quote
assert quote_contract.from_python(tuple(wire_quote)) == quote
assert quote_contract.from_json('["AAPL",["198.10",120],["198.14",90]]') == quote
assert converted.venue == "XNAS"
try:
    quote_contract.from_python({"symbol": "AAPL"})
except ValidationError as error:
    assert error.errors()[0]["code"] == "type"
else:
    raise AssertionError("a Mapping was accepted as a positional record")

try:
    quote_contract.from_python(["AAPL", ["198.10", -1], ["198.14", 90]])
except ValidationError as error:
    assert error.errors()[0]["location"] == [1, 1]
    assert error.errors()[0]["code"] == "greater_than_or_equal"
else:
    raise AssertionError("a negative displayed quantity was accepted")

invalid_secret = AuthenticatedQuote(quote, 7)  # ty: ignore[invalid-argument-type]
try:
    Contract(AuthenticatedQuote).validate(invalid_secret)
except ValidationError as error:
    assert error.errors()[0]["location"] == [1]
    assert error.errors()[0]["input"] == "<redacted>"
else:
    raise AssertionError("invalid sensitive state was accepted")

python_output = quote_contract.to_python(quote)
assert type(python_output) is tuple
assert python_output == ("AAPL", ("198.10", 120), ("198.14", 90), "XNAS")
assert quote_contract.to_json(quote) == '["AAPL",["198.10",120],["198.14",90],"XNAS"]'

snapshot = Snapshot.from_mapping({"source": "feed-a", "quote": wire_quote})
assert snapshot.quote is not quote
assert snapshot.quote == quote
packet_contract: Contract[Packet[Quote]] = Contract(Packet[Quote])
packet = packet_contract.from_python([42, wire_quote])
assert packet == Packet(42, quote)

chain_contract = Contract(Chain)
chain = chain_contract.from_python(["XNAS", (["BATS"],)])
assert chain == Chain("XNAS", (Chain("BATS"),))

schema = quote_contract.json_schema()
quote_schema = cast(dict[str, object], cast(dict[str, object], schema["$defs"])["Quote"])
assert quote_schema["type"] == "array"
assert quote_schema["minItems"] == 3
assert quote_schema["maxItems"] == 4
assert cast(list[object], quote_schema["prefixItems"])[3] == {
    "type": "string",
    "default": "XNAS",
}
openapi = quote_contract.openapi_schema()
openapi_schemas = cast(dict[str, object], cast(dict[str, object], openapi["components"])["schemas"])
openapi_quote = cast(dict[str, object], openapi_schemas["Quote"])
assert openapi_quote["type"] == "array"
assert openapi_quote["minItems"] == quote_schema["minItems"]
assert openapi_quote["maxItems"] == quote_schema["maxItems"]

info = inspect_contract(quote_contract)
assert isinstance(info.schema, NamedTupleSchema)
assert tuple(field.name for field in info.schema.fields) == ("symbol", "bid", "ask", "venue")
assert info.schema.required_count == 3
assert info.schema.fields[3].default == "XNAS"
try:
    info.schema.required_count = 4  # ty: ignore[invalid-assignment]
except (AttributeError, TypeError):
    pass
else:
    raise AssertionError("introspection schema was mutable")

recursive_schema = chain_contract._artifacts.schema
assert isinstance(recursive_schema, NamedTupleSchema)
children_schema = recursive_schema.fields[1].schema
assert isinstance(children_schema, VariadicTupleSchema)
assert isinstance(children_schema.item, NamedReferenceSchema)


@validate_call
def publish(value: Quote) -> Quote:
    """Publish an already-validated exact quote record."""

    return value


assert publish(quote) is quote
try:
    publish(tuple(quote))  # ty: ignore[invalid-argument-type]
except ValidationError:
    pass
else:
    raise AssertionError("callable validation accepted a plain tuple")

assert list(quote_contract.iter_python((wire_quote, tuple(wire_quote)))) == [quote, quote]
assert list(quote_contract.iter_jsonl(('["AAPL",["198.10",120],["198.14",90]]',))) == [quote]

try:
    quote_contract.from_python(wire_quote, policy=ResourcePolicy(max_nodes=3))
except ResourceLimitError as error:
    assert error.code == "nodes"
else:
    raise AssertionError("the positional graph exceeded max_nodes")
