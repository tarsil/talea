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

The current release supports scalar types, homogeneous built-in containers,
dictionaries, fixed and variadic tuples, PEP 604 unions, static defaults, and
per-instance default factories. See [Field semantics](field-semantics.md) for
the declaration and lifecycle contract. Spec inheritance, parsing, and
serialization are not available yet.
