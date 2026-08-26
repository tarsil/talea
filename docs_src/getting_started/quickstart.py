"""Five-minute Talea quickstart used by the documentation."""

from typing import Annotated, cast

from talea import MinLength, Spec, ValidationError


class User(Spec):
    id: int
    name: Annotated[str, MinLength(1)]
    active: bool = True


user = User(id=1, name="Ada")
assert user.to_dict() == {"id": 1, "name": "Ada", "active": True}

decoded = User.from_json('{"id":2,"name":"Grace","active":false}')
assert decoded.to_dict() == {"id": 2, "name": "Grace", "active": False}

try:
    User(id=cast(int, "1"), name="Ada")
except ValidationError as error:
    assert error.errors()[0]["code"] == "type"
    assert error.errors()[0]["location"] == ["id"]
else:
    raise AssertionError("strict construction must reject a string identifier")

schema = User.json_schema()
assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
