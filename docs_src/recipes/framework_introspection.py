"""Framework-facing immutable introspection without annotation reconstruction."""

from dataclasses import FrozenInstanceError
from typing import Annotated, cast

from talea import Alias, Contract, Description, Ge, Sensitive, Spec, Title, derive_spec
from talea.introspection import FieldInfo, inspect_contract, inspect_spec


class Account(
    Spec,
    metadata=(Title("Account"), Description("Framework-visible account payload.")),
):
    account_id: Annotated[int, Alias("id"), Ge(1)]
    display_name: Annotated[str, Alias("displayName")]
    token: Annotated[str, Sensitive()]


def field_descriptor(field: FieldInfo) -> dict[str, object]:
    """Project public info into one framework's simpler descriptor shape."""

    return {
        "python_name": field.name,
        "external_name": field.alias or field.name,
        "required": field.required,
        "omittable": field.omittable,
        "sensitive": field.sensitive,
        "schema_kind": type(field.schema).__name__ if field.schema is not None else None,
    }


account_info = inspect_spec(Account)
assert account_info.title == "Account"
assert account_info.description == "Framework-visible account payload."
assert account_info.operations == (
    "strict_python",
    "external_python",
    "json_input",
    "python_output",
    "json_output",
)

descriptors = [field_descriptor(field) for field in account_info.fields]
assert descriptors[0]["external_name"] == "id"
assert descriptors[0]["required"] is True
assert descriptors[2]["sensitive"] is True

AccountPatch = derive_spec(Account, exclude=("account_id",), partial=True, name="AccountPatch")
patch_info = inspect_spec(AccountPatch)
assert patch_info.presence_aware is True
assert patch_info.derivation is not None
assert patch_info.derivation.source is Account
assert patch_info.derivation.omitted_fields == ("account_id",)
assert all(field.omittable for field in patch_info.fields)

batch = Contract[list[Account]](list[Account])
contract_info = inspect_contract(batch)
assert contract_info.annotation == list[Account]
assert type(contract_info.schema).__name__ == "SequenceSchema"

try:
    frozen_attribute = "title"
    setattr(account_info, frozen_attribute, "Changed")
except FrozenInstanceError:
    pass
else:
    raise AssertionError("public introspection must be immutable")

try:
    inspect_spec(cast(type[object], dict))
except TypeError as error:
    assert "Spec class" in str(error)
else:
    raise AssertionError("non-Spec classes must not expose synthetic info")

# Frameworks may project this truth into route parameters, documentation, or
# dependency graphs. They should not mutate it or recover semantics by rereading
# __annotations__, compiler globals, generated functions, or private artifacts.
