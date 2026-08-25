# Talea

Talea is an early-stage Python 3.14 data-modelling and validation library. A
`Spec` declares required fields with Python annotations:

```python
from talea import Spec


class User(Spec):
    id: int
    name: str


user = User(id=1, name="Tiago")
```

Spec declarations resolve their annotations and compile strict validators once
when the class is created. Construction is keyword-only, performs no coercion,
and stores validated values in compact instance slots. Defaults, Spec
inheritance, parsing, and serialization are not implemented yet.

The canonical schema foundation covers built-in scalar types, homogeneous
built-in containers, dictionaries, fixed and variadic tuples, and PEP 604
unions composed from those forms. Schema and validator compiler internals are
not exported from the root `talea` package.
