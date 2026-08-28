# Production service boundary

A service boundary has three responsibilities: protect the process before and
during conversion, turn external representations into a validated application
value, and return failures without exposing secrets or implementation details.
It should distinguish invalid data from exhausted resource budgets because the
two failures have different operational meaning.

The example below is deliberately framework-neutral. `handle_create()` could
sit inside a FastAPI, Lilya, Django, Starlette, Flask, queue-consumer, or RPC
adapter without making Talea depend on any of those systems. It covers raw
bytes, nested contracts, aliases, constraints, Sensitive credentials, a finite
`ResourcePolicy`, structured validation errors, an application operation, a
separate response contract, JSON output, and input/output OpenAPI fragments.

{!> ../../../../docs_src/tutorials/production_service.py !}

## Compose the 0.2 boundary capabilities

Talea 0.2 can keep a standard-library dataclass as a domain representation,
validate and construct it through `Contract`, embed it in one canonical Spec
API contract, derive explicit request/response/PATCH shapes, and project only
the nested response fields an endpoint needs. No duplicate dataclass Spec,
hand-maintained read/write field list, or recursive post-serialization filter
is required.

The following executable flow uses canonical names for selection while aliases
remain the emitted external names. The output-derived view cannot contain the
write-only Sensitive password, the input-derived partial cannot contain the
read-only request identifier, and `apply_patch()` preserves the source
contract's complete-state validation.

{!> ../../../../docs_src/tutorials/python_interoperability.py !}

## Follow the ownership boundary

`UserCreate.from_json()` owns JSON decoding and contract conversion. Once it
returns, `create_user()` receives a complete immutable request and can focus on
domain behavior. It does not receive a partly populated model, and it does not
need to rediscover whether `displayName`, UUID text, or a nested address was
valid.

`StoredUser` is an application-facing value in this example. `UserResponse` is
the external output contract. Keeping them separate prevents request-only
credentials from accidentally appearing merely because one class was reused
for every layer. `Sensitive` protects Talea-owned error/repr surfaces; it is not
an instruction to remove a successfully validated field from serialization.
Designing a response contract is therefore the primary allow-list.

The example maps malformed contract data to 422 and resource rejection to 413,
but those numbers are illustrative application policy. Talea does not prescribe
HTTP status codes, exception middleware, routes, authentication, or persistence.

## Add PATCH without losing presence

For a partial update, derive the boundary once near the source declaration:

```python
UserPatch = derive_spec(
    User,
    exclude=("user_id",),
    partial=True,
    name="UserPatch",
)

patch = UserPatch.from_json(raw_body, policy=request_policy)
changed = patch.present_fields
updated = apply_patch(existing_user, patch)
```

`present_fields` uses canonical Python names even when JSON uses aliases. An
empty object has no changes; explicit `None` is a change only for an optional
field; and explicitly sending the current default remains a change. Applying
the patch creates a complete candidate and reruns whole-Spec invariants before
returning it. The executable [PATCH example](../presence-derived-contracts.md)
covers empty, default-equal, nullable, aliased, sensitive, invalid, and
whole-object cases.

## Publish schemas without giving Talea route ownership

`UserCreate.openapi_schema(mode="input")` and
`UserResponse.openapi_schema(mode="output")` return two keys: a root `schema`
fragment and reusable `components`. A framework adapter can attach the root to
its request or response description and merge the components into the
framework-owned OpenAPI document. Talea does not generate paths or operations.

Use input and output modes deliberately. They can differ in requiredness and
JSON representation, and callback-defined transforms or serializers can make
one direction impossible to describe statically. See [JSON Schema and
OpenAPI](../json-schema-openapi.md) for exact projection behavior.

## Failure and operational guidance

- Consume `ValidationError.errors()` and stable codes; do not parse rendered
  strings.
- Log request identifiers and bounded error details, not raw bodies.
- Treat `ResourceLimitError.code`, `limit`, and `observed` as operational data.
- Set a stricter application policy when the endpoint shape is known. Do not
  disable every dimension merely because one valid document exceeded a limit.
- Keep authentication, authorization, uniqueness, database state, and business
  rules in the application layer unless they are truly value invariants.

`ResourcePolicy` governs Talea-owned transport and compiled input work. It does
not sandbox custom JSON codecs, Mapping implementations, callbacks, or regular
expressions. Continue with the [resource/security model](../resource-security.md),
[error handling](../error-experience.md), and [troubleshooting](../engineering/troubleshooting.md).
