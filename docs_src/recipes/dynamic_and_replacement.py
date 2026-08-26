"""Dynamic declarations, introspection, and validated immutable replacement."""

from copy import replace
from typing import Annotated, cast

from talea import Description, Ge, Spec, Title, ValidationError, check, create_spec
from talea.introspection import inspect_spec


@check("balance")
def practical_balance(balance: int) -> None:
    if balance > 1_000_000:
        raise ValueError("example account exceeds configured range")


Account = create_spec(
    "Account",
    {"account_id": int, "balance": Annotated[int, Ge(0)]},
    defaults={"balance": 0},
    namespace={"practical_balance": practical_balance},
    metadata=(Title("Account"), Description("Dynamically declared account.")),
)

account: Spec = Account.from_mapping({"account_id": 7})
updated = replace(account, **{"balance": 10})
assert updated.to_dict()["balance"] == 10
assert account.to_dict()["balance"] == 0

info = inspect_spec(Account)
assert info.title == "Account"
assert [field.name for field in info.fields] == ["account_id", "balance"]
assert info.hook_names == ("practical_balance",)
assert Account.from_json('{"account_id":8,"balance":25}').to_dict()["balance"] == 25

try:
    replace(updated, balance=-1)
except ValidationError as error:
    assert error.errors()[0]["code"] == "greater_than_or_equal"
else:
    raise AssertionError("replacement must rerun the field constraint")

try:
    replace(updated, balance=1_000_001)
except ValidationError as error:
    assert error.errors()[0]["code"] == "field_check"
else:
    raise AssertionError("replacement must rerun the dynamic hook")

schema = Account.json_schema()
definitions = cast(dict[str, object], schema["$defs"])
account_schema = cast(dict[str, object], definitions["Account"])
properties = cast(dict[str, object], account_schema["properties"])
balance_schema = cast(dict[str, object], properties["balance"])
assert balance_schema["minimum"] == 0
