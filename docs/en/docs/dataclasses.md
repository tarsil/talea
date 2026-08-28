# Standard-library dataclasses

Talea can retain a standard-library dataclass as the runtime domain object while
`Contract` owns strict validation, external Mapping and JSON input, detached
output, structured errors, resource limits, introspection, JSON Schema, and
OpenAPI.

```python
from dataclasses import dataclass

from talea import Contract


@dataclass
class User:
    name: str
    age: int


users = Contract(User)
user = users.from_json('{"name":"Ada","age":37}')
assert type(user) is User
```

This does not turn `User` into a hidden `Spec`. Talea does not decorate or
replace the class, install descriptors, attach instance state, change its
constructor, or copy it into a generated declaration. The original dataclass
continues to own construction, equality, hashing, ordering, pattern matching,
pickle behavior, and application methods.

## Why combine a dataclass with Contract?

A codebase may already use dataclasses as mutable or frozen domain records. If
those objects cross JSON, queue, API, or plugin boundaries, manually maintained
validation, serialization, error, and schema implementations can drift apart.
`Contract(DomainType)` adds those boundary operations while leaving the domain
representation alone.

Use a `Spec` when Talea should own an immutable declaration, generated strict
constructor, transforms, checks, serialization hooks, or derived PATCH types.
Use a dataclass plus `Contract` when standard-library record semantics are the
deliberate domain choice and boundary capabilities are the missing piece.

## One canonical field contract

At Contract creation, Talea reads the effective result of `dataclasses.fields()`
and resolved type annotations once. It retains an immutable `DataclassSchema`
containing:

- exact dataclass type identity and concrete generic arguments;
- effective inherited stored fields in stdlib order;
- each field's canonical Talea schema and annotation metadata;
- `init`, `kw_only`, static default, and default-factory participation;
- the class's frozen binding policy;
- finite identity for recursive declarations.

Validation, input, serialization, introspection, JSON Schema, and OpenAPI all
consume this node. None of those operations independently rereads dataclass
fields. Ordinary contracts that do not use dataclasses retain their existing
compiled paths and perform no dataclass discovery.

`dataclasses.field(metadata=...)` remains application-owned and is not
interpreted as Talea configuration. Declare Talea aliases, constraints, and
documentation/security metadata with `Annotated`:

```python
from dataclasses import dataclass
from typing import Annotated

from talea import Alias, Description, Ge, Sensitive


@dataclass
class Account:
    account_id: Annotated[int, Alias("accountId"), Ge(1)]
    label: Annotated[str, Description("Operator-visible label")]
    token: Annotated[str, Sensitive()]
```

## Strict existing instances

`Contract(User).validate(user)` requires the exact declared dataclass runtime
type, validates current stored state, returns the same object, and does not call
`__init__` or `__post_init__` again. Dataclass subclasses are not accepted by a
base dataclass Contract because subclasses may override field contracts and
lifecycle behavior. Construct a Contract for the concrete subclass instead.

Mutable objects are revalidated at every boundary:

```python
user = User(name="Ada", age=37)
contract = Contract(User)
assert contract.validate(user) is user

user.age = "37"  # ordinary dataclass mutation
contract.validate(user)  # raises ValidationError at ("age",)
```

Talea does not intercept mutation. A frozen dataclass whose entire reachable
field graph is immutable remains valid after successful validation. Frozen
binding alone is insufficient: a frozen dataclass containing `list[int]` is
not permanently trustworthy because the list can still change. Initial strict
validation always checks a foreign dataclass instance even when its state is
transitively immutable.

## External Mapping and JSON construction

`from_python()` accepts a `Mapping` or an exact existing instance.
`from_json()` requires a JSON object after decoding. External keys use `Alias`
when present. Unknown keys are `unexpected`, required constructor fields are
`missing`, and arbitrary attribute-bearing objects are not accepted.

The original dataclass constructor remains the lifecycle owner:

1. Talea validates and converts supplied `init=True` field values.
2. Talea calls the dataclass type once with named keyword arguments.
3. Stdlib defaults and default factories run through that constructor.
4. The constructor invokes `__post_init__` according to normal dataclass rules.
5. Talea validates the returned object's complete stored state, including
   `init=False` fields.

Consequently, a default factory and `__post_init__` each run exactly once.
Talea does not pre-call factories, manually invoke post-init, reconstruct an
existing instance, or validate a different object from the one it returns.

An application exception raised by the constructor or `__post_init__`
propagates unchanged. Talea-owned field conversion and resulting-state failures
use `ValidationError`. A constructor is accepted only when its signature has
exactly the dataclass `init=True` fields with compatible keyword/default state;
an incompatible custom constructor is rejected when the Contract is created.

## Field lifecycle matrix

