# Documentation convergence audit

Version: 1.0
Last updated: 2026-08-26
Maintained by: Talea maintainers

This contributor artifact records the pre-rewrite audit and the evidence used
to converge the 0.x manual. It is intentionally outside the built user
documentation.

## Initial page classification

| Original page | Decision | Reason and destination |
| --- | --- | --- |
| `index.md` | REWRITE | Flat feature list became a product entry point with audience paths |
| `field-semantics.md` | MOVE + KEEP | Sound behavior moved into Reference with stale trust prose repaired |
| `supported-types.md` | MOVE + REWRITE | Became the authoritative type reference and points to merged recursion |
| `tagged-unions.md` | REWRITE | Added standards projection, event example, collision, and performance context |
| `constraints.md` | MOVE + REWRITE | Retained exhaustive vocabulary and removed campaign language |
| `metadata-security.md` | SPLIT + REWRITE | Metadata remains Reference; technical threat material moved to Engineering |
| `custom-validation.md` | MOVE + KEEP | Existing lifecycle reference was technically sound |
| `input-boundaries.md` | MOVE + KEEP | Existing boundary detail was retained and cross-linked from concepts/tutorials |
| `resource-security.md` | REWRITE | Kept the technical threat model, added resource-code reference, removed release-campaign prose |
| `serialization.md` | MOVE + REWRITE | Retained output matrix and made limitations/current schema ownership explicit |
| `error-experience.md` | REWRITE | Added the complete ErrorCode meaning/location/fix table |
| `composition-inheritance.md` | MOVE + KEEP | Existing structural behavior became a Concepts page |
| `recursive-generics.md` | MERGE + REWRITE | Now owns Specs, aliases, TypedDict, mutual/generic recursion, caching, and cycles |
| `recursive-named-graphs.md` | MERGE + REMOVE | Unique material merged into `recursive-generics.md`; duplicate page deleted |
| `contracts.md` | REWRITE | Removed chronology and added retained policy plus standards projection |
| `dynamic-utilities.md` | SPLIT + REWRITE | Dynamic Specs retained; introspection and immutable updates gained focused pages |
| `presence-derived-contracts.md` | REWRITE | Replaced future-schema claims with implemented projection and current limits |
| `json-schema-openapi.md` | MOVE + REWRITE | Retained as standards reference; removed campaign terminology |
| `release-ledger.md` | REWRITE | Became a current maturity/support and deliberate-boundary page |
| `contributing.md` | REWRITE | Replaced stale generic prose with repository tasks, docs_src, architecture, coverage, and benchmark policy |
| `sponsorship.md` | MOVE | Removed from learning paths; retained under Project |
| `release-notes.md` | KEEP | Campaign language is historical release context, not product explanation |

## Reference architecture findings

### Lilya

- Markdown includes substantial complete programs from domain-organized
  `docs_src/` modules.
- The documentation generator expands includes into a generated tree.
- Navigation separates Getting Started, Concepts, Tutorials, How-to, Reference,
  Integrations, and project material instead of exposing a flat page list.
- Section landing pages state learning order and prerequisites.

Talea adopted executable source ownership, generated includes, nested
navigation, and section landing pages. It did not copy Lilya's framework
terminology, integration hierarchy, or prose.

### Django

Django explicitly separates introductory tutorials, deep topic guides,
task-focused how-to guides, API reference, and meta/release material. Talea
adopted that reader-intent separation and the principle that tutorials teach by
doing while reference remains exhaustive.

### Pydantic

Pydantic combines product rationale, dense concept guides, runnable examples,
cross-links to API documentation, dedicated error/schema/performance material,
and migration guidance. Talea adopted the discoverability pattern while keeping
strict Talea vocabulary and documenting semantic differences fairly.

## Final information architecture

| Section | Reader question |
| --- | --- |
| Getting Started | How do I succeed in five minutes and then build a real boundary? |
| Concepts | Why are Specs, Contracts, strictness, recursion, tags, and presence designed this way? |
| How-to Guides | How do I implement a specific REST, PATCH, event, schema, security, or migration task? |
| Reference | What does each operation, type, marker, error, and public API do exactly? |
| Engineering | Can architecture, security, performance, enterprise, and limitation claims withstand review? |
| Project | How do maturity, contribution, release, and sponsorship work? |

## Public API convergence

