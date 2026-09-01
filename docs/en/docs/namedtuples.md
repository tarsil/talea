# Positional NamedTuple contracts

`typing.NamedTuple` is useful when a Python application and its wire protocol
agree that field order is part of the contract. Talea preserves that choice:
the Python value keeps its nominal NamedTuple type, while every external and
serialized representation is positional.

```python
from typing import NamedTuple

from talea import Contract


class Coordinate(NamedTuple):
    latitude: float
    longitude: float


coordinates = Contract(Coordinate)
value = coordinates.from_json("[47.37,8.54]")

assert type(value) is Coordinate
assert coordinates.to_python(value) == (47.37, 8.54)
```

`{"latitude": 47.37, "longitude": 8.54}` is not an alternative representation.
Choose a [Spec](concepts/specs.md), [stdlib dataclass](dataclasses.md), or
`TypedDict` when names belong on the external boundary.

## Boundary semantics

| Operation | NamedTuple behavior |
| --- | --- |
| `validate()` | requires the exact declared type and complete declared arity, validates each stored slot, preserves identity |
| `from_python()` | accepts exact `list` or exact `tuple`, converts slots, invokes the declared constructor once |
| `from_json()` | accepts a JSON array after strict JSON decoding |
| `to_python()` | returns an ordinary tuple containing recursively projected values |
| `to_json()` | returns a JSON array |
| `json_schema()` | projects a named Draft 2020-12 array definition |
| `openapi_schema()` | reuses the same array Schema Object under OpenAPI components |

A plain tuple or list fails strict validation even when its contents match.
External conversion accepts only the two concrete built-in positional families;
strings, bytes, mappings, custom sequences, and tuple/list subclasses are not
silently traversed. This keeps the boundary predictable and avoids invoking
arbitrary sequence protocol methods.

Slot failures use zero-based integer locations. A bad second slot is located at
`[1]`; a bad value nested inside that slot continues from `[1, ...]`. Field names
remain available for introspection and documentation, but they are not Mapping
keys or error-path segments for this positional boundary.

## Defaults and arity

NamedTuple defaults follow Python's trailing-default rule:

```python
class Record(NamedTuple):
    required: int
    optional: str = "default"


records = Contract(Record)
assert records.from_python([1]) == Record(1, "default")
assert records.from_python([1, "set"]) == Record(1, "set")
```

This contract accepts lengths one and two. An empty array reports `missing` at
index `0`; a third item reports `unexpected` at index `2`. Nullable types affect
the value in an occupied slot and do not make that position omittable.

Defaults are captured as canonical schema truth, validated when the Contract is
created, and validated again when an omitted position uses them. This matters
for the unusual but legal case of a mutable default whose contents change after
compilation. Talea neither copies nor recomputes a static NamedTuple default.

JSON Schema uses ordered `prefixItems`, `items: false`, `minItems` equal to the
required slot count, and `maxItems` equal to the total slot count. A safe
non-sensitive, serializable trailing default is attached to its own
`prefixItems` entry. `prefixItems` alone does not assert tuple length; the
explicit minimum and maximum encode the same arity law used by conversion.

## Metadata and composition

Field annotations use the normal Talea resolver. Constraints, `Sensitive`,
descriptive schema metadata, and `Representation` therefore work inside a
slot. A Representation can, for example, retain `Decimal` internally while
accepting and emitting exact decimal text in the array.

`Alias`, including legacy names, is rejected because a positional slot has no
external property name. `ReadOnly` and `WriteOnly` are also rejected: omitting
an interior array position would create sparse directional semantics that
Talea does not define. NamedTuple has no Talea serializer or validation hooks;
enclosing Specs retain ownership of their own hooks.

NamedTuple fields compose beneath Specs, dataclasses, TypedDicts, sequences,
mappings, ordinary unions, callable boundaries, Settings fields, incremental
Contract input, and JSONL input. A branch in a tagged union may contain a
NamedTuple, but a NamedTuple cannot itself become the object-property
discriminator owner.

Nested output selection treats a NamedTuple as one leaf. Selecting the enclosing
field is supported; descending by field name or index is not. The selection
grammar remains canonical-name object selection and gains no index language.

## Generics, recursion, and identity

Concrete generic specializations resolve each slot through the existing type
parameter owner:

```python
from typing import Generic, TypeVar

T = TypeVar("T")


class Pair(NamedTuple, Generic[T]):
    first: T
    second: T


assert Contract(Pair[int]).from_python([1, 2]) == Pair(1, 2)
```

An open `Contract(Pair)` is rejected rather than treating unresolved parameters
as `Any`. PEP 695 aliases containing concrete NamedTuple types compose normally.
Module-resolvable recursive and mutually recursive declarations reuse finite
named references. A function-local forward-reference name that has already
left scope remains unrecoverable; Talea does not inspect caller frames or add a
namespace registry.

Two separately declared NamedTuple classes do not collapse merely because they
have equal slot shapes. Declaration identity and generic arguments participate
in the same named-schema identity owner used by other recursive structures.

## Canonical schema and introspection

Annotation resolution creates one frozen `NamedTupleSchema` with the exact
declared type, ordered `NamedTupleField` values, slot schemas, defaults, required
count, and named identity. Strict validation, input, output, standards
projection, recursion, resource accounting, and `inspect_contract()` all consume
that node. Warm execution does not reread `__annotations__`, `_fields`, or
`_field_defaults`.

```python
from talea.introspection import inspect_contract
from talea.schema import NamedTupleSchema

info = inspect_contract(Contract(Record))
assert isinstance(info.schema, NamedTupleSchema)
assert tuple(field.name for field in info.schema.fields) == ("required", "optional")
assert info.schema.required_count == 1
assert info.schema.fields[1].default == "default"
```

The two schema-domain types are public from `talea.schema`, not the root
package. There is no parallel rich NamedTuple introspection hierarchy, and the
projection contains no generated functions, callbacks, locks, or caches.

## Construction and trust

Supported declarations must retain Python's standard generated NamedTuple
constructor shape. Talea validates or converts all positions first, then calls
that constructor exactly once. A declaration with an incompatible mutated
`__new__` is rejected when its schema is resolved. Ordinary user methods are
neither inspected nor executed by contract operations.

The outer tuple is immutable, but a slot may contain a mutable list, dictionary,
dataclass, or Spec graph. Strict validation and output revalidate such current
state instead of granting transitive trust. External traversal shares the
existing `ResourcePolicy` depth, node, and error accounting; runtime cycles
follow the normal cycle policy. `Sensitive` redacts Talea-owned failures, but a
successful output operation is still application-authorized disclosure.

Talea never calls `_asdict()` and introduces no NamedTuple registry. Annotation
evaluation remains trusted declaration-time Python work. Use the narrow
list/tuple external boundary when positional records are part of the protocol;
use an object-shaped owner when field-name evolution, aliases, sparse views, or
property discriminators are requirements.

## Complete market-data example

The executable example uses nested price levels, exact Decimal wire text,
constraints, a trailing venue default, Sensitive data, a generic packet,
recursion, a Spec envelope, standards projection, introspection, callable and
incremental boundaries, JSONL arrays, and resource limits.

{!> ../../../docs_src/tutorials/namedtuple_records.py !}
