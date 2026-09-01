"""Executable field-local serializer output-contract examples."""

from typing import Annotated, cast

from talea import Alias, SerializationError, Spec, serialize
from talea.introspection import inspect_spec


class AccountRecord(Spec):
    """Internal account state used by more than one application operation."""

    identifier: int
    display_name: str
    email: str


class AccountSummary(Spec):
    """The field-local response shape for one endpoint."""

    display_name: Annotated[str, Alias("displayName")]
    identifier: int


class TradeResponse(Spec):
    """Expose a summary for this field without redefining AccountRecord."""

    trade_id: str
    account: AccountRecord

    @serialize("account", output=AccountSummary)
    def summarize(account: AccountRecord) -> AccountSummary:
        return AccountSummary(
            display_name=account.display_name,
            identifier=account.identifier,
        )


trade = TradeResponse(
    trade_id="trade-7",
    account=AccountRecord(identifier=42, display_name="Ada", email="ada@example.test"),
)

assert trade.to_dict() == {
    "trade_id": "trade-7",
    "account": {"displayName": "Ada", "identifier": 42},
}
assert trade.to_dict(include={"account": {"display_name": True}}) == {"account": {"displayName": "Ada"}}
assert trade.to_json(include={"account": {"identifier": True}}) == '{"account":{"identifier":42}}'

input_schema = TradeResponse.json_schema(mode="input")
output_schema = TradeResponse.json_schema(mode="output")
assert "$defs" in input_schema and "$defs" in output_schema

serializer = inspect_spec(TradeResponse).serializers[0]
assert serializer.has_declared_output is True
assert serializer.output_schema is not None
assert not hasattr(serializer, "callback")


class BrokenResponse(Spec):
    """Demonstrate runtime enforcement of declared callback truth."""

    account: AccountRecord

    @serialize("account", output=AccountSummary)
    def summarize(account: AccountRecord) -> AccountSummary:
        return cast(AccountSummary, {"display_name": account.display_name})


try:
    BrokenResponse(account=trade.account).to_dict()
except SerializationError as error:
    assert error.location == ("account",)
else:
    raise AssertionError("an invalid serializer result escaped its declared output contract")
