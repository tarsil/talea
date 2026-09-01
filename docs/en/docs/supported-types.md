# Supported types

Talea resolves annotations once when a `Spec` class is created. Construction
then executes specialized Python checks with no annotation reflection, adapter
registry, or coercion.

## Strict values

The normal `Spec` constructor accepts Python objects that already satisfy the
declared contract. It does not parse strings into UUIDs, dates, paths, IP
objects, enum members, or decimal values.

```python
from datetime import datetime, timezone
from uuid import UUID

from talea import Spec


class Event(Spec):
    identifier: UUID
    created_at: datetime


event = Event(
    identifier=UUID("12345678-1234-5678-1234-567812345678"),
    created_at=datetime.now(timezone.utc),
)
```

Passing the UUID or datetime as a string to ordinary construction or
`from_mapping` raises `ValidationError` unless that field declares an explicit
inbound `transform`. `from_json` owns a separate schema-aware representation
contract for JSON strings. Talea provides no global coercion policy. See
[Input boundaries](input-boundaries.md).

## Type families

| Annotation | Accepted contract | Important boundary |
| --- | --- | --- |
| `int`, `float`, `str`, `bool`, `bytes` | Exact built-in type | `bool` is not an `int`; subclasses are rejected |
| `Enum`, `IntEnum`, `StrEnum` subclasses | Exact declared enum class | Raw integer/string values and other enum classes are rejected |
| `UUID` | `UUID` instances and subclasses | Strings are rejected |
| `date` | Exact `date` | `datetime` and custom `date` subclasses are rejected |
| `datetime` | `datetime` instances and subclasses | Naive and timezone-aware values are accepted |
| `time` | `time` instances and subclasses | No timezone policy beyond type validation |
| `timedelta` | `timedelta` instances and subclasses | Negative, zero, and large values are accepted |
| `Decimal` | `Decimal` instances and subclasses | Integers, floats, and strings are rejected |
| `PurePath` and `Path` families | Nominal Python path relationships | Strings are rejected; concrete availability remains platform-specific |
| IPv4/IPv6 addresses, networks, and interfaces | Exact declared IP class | Versions and address/network/interface families never cross-match |
| `TypedDict` | Exact `dict` for strict validation; `Mapping` at external input | Required/optional keys and unknown-key rejection follow structural declaration truth |
| stdlib dataclass | Exact declared class with current stored state | Mapping/JSON constructs the original class; `init=False` is output-only |
| `typing.NamedTuple` | Exact declared class with current positional state | External list/tuple and JSON array construct the original class; Mapping/object input is rejected |
| PEP 695 `type` aliases and `NewType` | Underlying supported contract | Named identity is retained without runtime alias dispatch |
| `Annotated[A | B, Discriminator(name)]` | Required single-Literal Spec or TypedDict branches | Direct tag selection; see [Tagged unions](tagged-unions.md) |
| `Annotated[T, Representation(...)]` | Strict internal `T` plus explicitly declared directional schemas | Trusted callbacks run once and their results are validated; see [custom representations](custom-representations.md) |

`date` is intentionally exact because Python defines `datetime` as a subclass
of `date`. A field described as a calendar day should not silently accept a
timestamp. IP contracts are also exact because, for example,
`IPv4Interface` subclasses `IPv4Address` even though interfaces and addresses
are distinct data contracts.

Path contracts follow `pathlib`'s nominal hierarchy. `PurePath` accepts pure
and concrete platform path descendants, while `Path` accepts concrete path
instances for the running platform. Portable code should normally annotate
`PurePath`, `Path`, `PurePosixPath`, or `PureWindowsPath` and avoid constructing
an incompatible `WindowsPath` or `PosixPath` on the host platform.

## Enum values

```python
from enum import StrEnum

from talea import Spec


class Status(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Account(Spec):
    status: Status


account = Account(status=Status.ACTIVE)
```

`Account(status="active")` fails. `IntEnum` and `StrEnum` do not inherit the
acceptance rules of their underlying primitive values.

## Literal

`Literal` supports strings, bytes, integers, booleans, `None`, and enum
members. Checks preserve both the value and its runtime type:

```python
from typing import Literal

from talea import Spec


class Feature(Spec):
    enabled: Literal[True]
    mode: Literal["safe", "fast"]


feature = Feature(enabled=True, mode="safe")
```

`Literal[True]` rejects integer `1`, even though `True == 1` in ordinary Python
equality. Literal alternatives compose inside containers, unions, and optional
fields. Unsupported Literal categories fail when the class is declared.

## Composition

Every supported type and Literal contract can appear inside Talea's existing
containers, tuples, unions, nested Specs, defaults, and inherited fields. A
failure retains the complete field and container location.

These rows describe already-Python construction. JSON input and output have
schema-specific representations, including exact Decimal strings, ISO duration
strings, and base64 bytes. See [Input boundaries](input-boundaries.md) and
[Serialization and JSON output](serialization.md).

`TypedDict` supports `total=False`, `Required`, `NotRequired`, inheritance,
nested declarations, containers, unions, constraints on child fields, and
concrete generic specialization. `ReadOnly` metadata is retained but has no
runtime mutation semantics. Recursive type aliases and recursive TypedDict
graphs resolve through finite declaration-identity back-edges, including
mutual and concrete generic recursion. See [Generics and
recursion](recursive-generics.md).

