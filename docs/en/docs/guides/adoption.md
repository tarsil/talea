# Adoption and migration

Talea can be introduced at one boundary without converting an entire codebase.
Start with a request, event, or third-party payload whose strictness and failure
semantics are explicit.

## From dataclasses

Keep dataclasses when their standard mutable or immutable record semantics are
the intended domain representation. Add `Contract(DomainType)` when the same
object needs strict current-state validation, Mapping/JSON construction,
structured errors, detached output, resource policy, introspection, or schema
projection.

```python
from dataclasses import dataclass

from talea import Contract


@dataclass(slots=True)
class Customer:
    customer_id: int
    name: str


customers = Contract(Customer)
customer = customers.from_json('{"customer_id":1,"name":"Ada"}')
```

The dataclass stays unchanged and its constructor/default/post-init lifecycle
remains authoritative. Choose a `Spec` instead when Talea should own immutable
construction, transforms, checks, serializers, or derived PATCH declarations.
See [Standard-library dataclasses](../dataclasses.md) for the complete boundary.

## From TypedDict

Use `Contract[Payload](Payload)` when dictionary identity is useful. Use a Spec
when nominal type identity, attributes, immutability, methods, inheritance, or
field hooks belong in the domain.

## From Pydantic

| Pydantic concept | Nearest Talea surface |
| --- | --- |
| `BaseModel` | `Spec` |
| `TypeAdapter` | `Contract` |
| `model_dump()` | `to_dict()` or `Contract.to_python()` |
| `model_validate_json()` | `from_json()` |
| field validator | `transform` for conversion; `check` for assertion |
| `create_model()` | `create_spec()` |

The mapping is conceptual, not drop-in compatibility. Talea is strict by
default, immutable, Python 3.14-only, and does not provide settings, ORM
attribute extraction, callable validation, or Pydantic's plugin ecosystem.
Pydantic's default coercion and Talea's external conversion must be reviewed
field by field.

## From manually written validators

Begin with `Contract(annotation)` around the value currently checked by custom
code. Preserve application-specific business rules as `check` callbacks only
when they belong to the data contract. Keep authorization, persistence, and
I/O outside Talea.

Before adoption, review [comparison](../engineering/comparison.md), [known
limitations](../engineering/limitations.md), and [security
architecture](../engineering/security.md).

## Introduce one boundary safely

Choose one request or event whose representation is already documented. Write
down the accepted Python form, JSON form, aliases, failure response, maximum
payload shape, and output contract before replacing code. Then:

1. declare the Spec or Contract without changing application business rules;
2. add accepted and rejected fixtures from production-shaped payloads;
3. compare old and new error/serialization semantics explicitly;
4. select a `ResourcePolicy` from the actual shape and upstream limits;
5. project input/output schemas and review them with the framework owner;
6. measure the exact operation under expected valid and invalid workloads;
7. cut over at the adapter seam, not throughout the domain at once.

This keeps rollback local. It also reveals whether the system truly wants
strictness or was relying on implicit conversion such as UUID, boolean, or
numeric strings.

## Preserve intentional coercion

Do not translate a coercive model mechanically and assume strict input is
compatible. Inventory every accepted representation. If a Python Mapping
boundary intentionally accepts one alternate form, declare a focused
`@transform` and test it. If broad parsing is a product requirement across many
types, Pydantic may remain the more coherent choice.

Transforms are trusted callback code and can make the input JSON Schema domain
unknowable. That tradeoff should be visible in review rather than hidden behind
a migration helper.

## Keep boundary and domain models distinct when needed

An API request can contain aliases, write-only credentials, and optional PATCH
presence that do not belong in a stored domain object. A response may need an
allow-listed shape that deliberately omits request secrets. Reusing one type
everywhere saves declarations but can weaken ownership.

The [production service example](../getting-started/production-service.md)
separates request, stored, and response contracts. The [PATCH
guide](../presence-derived-contracts.md) derives partial input from a complete
source while preserving whole-object checks on application.

## Adoption stop conditions

Stop or narrow the migration if the project requires Python below 3.14, depends
on unavailable framework integrations, needs a settings/ORM ecosystem Talea
does not provide, or produces mostly msgspec-native codec workloads already
served well. Also stop if the new declaration is less clear than a tiny manual
validator and none of Contract's additional boundaries are useful.

Adoption during the evolving 0.x series should pin versions, review release notes, keep boundary tests
in the application repository, and assign an owner for compatibility decisions.
The absence of required runtime dependencies reduces deployment surface; it
does not remove the normal operational cost of adopting a young library.
