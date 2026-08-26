# Specs

A `Spec` is an immutable, slotted Python record whose field contract is declared
with annotations.

```python
from talea import Spec, field


class Customer(Spec):
    customer_id: int
    name: str
    tags: list[str] = field(default_factory=list)
```

## Declaration contract

- construction is keyword-only;
- an annotation without a default is required, even when it accepts `None`;
- static defaults are validated at class declaration and reused;
- mutable defaults require `field(default_factory=...)`;
- field bindings cannot be assigned or deleted after construction;
- nested mutable values remain ordinary Python objects and are not deep-frozen;
- exact field order, overrides, hooks, aliases, and metadata are retained in
  canonical declaration truth.

`Spec` supports single inheritance and one state-bearing slot lineage in
multiple inheritance. Generic declarations use Python 3.14 syntax and execute
only after concrete specialization.

## Methods and properties

| Surface | Purpose |
| --- | --- |
| `from_mapping()` | Construct from an untrusted Python Mapping |
| `from_json()` | Decode JSON and construct |
| `to_dict()` | Detached Python mapping projection |
| `to_json()` | JSON projection and encoding |
| `json_schema()` | Draft 2020-12 schema |
| `openapi_schema()` | OpenAPI 3.1-compatible Schema Object/components fragment |
| `present_fields` | Supplied canonical names on a partial derived Spec |
| `copy.replace()` | Python-native validated immutable update |

Copying and deep copying preserve ordinary Python semantics. Pickle works for
importable declarations and acyclic instances; dynamic or local classes must
follow Python's normal importability rules. Cyclic runtime graphs are rejected
by validation or serialization rather than represented.

Continue with [fields and defaults](../field-semantics.md), [composition and
inheritance](../composition-inheritance.md), and [immutable
updates](../reference/immutable-updates.md).

## From declaration to committed instance

When Python creates a concrete Spec class, Talea resolves annotations, validates
defaults and factories, composes inherited fields/hooks, and produces canonical
declaration truth. Construction then evaluates supplied/defaulted values in
field order, runs transforms, structural checks, field checks, and complete
Spec checks, and publishes the immutable object only after every stage succeeds.

```python
from typing import Annotated

from talea import Alias, MinLength, Sensitive, Spec


class Credentials(Spec):
    token: Annotated[str, Sensitive(), MinLength(16)]


class Account(Spec):
    account_id: int
    display_name: Annotated[str, Alias("displayName"), MinLength(1)]
    credentials: Credentials
```

Trusted Python construction uses `account_id` and `display_name`. External
Mapping/JSON input uses `displayName`, can build the nested Credentials from an
object, and applies finite traversal policy. Output uses aliases by default.
Those are operations over one Account contract, not interchangeable aliases.

## Defaults, nullability, and absence

`field: T | None` controls whether `None` is a valid value. `field: T = value`
controls whether ordinary construction may omit the argument. A presence-aware
derived Spec controls whether an external partial may omit the field without
materializing a source default. These are three independent questions.

Mutable defaults require `field(default_factory=...)` so each instance gets its
own value. Factories are trusted application callbacks, run once for an omitted
ordinary field, and are never executed merely to generate JSON Schema.

## Nested mutability and replacement

The Spec binding is frozen; nested lists and dictionaries are not deep-frozen.
If application code mutates a child, later serialization or replacement
revalidates current state where needed. Use `copy.replace()` to create a
validated complete update and `apply_patch()` when change presence came from an
external partial contract.

Immutability does not imply value equality, deep copy, persistence identity, or
thread safety for nested mutable application objects. Choose those policies at
the domain layer.

## Failures, security, and schemas

Invalid construction raises `ValidationError` with a canonical field path and
stable code. Sensitive fields redact Talea-owned repr and failures. Successful
serialization still follows the declared output; a separate response Spec is
the safest allow-list when request credentials must never leave the service.

`json_schema()` and `openapi_schema()` project aliases, requiredness,
constraints, metadata, recursion, and tags from the finalized declaration.
Arbitrary transform/serializer domains can make one mode unprojectable rather
than producing a false schema.

## Performance and when not to use Spec

Declaration is cold work and the specialized constructor is reused. Features
that are absent from a Spec should impose approximately zero hot-path cost.
Nested mutable data and used hooks necessarily add their direct validation work.

Use a dataclass or attrs when you need a plain internal record and no Talea
boundary behavior. Use Contract when the useful root is already a container,
union, alias, TypedDict, or primitive. Use direct Python when the contract is a
small specialized predicate. Spec is most useful when nominal immutable record
behavior and multiple boundary operations belong together.
