"""Presence-aware REST PATCH with aliases, defaults, secrets, and invariants."""

from copy import replace
from typing import Annotated, cast
from uuid import UUID

from talea import (
    Alias,
    MinLength,
    Sensitive,
    Spec,
    ValidationError,
    apply_patch,
    check,
    derive_spec,
)


class User(Spec):
    user_id: Annotated[UUID, Alias("id")]
    display_name: Annotated[str, Alias("displayName"), MinLength(1)]
    recovery_email: Annotated[str | None, Alias("recoveryEmail")] = None
    api_token: Annotated[str | None, Alias("apiToken"), Sensitive()] = None
    enabled: bool = True

    @check("display_name", "enabled")
    def enabled_users_have_names(display_name: str, enabled: bool) -> None:
        if enabled and not display_name.strip():
            raise ValueError("enabled users require a display name")


UserPatch = derive_spec(User, exclude=("user_id",), partial=True, name="UserPatch")
user = User(
    user_id=UUID("12345678-1234-5678-1234-567812345678"),
    display_name="Ada Lovelace",
    api_token="server-secret",
)

# An empty JSON object means no requested changes. Defaults are not materialized.
empty = UserPatch.from_json("{}")
assert empty.present_fields == frozenset()
assert empty.to_dict() == {}
empty_result = apply_patch(user, empty)
assert empty_result.to_dict() == user.to_dict()
assert empty_result is not user

# Aliases are boundary names; presence is reported with canonical Python names.
patch = UserPatch.from_json('{"displayName":"Grace Hopper","recoveryEmail":null}')
assert patch.present_fields == frozenset({"display_name", "recovery_email"})
assert patch.to_dict(by_alias=False)["recovery_email"] is None
assert patch.to_dict() == {"displayName": "Grace Hopper", "recoveryEmail": None}
updated = apply_patch(user, patch)
assert updated.display_name == "Grace Hopper"
assert updated.recovery_email is None
assert updated.api_token == "server-secret"

# A value equal to a source default is still explicitly present.
default_equal = UserPatch.from_mapping({"enabled": True})
assert default_equal.present_fields == frozenset({"enabled"})

# Omission is not None and omitted attributes are genuinely absent.
omitted = UserPatch()
assert not hasattr(omitted, "recovery_email")
try:
    omitted_name = "recovery_email"
    _ = getattr(omitted, omitted_name)
except AttributeError:
    pass
else:
    raise AssertionError("an omitted partial field must raise AttributeError")

# Invalid field values fail while decoding the patch.
try:
    UserPatch.from_json('{"displayName":""}')
except ValidationError as error:
    assert error.errors()[0]["code"] == "min_length"
    assert error.errors()[0]["location"] == ["displayName"]
else:
    raise AssertionError("the field constraint must reject an empty name")

# Whole-object invariants run after present values are applied.
try:
    apply_patch(user, UserPatch.from_mapping({"displayName": " "}))
except ValidationError as error:
    assert error.errors()[0]["code"] == "spec_check"
else:
    raise AssertionError("the complete User invariant must run")

# Sensitive values remain redacted in repr and validation failures.
secret_patch = UserPatch.from_mapping({"apiToken": "rotated-secret"})
assert "rotated-secret" not in repr(secret_patch)
assert apply_patch(user, secret_patch).api_token == "rotated-secret"

# copy.replace is the non-PATCH route for trusted Python changes.
disabled = replace(updated, enabled=False)
assert disabled.enabled is False

schema = UserPatch.json_schema(mode="input")
definitions = cast(dict[str, object], schema["$defs"])
patch_schema = cast(dict[str, object], definitions["UserPatch"])
assert patch_schema["type"] == "object"
assert patch_schema.get("required", []) == []
properties = cast(dict[str, object], patch_schema["properties"])
assert "displayName" in properties
