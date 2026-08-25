# Field semantics

Every `Spec` field is keyword-only. An annotation alone declares a required
field, including when the annotation accepts `None`:

```python
from talea import Spec, field


class Required(Spec):
    value: int | None


class Defaulted(Spec):
    value: int | None = None


class Basket(Spec):
    items: list[int] = field(default_factory=list)
```

`Required()` fails because accepting `None` does not imply omission.
`Defaulted()` receives `None`. Each `Basket()` receives a new list.

## Static defaults

Talea validates static defaults when the class is declared. This is the
earliest deterministic point at which both the resolved schema and the value
exist, and it prevents an invalid declaration from manufacturing trusted
instances later. Static defaults are reused without validation when omitted;
explicit replacements are validated normally.

Lists, sets, dictionaries, and immutable containers containing them are
rejected as static defaults. Talea does not copy defaults implicitly. Use
`field(default_factory=...)` for mutable values so ownership and construction
cost are explicit.

## Default factories

`field` is a declaration function, not an instance wrapper. Its
`default_factory` is called once when the corresponding argument is omitted,
is not called for an explicit argument, and is never called merely to inspect a
class declaration. The returned value passes through the same strict validation
operations as an explicit value. One canonical validation emitter generates
both standalone validators and the operations inlined into Spec constructors;
construction does not call a Python validator function per field. A factory
exception is re-raised through a `TypeError` naming the field, with the original
exception retained as its cause.

## Immutability and validated trust

Spec field bindings are immutable after construction. Assignment and deletion
raise `AttributeError`, including assignment to unknown attributes.
Initialization uses Talea's generated constructor to validate all values before
writing slots.

Shallow immutability is not misrepresented as permanent trust. A declaration
whose schema contains a list, set, or dictionary is validated at construction
but is canonically marked as ineligible for Talea's future no-revalidation
trust path, including when that mutable container is nested in a tuple,
frozenset, or union. A declaration containing only transitively immutable value
schemas is permanently trusted. Referenced Spec declarations propagate their
own classification: an immutable nested Spec preserves permanent trust, while
a Spec containing a mutable value makes the containing declaration ineligible.
At each new nested validation boundary, permanently trusted references require
only nominal compatibility. Non-permanently-trusted references validate their
current canonical field state before they are accepted. This makes it
impossible for an already-invalid mutable Spec to cross a new validation
boundary solely because its Python type still matches.

Three policies were considered:

- Immutable by default: one construction validation boundary, no assignment
  compiler or assignment-time cost, and a direct trust guarantee.
- Mutable with compiled assignment validation: coherent, but introduces a
  second validation path and permanent assignment overhead for every Spec.
- Configurable mutability: possible, but adds policy and generated variants
  before a concrete consumer requires them.

Immutable-by-default bindings plus canonical permanent-trust classification win
because they preserve direct field-read performance and ordinary Python
container semantics without making a false deep-immutability claim. A future
explicit mutable declaration could compile a separate assignment path from the
retained `SpecSchema`; it does not require changing today's field metadata and
is intentionally not implemented now.

## Canonical ownership

The immutable ordered `SpecSchema` owns each field's name, resolved schema,
required, static-default, or factory state, and the derived permanent-trust
classification. Generated constructors and future projections consume that
retained declaration truth. Instances contain only their field values and never
reconstruct lifecycle semantics from annotations.
