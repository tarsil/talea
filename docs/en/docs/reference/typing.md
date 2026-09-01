# Typing

Talea supports Python 3.14 and newer and is checked with `ty`. The `py.typed`
marker ships with the package. Python 3.15 uses the standard-library
`typing.TypeForm` from PEP 747 where a public argument represents a type
expression; Python 3.14 uses a less precise, truthful fallback without
`typing_extensions`.

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

## Contract and TypeForm

On Python 3.15, `Contract(type-form-for-T)` infers `Contract[T]` for classes,
unions, containers, `TypedDict`, `Annotated`, `Literal`, PEP 695 aliases,
recursive aliases, dataclasses, Specs, and concrete generic specializations:

```python
integers = Contract(list[int])  # Contract[list[int]] on Python 3.15
choice = Contract(str | int)  # Contract[str | int] on Python 3.15
```

`TypeForm` also rejects values such as `123` or `object()` at a 3.15 static
call site because they are not type expressions. It does not execute or resolve
the expression. Talea's canonical resolver remains the runtime authority and
may reject a statically valid form that Talea does not support.

Python 3.14 has no `typing.TypeForm`. Classes still infer naturally with
`Contract(User)`, while aliases, unions, `TypedDict`, and container expressions
should receive an explicit annotation when precise output is required:

```python
values: Contract[list[int]] = Contract(list[int])
```

The fallback is `object`, not `Any`: it does not leak an unconstrained result
type into `validate()`, input, or output methods.

Both incremental operations preserve the retained Contract result exactly:

```python
from collections.abc import Iterator

users: Contract[User] = Contract(User)
strict: Iterator[User] = users.iter_validate(existing_users)
external: Iterator[User] = users.iter_python(mapping_cursor)
```

The source boundary is `Iterable[object]`, which permits honest validation of
untrusted Python items without public `Any`. `on_error` is exactly
`Callable[[int, ValidationError], None]`; a wrong callback return, non-iterable
source where statically visible, or `ResourcePolicy`/`ItemPolicy` mix-up is a
typing error.

## Representation relationships

Python 3.15 relates `Representation(input=...)` and `output=...` to the loader
and dumper types:

```python
representation = Representation(
    input=str | int,
    load=load_identifier,
    output=IdentifierPayload,
    dump=dump_identifier,
)
```

Here the loader must accept `str | int`, its result and the dumper argument
share the internal type, and the dumper must return `IdentifierPayload`.
Input-only and output-only declarations retain the same directional checks.
Python 3.14 cannot infer the type-form sides, but its generic callback checks
remain in force; explicit `Representation[InputT, InternalT, OutputT]`
annotations provide the missing declaration types when needed.

## Dynamic APIs

`create_spec()` and `derive_spec()` return `type[Spec]` because their fields are
runtime data. On Python 3.15, `create_spec(fields=...)` checks that mapping
values are type forms, but static constructor inference still cannot recover
arbitrary mapping keys. Defaults, factories, namespace values, and metadata are
not type forms and keep their own heterogeneous annotations. The same dynamic
shape limitation applies to selected `derive_spec()` projections.

`@serialize(..., output=TypeExpression)` also uses TypeForm on Python 3.15, so
the decorated callback result must match the declared output. Omitting
`output=` preserves the callback's own result type. Runtime output validation
continues to use the canonical schema on both Python versions.

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
result assignment. It also retains positional-only and keyword-only rules,
variadic item/value contracts, `Unpack[TypedDict]` required and optional keys,
and bound instance/class/static method result types. ParamSpec is typing
preservation, not a runtime binder or
TypeVar-inference engine. Runtime generic functions with unresolved type
parameters remain unsupported; the concrete implementation behind
`typing.overload` declarations owns runtime annotations. See
[Strict callable boundaries](../callable-boundaries.md).

TypedDict, PEP 695 aliases, `NewType`, recursive aliases, concrete recursive
generics, and specialized generic Specs retain their declared result types when
the annotation is statically visible. A type checker can accept an open generic
as a syntactically valid type expression, but Talea still rejects executable
open generics at runtime; use a concrete specialization.

Run the Python 3.14 repository typing contract with:

```console
task mypy
```

The task name is retained by project tooling; it currently runs `ty check` over
production code, benchmarks, executable docs, and positive/negative typing
contracts. CI runs a separate Python 3.15 lane over the shared contracts and
the focused TypeForm positive/negative matrix, using the real standard-library
`typing.TypeForm` signature.

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
free type parameter. TypeForm acceptance does not imply runtime executability.
Recursive PEP 695 aliases and deferred Spec/TypedDict references retain their
declared type graph. Runtime values must still be acyclic at JSON-shaped
boundaries.

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