| Surface | Implementation | Tests/typing | Guide/reference | Executable owner |
| --- | --- | --- | --- | --- |
| Spec lifecycle | root export and `talea.spec` | Spec, composition, input, serialization tests and typing contracts | Specs, fields, boundaries, API | quickstart and production service |
| Contract | root export and `talea.contract` | Contract, TypedDict, recursion, schema tests and typing contracts | Contracts and API | Contract/TypedDict recipe |
| constraints | eight root markers | constraint/schema tests and typing contracts | constraints and API | quickstart/finance examples |
| aliases/metadata/Sensitive | root markers | metadata, serialization, schema tests | metadata/security and API | event/security examples |
| transforms/checks/serialize | root decorators | hook, input, output tests and typing contracts | hooks, serialization, API | advanced/service examples |
| tagged unions | `Discriminator` | tagged, recursive, schema tests | tagged unions and API | event example |
| derived/PATCH | `derive_spec`, `apply_patch` | derivation, replacement, typing tests | derived/PATCH and immutable updates | PATCH example |
| dynamic Specs | `create_spec` | dynamic, introspection, typing tests | dynamic Specs and API | dynamic/replacement example |
| errors/resources | root exceptions, `ErrorCode`, policy | error and security tests | error/resource/security reference | error/security example |
| standards projection | Spec/Contract methods and projection error | JSON Schema/OpenAPI tests | standards and API | quickstart/event/advanced examples |
| introspection/schema domain | `talea.introspection`, `talea.schema` | introspection/schema tests | introspection and domain API inventory | dynamic example |

No root or declared domain `__all__` export was classified as an accidental
leak. Root exports are normal application API; declaration, validation, error,
schema, and introspection domain exports are advanced structural contracts and
are documented as such.

## Requirements-hit classification

- `AGENTS.md` campaign/workflow language is contributor governance and remains.
- `release-notes.md` campaign text is historical release context and remains.
- Product/concept/reference pages had chronological campaign prose; it was
  removed or rewritten as current behavior.
- `release-ledger.md` future/deferred entries were converted into current
  implemented, not-implemented, separate-owner, or rejected dispositions.
- Tests containing campaign labels preserve historical proof provenance and are
  not user documentation; behavior names remain discoverable through test files.
- Production `unsupported` assertions are closed internal exhaustiveness guards,
  not product requirements. Public unsupported categories are documented on the
  types, edge-cases, and limitations pages.
- Stale production docstrings that described JSON Schema as future work were
  corrected. Every root export has a docstring; short constraint and patch
  contracts were expanded where semantics or exceptions were missing.

## Claim evidence

| Claim | Evidence owner |
| --- | --- |
| pure Python | production source and build targets contain Python only |
| zero required runtime dependencies | `pyproject.toml` has `dependencies = []` |
| Python 3.14+ | project metadata, typing configuration, and syntax |
| strict semantics | validation/input tests and supported-types reference |
| finite external-boundary policy | `ResourcePolicy`, security tests, and resource benchmarks |
| 100% line coverage gate | coverage configuration `fail_under = 100` and coverage task |
| near manually written Python on measured core paths | permanent benchmark scripts; never stated as a universal ranking |
| Draft 2020-12/OpenAPI 3.1 compatibility | projection implementation and conformance tests |

Final command results and commit identities belong in the campaign reviewer
dossier because they are commit-specific evidence rather than stable manual
content.

## Steering amendment depth review

The measurements below expand `docs_src` includes before counting. “Examples”
counts fenced code/diagram blocks after expansion. Beginner coverage means the
page establishes its mental model or routes to the prerequisite; production
coverage means it discusses application use, ownership, or operational cost;
edge coverage means failures, limitations, security, typing, or subtle cases
are present or directly linked.

### 78. Major-page depth audit

