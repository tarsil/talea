"""Compose dataclass boundaries, directional views, PATCH, and nested output."""

import json
from dataclasses import dataclass
from typing import Annotated, Protocol, cast

from talea import (
    Alias,
    Contract,
    ReadOnly,
    ResourcePolicy,
    Sensitive,
    Spec,
    WriteOnly,
    apply_patch,
    derive_spec,
)


@dataclass(frozen=True, slots=True)
class PostalAddress:
    city: str
    country: str
    internal_note: str


@dataclass(frozen=True, slots=True)
class AccountRecord:
    record_id: Annotated[int, Alias("recordId")]
    address: PostalAddress
    permissions: list[str]


records = Contract(AccountRecord)
record = records.from_json(
    """{
      "recordId": 7,
      "address": {"city": "London", "country": "GB", "internal_note": "ops"},
      "permissions": ["account.read", "trade.read"]
    }""",
    policy=ResourcePolicy(max_depth=8, max_nodes=64),
)
assert type(record) is AccountRecord
assert records.validate(record) is record
record_mapping = cast(dict[str, object], records.to_python(record))
assert record_mapping["recordId"] == 7
assert json.loads(records.to_json(record))["address"]["city"] == "London"


class AccountBoundary(Spec):
    request_id: Annotated[int, Alias("requestId"), ReadOnly()]
    record: AccountRecord
    password: Annotated[str, Sensitive(), WriteOnly()]


AccountInput = derive_spec(AccountBoundary, mode="input", name="AccountInput")
AccountOutput = derive_spec(AccountBoundary, mode="output", name="AccountOutput")
AccountPatch = derive_spec(AccountBoundary, mode="input", partial=True, name="AccountPatch")


class AccountInputValue(Protocol):
    record: AccountRecord
    password: str


created = cast(
    AccountInputValue,
    AccountInput.from_json(
        """{
          "record": {
            "recordId": 7,
            "address": {"city": "London", "country": "GB", "internal_note": "ops"},
            "permissions": ["account.read", "trade.read"]
          },
          "password": "correct horse battery staple"
        }"""
    ),
)
assert type(created.record) is AccountRecord
assert "correct horse battery staple" not in repr(created)

source = AccountBoundary(
    request_id=42,
    record=created.record,
    password=created.password,
)
patch = AccountPatch.from_json('{"password":"replacement credential"}')
updated = apply_patch(source, patch)
assert updated.request_id == 42
assert updated.record is source.record
assert updated.record == record
assert updated.password == "replacement credential"

response = AccountOutput.from_mapping({"requestId": source.request_id, "record": records.to_python(source.record)})
projected = json.loads(
    response.to_json(
        include={
            "request_id": True,
            "record": {
                "record_id": True,
                "address": {"city": True, "country": True},
                "permissions": True,
            },
        }
    )
)
assert projected == {
    "requestId": 42,
    "record": {
        "recordId": 7,
        "address": {"city": "London", "country": "GB"},
        "permissions": ["account.read", "trade.read"],
    },
}

input_schema = json.dumps(AccountInput.json_schema(mode="input"), sort_keys=True)
output_schema = json.dumps(AccountOutput.json_schema(mode="output"), sort_keys=True)
assert '"requestId"' not in input_schema
assert '"password"' in input_schema
assert '"requestId"' in output_schema
assert '"password"' not in output_schema
