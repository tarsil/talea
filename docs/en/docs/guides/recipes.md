# Recipes

This page routes application tasks to the page that owns their full executable
example. The examples reuse account/identity, payment events, trading, and
recursive document domains so each guide can build on familiar contracts.

## Accept an API request body

Use `Spec.from_json()` at the raw-body seam, select a finite `ResourcePolicy`,
and catch resource exhaustion separately from contract invalidity:

```python
try:
    request = UserCreate.from_json(body, policy=request_policy)
except ResourceLimitError as error:
    return resource_failure(error)
except ValidationError as error:
    return invalid_request(error.errors())
```

When one source Spec carries read/write metadata, derive the request and
response shapes once with `derive_spec(..., mode="input")` and
`derive_spec(..., mode="output")`; normal source behavior remains unchanged.
Do application work only after conversion succeeds, then construct the explicit
response Spec and call `to_json()`. The [production service
boundary](../getting-started/production-service.md) owns a complete asserted
account flow with nested address/credentials, aliases, constraints, redaction,
invalid input, oversized transport, output, and OpenAPI fragments.

## Apply a REST PATCH

Derive the update contract once from the writable/input direction:

```python
UserPatch = derive_spec(User, mode="input", partial=True)
patch = UserPatch.from_json(body)
updated = apply_patch(existing, patch)
```

Do not use `None` to mean absent and do not merge dictionaries. The [PATCH and
presence guide](../presence-derived-contracts.md#complete-rest-patch-example)
owns the executable empty/nullable/default-equal/aliased/sensitive/failure/schema
flow.

## Dispatch an event protocol

Use a tagged union when every message has one stable required literal protocol
field. `Discriminator("type")` selects the branch directly at Mapping/JSON
input and supplies OpenAPI's discriminator map.

The [tagged event guide](../tagged-unions.md#production-event-stream) owns four
real event branches, generic envelopes, UUID/datetime/Decimal representations,
Sensitive data, invalid and nested failures, JSON output, JSON Schema, and
OpenAPI. Keep untagged unions when no genuine protocol tag exists.

## Validate a TypedDict or arbitrary root

Retain a `Contract` instead of inventing a wrapper Spec:

```python
payloads: Contract[list[PartnerPayload]] = Contract(list[PartnerPayload])
values = payloads.from_json(body)
```

The [Contract boundary set](../contracts.md#complete-executable-boundary-set)
covers primitive, UUID-list, Decimal-mapping, TypedDict, recursive alias, and
generic specialization roots across validation, input, output, schemas, policy,
and errors.

## Protect a hostile external boundary

Choose limits from the actual endpoint shape and separate
`ResourceLimitError` from a possibly truncated `ValidationError`. Mark secret
fields Sensitive, but use explicit output contracts to prevent valid secrets
from being serialized.

The [security scenarios](../resource-security.md#executable-hostile-input-scenarios)
execute oversized JSON, excessive depth, node exhaustion, broad invalid input,
redaction, and a hostile Mapping callback. The page also says what Talea cannot
sandbox.

## Model financial values without floats

Use UUID for identifiers, Decimal for exact quantities/prices, enums or
Literals for protocol vocabulary, timezone-aware datetime when the domain
requires instants, and aliases for external protocol names. Keep venue,
permission, credit, accounting, settlement, and compliance rules in the domain
layer.

The [supported-type finance example](../supported-types.md#financial-composition-example)
owns the full Order/Instrument/Money/Trade flow with constraints, a cross-field
currency invariant, failure handling, serialization, and schema projection.

## Represent a recursive document or AST

Choose the simplest recursive owner:

- a recursive Spec for one nominal node shape;
- a recursive PEP 695 alias for structural containers;
- a recursive TypedDict for dictionary identity;
- a tagged recursive alias for a protocol/grammar with node kinds.

The [recursive AST](../recursive-generics.md#recursive-tagged-ast) combines
TypedDict branches, direct tags, nested error paths, schemas, OpenAPI, and the
important distinction between a recursive type graph and a cyclic runtime
value.

## Publish JSON Schema or OpenAPI

Use `json_schema(mode="input" | "output")` for Draft 2020-12. Use
`openapi_schema(...)` when a framework needs a root Schema Object plus reachable
components. Talea does not generate paths or operations.

The [framework projection example](../json-schema-openapi.md#complete-framework-projection-example)
combines nested objects, aliases, metadata, constraints, read/write annotations,
Sensitive handling, a partial PATCH schema, a tagged event, and a discriminator
map.

## Build trusted declarations at runtime

Use `create_spec()` only when runtime configuration genuinely owns the fields.
Pass evaluated annotations and keep callbacks in a trusted namespace. Prefer
class syntax when fields are known in source so constructor typing stays
precise.

The [dynamic lifecycle](../dynamic-utilities.md#executable-dynamic-lifecycle)
executes creation, defaults, metadata, constraints, hooks, Mapping/JSON input,
introspection, immutable replacement, failures, and schema projection.

## Inspect a contract for framework tooling

Use `inspect_spec()` or `inspect_contract()` and consume frozen `FieldInfo`,
`SpecInfo`, `DerivationInfo`, and `ContractInfo`. Do not reread annotations or
reach into generated callables to reconstruct semantics.

The [framework adapter example](../reference/introspection.md#framework-adapter-example)
projects aliases, requiredness, omittability, constraints, metadata, Sensitive
state, partial provenance, and arbitrary Contract truth into a smaller
framework-owned descriptor.

## Advanced composition lab

This compact executable lab intentionally places generics, a recursive alias,
a recursive tagged Spec graph, metadata, aliases, Sensitive/read/write fields,
ResourcePolicy, Mapping/JSON input, PATCH derivation/application, Contract, and
OpenAPI in one declaration graph:

{!> ../../../../docs_src/tutorials/advanced_contracts.py !}

Use the focused pages above to learn each decision before adopting this density
in application code. Composition is valuable when the domain requires it;
putting every capability in every contract only increases cognitive and
declaration cost.

## Operation lookup

| Goal | API | Canonical reference |
| --- | --- | --- |
| Serialize an API response | `to_dict()`, `to_json()` | [Serialization](../serialization.md) |
| Decode a custom JSON codec | `from_json(..., loads=codec)` | [Input](../input-boundaries.md) |
| Encode a custom JSON codec | `to_json(dumps=codec)` | [Serialization](../serialization.md) |
| Constrain a value | `Annotated[T, Ge(...), MinLength(...)]` | [Constraints](../constraints.md) |
| Handle machine failures | `ValidationError.errors()` | [Errors](../error-experience.md) |
| Perform trusted immutable change | `copy.replace()` | [Immutable updates](../reference/immutable-updates.md) |
| Check exact signatures and failures | root/domain APIs | [Public API](../reference/api.md) |
| Diagnose common mistakes | broken and corrected code | [Troubleshooting](../engineering/troubleshooting.md) |
