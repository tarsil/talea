# Serialization and JSON output

`Sensitive()` does not omit a field from successful output, and `WriteOnly()`
is classification rather than core serialization policy. Sensitive
serialization-hook and codec failures drop callback causes and retain only safe
locations. See [Metadata and sensitive fields](metadata-security.md).

Talea has two outbound operations with deliberately different representations:

| API | Representation owner | Result |
| --- | --- | --- |
| `value.to_dict()` | Compiled Talea Python projector | New `dict[str, object]` with Python-native values |
| `value.to_json()` | Compiled Talea JSON projector, then selected codec | JSON `str` |

There is no `to_python()` alias and no `model_dump` vocabulary. `to_dict()` is
the one Python mapping operation; `to_json()` is the one encoded JSON operation.

```python
from datetime import datetime, timezone
from uuid import UUID

from talea import Spec


class Event(Spec):
    identifier: UUID
    created_at: datetime


event = Event(
    identifier=UUID("12345678-1234-5678-1234-567812345678"),
    created_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
)

python_data = event.to_dict()
json_text = event.to_json()
```

`python_data` retains the `UUID` and `datetime`. The JSON tree uses strings,
and the final value is compact JSON text. A codec only encodes the already
projected tree; changing codecs cannot change Talea's field representations.

## Python mapping semantics

`to_dict()` returns a fresh top-level dictionary. Nested Specs become nested
dictionaries. Scalar values that are already natural Python data remain the
same objects. Declared containers preserve their Python kind:

| Declared value | Python output |
| --- | --- |
| primitive, UUID, temporal, Decimal, path, IP, Enum, Literal | Same Python value |
| nested `Spec` | New nested dictionary |
| `list[T]` | New list with projected members |
| `tuple[...]`, `tuple[T, ...]` | New tuple with projected members |
| `set[T]` | New set with projected members |
| `frozenset[T]` | New frozenset with projected members |
| `dict[K, V]` | New dictionary with projected keys and values |

Preserving tuple, set, and frozenset makes a Python round trip strict:

```python
class Basket(Spec):
    items: list[int]
    labels: set[str]


basket = Basket(items=[1, 2], labels={"new"})
restored = Basket.from_mapping(basket.to_dict())
```

Specs use identity equality, so tests should compare declared field values or
another application-level equivalence contract rather than `restored == basket`.

### Mutable output never aliases Spec storage

Serialization does not expose a Spec's retained mutable containers:

```python
output = basket.to_dict()
output["items"].append(3)

assert basket.items == [1, 2]
```

The copying is declared-structure projection, not an unrestricted
`copy.deepcopy()`. Talea does not call arbitrary object protocols or copy
unknown objects. Hashable dictionary key contracts are preserved structurally.
A mapping key such as a nested Spec cannot become a dictionary without losing
hashability, so `to_dict()` raises `SerializationError` for that declared shape
instead of retaining an alias to the nested object.

## JSON representation table

`to_json()` projects every field to a JSON-native tree before calling a codec:

| Declared contract | Canonical JSON representation |
| --- | --- |
| `None`, `bool`, `int`, `str` | Corresponding JSON primitive |
| `float` | Finite JSON number |
| `Decimal` | Decimal text in a JSON string |
| `bytes` | Padded RFC 4648 base64 string |
| `UUID` | Canonical string |
| `datetime`, `date`, `time` | `isoformat()` string |
| `timedelta` | Exact ISO 8601 duration string |
| supported path types | String |
| supported IP address/network/interface | String |
| Enum | Supported member value |
| Literal | Validated value's canonical representation |
| nested `Spec` | JSON object |
| list, tuple, set, frozenset | JSON array |
| `dict[str, T]` | JSON object |

JSON object keys must be exact strings. Talea does not stringify integer,
tuple, UUID, or Enum mapping keys because `from_json()` would not reconstruct
the declared key contract.

Non-finite floats and Decimals are rejected before codec invocation. The
standard-library encoder is also configured with `allow_nan=False`, so Talea
does not emit the non-standard `NaN` or Infinity tokens accepted by Python's
default `json.dumps()` settings.

### Decimal

Decimal output never passes through `float`. Talea emits `str(decimal)` as a
JSON string and accepts that string on Decimal JSON input. This preserves the
coefficient and exponent exactly and gives stdlib and external codecs the same
genuinely JSON-native tree.

Finite JSON-number input remains accepted. It is useful for
external producers, and the default decoder still preserves fractional tokens
as Decimal. The canonical Talea outbound form is a string because JSON has no
standard in-memory arbitrary-precision numeric scalar that can be handed to an
arbitrary `dumps` callable without either codec-specific behavior or float
loss.

```python
from decimal import Decimal


class Price(Spec):
    amount: Decimal


price = Price(amount=Decimal("1234567890.12345678901234567890"))
assert Price.from_json(price.to_json()).amount == price.amount
```

### Timedelta

Timedelta uses a bounded ISO 8601 duration subset with days, hours, minutes,
seconds, and up to six fractional digits. It preserves Python's complete
microsecond-resolution range, including negative values. Calendar months and
years are not emitted because `timedelta` has no calendar-relative semantics.

Examples include `PT0S`, `P2D`, `PT1.000001S`, and
`-P1DT23H59M59.999999S`.

### Bytes

Bytes use padded RFC 4648 base64. Decoding validates alphabet and padding; an
ordinary string field is never base64-decoded. This schema-specific distinction
avoids ambiguity for a plain bytes field and projects to JSON Schema
`contentEncoding`.

Unions such as `bytes | str`, `Decimal | str`, or `timedelta | str` have
intrinsically overlapping JSON string representations. Talea applies its
deterministic canonical union order, but applications that need to preserve
which overlapping branch produced a string should use a discriminator or an
explicit field serializer rather than rely on indistinguishable JSON syntax.

