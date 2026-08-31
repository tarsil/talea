# Typing

Talea targets Python 3.14 and is checked with `ty`. The `py.typed` marker ships
with the package.

## Specs and constructors

`Spec` uses `dataclass_transform`-style typing so analyzers can infer required
and defaulted keyword-only fields, inherited fields, and concrete generic
specializations. Runtime validation remains the authority at boundaries;
static typing catches ordinary call-site mistakes before execution.

```python
class Box[T](Spec):
    value: T


box = Box[int](value=1)  # Box[int]
```

## Contract

Class annotations infer naturally with `Contract(User)`. Python 3.14 cannot
express every runtime `TypeForm`, so complex aliases, unions, `TypedDict`, and
container expressions should normally receive an explicit annotation:

```python
values: Contract[list[int]] = Contract(list[int])
```

## Dynamic APIs

`create_spec()` and `derive_spec()` return `type[Spec]` because their fields are
runtime data. Runtime behavior is complete, but static constructor inference
cannot recover arbitrary mapping keys. The same limitation applies to
dynamically selected include/exclude projections.

## Callable boundaries

`validate_call` uses `ParamSpec` and a return `TypeVar`, so the decorated
function retains its complete static parameter and result shape:

```python
from talea import validate_call


@validate_call
def transfer(amount: int, reference: str) -> bool:
    return True


accepted: bool = transfer(amount=1, reference="invoice-1843")
```

Static tooling rejects a wrong positional value, unknown keyword, or wrong
result assignment. ParamSpec is typing preservation, not a runtime binder or
TypeVar-inference engine. Runtime generic functions with unresolved type
parameters remain unsupported; the concrete implementation behind
`typing.overload` declarations owns runtime annotations. See
[Strict callable boundaries](../callable-boundaries.md).

TypedDict, PEP 695 aliases, `NewType`, recursive aliases, concrete recursive
generics, and specialized generic Specs retain their declared result types when
the annotation is statically visible. Open generic execution is rejected; use a
concrete specialization.

Run the repository typing contract with:

```console
task mypy
```

The task name is retained by project tooling; it currently runs `ty check` over
production code, benchmarks, executable docs, and positive/negative typing
contracts.

## Inheritance and safe narrowing

Static subclassing follows normal Python field lookup, while Talea additionally
checks at declaration time that an override does not widen the inherited
runtime contract. A child may strengthen a constraint or specialize a concrete
generic parameter; it cannot silently turn an inherited integer into an
unrelated string contract.

Constructor inference includes inherited required/defaulted keyword-only fields
in effective order. Multiple inheritance is supported only where one
state-bearing slot lineage keeps object layout and field ownership unambiguous.

## Generic and recursive typing

```python
class Page[T](Spec):
    items: list[T]


users: Page[User] = Page[User](items=[User(id=1)])
user_pages: Contract[list[Page[User]]] = Contract(list[Page[User]])
```

Execution requires concrete specializations; an open `Page` still contains a
free type parameter. Recursive PEP 695 aliases and deferred Spec/TypedDict
references retain their declared type graph. Runtime values must still be
acyclic at JSON-shaped boundaries.

## Deliberate static limits

Decorators such as `@transform` and `@serialize` can change runtime boundary
domains in ways a type checker cannot fully express. Custom codecs are likewise
ordinary callables whose external semantics require tests. `copy.replace()`
preserves the concrete return type but Python's protocol does not validate every
dynamic replacement keyword as precisely as Talea's generated constructor.

Do not add `cast()` merely to silence a disagreement. Determine whether the
call uses the wrong boundary, an open generic, runtime-generated fields, or a
real stub/inference gap. Keep negative typing contracts for rejected calls and
runtime tests for dynamic/callback behavior; neither proof substitutes for the
other.
