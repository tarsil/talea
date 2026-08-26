"""Contracts for primitive, container, TypedDict, recursive, and generic roots."""

from decimal import Decimal
from typing import NotRequired, TypedDict, cast
from uuid import UUID

from talea import Contract, ResourcePolicy, ValidationError

identifier: Contract[UUID] = Contract(UUID)
expected_id = UUID("12345678-1234-5678-1234-567812345678")
assert identifier.validate(expected_id) is expected_id
assert identifier.from_json('"12345678-1234-5678-1234-567812345678"') == expected_id
assert identifier.to_json(expected_id) == '"12345678-1234-5678-1234-567812345678"'

identifiers: Contract[list[UUID]] = Contract(list[UUID])
assert identifiers.from_python([expected_id]) == [expected_id]
assert identifiers.from_json('["12345678-1234-5678-1234-567812345678"]') == [expected_id]

balances: Contract[dict[str, Decimal]] = Contract(dict[str, Decimal])
balance = balances.from_json('{"CHF":"42.50","EUR":"10.00"}')
assert balance == {"CHF": Decimal("42.50"), "EUR": Decimal("10.00")}
assert balances.to_python(balance) == balance
assert balances.to_json(balance) == '{"CHF":"42.50","EUR":"10.00"}'


class PartnerPayload(TypedDict):
    account_id: UUID
    tags: NotRequired[list[str]]


partner: Contract[PartnerPayload] = Contract(PartnerPayload)
payload = partner.from_python({"account_id": expected_id, "tags": ["verified"]})
assert payload == {"account_id": expected_id, "tags": ["verified"]}
assert partner.from_json('{"account_id":"12345678-1234-5678-1234-567812345678","tags":["verified"]}') == payload

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
json_values: Contract[JsonValue] = Contract(JsonValue)
document = json_values.from_json('{"account":{"active":true,"scores":[1,2]}}')
assert document == {"account": {"active": True, "scores": [1, 2]}}

type Page[T] = list[T]
account_page: Contract[Page[PartnerPayload]] = Contract(Page[PartnerPayload])
assert account_page.from_python([payload])[0]["account_id"] == expected_id

partner_schema = partner.json_schema()
partner_definitions = cast(dict[str, object], partner_schema["$defs"])
partner_definition = cast(dict[str, object], partner_definitions["PartnerPayload"])
assert partner_definition["type"] == "object"
page_fragment = account_page.openapi_schema()
assert set(page_fragment) == {"schema", "components"}
page_schema = cast(dict[str, object], page_fragment["schema"])
assert page_schema.get("type") == "array" or "$ref" in page_schema

try:
    partner.from_python(
        {"account_id": "not-a-uuid", "tags": [1, 2]},  # type: ignore[typeddict-item]
        policy=ResourcePolicy(max_errors=2),
    )
except ValidationError as error:
    details = error.errors()
    assert details[0]["code"] == "type"
    assert details[0]["location"] == ["account_id"]
else:
    raise AssertionError("invalid TypedDict data must fail at the Contract boundary")

# Define a Spec when the contract should have named attributes, methods, and a
# reusable immutable record. Retain a Contract when the useful root is already
# a primitive, container, union, alias, or third-party TypedDict.
