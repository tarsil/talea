# Talea

Talea is an early-stage Python 3.14 data-modelling and validation library. A
`Spec` declares fields with Python annotations:

```python
from talea import Spec


class User(Spec):
    id: int
    name: str
    active: bool = True


user = User(id=1, name="Tiago")
```

Specs compose and inherit as normal Python classes:

```python
class Employee(User):
    employee_id: int


class Team(Spec):
    lead: Employee
    members: list[User]
```

Spec declarations resolve their annotations, validate static defaults, and
compile strict standalone validators once when the class is created. The same
validation compiler emits those operations directly into each specialized Spec
constructor, avoiding per-field validator calls. Construction is keyword-only,
performs no coercion, and stores validated values in compact instance slots.
Spec field bindings are immutable after construction. Talea
permanently trusts declarations only when their value graphs are also
transitively immutable. Nested mutable Specs are revalidated against their
current declared state at each new validation boundary. Mutable defaults use a
per-instance factory:

```python
from talea import Spec, field


class Basket(Spec):
    items: list[int] = field(default_factory=list)
```

Subclass fields follow inherited fields, while an override keeps its inherited
position. Each subclass has one flat keyword-only constructor for its complete
effective declaration. Compact multiple inheritance is available when there is
one state-bearing Spec slot lineage; method-only mixins must declare
`__slots__ = ()`.

Parsing and serialization are not implemented yet.

The canonical schema foundation covers built-in scalar types, homogeneous
built-in containers, dictionaries, fixed and variadic tuples, PEP 604 unions,
Literal, enums, UUIDs, dates and times, Decimal, pathlib paths, and IP address
families. `Annotated` carries Talea's immutable numeric, length, and pattern
constraints. Schema and validator compiler internals are not exported from the
root `talea` package.
