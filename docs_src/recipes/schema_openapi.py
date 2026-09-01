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
    display_name: Annotated[str, Alias("displayName", legacy=("name",))]
    credentials: Credentials


AccountPatch = derive_spec(
    Account,
    exclude=("account_id", "revision"),
    partial=True,
    name="AccountPatch",
)


class AccountOpened(Spec):
    kind: Annotated[Literal["account.opened"], Alias("type", legacy=("kind",))]
    account: Account


class AccountClosed(Spec):
    kind: Annotated[Literal["account.closed"], Alias("type", legacy=("kind",))]
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
assert set(account_properties) == {"id", "revision", "displayName", "name", "credentials"}
account_name_constraints = cast(list[dict[str, object]], account_definition["allOf"])
assert account_name_constraints == [{"oneOf": [{"required": ["displayName"]}, {"required": ["name"]}]}]
assert account_input is not account_output
account_output_definitions = cast(dict[str, object], account_output["$defs"])
account_output_definition = cast(dict[str, object], account_output_definitions["Account"])
account_output_properties = cast(dict[str, object], account_output_definition["properties"])
assert "displayName" in account_output_properties
assert "name" not in account_output_properties

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
assert set(patch_properties) == {"displayName", "name", "credentials"}
patch_constraints = cast(list[dict[str, object]], patch_definition["allOf"])
assert len(cast(list[object], patch_constraints[0]["oneOf"])) == 3

events: Contract[AccountEvent] = Contract(AccountEvent)
event_fragment = events.openapi_schema(mode="input")
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
for branch_name in ("AccountOpened", "AccountClosed"):
    branch = cast(dict[str, object], schemas[branch_name])
    branch_properties = cast(dict[str, object], branch["properties"])
    assert "type" in branch_properties
    assert "kind" in branch_properties

event_output = events.openapi_schema(mode="output")
event_output_components = cast(dict[str, object], event_output["components"])
event_output_schemas = cast(dict[str, object], event_output_components["schemas"])
for branch_name in ("AccountOpened", "AccountClosed"):
    branch = cast(dict[str, object], event_output_schemas[branch_name])
    branch_properties = cast(dict[str, object], branch["properties"])
    assert "type" in branch_properties
    assert "kind" not in branch_properties

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
