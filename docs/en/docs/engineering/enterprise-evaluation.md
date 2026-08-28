# Enterprise evaluation

## Product and footprint

Talea is designed as a 2026+ Python data-contract library: it starts at Python
3.14 and modern typing rather than carrying legacy-version compatibility. It
provides strict immutable Specs, arbitrary Contracts, external Mapping
and JSON input, serialization, structured failures, tagged unions, recursive
and generic types, PATCH derivation, introspection, and standards projection.
It targets Python 3.14+, is pure Python, and declares zero required runtime
dependencies.

## Review questions

| Question | Verifiable answer |
| --- | --- |
| What must we deploy? | Talea and a supported Python interpreter; no required runtime dependency graph |
| How predictable are boundaries? | strict Python semantics and separate external Mapping/JSON conversion APIs |
| Can APIs expose standard schemas? | Draft 2020-12 and OpenAPI 3.1-compatible Schema Object projection |
| What happens with hostile payloads? | finite transport, depth, compiled-work, and error budgets on external input |
| Can secrets leak through standard errors? | `Sensitive` redacts Talea-owned failures and representations; trusted callbacks remain caller-owned |
| What is the performance model? | compile once, execute specialized Python, and compare semantically described workloads with manually written Python and libraries |
| What is the lock-in story? | ordinary Python annotations and values, Mapping/JSON boundaries, standards schemas, and explicit documented APIs |

These answers describe technical behavior. They are not procurement claims,
certifications, service levels, or predictions about ecosystem adoption.

## Security and failure model

Default external boundaries apply finite transport, depth, node, and error
budgets. Validation failures expose stable codes and structured locations;
resource rejection is a separate `ResourceLimitError`. Sensitive declarations
redact Talea-owned failure surfaces. Callbacks, codecs, regex runtime, malicious
Mapping behavior, output size, and schema tooling remain caller-owned.

## Integration fit

- JSON Schema Draft 2020-12 and OpenAPI 3.1-compatible Schema Objects;
- framework-neutral request/response boundaries;
- tagged event and message contracts with discriminator projection;
- presence-aware REST PATCH contracts;
- `Contract` for `TypedDict`, aliases, recursive roots, and primitive/container
  roots without a wrapper Spec;
- immutable public introspection for framework adapters.

Talea does not generate routes or full OpenAPI documents. It supplies schema
fragments to framework-owned routing and document assembly.

## Performance governance

The repository has distinct benchmarks for construction, validation, Mapping,
JSON, serialization, errors, tagged unions, Contract, resources, schemas,
memory, and allocations. Comparisons call out semantic differences. See
[Performance](performance.md) for exact tasks.

## Maturity and support

The current version is in Talea's ongoing 0.x series. Public behavior is tested
with enforced 100% line coverage and static typing gates in the repository, but
compatibility and support governance remain intentionally evolvable across 0.x
releases. No compliance certification, service-level agreement, or bank
approval is claimed.

Review [known limitations](limitations.md), [comparison](comparison.md),
[security architecture](security.md), [version and support](../release-ledger.md),
and the [public API reference](../reference/api.md) before approval.
