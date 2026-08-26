"""Schema and OpenAPI projection for account and tagged-event contracts."""

from typing import Annotated, Literal, cast
from uuid import UUID

from talea import (
    Alias,
    Contract,
    Description,
    Discriminator,
    Ge,
    ReadOnly,
    Sensitive,
    Spec,
    Title,
    WriteOnly,
    derive_spec,
)


class Credentials(Spec):
    token: Annotated[str, Sensitive(), WriteOnly(), Description("One-time account token.")]


class Account(Spec, metadata=(Title("Account"), Description("Account API representation."))):
    account_id: Annotated[UUID, Alias("id"), ReadOnly()]
    revision: Annotated[int, Ge(1)]
    display_name: Annotated[str, Alias("displayName")]
    credentials: Credentials


AccountPatch = derive_spec(
    Account,
    exclude=("account_id", "revision"),
    partial=True,
    name="AccountPatch",
)


class AccountOpened(Spec):
    kind: Annotated[Literal["account.opened"], Alias("type")]
    account: Account


class AccountClosed(Spec):
    kind: Annotated[Literal["account.closed"], Alias("type")]
    account_id: Annotated[UUID, Alias("accountId")]


type AccountEvent = Annotated[AccountOpened | AccountClosed, Discriminator("type")]


account_input = Account.json_schema(mode="input")
account_output = Account.json_schema(mode="output")
account_definitions = cast(dict[str, object], account_input["$defs"])
account_definition = cast(dict[str, object], account_definitions["Account"])
assert account_input["$schema"] == "https://json-schema.org/draft/2020-12/schema"
assert account_definition["title"] == "Account"
account_properties = cast(dict[str, object], account_definition["properties"])
identifier_schema = cast(dict[str, object], account_properties["id"])
credentials_reference = cast(dict[str, object], account_properties["credentials"])
assert identifier_schema["readOnly"] is True
assert cast(str, credentials_reference["$ref"]).endswith("/Credentials")
assert account_input is not account_output

credentials_definition = cast(dict[str, object], account_definitions["Credentials"])
credentials_properties = cast(dict[str, object], credentials_definition["properties"])
token_schema = cast(dict[str, object], credentials_properties["token"])
assert token_schema["writeOnly"] is True
assert "sensitive" not in str(account_input).lower()

patch_schema = AccountPatch.json_schema()
patch_definitions = cast(dict[str, object], patch_schema["$defs"])
patch_definition = cast(dict[str, object], patch_definitions["AccountPatch"])
assert patch_definition.get("required", []) == []
patch_properties = cast(dict[str, object], patch_definition["properties"])
assert set(patch_properties) == {"displayName", "credentials"}

events: Contract[AccountEvent] = Contract(AccountEvent)
event_fragment = events.openapi_schema(mode="output")
assert set(event_fragment) == {"schema", "components"}
components = cast(dict[str, object], event_fragment["components"])
schemas = cast(dict[str, object], components["schemas"])
event_schema = cast(dict[str, object], schemas["AccountEvent"])
discriminator = cast(dict[str, object], event_schema["discriminator"])
mapping = cast(dict[str, str], discriminator["mapping"])
assert discriminator["propertyName"] == "type"
assert mapping == {
    "account.opened": "#/components/schemas/AccountOpened",
    "account.closed": "#/components/schemas/AccountClosed",
}

# Frameworks merge the returned components into their document and place the
# root fragment wherever a request or response Schema Object is required.
openapi_document = {
    "openapi": "3.1.2",
    "info": {"title": "Accounts", "version": "1.0.0"},
    "paths": {},
    "components": event_fragment["components"],
}
document_components = cast(dict[str, object], openapi_document["components"])
document_schemas = cast(dict[str, object], document_components["schemas"])
assert document_schemas["AccountEvent"] == event_schema
