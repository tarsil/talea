# Release Notes

## 0.1.0

### Added

- Compiled `Spec.from_mapping` construction for untrusted Mapping input with
  nested Spec construction, strict Python container semantics, structured
  missing/unexpected fields, and deterministic independent-field aggregation.
- Strict `Spec.from_json` decoding with a replaceable per-call decoder,
  duplicate-key and non-finite-token rejection in the standard-library path,
  precision-safe Decimal tokens, and schema-aware standard-library types.
- Dedicated Mapping/JSON timing, allocation, codec, and declaration-cost
  benchmarks plus production input-boundary documentation.
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