| Dataclass field form | External input | Stored-state validation | Output | Schema |
| --- | --- | --- | --- | --- |
| required `init=True` | required named key | validated | included | required in both modes |
| static default | omitted argument lets stdlib apply it | result validated | included | truthful default when safely projectable |
| `default_factory` | omitted argument calls factory once | result validated | included | no fabricated default |
| `kw_only=True` | ordinary named key | validated | included | normal requiredness; no positional concept |
| `init=False` | rejected as unexpected | validated after lifecycle | included | absent from input; required/read-only in output |
| `ClassVar` | rejected as unexpected | ignored | omitted | omitted |
| `InitVar` | unsupported | no retained state exists | no retained output exists | Contract creation fails |

Slots and `frozen=True, slots=True` are supported. Execution reads declared
fields directly and never requires `__dict__`.

## Python and JSON output

`to_python()` validates the current object and returns a
`dict[str, object]`. It includes all stored fields, including `init=False`, and
uses aliases. Declared mutable containers are detached recursively. Nested
dataclasses become dictionaries and nested Specs keep their Spec projection
semantics. The dataclass constructor and lifecycle do not run during output.

`to_json()` uses the same canonical field selection and aliases before the
normal Talea JSON encoder. Talea does not call `dataclasses.asdict()`; its deep
copy policy and unrestricted dataclass traversal would create a competing
projection contract.

Strict validation and projection answer different questions:

```python
user = User(name="Ada", age=37)
assert Contract(User).validate(user) is user
assert Contract(User).to_python(user) == {"name": "Ada", "age": 37}
```

## Composition, inheritance, generics, and recursion

Dataclasses work wherever the canonical Schema supports an ordinary type:

- dataclass fields may contain Specs, TypedDicts, aliases, NewTypes,
  constraints, containers, ordinary unions, and other dataclasses;
- Specs may contain dataclasses, including Mapping/JSON construction and
  output;
- stdlib dataclass inheritance and supported multiple inheritance use the
  effective fields reported by `dataclasses.fields()`;
- concrete generic dataclasses such as `Page[User]` preserve their substituted
  field contracts; open generic dataclasses are non-executable;
- self and mutual recursion use finite named references when the Python module
  annotation namespace resolves cleanly.

Tagged unions remain limited to homogeneous Spec or TypedDict families. A
dataclass union can use ordinary union behavior, but
`Annotated[Cat | Dog, Discriminator("kind")]` is not supported in this release.

## Schema and introspection

Input JSON Schema contains only constructor-accepted fields. Output schema
contains every stored field; `init=False` properties are marked `readOnly` and
required because every valid output instance must retain them. Static defaults
are emitted only if the default itself validates and projects without exposing
sensitive data or executing a factory.

Named and recursive dataclasses become Draft 2020-12 `$defs` or OpenAPI 3.1
components through the existing standards projector. Call
`inspect_contract(Contract(User))`; its `ContractInfo.schema` is the immutable
`DataclassSchema`, including exact type, fields, lifecycle participation,
generic identity, recursive references, and frozen trust truth. Mutable stdlib
`Field` objects and compiler artifacts are not exposed.

## Sensitive values and trusted application behavior

`Sensitive()` redacts values and unsafe causes from Talea-owned validation and
serialization failures. It does not remove a field from successful output.
The dataclass's generated `repr` is owned by Python and may display a sensitive
field. Talea cannot change that without mutating or redecorating the class, so
applications must declare `field(repr=False)` when their dataclass repr must
omit a secret.

Declared field access can execute custom `__getattribute__` or descriptor code,
and the original constructor and `__post_init__` are trusted application code.
Talea does not sandbox them. Annotation resolution uses the same trusted Python
namespace policy as other Talea declarations; no dataclass-specific eval or
process-global class registry is introduced.

## Current boundaries

This capability deliberately does not add dataclass transforms, checks,
serialization hooks, replacement APIs, attribute/ORM extraction, or support
for `InitVar`. Use application-level validation/composition for custom logic,
or choose a `Spec` when Talea-owned hooks belong in the declaration. Local
function-scoped forward references that are unavailable to Python's normal
module annotation namespace cannot be reconstructed by Contract; declare
recursive domain types in a resolvable module namespace.

## Complete trading-domain example

The executable example keeps `Money`, `Instrument`, `Trade`, `Customer`, and
`Address` as stdlib dataclasses. It proves frozen/slotted and mutable records,
defaults, nested Mapping/JSON construction, aliases, constraints, Sensitive
errors, `__post_init__` derived state, inheritance, concrete generics,
recursion, mutable revalidation, output, input/output schemas, OpenAPI, and
introspection.

{!> ../../../docs_src/tutorials/dataclass_contracts.py !}
