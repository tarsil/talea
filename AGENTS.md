# AGENTS.md

## Talea

Talea is a production-grade Python 3.14 data modeling, validation,
parsing, and serialization library.

Talea is not a Pydantic clone and is not a benchmark toy.

Its architectural objective is to provide production-level modelling
capabilities while minimizing runtime abstraction cost through
compile-once schema processing and specialized execution.

This file defines mandatory engineering rules for every contributor and
agent working in this repository.

---

## Python

Talea targets Python 3.14 and newer.

Production code remains 100% Python. Native extensions are not an accepted
substitute for sound Python architecture or equivalent semantics.

Do not introduce compatibility code for older Python versions.

Use modern Python 3.14 syntax, typing features, and standard-library
capabilities where they improve the implementation.

Do not add compatibility abstractions without an explicit requirement.

---

## Project Tooling

The project uses the existing Hatch-based project scaffold and taskfiles.

Use the repository-provided tasks for linting, formatting, type checking,
testing, benchmarking, documentation, and other established workflows.

Do not replace existing tooling without evidence that the replacement is
necessary and superior.

The expected Python quality toolchain includes:

- Hatch
- pytest
- ruff
- ty

Follow the repository configuration rather than inventing parallel tool
configuration.

---

## Engineering Standard

Production quality is required from the beginning.

Do not create knowingly disposable production code with the expectation
that it will be cleaned up later.

Research prototypes must be explicitly isolated and identified as such.

Every implementation must optimize for:

- correctness
- clarity
- maintainability
- performance
- memory efficiency
- reusability
- separation of concerns
- inspectability
- excellent typing
- predictable behaviour

Performance must never be obtained by silently implementing weaker
semantics than the behaviour being compared.

---

## AI Slop Is Forbidden

Code, tests, documentation, commit history, and architecture must read as
deliberate human engineering.

Do not generate:

- unnecessary wrappers
- speculative abstractions
- redundant helper layers
- excessive protocols or generic types
- repetitive tests
- duplicate implementations
- obvious comments that restate code
- filler documentation
- verbose exception hierarchies without purpose
- defensive branches for impossible states
- meaningless utility modules
- artificial indirection
- abstractions with no demonstrated consumer
- large amounts of mechanically similar boilerplate

Avoid words and prose patterns commonly associated with generated filler,
including unnecessary claims such as "robust", "seamless", "powerful",
"comprehensive", and similar marketing language.

Every abstraction must earn its existence.

Prefer the smallest design that fully satisfies the production
requirement.

---

## Architecture

Talea follows Single Owner Architecture.

Every important engineering truth must have exactly one canonical owner.

Other components may produce evidence, consume truth, persist it,
transport it, project it, or expose it, but must not independently own
the same truth.

The canonical schema representation owns model structural truth.

Validators, serializers, JSON Schema generation, introspection, and
other derived functionality must consume or project that truth rather
than recreate competing schema interpretations.

Do not duplicate validation rules across subsystems.

One shared validation-emission owner must supply standalone validators and
inline Spec constructors. Execution targets must not recreate type or
constraint semantics independently.

---

## Core Architectural Invariants

Python type annotations are Talea's primary schema language.

Reflection and annotation analysis belong at model-definition or schema
compilation time, not on the normal instance-validation hot path.

Schemas compile once.

Runtime execution should use specialized operations wherever practical.

The canonical schema representation must not become a generic
interpreted validation engine on the normal hot path.

Unused features should impose approximately zero runtime cost on models
that do not use them.

Adding support for an unrelated feature must not materially slow simple
models.

Accepted hot paths should remain near equivalent hand-written strict Python.
A material regression requires measurement, diagnosis, and repair or an
evidence-backed maintainer decision.

Successful validation should not pay the full cost of rich failure
reporting.

Strict Python values should not pay for coercion unless coercion has
been requested.

Trusted validated objects should not repeatedly pay for deep validation
of already-established invariants.

Serialization must not unnecessarily repeat validation.

Introspection metadata may be exposed, but rich field objects must not
become the canonical runtime validation authority.

---

## Execution Paths

Treat these as distinct operations unless evidence proves they should
share implementation:

1. construction from Python values
2. validation of untrusted Python data
3. conversion from mappings or similar structures
4. parsing from serialized formats such as JSON
5. serialization to Python structures
6. serialization to formats such as JSON

Do not force all operations through one generic runtime pipeline merely
for implementation convenience.

---

## Strictness

Prefer strict and predictable Python semantics.

Implicit coercion must be deliberate and explicit.

Do not introduce surprising conversion behaviour simply to imitate
another library.

Unsupported behaviour should fail clearly rather than silently falling
back to slower or ambiguous behaviour.

---

## Public API

Public API is intentional.

Do not expose implementation details accidentally.

Root-package exports must be explicitly reviewed.

A symbol becoming importable from `talea` should be treated as an API
decision.

Avoid unnecessary underscore-prefixed functions and classes, but do not
make internal implementation public merely to avoid underscores.

