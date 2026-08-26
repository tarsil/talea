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

Validation failures expose stable codes and structured, JSON-compatible paths:

```python
from talea import ValidationError

try:
    User(id="1", name="Tiago")
except ValidationError as exc:
    errors = exc.errors()
```

Human rendering identifies the Spec and nested failure without exposing
generated validator internals. Talea bounds representations of hostile input;
applications should use `code`, `location`, and structured context rather than
parse message text.

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

Applications can explicitly transform inbound field values before strict
validation and assert field or cross-field invariants afterward:

```python
from talea import check, transform


class Interval(Spec):
    start: int
    end: int

    @transform("start")
    def parse_start(value: object) -> object:
        return int(value) if isinstance(value, str) else value

    @check("start", "end")
    def ordered(start: int, end: int) -> None:
        if end < start:
            raise ValueError("invalid interval")
```

Transforms do not weaken unhooked fields and cannot bypass the canonical
schema. Checks run before immutable slot commitment. Outbound field serializers
use a separate `@serialize("field")` lifecycle and never change validation.

Subclass fields follow inherited fields, while an override keeps its inherited
position. Each subclass has one flat keyword-only constructor for its complete
effective declaration. Compact multiple inheritance is available when there is
one state-bearing Spec slot lineage; method-only mixins must declare
`__slots__ = ()`.

Untrusted Python mappings use `User.from_mapping(data)`, which constructs nested
Specs, reports missing and unexpected fields, and aggregates independent field
failures without changing ordinary construction. Serialized JSON uses
`User.from_json(data)`. The standard-library decoder is strict about duplicate
keys and non-standard numeric constants, while an explicit per-call `loads`
callable can select another JSON implementation. `user.to_dict()` returns a
detached Python mapping, and `user.to_json()` applies Talea's compiled
schema-aware JSON projection before an optional per-call `dumps` codec. Decimal
output is precision-safe text, timedelta uses exact ISO 8601 duration text, and
bytes use strict base64.

The canonical schema foundation covers built-in scalar types, homogeneous
built-in containers, dictionaries, fixed and variadic tuples, PEP 604 unions,
Literal, enums, UUIDs, dates and times, Decimal, pathlib paths, and IP address
families. `Annotated` carries Talea's immutable numeric, length, and pattern
constraints. Schema and validator compiler internals are not exported from the
root `talea` package.
