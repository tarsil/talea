# Talea

Talea is a Python 3.14 data-modelling and validation library built around
compile-once declarations.

```python
from talea import Spec


class User(Spec):
    id: int
    name: str
    active: bool = True


user = User(id=1, name="Tiago")
```

`Spec` fields are keyword-only and required unless they have an explicit static
default or factory. Talea validates exact Python types without coercion, retains
explicit mutable values rather than copying them, and reports validation
failures from the declared field boundary. Each declaration resolves its
annotations, validates static defaults, and compiles reusable validators before
any instance is constructed. Field bindings are immutable after construction;
only transitively immutable declarations are classified as permanently trusted.

The current release supports scalar and standard-library types, `Literal`,
`Annotated` constraints, homogeneous built-in containers, dictionaries, fixed
and variadic tuples, PEP 604 unions, static defaults, per-instance default
factories, nested and recursive Specs, concrete generic specialization, Spec
inheritance, explicit inbound transforms, and post-structural field and
cross-field checks. See
[Supported types](supported-types.md), [Constraints](constraints.md), and
[Field semantics](field-semantics.md) for the corresponding contracts, and
[Composition and inheritance](composition-inheritance.md) for object graphs and
subclass behavior. [Custom validation](custom-validation.md) documents the
application lifecycle. [Validation errors](error-experience.md) documents stable
codes, structured locations, JSON projection, union diagnostics, and safe human
rendering. [Input boundaries](input-boundaries.md) covers aggregated Mapping
construction, nested data, strict JSON decoding, schema-aware standard-library
conversion, and per-call decoder selection. [Serialization and JSON output](serialization.md)
covers compiled Python projection and strict JSON encoding. [Forward references,
recursion, and generics](recursive-generics.md) covers deferred graph finalization,
cycle behavior, Python 3.14 type parameters, and concrete specialization.