The objective is clean ownership, not arbitrary naming rules.

Use module boundaries, cohesive objects, and domain ownership to avoid
large collections of private helpers.

---

## Object-Oriented Design

Prefer object-oriented design where concepts have identity, state,
behaviour, lifecycle, or clear ownership.

Do not force classes around operations that are naturally simple
functions.

Functions are appropriate for stateless transformations.

Classes are appropriate when they improve ownership, reuse,
encapsulation, extension, or clarity.

Avoid both procedural helper sprawl and unnecessary class hierarchies.

Composition is preferred over inheritance unless inheritance clearly
models the domain.

---

## Modules and Packages

Organize code by domain and responsibility.

Prefer small, cohesive modules.

Do not create oversized files containing unrelated concepts.

Do not allow generic dumping-ground modules such as:

- utils.py
- helpers.py
- common.py
- misc.py
- core.py

unless the module has a precise, defensible domain meaning.

If a file begins accumulating multiple independent responsibilities,
split it.

Do not split cohesive code merely to meet arbitrary line-count limits.

Package structure must reflect architectural ownership.

Evolve cohesive domain packages when independent owners create distinct
reasons to change. Do not preserve flat mega-modules merely to avoid a
behavior-preserving migration.

---

## Functions

Do not create excessive private helper functions.

Private functions are acceptable when they encapsulate a meaningful
implementation detail.

A large cluster of underscore-prefixed helpers is usually evidence that
the module lacks appropriate domain decomposition or object ownership.

Prefer cohesive objects or domain modules when they better represent the
responsibility.

Do not create functions that merely rename another function call.

---

## Docstrings

Proper detailed docstrings are mandatory for public modules, classes,
methods, functions, and meaningful public attributes where appropriate.

Docstrings must explain behaviour, contracts, parameters, return values,
exceptions, invariants, and important performance or semantic
characteristics.

Do not write docstrings that merely repeat the function signature.

Internal implementation with non-obvious behaviour should also be
documented where doing so materially improves maintainability.

Documentation must describe actual implemented behaviour.

Do not document planned behaviour as though it already exists.

---

## Comments

Comments should explain why, invariants, constraints, unusual algorithms,
or non-obvious decisions.

Do not narrate obvious syntax.

Bad:

    # Increment the counter.
    counter += 1

Good:

    # IDs start at one because zero is reserved by the compiled schema.
    counter += 1

---

## Reusability

Reuse should come from coherent abstractions, not generic indirection.

Do not abstract code solely because two blocks currently look similar.

Prefer domain-specific reusable components over catch-all helpers.

A new abstraction should normally have more than one demonstrated
consumer or represent a clear architectural boundary.

---

## Separation of Concerns

Parsing, schema resolution, schema representation, validation,
serialization, error construction, introspection, and benchmarking
should have clear ownership boundaries.

Do not mix unrelated responsibilities for convenience.

Avoid circular domain dependencies.

Dependencies should flow toward canonical abstractions rather than
between competing representations.

---

## Dependencies

Talea's core runtime dependency list is empty. Do not add a required
third-party runtime dependency without an explicit maintainer-approved change
to this permanent product constraint.

Development and testing dependencies are separate from runtime
dependencies.

Every runtime dependency requires justification.

---

## Errors

Validation errors are part of Talea's public behaviour.

Error types, locations, codes, expected values, received values, and
nested paths must be designed consistently.

Do not build expensive rich error structures on successful validation
paths.

Failure-path optimization is secondary to success-path performance
unless profiling demonstrates otherwise.

Do not leak internal implementation exceptions through the public
validation API unintentionally.

---

## Typing

Typing quality is a product requirement.

Public APIs should provide excellent inference in modern Python tooling.

Avoid unnecessary Any.

Do not introduce casts merely to silence the type checker without
understanding the underlying ownership or typing problem.

Use dataclass_transform or related typing mechanisms where they improve
static understanding without forcing inappropriate runtime
implementation.

Type-checking failures are release blockers.

Inheritance is a production requirement. Type, constraint, lifecycle, trust,
and generated-constructor changes must preserve documented inheritance and
override behaviour.

---

## Tests

Tests begin with implementation, not after implementation.

Every production behaviour requires appropriate automated coverage.

Use focused unit tests for local behaviour and integration tests for
cross-domain contracts.

Include invalid and boundary cases, not only happy paths.

Avoid repetitive tests that assert the same behaviour through trivial
variations.

Bug fixes require regression tests whenever practical.

Line coverage is enforced at 100%. Coverage failures are release blockers;
do not hide untested production paths with broad exclusions.

Tests should validate behaviour rather than implementation details unless
the implementation detail represents an architectural invariant.

Property-based and fuzz testing should be introduced for validation,
serialization, recursive structures, and other combinatorial behaviour.

---

## Benchmarks

Performance is a correctness property for Talea.

Benchmark from the beginning.

Maintain comparisons where relevant against:

- plain Python classes
- slotted Python classes
- dataclasses
- slotted dataclasses
- attrs
- Pydantic
- msgspec

