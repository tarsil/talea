# Talea

Talea is a Python 3.14 data-modelling and validation library built around
compile-once declarations.

```python
from talea import Spec


class User(Spec):
    id: int
    name: str


user = User(id=1, name="Tiago")
```

`Spec` fields are required and keyword-only. Talea validates exact Python types
without coercion, retains mutable values rather than copying them, and reports
validation failures from the declared field boundary. Each declaration resolves
its annotations and compiles reusable validators before any instance is
constructed.

The current release supports scalar types, homogeneous built-in containers,
dictionaries, fixed and variadic tuples, and PEP 604 unions. Defaults, Spec
inheritance, parsing, and serialization are not available yet.
