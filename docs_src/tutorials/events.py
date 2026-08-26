"""A tagged payment event boundary with direct dispatch and schema projection."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, cast
from uuid import UUID

from talea import Alias, Contract, Discriminator, Sensitive, Spec, ValidationError, WriteOnly


class PaymentAuthorized(Spec):
    kind: Annotated[Literal["payment.authorized"], Alias("type")]
    event_id: Annotated[UUID, Alias("eventId")]
    occurred_at: Annotated[datetime, Alias("occurredAt")]
    payment_id: Annotated[UUID, Alias("paymentId")]
    amount: Decimal
    authorization_token: Annotated[str, Alias("authorizationToken"), Sensitive(), WriteOnly()]


class PaymentDeclined(Spec):
    kind: Annotated[Literal["payment.declined"], Alias("type")]
    event_id: Annotated[UUID, Alias("eventId")]
    occurred_at: Annotated[datetime, Alias("occurredAt")]
    payment_id: Annotated[UUID, Alias("paymentId")]
    reason_code: Annotated[Literal["insufficient_funds", "issuer_declined"], Alias("reasonCode")]


class AccountFrozen(Spec):
    kind: Annotated[Literal["account.frozen"], Alias("type")]
    event_id: Annotated[UUID, Alias("eventId")]
    occurred_at: Annotated[datetime, Alias("occurredAt")]
    account_id: Annotated[UUID, Alias("accountId")]
    reason: str


class AccountUpdated(Spec):
    kind: Annotated[Literal["account.updated"], Alias("type")]
    event_id: Annotated[UUID, Alias("eventId")]
    occurred_at: Annotated[datetime, Alias("occurredAt")]
    account_id: Annotated[UUID, Alias("accountId")]
    changed_fields: Annotated[list[str], Alias("changedFields")]


type Event = Annotated[
    PaymentAuthorized | PaymentDeclined | AccountFrozen | AccountUpdated,
    Discriminator("type"),
]


class EventEnvelope[T](Spec):
    stream: str
    sequence: int
    payload: T


events: Contract[Event] = Contract(Event)
authorized_json = """{
  "type": "payment.authorized",
  "eventId": "10000000-0000-0000-0000-000000000001",
  "occurredAt": "2026-08-26T10:15:00Z",
  "paymentId": "20000000-0000-0000-0000-000000000002",
  "amount": "42.50",
  "authorizationToken": "gateway-secret"
}"""
event = events.from_json(authorized_json)
assert isinstance(event, PaymentAuthorized)
assert event.amount == Decimal("42.50")

projected = cast(dict[str, object], events.to_python(event))
assert projected["type"] == "payment.authorized"
assert projected["authorizationToken"] == "gateway-secret"
assert '"type":"payment.authorized"' in events.to_json(event)

envelope = EventEnvelope[Event](stream="payments", sequence=42, payload=event)
assert isinstance(envelope.payload, PaymentAuthorized)
assert EventEnvelope[Event].from_json(envelope.to_json()).sequence == 42

try:
    events.from_json('{"type":"payment.refunded"}')
except ValidationError as error:
    detail = error.errors()[0]
    assert detail["code"] == "discriminator_unknown"
    assert detail["location"] == ["type"]
    # This union contains a Sensitive field, so discriminator diagnostics are
    # conservatively redacted with the rest of the boundary failure.
    assert detail["discriminator"] == "<redacted>"
else:
    raise AssertionError("an unknown event tag must fail before branch validation")

try:
    events.from_json(
        '{"type":"account.updated","eventId":"bad",'
        '"occurredAt":"2026-08-26T10:15:00Z","accountId":"bad","changedFields":[]}'
    )
except ValidationError as error:
    locations = {tuple(item["location"]) for item in error.errors()}
    assert ("eventId",) in locations
    assert ("accountId",) in locations
else:
    raise AssertionError("the selected branch must validate its nested fields")

schema = events.json_schema()
assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
assert "$defs" in schema
openapi = events.openapi_schema()
components = cast(dict[str, object], openapi["components"])
schemas = cast(dict[str, object], components["schemas"])
event_schema = cast(dict[str, object], schemas["Event"])
discriminator = cast(dict[str, object], event_schema["discriminator"])
mapping = cast(dict[str, str], discriminator["mapping"])
assert discriminator["propertyName"] == "type"
assert set(mapping) == {
    "payment.authorized",
    "payment.declined",
    "account.frozen",
    "account.updated",
}