### Enum and Literal

An Enum member emits its value only when that value is `None`, `bool`, `int`,
`str`, or a finite `float`. Talea never uses `repr()` or the member name.
Unsupported member values raise `SerializationError`. Literal values do not
emit metadata; they use the same actual-value representation, including base64
for a bytes Literal and the member value for an Enum Literal.

## Aliases

Aliases are one bidirectional external-field truth declared with top-level
`Annotated` metadata:

```python
from typing import Annotated

from talea import Alias, Spec


class User(Spec):
    first_name: Annotated[str, Alias("firstName")]


user = User(first_name="Ada")
assert user.to_dict() == {"firstName": "Ada"}
assert User.from_mapping({"firstName": "Ada"}).first_name == "Ada"
```

The Python constructor and attribute remain `first_name`; `Alias` does not
alter static constructor typing. `from_mapping()` and `from_json()` accept the
alias instead of the canonical field name when one is declared. `to_dict()`
and `to_json()` use aliases by default, so their ordinary output round trips.
Pass `by_alias=False` when an internal-name diagnostic mapping is needed.

Aliases must be non-empty and unique. An alias cannot collide with any
canonical or external name in the effective inherited declaration. Input,
output, and standards projection all consume the same `SpecField.alias`;
there are no separate validation and serialization alias maps.

## Field selection

Both methods accept top-level canonical-name sets:

```python
public = user.to_dict(include={"first_name"})
without_debug = user.to_json(exclude={"debug"}, exclude_none=True)
```

`include` selects fields, then `exclude` removes fields, so exclusion wins.
Unknown field names raise `ValueError`; non-set or non-string values raise
`TypeError`. Selections always use canonical Python names even when output keys
use aliases. This avoids policies changing meaning with `by_alias`.

Plain output pays no per-field selection branches. A separate filtered
serializer is compiled lazily only when one of these options is used.

Nested include/exclude trees are not supported. Callers can serialize a
nested Spec directly or use a field serializer when a nested public view is a
domain contract. `exclude_defaults` is absent because default factories do not
own one stable comparable default. `exclude_unset` is absent because Talea does
not add per-instance provenance metadata to every Spec.

## Field serializers

`@serialize("field")` is separate from inbound `transform` and `check`:

```python
from talea import Spec, serialize


class Token(Spec):
    raw: bytes

    @serialize("raw")
    def hexadecimal(raw: bytes) -> str:
        return raw.hex()
```

The callback receives the validated Python value before canonical projection.
Its return value replaces the normal field representation for that one
operation. `to_dict()` copies supported containers in the returned value;
`to_json()` recursively requires a JSON-compatible result and applies the same
standard scalar representations. A hook runs once in `to_dict()` and once in
`to_json()`—`to_json()` never calls `to_dict()` internally.

Serializer method names follow normal Python/MRO identity. A subclass method
with the same name replaces an inherited serializer in place; an ordinary
same-named method shadows it; subclass additions follow inherited serializers.
Only one effective serializer may target a field. Async functions, generators,
descriptors, unknown targets, and incompatible signatures fail when the class
is declared.

Hook exceptions become `SerializationError` at the field location and remain
available as `__cause__`. Validation errors continue to mean input rejected by
a contract; they are not reused for output failures. Whole-Spec serializers
are not supported because replacing the complete declared representation would
undermine canonical field, alias, and standards schema truth.

## Selecting a JSON encoder

Pass one encoder explicitly for one call:

```python
import orjson

text = event.to_json(dumps=orjson.dumps)
```

The callable receives only dictionaries, lists, strings, integers, finite
floats, booleans, and `None`. It never receives a Spec, Schema, UUID, Decimal,
or serializer context. A returned `str` is used directly. `bytes` or
`bytearray` must contain UTF-8 JSON and are decoded, so the public return type
is always `str` regardless of codec.

A custom encoder's `ValueError` becomes `SerializationError`; other exceptions
propagate as codec defects. Talea verifies the output type and UTF-8 boundary,
but it does not parse successful custom output again. Applications are
responsible for choosing a codec that emits valid JSON syntax.

There is no registry or global codec configuration. Codec choice is per call
and stores no state on the class or instance.

## Compilation and performance

Each Spec owns independent serializer slots. Python and JSON projection compile
on their first use and publish atomically under the same proven cold-path
pattern as Mapping and JSON input. Alias/canonical-key and filtered variants are
also independent. Repeated calls read the retained function directly and take
no lock.

Compilation walks the canonical Schema once and emits direct attribute reads,
dictionary literals, container comprehensions, scalar conversions, and nested
serializer calls. Repeated output does not walk `SpecSchema`, inspect
annotations, or dispatch through a serializer registry. Serializer metadata is
class-owned; instance slots and size are unchanged.

The serialization benchmark reports direct `to_dict()` versus equivalent
hand-written dictionary construction, nested/container/standard/hook/alias and
filter cases, JSON projection separately from dumps, full `to_json()`, cold
first use, declaration cost, allocations, and retained shallow memory.

## Security and limits

Talea never serializes through `repr()`. Strings and Unicode go to the selected
JSON codec as data. Standard types use fixed schema-selected operations.
Non-finite numbers, unsupported Enum values, non-string JSON keys, invalid
hook results, wrong codec return types, and non-UTF-8 codec bytes fail clearly.

Serialization creates output proportional to the declared value graph. Talea
does not apply `ResourcePolicy` to output: serialization operates on
application-owned, already-validated values rather than an external transport.
Recursive Specs and named graphs are supported by separately compiled back
edges, with deliberate cycle rejection. Applications that serialize mutable or
otherwise unbounded graphs own output size, deadlines, and process isolation.