| Page | Approx. lines | Approx. words | Examples | `docs_src` owners | Beginner | Production | Edge |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| `index.md` | 239 | 1,337 | 5 | 1 | yes | yes | yes |
| `getting-started/why-talea.md` | 291 | 1,702 | 4 | 0 | yes | yes | yes |
| `getting-started/tutorial.md` | 233 | 883 | 8 | 0 | yes | yes | yes |
| `getting-started/production-service.md` | 229 | 882 | 2 | 1 | yes | yes | yes |
| `concepts/mental-model.md` | 112 | 633 | 2 | 0 | yes | yes | yes |
| `concepts/specs.md` | 128 | 724 | 2 | 0 | yes | yes | yes |
| `concepts/strictness-boundaries.md` | 113 | 572 | 4 | 0 | yes | yes | yes |
| `field-semantics.md` | 171 | 998 | 3 | 0 | yes | yes | yes |
| `composition-inheritance.md` | 141 | 817 | 2 | 0 | yes | yes | yes |
| `constraints.md` | 216 | 1,102 | 7 | 0 | yes | yes | yes |
| `custom-validation.md` | 174 | 1,082 | 2 | 0 | yes | yes | yes |
| `supported-types.md` | 326 | 1,716 | 5 | 1 | yes | yes | yes |
| `contracts.md` | 354 | 1,601 | 9 | 1 | yes | yes | yes |
| `input-boundaries.md` | 330 | 1,878 | 6 | 0 | yes | yes | yes |
| `serialization.md` | 325 | 1,778 | 8 | 0 | yes | yes | yes |
| `error-experience.md` | 260 | 1,656 | 5 | 0 | yes | yes | yes |
| `metadata-security.md` | 308 | 1,401 | 10 | 0 | yes | yes | yes |
| `tagged-unions.md` | 269 | 1,255 | 3 | 1 | yes | yes | yes |
| `recursive-generics.md` | 500 | 2,049 | 14 | 1 | yes | yes | yes |
| `presence-derived-contracts.md` | 451 | 2,015 | 12 | 1 | yes | yes | yes |
| `dynamic-utilities.md` | 286 | 1,161 | 7 | 1 | yes | yes | yes |
| `json-schema-openapi.md` | 481 | 2,274 | 10 | 1 | yes | yes | yes |
| `resource-security.md` | 351 | 2,193 | 3 | 1 | yes | yes | yes |
| `reference/introspection.md` | 157 | 704 | 2 | 1 | yes | yes | yes |
| `reference/api.md` | 275 | 1,589 | 4 | 0 | entry links | yes | yes |
| `engineering/architecture.md` | 147 | 974 | 1 | 0 | yes | yes | yes |
| `engineering/security.md` | 74 | 542 | 0 | 0 | yes | yes | linked executable attacks |
| `engineering/performance.md` | 129 | 843 | 1 | 0 | yes | yes | yes |
| `engineering/comparison.md` | 152 | 815 | 5 | 0 | yes | yes | yes |
| `engineering/troubleshooting.md` | 229 | 908 | 14 | 0 | failure-first | yes | yes |
| `guides/adoption.md` | 113 | 665 | 1 | 0 | yes | yes | yes |
| `guides/recipes.md` | 235 | 984 | 4 | 1 | routes prerequisites | yes | yes |

Short section landing pages, installation, quickstart, release/project pages,
and narrow edge/typing summaries were reviewed but are not misclassified as
standalone major feature manuals. They route into the substantial owners.

### 79. Real-world example inventory

| Executable owner | Domain and demonstrated capabilities |
| --- | --- |
| `getting_started/quickstart.py` | minimal Spec, strict failure, JSON, output, Draft 2020-12 |
| `tutorials/production_service.py` | account API, nested address/credentials, aliases, constraints, Sensitive, ResourcePolicy, validation/resource handling, response allow-list, OpenAPI modes |
| `tutorials/patches.py` | account PATCH, exclusion, aliases, defaults, `None`, presence, Sensitive, field/whole-object failures, `apply_patch`, replacement, schema |
| `tutorials/events.py` | payment/account events, four discriminator tags, UUID/datetime/Decimal, Sensitive, generic envelope, direct dispatch failures, JSON/output/OpenAPI |
| `tutorials/finance.py` | instrument/order/money/trade, exact Decimal, UUID, enums/Literal, timezone-aware datetime, aliases, constraint and currency invariant, serialization/schema |
| `tutorials/recursive_ast.py` | expression grammar, recursive TypedDict alias, tagged direct dispatch, nested failure paths, runtime-cycle rejection, JSON Schema/OpenAPI |
| `recipes/contracts.py` | UUID, `list[UUID]`, `dict[str, Decimal]`, TypedDict, recursive alias, generic specialization, every Contract boundary, error and schema behavior |
| `recipes/errors_and_security.py` | secret-bearing failure, input size, depth, node work, error truncation, hostile custom Mapping, sandbox limitation |
| `recipes/schema_openapi.py` | account input/output, metadata, read/write/Sensitive, PATCH schema, tagged events, components/discriminator, framework document assembly |
| `recipes/framework_introspection.py` | immutable field/spec/derivation/contract descriptions, aliases, constraints, Sensitive, partial provenance, adapter projection |
| `recipes/dynamic_and_replacement.py` | trusted dynamic declaration, default, metadata, constraint/check, Mapping/JSON, introspection, replacement success/failures, schema |
| `tutorials/advanced_contracts.py` | generic Spec, recursive alias and tagged Spec graph, metadata, policy, Contract, PATCH, Mapping, OpenAPI composition |

### 80. Shallow-page rejection audit

The first rewrite still left these surfaces in the rejected “short prose + tiny
example/table” shape. They were repaired as follows:

| Initial shallow surface | Repair |
| --- | --- |
| README and homepage | full product rationale, boundary model, realistic example, capabilities, fit/non-fit, maturity/evidence, learning paths |
| tutorial and production service | progressive account boundary and complete executable raw-body-to-response flow |
| mental model, Specs, strictness | concrete operation, presence, trust, failure, security, performance, and non-fit explanations |
| supported types and constraints | boundary/edge examples plus realistic trading composition and schema consequences |
| tagged unions | four-event executable protocol, generic composition, failures, Sensitive caveat, schema/OpenAPI, when not to tag |
| Contract | primitive/container/TypedDict/recursive/generic boundary set instead of `Contract(int)` only |
| comparison and adoption | fair representative code, real selection scenarios, migration steps and stop conditions |
| API and troubleshooting | significant API semantics; broken/reason/corrected code for common failures |
| introspection and immutable updates | framework adapter and atomic replacement lifecycle, cache/generic/error/extension boundaries |
| architecture, security, performance | one-field ownership trace, operation lifecycle, concrete attacks, measurement and regression method |
| recipes | changed from duplicated snippet dump into task-focused routes to canonical full examples plus one composition lab |

