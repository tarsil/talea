# Talea

<p align="center">
  <a href="https://talea.tarsild.io"><img src="https://res.cloudinary.com/dymmond/image/upload/v1787765742/Talea/logo_gn9nx6.png" alt='Talea'></a>
</p>

<p align="center">
    <em>Data contracts, built for modern Python</em>
</p>

<p align="center">
<a href="https://github.com/tarsil/talea/actions/workflows/test-suite.yml/badge.svg?event=push&branch=main" target="_blank">
    <img src="https://github.com/tarsil/talea/actions/workflows/test-suite.yml/badge.svg?event=push&branch=main" alt="Test Suite">
</a>

<a href="https://pypi.org/project/talea" target="_blank">
    <img src="https://img.shields.io/pypi/v/talea?color=%2334D058&label=pypi%20package" alt="Package version">
</a>

<a href="https://pypi.org/project/talea" target="_blank">
    <img src="https://img.shields.io/pypi/pyversions/talea.svg?color=%2334D058" alt="Supported Python versions">
</a>
</p>

---

**Documentation**: [https://talea.tarsild.io](https://talea.tarsild.io) 📚

**Source Code**: [https://github.com/tarsil/talea](https://github.com/tarsil/talea)

**The official supported version is always the latest released**.

---

Talea is a **2026+ Python data-contract library** for applications that want
strict Python semantics, explicit external boundaries, immutable records, and
standards-aware schemas without a required runtime dependency graph.

It is built for Python 3.14 and newer. An annotation is resolved once into one
canonical contract, then Talea compiles specialized pure-Python operations for
construction, external Mapping input, JSON input, Python output, JSON output,
and schema projection.

```python
from typing import Annotated
from uuid import UUID

from talea import Alias, MinLength, Sensitive, Spec


class Credentials(Spec):
    token: Annotated[str, Sensitive(), MinLength(16)]


class UserCreate(Spec):
    user_id: Annotated[UUID, Alias("id")]
    display_name: Annotated[str, Alias("displayName"), MinLength(1)]
    credentials: Credentials


request = UserCreate.from_json(
    """{
      "id": "12345678-1234-5678-1234-567812345678",
      "displayName": "Ada Lovelace",
      "credentials": {"token": "correct-horse-battery-staple"}
    }"""
)

assert request.user_id == UUID("12345678-1234-5678-1234-567812345678")
assert request.display_name == "Ada Lovelace"
assert "correct-horse-battery-staple" not in repr(request)
```

The conversion above is deliberately attached to `from_json()`. Ordinary
Python construction is strict:

```python
UserCreate(
    user_id="12345678-1234-5678-1234-567812345678",  # ValidationError
    display_name="Ada Lovelace",
    credentials=Credentials(token="correct-horse-battery-staple"),
)
```

A UUID field accepts a Python `UUID` on the trusted path. JSON has no UUID
value, so the JSON boundary owns its documented string representation. That
separation is the core of Talea's mental model: conversion is explicit, and
already-valid Python values do not pass through a general parsing pipeline.

Application-owned types can declare the same explicit boundary truth at any
annotation position with `Representation(input=..., load=..., output=...,
dump=...)`; Talea validates callback results and reuses the declared schemas for
input, output, JSON Schema, OpenAPI, and nested projection.

## Why Talea exists

Python applications often need more than a typed record. At an API, event,
configuration, or third-party boundary they need to answer all of these
questions consistently:

- Which Python values are valid without conversion?
- Which external representations are accepted from Mapping and JSON input?
- Where does hostile input receive finite work and error budgets?
- What locations and stable codes does invalid nested data produce?
- Which names and representations appear in serialized output?
- Can a framework project the same contract as Draft 2020-12 JSON Schema or
  an OpenAPI 3.1 Schema Object?
- Can tooling inspect the contract without reconstructing annotations?

Talea answers them from one canonical schema graph, while retaining separate
execution paths for operations that have different trust and performance
requirements.

“2026+” describes that starting point. Talea began with Python 3.14+, PEP 695
generics, deferred annotations, recursive type graphs, current typing behavior,
and modern JSON Schema as architectural assumptions. It did not need to carry
compatibility requirements for historical Python releases or retrofit those
assumptions into an older public contract. This is a design circumstance—not a
claim that mature libraries are obsolete, a prediction of ecosystem
replacement, or a guarantee of future superiority.

## The boundary model

| Operation | Use it when | What it does |
| --- | --- | --- |
| `User(...)` | application code already has Python values | strict, keyword-only construction |
| `Contract(T).validate(value)` | an arbitrary root is already Python-shaped | strict validation without conversion |
| `User.from_mapping(data)` | an external Python Mapping represents an object | structural conversion with finite traversal policy |
| `Contract(T).from_python(data)` | an external root may be a list, union, alias, or TypedDict | structural conversion with finite traversal policy |
| `from_json(data)` | text or bytes crosses a serialized boundary | strict decoding, JSON representations, conversion, and resource policy |
| `to_dict()` / `to_python()` | an application needs detached Python output | schema-aware projection and current-state validation |
| `to_json()` | an application needs JSON text | schema-aware projection followed by encoding |
| `json_schema()` / `openapi_schema()` | tooling needs a standards description | projection from the same canonical graph |

JSON and Mapping boundaries are not aliases for the constructor. For example,
`Decimal`, UUID, temporal values, paths, IP values, bytes, enums, nested Specs,
and tagged unions each retain an explicit Python contract and an explicit JSON
representation.

## What is implemented

Talea currently provides:

- strict, keyword-only, immutable, slotted `Spec` records;
- defaults and factories, inheritance, safe narrowing, custom transforms,
  field checks, whole-Spec checks, and serializers;
- built-in numeric, length, and pattern constraints carried by `Annotated`;
- aliases, titles, descriptions, examples, deprecation, read/write metadata,
  and sensitive-value handling;
- `Contract` for primitives, containers, unions, `TypedDict`, type aliases,
  stdlib dataclasses, recursive graphs, tagged unions, and concrete generic
  specializations;
- first-class Mapping and JSON input with structured nested errors;
- finite transport-size, depth, traversal-node, and error-aggregation policy;
- presence-aware partial Specs, `derive_spec()`, and `apply_patch()` for PATCH
  semantics where absent is not confused with `None`;
- explicit input/output Spec views derived from `ReadOnly` and `WriteOnly`;
- canonical discriminator-based union dispatch and OpenAPI discriminator maps;
- Python and JSON serialization with explicit per-call codec boundaries and
  finite nested include/exclude selection;
- JSON Schema Draft 2020-12 and OpenAPI 3.1-compatible Schema Objects;
- public immutable introspection and runtime `create_spec()` declarations;
- compile-once specialized pure-Python execution with permanent benchmark
  canaries for distinct workloads.

The documentation proves these features with executable account API, REST
PATCH, event, financial, recursive AST, arbitrary Contract, error/security,
schema/OpenAPI, dynamic declaration, and immutable replacement examples.

## A complete service boundary

A framework-neutral request flow looks like this:

```text
raw request bytes
    -> ResourcePolicy
    -> UserCreate.from_json(...)
    -> ValidationError or ResourceLimitError
    -> application/domain operation
    -> UserResponse
    -> to_json()
```

Talea does not choose routes, dependency injection, HTTP status codes, ORM
behavior, or response envelopes. A FastAPI, Lilya, Django, Starlette, Flask, or
other adapter can own those framework concerns while calling the explicit
Talea boundary operations. The manual includes the entire executable flow,
plus presence-aware PATCH and generated input/output OpenAPI fragments.

## Installation

Talea requires Python 3.14+. Install the published release from PyPI:

```console
python -m pip install talea
```

To install from a source checkout instead:

```console
git clone https://github.com/tarsil/talea.git
cd talea
python -m pip install .
```

The core package declares `dependencies = []`. Development, test, benchmark,
build, and documentation tools remain separate development dependencies.

## Not a competition

Talea is not trying to replace Pydantic, msgspec, dataclasses, attrs, or
manually written validation.

Pydantic has broad adoption, extensive integrations, a mature ecosystem, and
coercive/parsing workflows many applications actively want. msgspec has an
extremely fast native implementation, mature serialization, and a different
set of representation and performance tradeoffs. Dataclasses and attrs remain
excellent for internal records that do not need a full external-boundary
contract. Direct Python is often clearest for three checks in one specialized
function.

Talea is another design point: strict, dependency-light, Python-native,
compile-once, explicit-boundary, introspectable, standards-aware, and
security-conscious. Selection is a requirements decision, not a winner/loser
ranking.

## When Talea fits—and when it does not

Talea is worth evaluating when a project uses Python 3.14+, wants strict
ordinary Python construction, needs Mapping or JSON boundaries, values an
empty required dependency graph, and can benefit from structured errors,
finite external-input policy, schemas, or framework introspection.

It is likely the wrong choice when:

- the application depends heavily on Pydantic-specific integrations or wants
  broad coercion by default;
- Python 3.13 or earlier must remain supported;
- settings, ORM extraction, or a large plugin ecosystem must come from the
  same package;
- msgspec already exactly matches a high-throughput native serialization
  workflow;
- the only requirement is a small internal record, where a dataclass or attrs
  class is simpler;
- specialized validation is shorter and clearer as manually written Python;
- adopting a 0.x library with an evolving API and small ecosystem is
  unacceptable.

## Documentation

- [Documentation home](https://talea.tarsild.io)
- [Why Talea?](https://talea.tarsild.io/getting-started/why-talea/)
- [Five-minute quickstart](https://talea.tarsild.io/getting-started/quickstart/)
- [Progressive tutorial](https://talea.tarsild.io/getting-started/tutorial/)
- [Production service boundary](https://talea.tarsild.io/getting-started/production-service/)
- [Concepts and mental model](https://talea.tarsild.io/concepts/)
- [How-to recipes](https://talea.tarsild.io/guides/recipes/)
- [Complete API reference](https://talea.tarsild.io/reference/api/)
- [Security and resource model](https://talea.tarsild.io/resource-security/)
- [Performance method and evidence](https://talea.tarsild.io/engineering/performance/)
- [Known limitations](https://talea.tarsild.io/engineering/limitations/)

For a local checkout, `task docs_test` executes all `docs_src` examples and
checks navigation, links, API inventory, and documentation policy. `task build`
builds the site; `task build_with_checks` verifies release artifacts.

## Maturity and evidence

Talea deliberately remains in the 0.x release series. Compatibility,
deprecation, support, and release governance are not yet frozen, and its
ecosystem is necessarily much smaller than mature alternatives. This is an
ongoing product stage, not a signal that a 1.0 freeze is imminent.

Repository gates include unit and integration tests, 100% line coverage,
linting, formatting, static typing, package checks, executable documentation,
standards-conformance tests, security/adversarial cases, and 19 permanent
benchmark workloads. Performance comparisons require semantically equivalent
operations; no claim is based on removing validation from one side.

See [Contributing](https://talea.tarsild.io/contributing/) for exact commands,
[Maturity and support](https://talea.tarsild.io/release-ledger/) for current
governance, and [Security](https://talea.tarsild.io/engineering/security/) for
the technical threat model and reporting status.

Talea is licensed under the MIT License.
