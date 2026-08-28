"""A framework-neutral account API from hostile bytes to a safe response."""

import json
from collections.abc import Callable
from typing import Annotated, Literal, cast
from uuid import UUID

from talea import (
    Alias,
    MaxLength,
    MinLength,
    ReadOnly,
    ResourceLimitError,
    ResourcePolicy,
    Sensitive,
    Spec,
    ValidationError,
    WriteOnly,
)


class Address(Spec):
    line_1: Annotated[str, Alias("line1"), MinLength(1), MaxLength(120)]
    city: Annotated[str, MinLength(1), MaxLength(80)]
    postcode: Annotated[str, MinLength(3), MaxLength(16)]
    country: Annotated[str, MinLength(2), MaxLength(2)]


class Credentials(Spec):
    password: Annotated[str, Sensitive(), WriteOnly(), MinLength(12), MaxLength(128)]


class UserCreate(Spec):
    email: Annotated[str, MinLength(3), MaxLength(254)]
    display_name: Annotated[str, Alias("displayName"), MinLength(1), MaxLength(80)]
    address: Address
    credentials: Credentials


class UserResponse(Spec):
    user_id: Annotated[UUID, Alias("id"), ReadOnly()]
    email: str
    display_name: Annotated[str, Alias("displayName")]
    address: Address
    status: Literal["active"] = "active"


class StoredUser(Spec):
    user_id: UUID
    email: str
    display_name: str
    address: Address


StoreUser = Callable[[UserCreate], StoredUser]


def create_user(request: UserCreate) -> StoredUser:
    """Stand in for application/domain work after boundary validation."""

    return StoredUser(
        user_id=UUID("12345678-1234-5678-1234-567812345678"),
        email=request.email,
        display_name=request.display_name,
        address=request.address,
    )


def error_response(error: ValidationError) -> str:
    return json.dumps({"errors": error.errors()}, separators=(",", ":"))


def handle_create(body: bytes, store: StoreUser = create_user) -> tuple[int, str]:
    """Translate transport and validation failures without choosing a framework."""

    try:
        request = UserCreate.from_json(
            body,
            policy=ResourcePolicy(
                max_input_bytes=4_096,
                max_depth=8,
                max_nodes=200,
                max_errors=10,
            ),
        )
    except ResourceLimitError as error:
        return 413, json.dumps({"error": error.code}, separators=(",", ":"))
    except ValidationError as error:
        return 422, error_response(error)

    stored = store(request)
    response = UserResponse(
        user_id=stored.user_id,
        email=stored.email,
        display_name=stored.display_name,
        address=stored.address,
    )
    return 201, response.to_json()


request_body = b"""{
  "email": "ada@example.test",
  "displayName": "Ada Lovelace",
  "address": {
    "line1": "1 Analytical Engine Way",
    "city": "London",
    "postcode": "SW1A 1AA",
    "country": "GB"
  },
  "credentials": {"password": "correct horse battery staple"}
}"""

status, body = handle_create(request_body)
assert status == 201
document = json.loads(body)
assert document["id"] == "12345678-1234-5678-1234-567812345678"
assert document["displayName"] == "Ada Lovelace"
assert "credentials" not in document

invalid = request_body.replace(b'"postcode": "SW1A 1AA"', b'"postcode": "X"')
status, body = handle_create(invalid)
assert status == 422
detail = json.loads(body)["errors"][0]
assert detail["location"] == ["address", "postcode"]
assert detail["code"] == "min_length"

oversized = b'{"email":"' + b"x" * 5_000 + b'"}'
status, body = handle_create(oversized)
assert status == 413
assert json.loads(body) == {"error": "input_size"}

input_schema = UserCreate.openapi_schema(mode="input")
output_schema = UserResponse.openapi_schema(mode="output")
input_root = cast(dict[str, object], input_schema["schema"])
output_root = cast(dict[str, object], output_schema["schema"])
assert cast(str, input_root["$ref"]).endswith("/UserCreate")
assert cast(str, output_root["$ref"]).endswith("/UserResponse")
input_components = cast(dict[str, object], input_schema["components"])
output_components = cast(dict[str, object], output_schema["components"])
input_definitions = cast(dict[str, object], input_components["schemas"])
output_definitions = cast(dict[str, object], output_components["schemas"])
assert "UserCreate" in input_definitions
assert "UserResponse" in output_definitions


class Permission(Spec):
    code: str
    source: str


class AccountProfile(Spec):
    display_name: Annotated[str, Alias("displayName")]
    address: Address
    permissions: list[Permission]
    internal_note: str


class AccountSnapshot(Spec):
    account_id: Annotated[UUID, Alias("id")]
    profile: AccountProfile
    revision: int


snapshot = AccountSnapshot(
    account_id=UUID("12345678-1234-5678-1234-567812345678"),
    profile=AccountProfile(
        display_name="Ada Lovelace",
        address=Address(line_1="1 Engine Way", city="London", postcode="SW1A 1AA", country="GB"),
        permissions=[Permission(code="account.read", source="role"), Permission(code="trade.read", source="grant")],
        internal_note="operator-only",
    ),
    revision=7,
)
public_snapshot = json.loads(
    snapshot.to_json(
        include={
            "account_id": True,
            "profile": {
                "display_name": True,
                "address": {"city": True, "country": True},
                "permissions": {"code": True},
            },
        }
    )
)
assert public_snapshot == {
    "id": "12345678-1234-5678-1234-567812345678",
    "profile": {
        "displayName": "Ada Lovelace",
        "address": {"city": "London", "country": "GB"},
        "permissions": [{"code": "account.read"}, {"code": "trade.read"}],
    },
}
