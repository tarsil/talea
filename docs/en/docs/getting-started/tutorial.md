# Tutorial: from record to production boundary

This tutorial grows one account contract from strict Python storage into an
external API boundary. It introduces each operation separately so conversion,
validation, output, errors, security, and schemas never blur into one pipeline.

## 1. Declare an immutable record

```python
from talea import Spec, field


class Address(Spec):
    line_1: str
    city: str
    postcode: str
    country: str


class Account(Spec):
    account_id: int
    display_name: str
    address: Address
    labels: list[str] = field(default_factory=list)
```

Every field without a default or factory is required, even if its type accepts
`None`. Construction is keyword-only and strict. Nested values on this path are
already-valid Python objects:

```python
address = Address(
    line_1="1 Analytical Engine Way",
    city="London",
    postcode="SW1A 1AA",
    country="GB",
)
account = Account(account_id=7, display_name="Ada", address=address)
```

`Account(account_id="7", ...)` fails: a string is not an integer. Likewise, a
dictionary is not an Address on the strict constructor path. This gives
application code a stable contract and keeps conversion attached to visible
external operations.

The factory runs once when `labels` is omitted, and each account receives a new
list. Spec field bindings are immutable; the nested list remains an ordinary
mutable Python value and will be revalidated at later boundaries.

## 2. Add aliases, constraints, and sensitive data

```python
from typing import Annotated

from talea import Alias, MaxLength, MinLength, Sensitive, WriteOnly


class Credentials(Spec):
    token: Annotated[str, Sensitive(), WriteOnly(), MinLength(16)]


class AccountCreate(Spec):
    display_name: Annotated[
        str,
        Alias("displayName"),
        MinLength(1),
        MaxLength(80),
    ]
    address: Address
    credentials: Credentials
```

`display_name` remains the Python attribute and static type. `displayName` is
the external Mapping/JSON/output name. Constraints are resolved and normalized
once; contradictory or inapplicable combinations fail at declaration rather
than waiting for a request.

Sensitive redacts Talea-owned repr and validation failures. `WriteOnly` appears
in standards metadata. Neither marker deletes a successfully validated field
from output automatically, so a service should declare a separate response
contract that never contains credentials.

## 3. Convert an external Mapping

```python
request = AccountCreate.from_mapping(
    {
        "displayName": "Ada Lovelace",
        "address": {
            "line_1": "1 Analytical Engine Way",
            "city": "London",
            "postcode": "SW1A 1AA",
            "country": "GB",
        },
        "credentials": {"token": "correct-horse-battery-staple"},
    }
)

assert request.display_name == "Ada Lovelace"
assert isinstance(request.address, Address)
```

`from_mapping()` accepts an external `Mapping` and constructs nested Specs from
nested mappings. Primitive Python values remain strict: an integer field still
does not accept a numeric string. Unknown keys fail instead of being discarded.

This boundary applies the finite default depth, work-node, and error budgets.
Pass a narrower `ResourcePolicy` when the endpoint's maximum shape is known.

## 4. Decode JSON representations

```python
body = b"""{
  "displayName": "Ada Lovelace",
  "address": {
    "line_1": "1 Analytical Engine Way",
    "city": "London",
    "postcode": "SW1A 1AA",
    "country": "GB"
  },
  "credentials": {"token": "correct-horse-battery-staple"}
}"""

request = AccountCreate.from_json(body)
```

`from_json()` checks encoded size, decodes strict JSON, converts each declared
JSON representation, and validates. The default decoder rejects duplicate
object keys and non-standard NaN/Infinity constants. UUID, Decimal, temporal,
IP, path, and bytes contracts use documented JSON strings because JSON lacks
those Python value types.

Custom codecs can replace only decoding; Talea's compiled conversion still
runs afterward. The custom callable is trusted application code and is not
sandboxed by ResourcePolicy.

## 5. Handle realistic failures

```python
from talea import ResourceLimitError, ResourcePolicy, ValidationError


try:
    request = AccountCreate.from_json(
        body,
        policy=ResourcePolicy(
            max_input_bytes=4_096,
            max_depth=8,
            max_nodes=200,
            max_errors=10,
        ),
    )
except ResourceLimitError as error:
    handle_resource_failure(error.code, error.limit, error.observed)
except ValidationError as error:
    handle_invalid_request(error.errors(), truncated=error.truncated)
```

`ValidationError.errors()` returns fresh JSON-compatible details with stable
codes and nested locations. Consume those facts; do not parse human strings.
Sensitive failure snapshots are redacted. A configured error budget can stop
independent aggregation and set `truncated=True`.

`ResourceLimitError` is different: transport size, depth, or node work exceeded
the selected policy. The exception retains only its numeric facts, not the
payload. Your framework decides status codes, logging, retry, and metrics.

## 6. Return an explicit response

```python
from typing import Literal


class AccountResponse(Spec):
    account_id: Annotated[int, Alias("id")]
    display_name: Annotated[str, Alias("displayName")]
    status: Literal["active"] = "active"


response = AccountResponse(
    account_id=7,
    display_name=request.display_name,
)
response_body = response.to_json()

assert response_body == '{"id":7,"displayName":"Ada Lovelace","status":"active"}'
```

The response type is an allow-list. It contains no credentials, regardless of
what the request type can serialize. `to_dict()` produces detached Python
containers; `to_json()` applies JSON-specific representations and encoding.
Both validate reachable mutable current state before projection.

Use `include`, `exclude`, `exclude_none`, and `by_alias` for deliberate local
views, but prefer named response Specs when an output shape is a public
contract.

## 7. Publish input and output schemas

```python
input_fragment = AccountCreate.openapi_schema(mode="input")
output_fragment = AccountResponse.openapi_schema(mode="output")

assert input_fragment["schema"]["$ref"].endswith("/AccountCreate")
assert output_fragment["schema"]["$ref"].endswith("/AccountResponse")
```

The fragments contain a root Schema Object and reachable components. A web
framework owns routes, operations, request/response placement, and component
merging. JSON Schema projection uses Draft 2020-12 and the same aliases,
requiredness, constraints, metadata, recursion, and tags as runtime behavior.

## 8. Add production composition deliberately

From here, add only the features the boundary needs:

- [Production service flow](production-service.md): complete raw bytes to
  response, including resource and validation errors.
- [PATCH and presence](../presence-derived-contracts.md): absence, `None`,
  defaults, aliases, `apply_patch`, invariants, and schemas.
- [Tagged events](../tagged-unions.md): direct protocol dispatch, failures,
  generic envelopes, output, and OpenAPI mappings.
- [Contract](../contracts.md): containers, TypedDicts, aliases, recursive roots,
  and generic specializations without wrapper records.
- [Errors](../error-experience.md) and [security](../resource-security.md):
  application handling and hostile-input limits.
- [Troubleshooting](../engineering/troubleshooting.md): broken and corrected
  examples for common boundary mistakes.

Keep authentication, authorization, database uniqueness, external I/O,
transaction behavior, and changing business policy outside structural data
contracts. Talea should make a boundary explicit, not absorb the application.
