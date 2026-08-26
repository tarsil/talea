"""A recursive, tagged expression language with direct JSON dispatch."""

from typing import Annotated, Literal, TypedDict, cast

from talea import Contract, Discriminator, MaxLength, ValidationError


class SourceLocation(TypedDict):
    line: int
    column: int


class LiteralExpression(TypedDict):
    type: Literal["literal"]
    value: int | str | bool | None
    location: SourceLocation


class BinaryExpression(TypedDict):
    type: Literal["binary"]
    operator: Literal["+", "-", "*", "/"]
    left: Expression
    right: Expression
    location: SourceLocation


class FunctionCall(TypedDict):
    type: Literal["call"]
    function: Annotated[str, MaxLength(80)]
    arguments: list[Expression]
    location: SourceLocation


type Expression = Annotated[
    LiteralExpression | BinaryExpression | FunctionCall,
    Discriminator("type"),
]


expressions: Contract[Expression] = Contract(Expression)
source = """{
  "type": "call",
  "function": "round",
  "arguments": [
    {
      "type": "binary",
      "operator": "/",
      "left": {"type": "literal", "value": 10, "location": {"line": 1, "column": 7}},
      "right": {"type": "literal", "value": 3, "location": {"line": 1, "column": 12}},
      "location": {"line": 1, "column": 7}
    }
  ],
  "location": {"line": 1, "column": 1}
}"""

expression = expressions.from_json(source)
assert expression["type"] == "call"
assert expression["arguments"][0]["type"] == "binary"
assert expression["arguments"][0]["right"]["type"] == "literal"
assert expressions.from_json(expressions.to_json(expression)) == expression

try:
    expressions.from_json(source.replace('"value": 3', '"value": []'))
except ValidationError as error:
    detail = error.errors()[0]
    assert detail["code"] == "union"
    assert detail["location"] == ["arguments", 0, "right", "value"]
else:
    raise AssertionError("a list is not a supported literal value")

try:
    expressions.from_json(source.replace('"type": "binary"', '"type": "unknown"'))
except ValidationError as error:
    assert error.errors()[0]["code"] == "discriminator_unknown"
    assert error.errors()[0]["location"] == ["arguments", 0, "type"]
else:
    raise AssertionError("an unknown recursive node tag must fail")

schema = expressions.json_schema()
assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
definitions = cast(dict[str, object], schema["$defs"])
assert {"LiteralExpression", "BinaryExpression", "FunctionCall"} <= set(definitions)

openapi = expressions.openapi_schema()
components = cast(dict[str, object], openapi["components"])
schemas = cast(dict[str, object], components["schemas"])
root = cast(dict[str, object], schemas["Expression"])
assert cast(dict[str, object], root["discriminator"])["propertyName"] == "type"

# The declaration is a finite recursive type graph. A Python container that
# contains itself is a cyclic runtime value and is rejected on Python input or
# output rather than being mistaken for valid recursion.
type NestedIntegers = int | list[NestedIntegers]
nested: Contract[NestedIntegers] = Contract(NestedIntegers)
cyclic: list[object] = []
cyclic.append(cyclic)
try:
    nested.from_python(cyclic)
except ValidationError as error:
    assert error.errors()[0]["code"] == "cycle"
else:
    raise AssertionError("cyclic runtime data must not recurse forever")
