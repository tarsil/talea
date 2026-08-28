# Release Notes

## Unreleased

### Added

- Standard-library dataclasses can be consumed directly through `Contract`
  across exact current-state validation, Mapping and JSON construction,
  detached Python/JSON output, resource policy, introspection, JSON Schema, and
  OpenAPI. The original dataclass, constructor lifecycle, defaults,
  `__post_init__`, equality, hashing, and pickle behavior remain unchanged.

## 0.1.0 — First Public Release

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
- This is a pre-1.0 release. Public APIs, compatibility policy, deprecation
  windows, and long-term support policy may evolve prior to 1.0.
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
