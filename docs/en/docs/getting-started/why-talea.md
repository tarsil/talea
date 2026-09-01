# Why Talea?

Talea is a 2026+ Python data-contract library. It asks what a data-contract
library should look like when designed today for modern Python, without an
inherited requirement to preserve older Python releases or retrofit new
behavior into a decade-old public contract.

That circumstance made a particular design point practical: Python 3.14+,
modern typing and PEP 695 generics, deferred and recursive annotations, strict
construction, explicit external boundaries, immutable Specs, arbitrary
Contracts, pure-Python compile-once execution, zero required runtime
dependencies, standards projection, finite input policies, sensitive-data
handling, and one canonical owner for each form of truth.

"2026+" is not a prediction that Talea will replace other libraries, remain
faster forever, or be superior for every workload. It means the minimum Python
platform and architectural assumptions begin with what is available now rather
than carrying historical compatibility requirements.

## The problem Talea solves

Applications need more than typed storage at service, message, and persistence
boundaries. They need predictable validation, controlled conversion, useful
failures, serialization, standards schemas, security limits, and framework
introspection. Those capabilities become hard to reason about when trusted
Python objects, external mappings, JSON, output, and schema generation all pass
through one implicit pipeline.

Talea separates those operations while deriving them from one canonical
schema. A team can therefore answer which conversions are permitted, where
hostile input enters, what output means, and whether a schema honestly
represents runtime behavior.

## Why another data-contract library?

Mature libraries necessarily preserve established versions, semantics,
integrations, and user expectations. Talea began later and could choose a
narrower baseline from the start. This is a design circumstance, not criticism
of software that serves broader compatibility and ecosystem needs.

Another design point is useful for projects that value strictness,
dependency-light deployment, Python-native behavior, explicit boundaries,
introspection, current standards, and security-conscious defaults together.

## Why strict by default?

Strict validation answers a stable question: does this Python value already
satisfy the declared Python contract? An integer field does not accept `True`,
`1.0`, or `"1"`; a UUID field accepts a UUID object. This avoids conversion
rules changing silently as data crosses layers.

```python
from uuid import UUID

from talea import Spec


class Account(Spec):
    account_id: UUID
    revision: int


account = Account(
    account_id=UUID("12345678-1234-5678-1234-567812345678"),
    revision=1,
)
```

If application code accidentally passes a UUID string or a boolean revision,
the constructor reports a type failure at that field. A JSON document may use
UUID text because JSON has no UUID primitive, but that representation is owned
by `from_json()` rather than silently becoming valid everywhere.

Applications that want a conversion can place it at an external boundary or in
an explicit field transform. Broad coercion is a valid product choice, but it
is not Talea's default.

## Why separate trusted Python from external boundaries?

Trusted construction, untrusted Mapping conversion, and JSON decoding do
different work. JSON lacks direct representations for UUID, `datetime`,
`Decimal`, and bytes, while already-valid Python values should not repeatedly
pay for parsing or reconstruction.

`Spec(...)` and `Contract.validate()` are strict Python paths.
`from_mapping()`, `Contract.from_python()`, and `from_json()` are explicit
external paths with conversion, aggregation, cycle handling, and resource
policy where applicable. The distinction is visible in code review and threat
modeling.

```python
account = Account.from_json(
    '{"account_id":"12345678-1234-5678-1234-567812345678","revision":1}'
)
```

The external operation may allocate a detached container, convert nested
mappings to Specs, decode standard-library JSON representations, aggregate
independent failures, and consume a finite work budget. The direct constructor
does not inherit those costs merely for API uniformity.

## Why zero required runtime dependencies?

`pyproject.toml` declares `dependencies = []`. Deploying Talea adds the package
and requires a supported Python interpreter, not a transitive runtime dependency
graph. This reduces packaging coordination and runtime supply-chain surface.
It does not mean the repository has no development, test, benchmark, build, or
documentation dependencies.

## Why pure Python?

Pure Python keeps installation, debugging, generated execution, and platform
support within ordinary CPython tooling. Talea does not require a native
extension to reach its intended semantics or its measured performance profile.

Native implementations are often the right choice for serialization-heavy or
specialized workloads. Talea's choice favors deployment simplicity,
inspectability, and Python-level architectural control; it is not a claim that
pure Python is universally faster.

## Why compile once?

Reflection and generic schema interpretation are cold declaration work. Talea
resolves annotations once, compiles specialized Python for each used operation,
and reuses ordinary CPython callables. Unused operations compile lazily where
appropriate, so unrelated features do not belong on a simple Spec's hot path.

This supports low repeated-use overhead while keeping validation, input,
serialization, and standards projection distinct. Performance remains a
measured property; see [Performance](../engineering/performance.md).

## Why immutable Specs?

Validated field bindings should not become invalid through ordinary assignment.
Immutability gives construction an atomic commit point, keeps field reads
direct, and supports Python-native `copy.replace()` for controlled updates.
Talea does not pretend nested mutable lists or dictionaries are deeply frozen;
their current state is revalidated at later contract boundaries.

Use a dataclass or another record type when mutation is central. If that
dataclass later needs Talea boundary validation without changing its domain
representation, retain it through `Contract(DataclassType)`.

## Why Contract?

Not every useful contract is a named record. `Contract` applies strict
validation, external conversion, JSON, serialization, resource policy, schemas,
and introspection to `list[User]`, `TypedDict`, a tagged union, recursive alias,
or primitive without inventing a wrapper class.

