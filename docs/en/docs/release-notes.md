# Release Notes

## 0.1.0

### Added

- First-class `Discriminator` declarations and finite immutable tagged-union
  schema truth derived from required single-value Literal fields.
- Direct tagged branch selection for strict Spec values, Mapping and JSON
  input, TypedDict contracts, Python output, and JSON output, including aliases,
  optional values, concrete generics, recursive Spec graphs, and introspection.
- Stable `discriminator_missing` and `discriminator_unknown` errors with exact
  nested locations, machine-readable discriminator/tag context, and sensitive
  tag redaction.
- Dedicated tagged-union typing contracts, security and declaration tests, and
  branch-count/input/output performance benchmarks.
- Canonical immutable field, Spec, Contract, TypedDict, and type-alias metadata
  for title, description, examples, deprecation, read-only, write-only, and
  sensitive classification.
- Sensitive validation and serialization failure policy with default redaction,
  raw-value discard, cause removal, nested path propagation, immutable public
  introspection, adversarial tests, and dedicated benchmarks.
- Retained `Contract[T]` validation, external Python conversion, JSON input,
  Python projection, and JSON output for arbitrary supported roots without
  wrapper Specs.
- First-class TypedDict, PEP 695 type-alias, generic alias, and `NewType`
  canonical schema support with strict structural input/output semantics.
- `create_spec()` for normal dynamically declared Spec subclasses with explicit
  fields, defaults, factories, identity, inheritance, and trusted namespace
  contributions.
- Immutable public `FieldInfo`, `SpecInfo`, and `ContractInfo` views in
  `talea.introspection`.
- Python-native immutable reconstruction through `copy.replace`, including
  changed-value validation, mutable current-state checks, and whole-Spec
  invariants.
- Dedicated Contract, dynamic Spec, replacement, introspection, cold/warm,
  memory, and retained-owner benchmarks.
- Python 3.14 generic Specs with concrete weakly cached specializations,
  bounds, constraints, parameter defaults, nested generic composition, generic
  inheritance, and recursive generic schemas.
- Deferred forward-reference graph finalization for self and mutually recursive
  Specs without a global registry, plus recursive Mapping/JSON conversion and
  Python/JSON serialization.
- Deliberate cyclic-input and cyclic-serialization failures with exact paths,
  recursion-safe current-state validation, copy/deep-copy support, and acyclic
  pickle reconstruction for importable Spec classes.
- Dedicated generic cold/hot specialization, equivalent concrete construction,
  recursive traversal, cycle detection, and specialization-retention benchmarks.
- Lazy compiled `Spec.to_dict()` Python projection and `Spec.to_json()` strict
  JSON encoding with detached container output and nested Spec serialization.
- Precision-safe Decimal strings, exact ISO 8601 timedelta durations, strict
  base64 bytes, standard-library/Enum/Literal symmetry, and per-call custom
  `dumps` normalization to `str`.
- Canonical `Alias` field metadata, top-level include/exclude/exclude-none
  policies, and inherited `@serialize` field hooks with a separate
  `SerializationError` domain.
- Dedicated Python projection, JSON projection, codec, declaration, first-use,
  allocation, and memory benchmarks plus production serialization docs.
- Compiled `Spec.from_mapping` construction for untrusted Mapping input with
  nested Spec construction, strict Python container semantics, structured
  missing/unexpected fields, and deterministic independent-field aggregation.
- Strict `Spec.from_json` decoding with a replaceable per-call decoder,
  duplicate-key and non-finite-token rejection in the standard-library path,
  precision-safe Decimal tokens, and schema-aware standard-library types.
- Dedicated Mapping/JSON timing, allocation, codec, and declaration-cost
  benchmarks plus production input-boundary documentation.
- Lazy, independently cached Mapping and JSON boundary compilation, with a
  complete-dict execution path that preserves aggregation while restoring
  ordinary Spec declaration cost and converging on equivalent handwritten
  Mapping performance.
- Public `ValidationError`, `ErrorCode`, and typed JSON-compatible `ErrorData`
  projection with structured locations and stable machine codes.
- Talea-native multiline rendering, bounded hostile-input representation,
  compact union branch diagnostics, and unified custom-validation failures.
- Structured factory execution failures with preserved exception causes and a
  dedicated error creation, rendering, projection, and allocation benchmark.
- Explicit `transform` declarations for inbound field conversion before normal
  strict structural validation.
- Post-structural `check` declarations for field assertions and atomic
  cross-field invariants, including deterministic inheritance and override
  semantics.
- Custom failure transport and specialized current-state revalidation of nested
  mutable Spec invariants.
- Campaign 6 strict support for Literal, enums, UUID, temporal values, Decimal,
  pathlib paths, and IPv4/IPv6 address, network, and interface families.
- Immutable `Gt`, `Ge`, `Lt`, `Le`, `MultipleOf`, `MinLength`, `MaxLength`, and
  `Pattern` declarations carried by `Annotated` and compiled into specialized
  checks.
- Early constraint applicability, normalization, contradiction detection, and
  constrained-field covariance.
- Added `talea.Spec` with required keyword-only fields, strict construction,
  compact slots, compile-once declaration processing, and field-aware errors.
- Added validated static defaults, `field(default_factory=...)`, mutable-default
  safety, and immutable Spec instances.
- Added nominal nested Spec fields across containers and unions, transitive
  trust classification, inherited fields, covariant field overrides, and flat
  specialized subclass construction.
- Added compact multiple inheritance for one state-bearing Spec lineage and
  empty-slotted method mixins.

### Changed

- Spec constructors now inline strict field-validation operations from the same
  compiler owner used for standalone validators, eliminating per-field Python
  validator calls during construction.
- Nested Spec references that are not permanently trusted now receive
  specialized current-state validation at each new validation boundary.

### Fixed