### 81. Feature-composition audit

| Required composition | Documentation evidence |
| --- | --- |
| Spec + nested Specs + constraints + aliases + Sensitive + JSON + errors | production service/account tutorial |
| tagged union + generic envelope + Contract + JSON + OpenAPI | payment event example |
| tagged union + recursion + Contract + JSON + OpenAPI | recursive AST example |
| partial/PATCH + aliases + defaults + Sensitive + `apply_patch` + schema | PATCH example |
| ResourcePolicy + `from_json` + ValidationError + ResourceLimitError | production service and hostile-input examples |
| Decimal + UUID + datetime + Enum/Literal + aliases + nested Specs | financial example |
| metadata + read/write + Sensitive + derived schema + discriminator | schema/OpenAPI example |
| dynamic creation + hooks + introspection + replacement + schema | dynamic lifecycle example |

### 82. Real-world domain consistency audit

- Account/identity reuses Address, Credentials, request/source/patch/response
  roles across the tutorial, production boundary, PATCH, schema, errors, and
  introspection rather than unrelated User/Foo examples.
- Payments/events reuses authorized/declined and account events across tagged
  dispatch, generic envelopes, serialization, failure, and OpenAPI teaching.
- Trading uses Money, Instrument, Order, and Trade with exact financial types;
  the manual explicitly separates structural contracts from business and
  compliance rules.
- Recursive documents use Expression/Literal/Binary/FunctionCall across
  recursion, TypedDict, discriminator, error, cycle, and schema teaching.
- Small examples remain only where they isolate one initial syntax or edge case.

### 83. Production-readiness documentation audit

| Feature owner | Errors | Security | Performance | Typing | Serialization | Schemas | Edge cases |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Spec/fields/inheritance | covered | Sensitive/trust | compile/current state | constructors/narrowing | linked | covered | defaults, factories, mutability |
| constraints/hooks | stable codes/callback failures | trusted callback/regex | direct emitted work | `Annotated` | serializer boundary | projection limits | contradiction/inheritance |
| input/Contract | nested/aggregated | finite policy/cycles | separate compiled path | explicit `Contract[T]` | both outputs | both modes | codecs/mappings/open generics |
| tags/recursion/generics | tag/nested/cycle errors | direct branch + budgets | direct dispatch/cache | concrete specialization | round-trip | `$ref`/discriminator | collisions/type graph vs cycle |
| PATCH/replacement | field and whole-object | Sensitive/presence authorization boundary | presence-only cost | dynamic return limit | present-only | omittable truth | absent/None/default-equal |
| metadata/introspection/dynamic | declaration failures | redaction/trusted config | weak caches/startup work | dynamic precision limit | metadata behavior | read/write/current limits | open generic/pickle/cardinality |
| standards | projection error | no secret defaults | cold tooling work | typed dict result | input/output modes | full reference | callbacks/unsupported keys |

### 84. Example execution depth

`docs_test` discovers 12 executable source programs. All 12 contain meaningful
assertions; none merely imports or runs. The current AST count is 146 `assert`
statements, in addition to explicit failure branches that raise
`AssertionError` if an expected exception does not occur. The gate now rejects
any new executable example with zero assertions and rejects unresolved include
paths before the site build.

### 85. Documentation reviewer rejection pass

| Rejection attempt | Result and repair |
| --- | --- |
| shallowness | valid initially; repaired surfaces listed in item 80 and remeasured in item 78 |
| toy-only examples | valid initially; 12-domain executable library added, with six substantial application flows |
| missing composition | valid initially; required combinations mapped in item 81 and asserted in source |
| missing edge cases | valid on strictness/types/PATCH/API initially; failures, cycles, defaults, aliases, callbacks, open generics, projection limits added |
| missing production guidance | valid on entry/tutorial pages initially; security, performance, typing, schema, ownership, and non-fit guidance added |
| campaign-shaped prose | product pages scanned by `docs_test`; chronology remains only in release history/contributor audit |
| repetitive generated prose | manual pass removed duplicated snippet blocks, merged recursive owners, used domain-specific explanations, and kept landing pages short only when they route rather than teach a feature |

No valid rejection from this pass remains open. This is documentation evidence,
not a claim that a 0.x ecosystem or support policy is mature.