`Spec` owns nominal immutable records. `Contract` owns a retained arbitrary
annotation boundary. Neither is a substitute name for the other.

```python
from talea import Contract


account_batch = Contract[list[Account]](list[Account])
accounts = account_batch.from_json(
    '[{"account_id":"12345678-1234-5678-1234-567812345678","revision":1}]'
)
```

No `AccountBatch(Spec)` wrapper is needed unless that wrapper has genuine domain
meaning, fields, methods, or invariants.

## Why canonical ownership?

Field types, aliases, constraints, requiredness, metadata, recursive identity,
and discriminator tags should not acquire slightly different meanings in
validation, input, output, errors, introspection, and JSON Schema. Talea
resolves that truth once and requires every execution or projection owner to
consume it. The benefit for users is agreement between APIs, not compiler
terminology.

## Why security and standards belong in the design

An external boundary is also a resource and disclosure boundary. Talea's
immutable `ResourcePolicy` can cap encoded transport size before decoding,
structural depth, actual compiled schema visits, and retained error count.
Sensitive metadata redacts Talea-owned repr and rejected-value diagnostics.
These controls do not claim to sandbox arbitrary Python callbacks, custom
codecs, regular expressions, or Mapping methods; the security documentation
states the exact caller-owned remainder.

JSON Schema Draft 2020-12 and OpenAPI 3.1 projection consume the same aliases,
requiredness, constraints, metadata, recursive identities, and discriminator
tags used by runtime behavior. Talea returns schema fragments, not routes or
operations. If a transform or serializer makes a direction unknowable, schema
projection fails explicitly rather than publishing a reassuring but false
description.

## What Python 3.14+ changes concretely

The platform baseline is visible in user code rather than being a release-year
label:

```python
class Page[T](Spec):
    items: list[T]
    cursor: str | None = None


type Tree[T] = T | list[Tree[T]]

account_page = Page[Account](items=[account])
integer_tree = Contract[Tree[int]](Tree[int])
```

PEP 695 declarations, deferred annotations, concrete generic specialization,
and recursive type aliases are normal architecture. Talea does not need a
parallel compatibility syntax or conditional runtime for historical Python
versions. Starting now allowed this narrower baseline; it says nothing
negative about libraries whose users require a wider version range.

## Performance philosophy, not a slogan

Talea resolves annotations and emits specialized Python once, then retains the
operation. A simple constructor should not pay for JSON, OpenAPI, tagged
dispatch, or resource accounting it does not use. External paths compile their
own operation when needed because their conversion and security semantics are
different from trusted construction.

The meaningful baseline is semantically equivalent manually written strict
Python for the same operation. Repository benchmarks separate schema creation,
construction, validation, Mapping input, JSON input, serialization, recursion,
errors, metadata, tagging, schema projection, and resources. Pure Python and
compile-once describe the implementation model; they do not guarantee Talea
will beat native code, every library, or future versions on every workload.

## Not a competition

Talea is not trying to replace Pydantic, msgspec, dataclasses, attrs, or
manually written validation. Different systems need different tradeoffs.

- Pydantic has broad adoption, a mature ecosystem, extensive integrations, and
  coercive/parsing workflows that many applications actively want.
- msgspec has an extremely fast native implementation, mature serialization,
  and different representation and performance tradeoffs. Its supported
  workflow may already be the ideal fit.
- dataclasses and attrs are excellent when typed records are sufficient; stdlib
  dataclasses can opt into Talea boundaries through `Contract` when needed.
- manually written Python remains appropriate for a small or highly specialized
  contract where direct code is clearer to review and maintain.

Talea offers another point in that space: strict, dependency-light,
Python-native, compile-once, explicit-boundary, introspectable,
standards-aware, and security-conscious. Selection should follow the system's
requirements, not a winner/loser ranking. The [comparison
guide](../engineering/comparison.md) makes the tradeoffs explicit.

## When not to use Talea

Talea is likely a poor fit when:

- the application depends deeply on Pydantic-specific integrations or wants
  its coercion-first workflow;
- supported Python versions include anything below 3.14;
- ORM extraction or a broad framework/integration ecosystem must come from the
  same mature package;
- msgspec's native serialization workflow already matches the dominant
  workload and representation choices;
- mutable internal records need no external validation, making dataclasses or
  attrs simpler;
- the contract is small or specialized enough that manually written Python is
  clearer;
- 0.x API maturity, a small ecosystem, or the current limitations do not
  meet organizational support requirements.

## Maturity and evidence

Talea deliberately remains in the 0.x series, and its ecosystem is much smaller
than Pydantic's or msgspec's.
Adopters accept that compatibility, deprecation, support, and release governance
are not yet frozen. The [known limitations](../engineering/limitations.md) page
is the authoritative boundary.

Important claims have repository owners:

| Claim | Evidence |
| --- | --- |
| zero required runtime dependencies | package metadata declares an empty runtime dependency list |
| pure Python | production implementation and build contents |
| strict semantics | type, boundary, error, and typing-contract tests |
| finite external-boundary policy | `ResourcePolicy`, adversarial tests, and resource benchmarks |
| standards support | JSON Schema/OpenAPI projection and conformance tests |
| performance characteristics | permanent workload-specific benchmarks with semantic caveats |
| documentation behavior | executable `docs_src` programs, link/API checks, and docs build |

These are verifiable engineering claims, not certifications or guarantees of
future superiority. Continue with the [quickstart](quickstart.md) or the
[enterprise evaluation](../engineering/enterprise-evaluation.md).
