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
whose root may be a scalar, container, union, `TypedDict`, dataclass, alias, or
`Spec`.

| Need | Owner |
| --- | --- |
| Immutable domain object | `Spec` |
| Validate `list[User]` without a box class | `Contract[list[User]]` |
| Convert an external mapping into `User` | `User.from_mapping` |
| Convert an external list of mappings into users | `Contract[list[User]].from_python` |
| Keep a stdlib dataclass and add external boundaries | `Contract(DomainDataclass)` |
| Validate an existing value without conversion | `Contract.validate` |
| JSON for an arbitrary root | `Contract.from_json` / `Contract.to_json` |

A Contract consumes the same canonical schema, validator, input, JSON, and
serialization owners as a Spec. It is not an adapter-specific validation
engine.

For an application-owned Python class at an explicit annotation position, use
root-public `Representation` to declare its external input and output contracts.
The same alias then works as a Contract root or beneath every normal container
and object owner. See [Custom domain representations](custom-representations.md).

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

## Standard-library dataclass domains

`Contract` can retain an exact stdlib dataclass type as the runtime result.
Strict validation preserves an existing instance and checks its current stored
state. Mapping and JSON objects construct the original dataclass through its
normal constructor, defaults, factory, and `__post_init__` lifecycle; detached
output becomes a dictionary.

```python
from dataclasses import dataclass


@dataclass(slots=True)
class Customer:
    name: str


customers = Contract(Customer)
ada = customers.from_python({"name": "Ada"})
assert type(ada) is Customer
assert customers.to_python(ada) == {"name": "Ada"}
```

Dataclasses remain unchanged and are not copied into Specs. See
[Standard-library dataclasses](dataclasses.md) for lifecycle, trust, generics,
recursion, schema modes, and security boundaries.

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
`typing.ReadOnly` metadata is retained in canonical schema truth but does not
change runtime dictionary validation. TypedDict is not part of Spec
`derive_spec(mode=...)` directional selection. TypedDict keys stay exactly as
declared, including keys created with functional `TypedDict` syntax.

## Type aliases and NewType

PEP 695 aliases and `NewType` retain their names in canonical schema truth for
introspection, error context, recursion, and standards projection.
Their underlying validation remains compiled inline.

```python
from typing import Annotated, NewType

from talea import Contract, Ge


type UserId = Annotated[int, Ge(1)]
LegacyId = NewType("LegacyId", int)

assert Contract[UserId](UserId).validate(1) == 1
assert Contract[LegacyId](LegacyId).validate(1) == 1
```

Recursive PEP 695 aliases, TypedDict declarations, and dataclasses use finite
canonical named-reference graphs. Self recursion, mutual recursion, mixed
graphs, concrete generic specializations, and recursive tagged TypedDict ASTs
support their documented Contract boundaries. See [Generics and
recursion](recursive-generics.md).

## Generic and recursive Specs

Generic specialization and recursive-reference architecture is consumed
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

## Retained resource policy

`Contract` may retain one immutable input policy for repeated boundary use:

```python
from talea import Contract, ResourcePolicy

identifiers = Contract(
    list[int],
    policy=ResourcePolicy(max_nodes=10_001),
)
values = identifiers.from_json("[1, 2, 3]")
```

`from_python()` and `from_json()` use the retained policy. An explicit per-call
policy replaces it completely; policies are never merged. `None` on a policy
field disables that dimension. `validate()` remains the trusted strict-value
hot path and does no resource accounting. Python/JSON output and standards
projection likewise remain application/tooling-owned operations.

Specs do not retain class-level resource configuration. Their Mapping and JSON
operations use Talea's finite default or one explicit per-call policy. See
[Resource and security model](resource-security.md) for the ownership and trust
boundary.

## Standards projection

`json_schema(mode="input" | "output")` returns Draft 2020-12 for the retained
root annotation. `openapi_schema(...)` returns an OpenAPI 3.1-compatible Schema
Object/components fragment. Aliases, metadata, constraints, recursion, tagged
unions, and standard JSON representations come from the same canonical graph.
An arbitrary transform or serializer can make one mode unknowable and then
raises `SchemaProjectionError` rather than publishing a false schema.

## Complete executable boundary set

The following example moves beyond `Contract(int)`. It exercises UUID and
`list[UUID]`, `dict[str, Decimal]`, a third-party-shaped TypedDict, a recursive
JSON-value alias, a concrete generic alias specialization, Python and JSON
input/output, schemas, and nested failure handling.

{!> ../../../docs_src/recipes/contracts.py !}

Notice that each operation answers a different question. `validate()` requires
the Python representation already to be correct. `from_python()` admits the
external structural form, such as a Mapping for a TypedDict or Spec.
`from_json()` additionally owns JSON string representations. `to_python()`
returns detached Python containers; `to_json()` validates current state and
encodes the JSON representation. Schema methods project the same retained
annotation rather than inspecting the example values.

## Failure, security, and lifecycle guidance

A Contract is immutable after construction and retains no process-global codec
or mutable resource state. Reuse one Contract when the annotation and retained
policy are stable; create different Contracts when two boundaries intentionally
have different policies. An explicit per-call policy replaces the retained
policy instead of merging dimensions, which makes the effective limit
reviewable at the call site.

Contract does not add a sandbox around a Python type. Custom callbacks,
Mapping methods, and codecs remain trusted application behavior. Mutable input
and output containers are revalidated at the next boundary, and cyclic runtime
values fail rather than recursing indefinitely. See [Resource and security](resource-security.md)
and [Error experience](error-experience.md).

Do not introduce Contract solely to wrap a three-line internal predicate. A
small direct function can be easier to review. Contract earns its place when
the same arbitrary annotation needs multiple Talea boundaries, structured
errors, policies, serialization, schemas, or introspection.

## Compilation, caching, and performance

Contract construction resolves the annotation and compiles strict validation.
External Python, JSON input, Python output, and JSON output compile independently
on first use. A Contract instance retains and reuses those artifacts under a
thread-safe publication lock.

There is no process-global Contract cache. Construct a Contract once at the
service or message boundary that owns it, rather than recreating it for every
request. Per-call codecs do not become retained configuration.

## Python 3.14 and 3.15 typing

Python 3.14 has no `typing.TypeForm`; PEP 747 targets Python 3.15. Consequently,
`Contract(int)` can infer `int`, while arbitrary forms should be written as
`Contract[list[int]](list[int])` when precise static output is required. Talea
does not add a runtime dependency or claim inference Python 3.14 cannot express.
This is a static typing limitation, not a runtime limitation.

On Python 3.15, the same public constructor uses the standard-library
`TypeForm[T]`, so `Contract(list[int])`, `Contract(str | int)`, aliases,
`TypedDict`, and other valid type expressions infer `Contract[T]` directly.
Invalid value expressions are rejected statically. Runtime resolution and the
set of executable Talea annotations remain unchanged; in particular, a
statically valid open generic is not an executable contract.

## Security and operational guidance

- Treat one retained Contract as application-owned immutable boundary state.
- External input remains hostile and receives the same resource, cycle, union,
  numeric, and representation checks as Spec boundaries.
- Annotation resolution never evaluates arbitrary callbacks. Explicit string
  forward references use Talea's restricted structural resolution policy.
- Custom JSON codecs select syntax only; they cannot replace canonical
  conversion or output validation.
- Use `Contract(list[T])` for a materialized batch. Streaming, JSONL, per-item
  failure isolation, and callable decoration are not implemented. Use
  `derive_spec(..., partial=True)` for Spec PATCH contracts.
