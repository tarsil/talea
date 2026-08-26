# Arbitrary contracts

`Contract` validates, converts, and serializes one supported annotation without
requiring a wrapper `Spec`.

```python
from typing import Annotated

from talea import Contract, Ge


identifier_contract = Contract[list[Annotated[int, Ge(1)]]](
    list[Annotated[int, Ge(1)]]
)
identifiers = identifier_contract.validate([1, 2, 3])
```

The explicit generic argument is intentional on Python 3.14. It gives type
checkers the result type for runtime forms such as `list[int]`; it does not
change runtime behavior.

## Contract or Spec?

Use a `Spec` when the contract is a named immutable domain object with fields,
methods, validation hooks, and inheritance. Use a `Contract` at a boundary
whose root may be a scalar, container, union, `TypedDict`, alias, or `Spec`.

| Need | Owner |
| --- | --- |
| Immutable domain object | `Spec` |
| Validate `list[User]` without a box class | `Contract[list[User]]` |
| Convert an external mapping into `User` | `User.from_mapping` |
| Convert an external list of mappings into users | `Contract[list[User]].from_python` |
| Validate an existing value without conversion | `Contract.validate` |
| JSON for an arbitrary root | `Contract.from_json` / `Contract.to_json` |

A Contract consumes the same canonical schema, validator, input, JSON, and
serialization owners as a Spec. It is not an adapter-specific validation
engine.

## Strict Python and external Python

`validate()` is strict. It accepts an already-valid Python value and returns
the same root object. It does not turn strings into integers or mappings into
Specs.

```python
from talea import Contract, Spec


class User(Spec):
    name: str


users = Contract[list[User]](list[User])
ada = User(name="Ada")
assert users.validate([ada])[0] is ada
```

`from_python()` is the external Python boundary. It accepts the Mapping forms
supported by Talea input and returns detached container structure where the
boundary requires it.

```python
converted = users.from_python([{"name": "Ada"}])
assert isinstance(converted[0], User)
```

Strict primitive rules remain in force at this boundary. Conversion is
structural, not a global coercion policy.

## JSON input and output

`from_json()` uses Talea's strict standard-library decoder by default. A
one-argument `loads` callable can be selected per call. `to_json()` first
validates and performs Talea's schema-aware projection, then uses the default
encoder or a per-call `dumps` callable.

```python
contract = Contract[list[User]](list[User])
value = contract.from_json('[{"name":"Ada"}]')
encoded = contract.to_json(value)
assert encoded == '[{"name":"Ada"}]'
```

There is no global codec registry and no Contract-specific JSON representation.
`to_python()` returns the corresponding detached Python projection. This name
is used because an arbitrary root is not necessarily a dictionary.

Outbound operations validate current state before projecting it. Mutable
containers therefore cannot bypass their contract after initial validation.
Failures use the existing `ValidationError` and `SerializationError` domains;
root failures have an empty location and nested failures retain their complete
path.

## TypedDict service boundaries

`TypedDict` is a structural mapping contract, not a hidden Spec constructor.
Strict validation requires an exact `dict`; external Python input accepts a
`Mapping` and returns a detached exact dictionary. Unknown keys are rejected.

```python
from typing import Annotated, NotRequired, Required, TypedDict

from talea import Contract, Ge


class PaymentEvent(TypedDict, total=False):
    event_id: Required[str]
    amount_cents: Required[Annotated[int, Ge(0)]]
    trace_id: NotRequired[str]


payment = Contract[PaymentEvent](PaymentEvent)
event = payment.from_json(
    '{"event_id":"evt-1","amount_cents":4200,"trace_id":"trace-7"}'
)
```

Talea follows Python 3.14 required, optional, `total=False`, `Required`,
`NotRequired`, inheritance, nested, union, and generic TypedDict semantics.
`ReadOnly` metadata is retained in canonical schema truth but does not change
runtime dictionary validation. TypedDict keys stay exactly as declared,
including keys created with functional `TypedDict` syntax.

## Type aliases and NewType

PEP 695 aliases and `NewType` retain their names in canonical schema truth for
introspection, error context, recursion work, and future schema projection.
Their underlying validation remains compiled inline.

```python
from typing import Annotated, NewType

from talea import Contract, Ge


type UserId = Annotated[int, Ge(1)]
LegacyId = NewType("LegacyId", int)

assert Contract[UserId](UserId).validate(1) == 1
assert Contract[LegacyId](LegacyId).validate(1) == 1
```

Recursive PEP 695 aliases and TypedDict declarations use finite canonical
named-reference graphs. Self recursion, mutual recursion, mixed graphs,
concrete generic specializations, and recursive tagged TypedDict ASTs support
every Contract boundary. See [Recursive aliases and TypedDict
graphs](recursive-named-graphs.md).

## Generic and recursive Specs

Campaign 11's specialization and recursive-reference architecture is consumed
directly by Contract.

```python
from talea import Contract, Spec


class Page[T](Spec):
    items: list[T]


class User(Spec):
    name: str


page_contract = Contract[Page[User]](Page[User])
page = page_contract.from_python({"items": [{"name": "Ada"}]})


class Node(Spec):
    value: int
    children: list["Node"]


forest = Contract[list[Node]](list[Node])
nodes = forest.from_json('[{"value":1,"children":[]}]')
```

Concrete generic specializations are required. An open generic cannot define a
complete runtime contract.

## Compilation, caching, and performance

Contract construction resolves the annotation and compiles strict validation.
External Python, JSON input, Python output, and JSON output compile independently
on first use. A Contract instance retains and reuses those artifacts under a
thread-safe publication lock.

There is no process-global Contract cache. Construct a Contract once at the
service or message boundary that owns it, rather than recreating it for every
request. Per-call codecs do not become retained configuration.

## Python 3.14 typing

Python 3.14 has no `typing.TypeForm`; PEP 747 targets Python 3.15. Consequently,
`Contract(int)` can infer `int`, while arbitrary forms should be written as
`Contract[list[int]](list[int])` when precise static output is required. Talea
does not add a runtime dependency or claim inference Python 3.14 cannot express.
This is a static typing limitation, not a runtime limitation.

## Security and operational guidance

- Treat one retained Contract as application-owned immutable boundary state.
- External input remains hostile and receives the same depth, cycle, union,
  numeric, and representation checks as Spec boundaries.
- Annotation resolution never evaluates arbitrary callbacks. Explicit string
  forward references use Talea's restricted structural resolution policy.
- Custom JSON codecs select syntax only; they cannot replace canonical
  conversion or output validation.
- Use `Contract(list[T])` for a materialized batch. Streaming, JSONL, per-item
  failure isolation, callable decoration, and partial/presence contracts are
  separate owners recorded in the [release ledger](release-ledger.md).
