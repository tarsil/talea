# Version, maturity, and support

Talea is currently version 0.5.0 and deliberately remains in the 0.x release
series. The implemented runtime is substantial, but compatibility, deprecation
windows, long-term support, and formal vulnerability-reporting governance are
not yet frozen. The 0.x series is an ongoing product stage, not an indication
that a 1.0 compatibility freeze is imminent.
Meaningful 0.4.x, 0.5.x, 0.6.x, and later 0.x releases are expected over the
coming years; there is no declared 1.0 target date.

## Implemented product surface

The release includes Specs, Contracts, strict validation, constraints,
standard-library types, defaults/factories, inheritance, recursive and generic
types, stdlib dataclass Contracts, TypedDict and PEP 695 aliases, tagged unions, Mapping/JSON input,
serialization, structured errors, metadata and redaction, presence-aware
derived/PATCH Specs, explicit input/output derived views, introspection, dynamic creation, JSON Schema Draft 2020-12,
OpenAPI 3.1-compatible projection, and finite external-input resource policies.
Talea 0.3.0 added root-public `Representation` contracts for explicit
custom domain types across directional input/output, standards projection,
introspection, and nested selection, plus optional declared output contracts on
field serializers. Talea 0.4.0 adds strict compiled callable boundaries for
synchronous and asynchronous functions, complete Python parameter binding,
methods and descriptors, return validation, typing, and immutable
introspection. Talea 0.5.0 adds finite historical Mapping and JSON input names
through `Alias(..., legacy=(...))`, with conflict rejection and current-only
output across Specs, dataclasses, tagged unions, derived contracts, JSON Schema,
OpenAPI, and introspection.

The current unreleased development surface adds an import-isolated
`talea.settings` subpackage. It loads concrete Specs through one immutable plan
with explicit Mapping/environment/local-secrets/TOML sources, canonical-leaf
precedence, schema-directed textual decoding, value-free provenance, bounded
source acquisition, and no required dependency. This describes the development
checkout and does not announce a 0.6.0 release.
The same unreleased surface adds lazy retained-Contract item validation for
synchronous Python iterables, with strict and external modes, canonical indexed
errors, explicit continuation, and finite item/invalid-item limits. It also adds
bounded JSON Lines input for strict UTF-8 text/bytes record iterables, reusing
the canonical JSON decoder and retained external Contract artifact with safe
one-based framing errors, explicit continuation, and separate line/total byte
limits. This remains development-checkout behavior, not a 0.6.0 release.
The unreleased development surface also supports annotated
`typing.NamedTuple` declarations through one canonical positional schema:
exact nominal strict validation, exact list/tuple and JSON-array input,
tuple/array output, trailing defaults, concrete generics, recursion,
composition, immutable introspection, and Draft 2020-12/OpenAPI array
projection. It does not add Mapping compatibility, a root export, or a runtime
dependency, and it does not announce a 0.6.0 release.

## Deliberate boundaries

| Capability | Current disposition |
| --- | --- |
| callable argument/return validation | Implemented for synchronous and asynchronous functions and methods through `validate_call`; generators, async generators, callable instances, runtime generic-function specialization, callable `ResourcePolicy`, timeouts, and retries remain outside this owner |
| explicit ReadOnly/WriteOnly input/output Spec views | Implemented through declaration-time `derive_spec(mode=...)` selection |
| nested runtime output projection | Implemented through finite canonical-name include/exclude trees on `Spec.to_dict()` and `Spec.to_json()`; schema/OpenAPI remain unchanged |
| automatic runtime ReadOnly/WriteOnly enforcement | Deliberately absent; ordinary source-Spec behavior remains unchanged |
| NamedTuple and ordinary-class mapping | Annotated `typing.NamedTuple` has positional list/tuple and array interoperability; object/Mapping compatibility and ordinary-class mapping remain deliberately absent |
| stdlib dataclass boundaries | Implemented through `Contract`; no ORM-style attribute extraction |
| settings/environment loading | Implemented in the separate import-isolated `talea.settings` owner on the current unreleased development surface; no root exports, source registry, watcher, framework lifecycle, or remote sources |
| incremental records and JSONL | Synchronous Python item iteration and JSONL input are implemented through retained `Contract(T)`; JSONL output and async iteration are not implemented |
| arbitrary annotation callbacks | Explicit per-position `Representation` contracts are implemented; no registry/discovery |
| field-local serializer output truth | Optional `@serialize(..., output=...)` contracts are implemented; undeclared hooks remain opaque |
| retained global codec/Contract registries | Rejected for core; application boundaries own retained objects |
| `Any`/`object` passthrough contracts | Rejected because they erase contract truth |
| abstract Mapping/Sequence conversion | Rejected because concrete output shape is ambiguous |
| ORM attribute extraction | Rejected for core; an integration must own lazy access and errors |
| output/schema resource governance | Caller-owned in the current threat model |
| migration warnings, telemetry, and retirement timing | Application-owned; Talea declares finite accepted names and does not run a migration lifecycle |
| TypedDict key migration names | Not implemented; migration names belong to Spec fields, stdlib dataclass fields, and compatible tagged Spec discriminators |
| API-version negotiation | Application or framework-owned, not a Talea input-name policy |

The complete operational list is on [Known limitations](engineering/limitations.md).

## Quality evidence

Repository acceptance requires tests, enforced 100% line coverage, Ruff lint
and formatting, `ty` contracts, executable documentation examples, internal
navigation/link validation, documentation and package builds, and permanent
benchmark tasks. A passing development checkout is evidence for that commit;
it is not a promise that every downstream environment is identical.

Release history belongs in [Release notes](release-notes.md). Contributor
workflow is documented in [Contributing](contributing.md).

## 0.5.0 owner and evidence

`Alias(name, *, legacy=())` is the only declaration surface for migration-safe
field names. The resolved `SpecField`, `DataclassField`, and tagged discriminator
schema retain the ordered current-plus-historical input vocabulary; compiled
Mapping/JSON operations, standards projection, and immutable introspection
consume that truth. Serialization consumes only the current external name.
There is no migration registry, precedence rule, warning engine, telemetry, or
retirement policy.

Permanent acceptance evidence is grouped rather than duplicated into one test
per claim:

- `tests/test_aliases.py`, `tests/test_alias_composition.py`, and
  `tests/test_alias_schema_projection.py` cover declaration, conflicts,
  inheritance, derivation, PATCH, views, generics, recursion, dataclasses,
  tagged dispatch, errors, security, introspection, JSON Schema, and OpenAPI;
- the general Spec, dataclass, tagged-union, schema, serialization, resource,
  representation, and callable suites remain zero-feature regression canaries;
- the Mapping/JSON, presence, recursive/generic, dataclass, tagged, and JSON
  Schema benchmark tasks measure migration execution, equivalent conflict
  detection, direct dispatch, compositional projection growth, allocations,
  retention, and no-migration paths;
- release acceptance additionally requires Python 3.14 quality gates, the
  configured Python 3.15 CI lane, checked wheel/sdist metadata and contents,
  and an isolated no-dependency wheel smoke test.