## Type and operation matrix

| Contract family | Strict Python | External Python | JSON input | Python output | JSON output | Schema |
| --- | --- | --- | --- | --- | --- | --- |
| primitives | exact built-in values | same strict values | native JSON scalar where compatible | same scalar | JSON scalar; finite-number rules apply | scalar type |
| UUID, temporal, Decimal, Path, IP, bytes | declared Python object | same strict object | documented string representation | Python object | documented string representation | type plus format/content encoding where defined |
| Enum and Literal | exact member/value and runtime type | same | canonical JSON member/value | same Python value | canonical JSON value | enum/const |
| list/set/frozenset/dict/tuple | exact concrete container | recursively converts nested mappings/Specs | JSON array/object where representable | detached concrete Python containers | arrays/objects | array/object shapes |
| Spec | instance of the declared nominal type | Mapping constructs a Spec | object constructs a Spec | aliased detached mapping | object | named object definition |
| TypedDict | exact dict with closed keys | Mapping becomes a detached dict | object becomes a detached dict | detached dict | object | named closed object definition |
| stdlib dataclass | exact declared instance | Mapping constructs the original dataclass | object constructs the original dataclass | aliased detached dictionary | object | directional named object definition |
| `typing.NamedTuple` | exact declared instance; plain tuple/list reject | exact list or tuple constructs one instance | array constructs one instance | ordinary positional tuple | array | named array definition with `prefixItems`, `minItems`, and `maxItems`; OpenAPI reuses it |
| untagged union | first strict branch that succeeds | branches attempted in canonical order | branches attempted in canonical order | selected runtime branch | selected branch representation | `anyOf` |
| tagged union | nominal Spec or exact tagged dict | direct discriminator dispatch | direct external-tag dispatch | selected branch | selected branch | `oneOf`; OpenAPI discriminator |
| recursive named graph | strict acyclic/cycle-aware graph | resource-governed traversal | resource-governed traversal | detached acyclic graph | acyclic JSON | finite definitions and references |
| represented custom type | strict internal type/schema | declared `input=` then one validated load | same declared input after JSON decoding | one dump, declared-output validation, detached projection | same output through JSON projection/encoding | declared input/output by mode |

Transforms or serializers can deliberately change a boundary domain. If their
input or output cannot be expressed statically, the corresponding schema mode
raises `SchemaProjectionError` instead of guessing.

## Important JSON representations and edge cases

### Decimal and exact financial values

Strict Python construction accepts a `Decimal`; it rejects strings, integers,
and floats. JSON input and output use strings so decimal text survives without
binary floating-point loss:

```python
from decimal import Decimal
from talea import Contract


amount = Contract[Decimal](Decimal)
assert amount.validate(Decimal("42.50")) == Decimal("42.50")
assert amount.from_json('"42.50"') == Decimal("42.50")
assert amount.to_json(Decimal("42.50")) == '"42.50"'
```

JSON Schema therefore describes Decimal as a string. Numeric Decimal
constraints remain runtime-only because JSON Schema numeric keywords do not
apply to numeric text. Use Decimal for representational exactness, then keep
currency conversion, rounding policy, tick sizes, and accounting rules in the
domain layer.

### Date, datetime, and time

`date` rejects `datetime` despite Python's subclass relationship. JSON uses
ISO-formatted strings and reconstructs the declared temporal type. Talea
accepts both naive and timezone-aware `datetime`; it does not impose an
application timezone policy. API contracts that require instants should add a
check for `tzinfo` and normalize elsewhere deliberately.

### Bytes

Python paths accept exact bytes. JSON uses padded base64 text; arbitrary plain
text is not silently encoded. Schema output carries the content encoding, while
length constraints describe base64 blocks conservatively and runtime
validation owns exact decoded length. Keep streaming uploads outside a complete
in-memory JSON field when their size warrants a streaming protocol.

### IP addresses and paths

Address, interface, and network types remain distinct, as do IPv4 and IPv6.
JSON uses their standard string form, but external Python mappings still require
the declared Python object. Path annotations follow `pathlib`'s nominal
hierarchy and JSON uses text; Talea does not check file existence, permissions,
or path traversal policy.

### Tuples, sets, and dictionaries

Fixed tuples validate each declared position; variadic tuples validate every
item against one contract. JSON represents both as arrays. Sets and frozensets
also use arrays at JSON boundaries and reject duplicate decoded values rather
than silently collapsing them. JSON dictionaries require representable string
keys; a Python mapping contract with non-string keys cannot claim an ordinary
JSON-object schema.

Annotated `typing.NamedTuple` declarations are nominal Python values with the
same positional external shape. Strict validation requires the exact declared
class, while external Python accepts exact list or tuple input. Trailing
defaults reduce `minItems` without changing `maxItems`. See [Positional
NamedTuple contracts](namedtuples.md).

## Financial composition example

The following application-shaped example uses UUID identifiers, Decimal
quantity and price, currency/side enums, a timezone-aware datetime, aliases
matching an external protocol, nested instrument and money Specs, constraints,
serialization, schema output, and a whole-order currency invariant.

{!> ../../../docs_src/tutorials/finance.py !}

Talea establishes representation, type, constraints, and declared cross-field
invariants. Venue calendars, market permissions, credit limits, regulatory
classification, settlement behavior, and persistence remain domain concerns.
This example makes no compliance claim.
