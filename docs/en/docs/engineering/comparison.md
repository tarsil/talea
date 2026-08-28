# Comparison

The useful question is which semantics and ecosystem fit a boundary, not which
library wins a universal ranking.

| Option | Best fit | Validation/conversion | Runtime footprint | Schemas/JSON | Maturity |
| --- | --- | --- | --- | --- | --- |
| dataclasses alone | typed Python storage | none unless written separately | standard library | manual | standard and mature |
| dataclass + Talea Contract | stdlib domain record with explicit boundaries | exact current state; Mapping/JSON construction | pure Python, zero required runtime dependencies | built in | Talea pre-1.0 |
| TypedDict | static dictionary shapes | static only | standard library | manual | standard and mature |
| Talea | strict contracts with explicit external boundaries | strict Python; schema-aware Mapping/JSON conversion | pure Python, zero required runtime dependencies | built in | pre-1.0, smaller ecosystem |
| Pydantic | broad validation/coercion ecosystem and integrations | configurable; coercive by default with strict modes | Python plus `pydantic-core` | built in | large mature ecosystem |
| msgspec | high-throughput serialization and structured decoding | library-specific strict/conversion semantics | native extension | built in | mature focused ecosystem |
| manually written Python | narrow bespoke hot path or unusual semantics | exactly what is implemented | no framework dependency | manual | maintenance owned by application |

## Where alternatives are stronger

Dataclasses are simpler when runtime validation is unnecessary. TypedDict works
well when only static dictionary shape matters. Pydantic has a much larger
ecosystem, older-version support, settings and integration packages, callable
validation, and established migration knowledge. msgspec has a mature native
codec implementation and excellent serialization throughput. Manually written
Python can be smaller and faster for one fixed operation.

## Talea's distinct choice

Talea combines pure-Python, zero-required-runtime-dependency deployment with
compile-once execution, strict ordinary construction, explicit external
conversion, immutable records, arbitrary Contract roots, rich errors, standards
projection, and finite input policies. Those choices are useful when dependency
surface and predictable boundaries matter more than ecosystem breadth or
implicit coercion.

Benchmark numbers are not reproduced here. See [Performance](performance.md)
for methodology and semantic caveats, and [Adoption](../guides/adoption.md) for
concept mappings.

## The same small strict record

Code shape can clarify vocabulary, but it is not a benchmark and line count is
not a capability score. These representative declarations aim for an immutable
record with strict integer/name validation and unknown-field rejection where
the library supports it.

### Manually written Python

```python
class User:
    __slots__ = ("id", "name")

    def __init__(self, *, id: int, name: str) -> None:
        if type(id) is not int:
            raise TypeError("id must be int")
        if type(name) is not str:
            raise TypeError("name must be str")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("User is immutable")
```

This is explicit and attractive for one tiny internal path. Mapping conversion,
JSON representations, nested locations, serialization, and schemas would need
additional application code if required.

### Dataclass plus manual validation

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class User:
    id: int
    name: str

    def __post_init__(self) -> None:
        if type(self.id) is not int or type(self.name) is not str:
            raise TypeError("invalid User")
```

The dataclass owns record mechanics. When those boundaries are needed without
rewriting the domain type, `Contract(User)` can own conversion, structured
errors, JSON, and schemas while preserving this class and its lifecycle.

### Pydantic

```python
from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    id: int
    name: str
```

This deliberately enables strict and frozen behavior rather than presenting a
poor default-only comparison. Pydantic additionally brings its mature model,
schema, settings, integration, and plugin ecosystems.

### msgspec

```python
import msgspec


class User(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    id: int
    name: str
```

msgspec's native codecs and Struct representation are central to its design.
Its exact conversion, supported types, validation, and error semantics should
be evaluated against the real workload rather than inferred from syntax.

### Talea

```python
from talea import Spec


class User(Spec):
    id: int
    name: str
```

Talea's class is strict, frozen, keyword-only, slotted, Mapping/JSON capable,
serializable, and schema-projectable by contract. That larger built-in surface
is useful only if the application needs it.

## Scenario-based selection

- A FastAPI estate with Pydantic-specific plugins, settings models, and staff
  expertise should usually keep the mature integration path unless a measured
  boundary need justifies isolation.
- A high-throughput service whose supported payloads and native codec workflow
  align exactly with msgspec should use that strength rather than changing for
  novelty.
- An internal scheduler record that never crosses an external boundary may be
  clearest as a frozen dataclass or attrs class.
- A payment event API needing strict Python construction, explicit JSON
  conversion, tagged OpenAPI schemas, finite hostile-input work, and no required
  runtime dependency graph is the kind of combined tradeoff Talea targets.
- A proprietary binary message with three unusual checks may remain clearer as
  direct Python, especially if standards projection has no value.

Performance comparisons must use equivalent work: validation cannot be removed
from one side, coercion cannot be mistaken for strict acceptance, a native JSON
codec cannot be compared to Python-object construction, and error-rich failure
paths cannot be compared to boolean predicates without stating the semantic
difference.
