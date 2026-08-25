# Custom validation

Type annotations and Talea constraints describe reusable structural truth. An
application still needs rules such as “an interval ends after it starts” or an
explicit boundary that accepts a decimal string. Custom validation adds those
application rules without turning them into competing type schemas.

Talea exposes two lifecycle words:

- `transform("field")` explicitly changes inbound data before structural
  validation;
- `check("field")` or `check("a", "b")` asserts validity after structural
  validation.

There is no generic validator mode. A transform is the only custom operation
that can replace a value, and its output must still pass the field's canonical
schema. A check returns `None` and cannot replace anything.

## Field lifecycle

```python
from typing import Annotated

from talea import Ge, Spec, check, transform


class Product(Spec):
    quantity: Annotated[int, Ge(0)]

    @transform("quantity")
    def parse_quantity(value: object) -> object:
        if isinstance(value, str):
            return int(value)
        return value

    @check("quantity")
    def available(quantity: int) -> None:
        if quantity > 10_000:
            raise ValueError("quantity exceeds available stock")
```

`Product(quantity="10")` succeeds because this declaration explicitly names an
inbound transform. Without that decorator, the same string remains invalid;
Talea has no global coercion mode.

For each field, construction runs:

1. declared transforms in order;
2. exact structural type validation;
3. normalized `Annotated` constraints;
4. declared field checks in order.

A transform that returns `"10"` for an `int` field fails normal structural
validation. It cannot approve its own output or bypass constraints.

## Cross-field invariants and atomic construction

Passing more than one field to `check` declares a whole-Spec invariant:

```python
class Interval(Spec):
    start: int
    end: int

    @check("start", "end")
    def ordered(start: int, end: int) -> None:
        if end < start:
            raise ValueError("end must not precede start")
```

The callback receives validated local values positionally in the order named by
the decorator. Talea runs all field-local pipelines first, then cross-field
checks, then commits immutable slots. It does not create a dictionary of field
values and does not expose a partially initialized `self`.

## Callback contract

Validation hooks are plain functions declared in the class body. Talea treats
them as static callbacks: their arguments are exactly the values named by the
decorator, never `self` or `cls`. The callback remains accessible on the class
as a static method, but construction binds the original function directly into
generated code.

Transforms take one positional argument and return the candidate value. Checks
take one positional argument per target and must return `None`; returning a
boolean or replacement value is a programming error. Check parameter names
must match their decorator targets, which keeps cross-field access explicit and
statically typed. Default parameters,
variadic parameters, instance methods, `staticmethod`/`classmethod` stacking,
generators, and async functions are rejected when the Spec class is declared.
Synchronous construction never stores an unawaited coroutine. An async input
boundary, if introduced, will be a separate API.

Checks are assertions and must not mutate their arguments. Transforms may
deliberately mutate an inbound object, but any such side effect belongs to the
application callback and can remain visible even if later validation fails.
Talea's atomicity guarantee concerns Spec slot commitment; it cannot roll back
arbitrary user code.

Parameter annotations document the two sides of the lifecycle. A transform may
accept `object` or another broad input and returns a value for runtime structural
validation. A field check can annotate the field's validated type. Cross-field
checks annotate each named field independently. Python's `dataclass_transform`
typing still describes constructor arguments by their declared output field
types: static analyzers cannot derive a wider constructor input from a string
decorator target without a Talea-specific plugin.

## Ordering and inheritance

Fields run in canonical field declaration order. Within a field, transforms and
checks preserve their class-body declaration order. Cross-field checks preserve
their own class-body order after all local pipelines. Lifecycle phase therefore
takes precedence over textual interleaving: a check written above a transform
still runs after structure, and every cross-field check runs after all fields.

The method name is hook identity. Inherited hooks run before subclass additions.
A decorated subclass method with the same name replaces the inherited hook at
its established position. An ordinary same-named method removes the inherited
hook, matching normal Python method shadowing. Diamonds retain one MRO-selected
hook rather than running an ancestor twice. A field type override does not
silently remove hooks targeting that field; the effective transform output must
pass the narrowed schema, and inherited checks still apply unless explicitly
overridden or shadowed.

## Defaults and factories

Static defaults are developer-provided Python state. Talea does not transform
them. They must already pass structural validation and every applicable field
check when the class is declared. If a subclass adds a field check to an
inherited static default, that effective default is checked during subclass
declaration. Cross-field checks run at construction because their result depends
on the complete set of values.

An omitted static default is reused without hook calls. An explicitly supplied
value remains distinguishable from omission and runs its transform pipeline,
even when it is identical to the static default. Default factories are input
producers: their outputs run transforms, structural validation, and field
checks. Factories are never called at class declaration.

## Failures

A hook deliberately rejects input by raising `ValueError`. Talea translates it
to `CustomValidationError`, retains the hook name and lifecycle stage, records
all affected root-relative locations, and preserves the original exception as
the cause. Field transforms and checks have one location. Cross-field checks
retain every named field location and use the Spec root as their primary legacy
location.

`TypeError`, `RuntimeError`, `KeyboardInterrupt`, `SystemExit`, and other
unexpected exceptions are not relabeled as bad user input. Talea never catches
`BaseException`. Campaign 8 will own polished rendering and error aggregation;
Campaign 7 supplies the stable failure transport it can consume.

## Nested trust and performance

Inbound transforms run only when input enters construction. They never run
against already-retained current state. A non-permanently-trusted nested Spec is
revalidated at a new Talea boundary using its structural contract, field
checks, and cross-field checks. This catches custom invariants invalidated by
later mutation of a retained list, set, or dictionary without reconstructing
the nested object.

Specs containing only transitively immutable values remain permanently trusted
after their initial custom checks. Hook code should therefore express a
deterministic invariant of its arguments, not depend on mutable global state.

Hook declarations are compiled once into direct callback calls. A hooked field
pays for its Python callback and narrow `ValueError` boundary. A Spec with no
hooks retains no callback, hook branch, registry, dispatcher, or metadata loop
in its generated constructor.
