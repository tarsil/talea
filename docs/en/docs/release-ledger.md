# Version, maturity, and support

Talea is currently version 0.6.0 and deliberately remains in the 0.x release
series. The implemented runtime is substantial, but compatibility, deprecation
windows, long-term support, and formal vulnerability-reporting governance are
not yet frozen. Meaningful later 0.x releases will continue; there is no
declared 1.0 target date.

## 0.6.0 release identity

Talea 0.6.0 focuses on application boundaries, record ingestion, and Python
interoperability. The release retains Python 3.14 as its minimum, declares
Python 3.15 support, is implemented in pure Python, and has zero required
runtime dependencies.

The six 0.6 owners are accepted and frozen:

| Owner | Canonical authority | Permanent evidence | Remaining boundary |
| --- | --- | --- | --- |
| Python 3.15 TypeForm typing | public declarations in `Contract`, `Representation`, `create_spec`, and `serialize` | Python 3.14 and 3.15-target `ty` contracts | Python 3.14 uses an honest `object` fallback; runtime support remains narrower than all statically valid type expressions |
| Settings source resolution | immutable `talea.settings.Settings` source plan over canonical Spec introspection | Settings runtime, security, property, typing, documentation, and permanent benchmark suites | no dotenv, remote provider, registry, watcher, profile engine, automatic CLI, or framework lifecycle |
| incremental Contract validation | retained `Contract` artifacts plus operation-local `ItemPolicy` state | incremental runtime, typing, documentation, retention, and permanent benchmark suites | synchronous iterables only; source lifetime and callback work remain application-owned |
| JSON Lines input | `talea.jsonl` framing and `JsonlPolicy`, reusing Contract item policy and strict JSON decoding | JSONL runtime, adversarial, typing, documentation, retention, and permanent benchmark suites | input records only; no paths, compression, output, multiline recovery, chunks, or async streams |
| NamedTuple interoperability | immutable `NamedTupleSchema` positional truth | NamedTuple runtime, composition, typing, standards, documentation, and permanent benchmark suites | annotated `typing.NamedTuple` only; no Mapping/object input, slot aliases, derived views, or arbitrary tuple subclasses |
| nested validation-error projection | canonical `ErrorData` facts projected by `ValidationError.error_tree()` | error-tree runtime, composition, Sensitive, documentation, and errors benchmark suites | projection is read-only and location-based; it is not a second mutable error store |

These owners consume the same canonical schema graph as existing validation,
serialization, introspection, JSON Schema, and OpenAPI operations. None adds a
second schema interpretation or a required dependency.

## Implemented product surface

The release includes Specs, arbitrary Contracts, strict construction and
validation, constraints, Mapping and JSON input, serialization, structured
errors, Sensitive redaction, finite resource policies, stdlib dataclass and
NamedTuple contracts, TypedDict and PEP 695 aliases, tagged unions, recursive
and generic graphs, presence-aware derived/PATCH Specs, explicit directional
views, `Representation`, strict callable boundaries, introspection, dynamic
Spec creation, JSON Schema Draft 2020-12, and OpenAPI 3.1-compatible
projection.

For application configuration, `talea.settings` loads concrete Specs through
the deterministic precedence order override > environment > secrets directory
> TOML > defaults. It snapshots each operation, supports current and historical
aliases, schema-directed textual decoding, optional value-free provenance, and
separate acquisition and final-input resource policies. Importing `talea` does
not import the settings package.

For record ingestion, retained Contracts provide lazy strict and external
Python item validation with finite defaults, exact source indexes, fail-fast or
explicit continuation behavior, and fresh per-item external-input state. JSON
Lines adds homogeneous text/bytes records, strict UTF-8, strict canonical JSON
semantics, one-based framing locations, raw-byte policy, and the same shared
item/invalid-item budget.

## Deliberate boundaries

| Capability | Current disposition |
| --- | --- |
| callable argument/return validation | Implemented for synchronous and asynchronous functions and methods through `validate_call`; generators, async generators, callable instances, runtime generic-function specialization, callable resource policy, timeouts, and retries remain outside this owner |
| settings/environment loading | Implemented in the import-isolated `talea.settings` owner; no root export, dotenv parser, source registry, watcher, profile engine, framework lifecycle, automatic CLI, or remote source |
| incremental records and JSONL | Synchronous Python item iteration and JSON Lines record input are implemented; JSONL output, arbitrary chunks, paths, compression, and async iteration are not |
| NamedTuple and ordinary-class mapping | Annotated `typing.NamedTuple` has positional list/tuple and JSON-array interoperability; object/Mapping compatibility, ordinary-class guessing, and ORM extraction remain absent |
| explicit custom representations | Per-position `Representation` declarations are implemented; no registry, discovery, generic factory, or custom format vocabulary |
| output/schema resource governance | Caller-owned in the current trust model |
| migration warnings and retirement timing | Application-owned; Talea declares a finite accepted-name vocabulary without a migration lifecycle |
| schema compatibility/version tooling | Not implemented; JSON Schema and OpenAPI project the current contract only |

The complete operational list is on [Known limitations](engineering/limitations.md).

## Quality evidence

Repository acceptance requires Python 3.14 tests, exactly 100% line coverage,
Ruff lint and formatting, Python 3.14 and Python 3.15-target `ty` contracts,
executable documentation examples, navigation and internal-link validation,
documentation and package builds, isolated no-dependency wheel checks, and all
25 permanent benchmark tasks. GitHub CI additionally executes the test and
package matrix on Python 3.14 and 3.15.

Settings performance evidence compares the complete Talea source-resolution
operation with an equivalent handwritten implementation. The earlier narrow
manual lower bound omitted required behavior and remains non-equivalent; it is
not the release comparator. Timing varies by machine, but the permanent suite
guards the converged architectural class and the no-Settings core canary.

A passing checkout or release artifact is evidence for that exact revision; it
is not a promise that every downstream environment is identical. Release
history belongs in [Release notes](release-notes.md), contributor workflow in
[Contributing](contributing.md), and trust boundaries in
[Security architecture](engineering/security.md).
