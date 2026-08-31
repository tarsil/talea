# Release Notes

## Unreleased

## 0.4.0

### Added

- Python 3.15 support, with prerelease compatibility testing before the final
  Python 3.15 release. Coverage, static typing, and documentation integrity
  release gates continue to run on Python 3.14.
- `validate_call` for complete strict synchronous and asynchronous Python
  boundaries: every parameter kind, defaults, `*args`, scalar `**kwargs`,
  `Unpack[TypedDict]`, instance methods, classmethods, staticmethods, native
  Python binding, compiled argument and awaited-return validation,
  ParamSpec-preserving typing, cancellation transparency, descriptor metadata,
  and frozen `inspect_callable` projections.

## 0.3.0

### Added

- Reusable annotation-scoped `Representation` contracts for custom domain
  types, with explicit one-way or bidirectional input/output declarations.
- Representation execution across strict validation, external Python/JSON
  input, detached Python/JSON output, JSON Schema, OpenAPI, callback-free
  introspection, and nested composition through Specs, dataclasses, TypedDicts,
  containers, unions, aliases, generics, and recursive containing graphs.
- Optional declared `output=` contracts for field serializers, with callback
  result validation, canonical Python/JSON projection, output-only JSON
  Schema/OpenAPI truth, callback-free introspection, and nested output
  selection. Serializers without `output=` retain their opaque behavior.

### Fixed

- Sensitive represented input now redacts ordinary loader exceptions instead
  of allowing secret-bearing non-`ValueError` failures to escape unchanged.
- Cyclic built-in containers returned by opaque field serializers now fail with
  a located `SerializationError` instead of an unhandled recursion failure.

Callbacks remain synchronous, trusted, and ungoverned by output
`ResourcePolicy`; Representation is explicit rather than registry-driven, and
serializers without `output=` remain structurally opaque.

## 0.2.0

### Added

- `Spec.to_dict()` and `Spec.to_json()` accept finite nested canonical-name
  include/exclude mappings. Direct schema-specialized projection covers nested
  Specs, homogeneous containers, mapping values, compatible fixed tuples,
  branch-specific unions, dataclasses, TypedDicts, partials, and directional
  views while preserving aliases, recursive exclusion precedence, and the
  serializer-hook leaf boundary.
- `derive_spec(mode="input" | "output")` creates normal explicit request and
  response contract classes from canonical `ReadOnly`/`WriteOnly` metadata. It
  composes with include/exclude and partial presence, exposes immutable
  directional provenance, and permits only input-derived partials through the
  source patch path.
- Standard-library dataclasses can be consumed directly through `Contract`
  across exact current-state validation, Mapping and JSON construction,
  detached Python/JSON output, resource policy, introspection, JSON Schema, and
  OpenAPI. The original dataclass, constructor lifecycle, defaults,
  `__post_init__`, equality, hashing, and pickle behavior remain unchanged.

## 0.1.0

Talea 0.1.0 is the first public release of Talea, a pure-Python data-contract
library for Python 3.14 and newer. It provides strict validation, explicit
Mapping and JSON boundaries, serialization, structured errors, and standards
projection from one canonical schema graph, with zero required runtime
dependencies.

### Highlights

- Immutable, slotted `Spec` records with strict keyword-only construction,
  defaults and factories, inheritance, concrete generics, recursive contracts,
  constraints, transforms, checks, and serialization hooks.
- `Contract[T]` validation and conversion for arbitrary supported roots,
  including primitives, containers, unions, `TypedDict`, PEP 695 aliases,
  `NewType`, tagged unions, and concrete generic specializations.
- Separate execution paths for trusted Python construction, untrusted Mapping
  input, JSON input, Python output, JSON output, introspection, and schema
  projection.
- Presence-aware derived contracts through `derive_spec()` and `apply_patch()`,
  so PATCH-style input distinguishes an absent field from a field set to
  `None`.
- JSON Schema Draft 2020-12 and OpenAPI 3.1-compatible Schema Objects generated
  from the same contract truth used at runtime.
- Pure-Python compile-once execution with permanent performance and allocation
  canaries for distinct workloads.

### Contracts, input, and output

Ordinary `Spec` construction uses strict Python semantics. Conversion belongs
to explicit external boundaries: `from_mapping()`, `from_json()`, and the
corresponding `Contract` operations. Standard-library values such as UUIDs,
temporal values, `Decimal`, paths, IP types, bytes, enums, and literals retain
documented Python and JSON representations.

Nested and inherited Specs, recursive and generic graphs, aliases, constraints,
tagged unions, and custom validation all participate in the same structural
model. `to_dict()`, `to_python()`, and `to_json()` provide schema-aware output
with aliases, include/exclude policy, sensitive-value handling, and custom
serializers where declared.

Runtime `create_spec()` declarations, immutable `FieldInfo`, `SpecInfo`, and
`ContractInfo` views, and `copy.replace()` support make the compiled contract
available to frameworks and application tooling without requiring annotation
reconstruction.

### Errors and external-boundary policy

Validation failures expose nested locations, stable machine-readable codes,
bounded rendering, and JSON-compatible error data. Sensitive fields redact
rejected values and remove unsafe causes by default.

`ResourcePolicy` places finite limits on JSON transport size, structural depth,
compiled traversal work, and aggregated errors at untrusted Mapping and JSON
boundaries. Talea also rejects duplicate JSON object keys, non-finite numeric
tokens, cyclic runtime graphs, invalid discriminator tags, and unsupported
recursive execution shapes with explicit errors.

Custom transforms, checks, serializers, codecs, and Mapping implementations are
trusted application code. Talea does not sandbox callbacks. The release includes
a documented technical threat model, adversarial tests, property tests, and
resource benchmarks; these controls are not a claim of independent security
certification.

### Schema and documentation

`json_schema()` projects Draft 2020-12 schemas and `openapi_schema()` projects
OpenAPI 3.1-compatible Schema Objects. Both support input and output modes,
reusable definitions, recursive references, aliases, constraints, metadata,
requiredness, PATCH presence, tagged-union discriminator maps, and concrete
generic specializations.

The manual covers the mental model, supported types, input and output,
constraints, composition, recursion, tagged unions, derived contracts, errors,
resource policy, JSON Schema and OpenAPI, introspection, performance, security,
and adoption. Its substantial examples execute as part of the repository
release gates.

### Requirements and maturity

- Python 3.14 or newer is required.
- Talea is implemented in pure Python and declares zero required runtime
  dependencies.
- Talea deliberately remains in the 0.x release series. Public APIs,
  compatibility policy, deprecation windows, and long-term support policy may
  evolve between 0.x releases.
- The ecosystem and integration surface are new; Talea deliberately does not
  include settings management, ORM extraction, or callable-signature validation
  in core.

### Known limitations

Custom hooks are synchronous trusted callbacks, arbitrary transforms and
serializers cannot always be represented in JSON Schema, open generic contracts
must be specialized for execution, and external-input resource policy does not
govern trusted construction or arbitrary callback work. See the
[authoritative limitations list](engineering/limitations.md) for the complete
scope and operational boundaries.