Measure distinct workloads independently.

Important categories include:

- class/schema creation
- import/startup cost
- object construction
- trusted composition
- Python-value validation
- mapping-to-model conversion
- nested models
- containers
- unions
- serialization to Python
- JSON decoding and validation
- JSON encoding
- invalid-data handling
- memory usage
- peak allocations
- per-instance memory

Benchmarks must compare equivalent semantics.

Never remove validation or functionality merely to produce a favourable
benchmark.

Benchmark methodology must be reproducible.

Meaningful unexplained performance regressions block acceptance.

---

## Performance Architecture

Do not optimize from intuition alone.

Measure first.

Use profiling and allocation evidence before introducing complex
optimizations.

Simple models are permanent performance canaries.

Adding aliases, generics, custom validators, discriminated unions,
serialization features, JSON Schema support, or other unrelated
capabilities must not materially degrade models that do not use those
features.

---

## Testing Against Competitors

Pydantic and msgspec are references, not architectures to copy.

Before adopting an architectural technique from either library, inspect
the relevant behaviour and implementation directly.

Document semantic differences in benchmarks.

Talea must not claim superiority based on incomparable workloads.

---

## Security and Adversarial Behaviour

Treat external input as hostile.

Validation and parsing must consider:

- deeply nested data
- very large containers
- recursive schemas
- malicious payloads
- pathological unions
- malformed serialized input
- invalid encoding
- numeric edge cases
- resource exhaustion

Where practical, fuzz parsers and validators.

Never rely solely on happy-path benchmark payloads.

---

## Commits

Use Conventional Commits.

Examples:

    feat(schema): add canonical primitive schema nodes
    perf(validation): compile primitive validators
    test(errors): cover nested validation locations
    refactor(schema): separate annotation resolution from schema storage

Do not create a commit for every tiny edit.

Commits should represent coherent engineering slices that a human
maintainer would naturally review, revert, or reason about.

Avoid both extremes:

- one enormous campaign commit containing unrelated changes
- dozens of tiny mechanical commits with no independent value

Tests and implementation belonging to one coherent behaviour may live in
the same commit.

---

## Campaign Workflow

Work in coherent owner-based campaigns.

Before changing code:

1. identify the canonical owner of the behaviour
2. inspect existing implementation and tests
3. define acceptance criteria
4. define performance expectations when relevant
5. identify regression risks

Then follow:

Observe -> Verify -> Diagnose -> Mutate -> Validate -> Claim

Do not claim completion based solely on code inspection.

Execution evidence is required.

---

## Architectural Burden of Proof

Do not change established architecture merely because another design
looks cleaner.

Before architectural change, demonstrate why the current architecture
cannot satisfy the requirement or why the alternative is materially
superior.

Significant architectural changes should define an Architectural Kill
Test:

What class of bugs, duplication, ambiguity, or performance cost becomes
impossible after this change?

After implementation, perform a Manifestation Test:

Did the new architecture actually eliminate the failure mode it was
introduced to remove?

---

## Claims

Claims require evidence.

"Tests pass" requires test output.

"Type checking passes" requires type-check output.

"Faster" requires benchmark evidence.

"Lower memory" requires measured allocation or memory evidence.

"Compatible" requires compatibility tests.

"Production-ready" requires the relevant release gates.

Never infer successful behaviour merely because the implementation looks
correct.

---

## Campaign Completion

At the end of every engineering campaign provide:

- concise summary of completed behaviour
- architectural decisions made
- files added, changed, or removed
- tests added or changed
- exact verification commands executed
- test results
- lint results
- type-check results
- benchmark results when relevant
- known limitations
- unresolved risks
- commit hash and commit message
- recommended next owner/campaign

Do not hide failing checks.

Do not silently defer incomplete requirements.

---

## Documentation

Keep technical documentation close to implemented behaviour.

Substantial user-facing examples should live as executable programs in
`docs_src/` and be included into prose where practical. `task docs_test` must
execute those examples and validate documentation navigation, internal links,
and the root public API inventory. Documentation integrity failures are release
blockers.

Update documentation in the same coherent campaign when public behaviour
changes.

Do not generate large speculative documentation trees before the
corresponding architecture exists.

Examples should be executable or tested where practical.

Documentation depth, organization, navigability, production examples, and
edge-case coverage are production release gates. Do not call Talea
production-ready while its documentation remains implementation-note level.

---

## Requirements Convergence

Before production acceptance, audit every deliberately discussed or deferred
requirement. Each must map to implementation, tests, documentation, and
performance or other evidence where relevant, or to an explicit
maintainer-approved decision not to implement it.

---

## Final Rule

Talea should feel as though it was built by a small group of experienced
Python engineers who care deeply about the language.

Prefer deliberate simplicity over cleverness.

Prefer measured evidence over assumptions.

Prefer one clear owner over duplicated truth.

Prefer production correctness over benchmark theatre.

Prefer fast, maintainable execution over both premature abstraction and
premature optimization.
