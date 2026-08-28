# Validation errors

Validation failures are an application-facing API. Talea separates stable
machine facts from human wording: applications should branch on error codes and
structured locations, while logs and terminals use `str(error)`.

## Catching failures

`ValidationError` is available from the root package:

```python
from typing import Annotated

from talea import Ge, Spec, ValidationError


class User(Spec):
    name: str
    age: Annotated[int, Ge(18)]


try:
    User(name="Ada", age=15)
except ValidationError as exc:
    print(exc)
```

The output identifies the Spec, exact location, failed contract, input, and
received type without exposing compiler terminology:

```text
User
  age
    Expected value >= 18
    received: 15 (int)
```

Human wording may improve between releases. Do not parse it. `ErrorCode` values,
locations, and documented structured keys are the compatibility surface.

## Structured errors

`errors()` returns a new list of JSON-compatible dictionaries on every call:

```python
try:
    User(name="Ada", age=15)
except ValidationError as exc:
    payload = exc.errors()

assert payload == [
    {
        "code": "greater_than_or_equal",
        "location": ["age"],
        "message": "Expected value >= 18",
        "expected": "int satisfying Ge(18)",
        "received": "int",
        "input": 15,
        "context": {"limit": 18},
    }
]
```

The projection is described by the public `ErrorData` typed dictionary. It is
safe to mutate while adapting an API response; changing it cannot mutate the
exception or a later `errors()` result. `json.dumps(exc.errors())` works without
a custom encoder. `ErrorCode` is a string enum for typed comparisons, while the
projected `code` is an ordinary JSON string.

Keys appear only when meaningful:

| Key | Meaning |
| --- | --- |
| `code` | Stable machine-readable category |
| `location` | Root-relative list of field names, indexes, or member keys |
| `message` | Human wording for this detail |
| `expected` | Contract rejected at this location |
| `received` | Concrete Python type name |
| `input` | Bounded JSON scalar or safe representation |
| `context` | Constraint or lifecycle facts, such as a numeric limit |
| `hook`, `stage` | Custom callback identity and lifecycle stage |
| `locations` | All fields involved in a whole-Spec check |
| `branches` | Compact diagnostics for attempted union alternatives |

The exception's `location` property remains an immutable tuple, such as
`("members", 2, "email")`. JSON projection uses a list because arrays are the
native framework and wire-format representation. Mapping keys and set members
remain individual location segments. A segment that is not itself a safe JSON
scalar becomes bounded representation text in `errors()`; the internal Python
location remains structured and unchanged.

## ErrorCode reference

| Code | Meaning and typical location | Likely fix |
| --- | --- | --- |
| `type` | wrong exact/nominal type at a field or item | supply the declared Python type or use the proper external boundary |
| `literal` | value is not one declared Literal | use one exact type-sensitive alternative |
| `union` | every untagged union branch failed | inspect `branches` and correct the selected shape |
| `missing` | required Mapping/JSON key is absent | supply the external field name |
| `unexpected` | Mapping/JSON contains an unknown key | remove it or correct an alias |
| `greater_than` | numeric value is not above the limit | raise the value or change the declaration |
| `greater_than_or_equal` | numeric value is below the inclusive limit | raise the value or change the declaration |
| `less_than` | numeric value is not below the limit | lower the value or change the declaration |
| `less_than_or_equal` | numeric value is above the inclusive limit | lower the value or change the declaration |
| `multiple_of` | numeric value is not divisible by the declaration | supply a valid multiple |
| `min_length` | string/bytes/container is too short | add members or lower the constraint |
| `max_length` | string/bytes/container is too long | remove members or raise the constraint |
| `pattern` | string does not contain a regex match | correct the value or pattern |
| `transform` | trusted pre-validation callback raised | correct callback input policy or behavior |
| `field_check` | field-local assertion raised | satisfy the business invariant |
| `spec_check` | multi-field/whole-Spec assertion raised | inspect `locations` and satisfy the invariant |
| `factory` | default factory raised | repair it; inspect a non-sensitive `__cause__` |
| `representation_load` | trusted representation loader rejected accepted external input | correct the input or loader behavior |
| `representation_result` | loader result failed the internal contract at the containing path | repair the loader return value |
| `json_invalid` | decoder could not produce valid JSON input | correct syntax or codec contract |
| `json_duplicate` | default decoder found a duplicate object key | emit each key once |
| `cycle` | repeated object identity formed an unsupported cycle | replace back-references with IDs or an acyclic representation |
| `discriminator_missing` | tagged input lacks its required tag | supply the common external discriminator key |
| `discriminator_unknown` | tag has the right type but no branch | use one value in `expected_tags` |

`missing` and `unexpected` belong to `from_mapping` and `from_json`. Those
external boundaries aggregate independent field failures. Normal Spec
construction retains its real keyword-only Python signature, so omitted
required arguments, unknown keywords, and positional misuse raise native
`TypeError`, not `ValidationError`.

Parser line, column, and character position appear in `context` when available;
they do not become Talea field-location segments.

Representation dump failures belong to `SerializationError`, not the input
`ErrorCode` vocabulary. A raised dumper and an invalid declared-output result
have distinct safe reasons at the current canonical output location. Ordinary
failures preserve a useful cause; Sensitive paths suppress callback and
validation causes so secret-bearing messages cannot escape. No user-defined
representation error-code namespace is accepted.

## Nested and constraint failures

Locations compose through fields and containers. Invalid input at
`members[2].email` projects as `['members', 2, 'email']`; no renderer has to
recover structure from a string. Nested mutable Specs are revalidated at each
required trust boundary, and current-state errors retain the complete outer
path.

Constraint details retain both the expected declaration-like contract and the
single failed fact. For `Ge(18)`, applications receive code
`greater_than_or_equal` and context `{"limit": 18}`. This keeps user interfaces
and API adapters from parsing `"Expected value >= 18"`.

## Union alternatives

A union failure is one outer detail with code `union`. `branches` contains
attempted alternatives and their local details:

```python
from uuid import UUID

from talea import Spec


class Choice(Spec):
    value: int | UUID
```

For `Choice(value="hello")`, the outer expected contract is `int | UUID`; two
compact type branches explain why neither alternative matched. If an alternative
has the correct outer shape but fails deeper, its branch retains that deeper
location. Talea does not flatten every branch into unrelated top-level errors or
dump failed object graphs. Branch diagnostics are collected only after the
union cannot succeed. A successful first or later branch does no rendering or
structured projection work.

## Transforms and checks

Custom rejections use the same `ValidationError` interface. Applications do not
need separate handling for structural, constraint, and custom failures.
`CustomValidationError` remains a compatibility subtype in
`talea.validation`; catching the root `ValidationError` catches it.

- a transform `ValueError` has code and stage `transform`, the field location,
  callback name, and the original pre-structural input;
- a field check has code and stage `field_check` and cannot be confused with a
  structural type failure;
- a multi-field check has code and stage `spec_check`, a root primary location,
  and `locations` for each declared target.

Callback exception text is not the machine-readable message contract. The
original `ValueError` is retained as `exc.__cause__` for deliberate debugging;
normal rendering does not incorporate its arbitrary message text.
For a `Sensitive()` target, Talea drops the cause because the callback may have
embedded the secret in its message or attributes.

## Default factories

If a default factory itself raises an `Exception`, Talea raises
`ValidationError` with code `factory`, the field location, and the original
exception as `__cause__`. If the factory returns normally but its value violates
the field schema, the actual structural or constraint code is reported instead.
This distinguishes producer failure from invalid producer output without
inventing a second validation path.

## Safe input representation

Error creation treats input as hostile. Strings, bytes, containers, and custom
objects are bounded; recursion cannot expand indefinitely; control characters
are escaped in human labels; and a failing or pathological `repr` cannot replace
the validation exception. Repeated `str(error)` and `errors()` calls use the
captured failure detail and remain stable.

Simple JSON scalars remain scalars in `errors()`. Other inputs become bounded
text rather than recursive object projections. For ordinary fields the exact
rejected Python object remains available through `exc.value` for deliberate
local debugging, but it is not inserted raw into the framework projection.

Fields and Contracts declared with `Sensitive()` use a stricter policy:
rendering and projection contain `<redacted>`, `exc.value` is redacted, the raw
object is not retained, value-derived location members are redacted, and
callback causes are dropped. See [Metadata and sensitive
fields](metadata-security.md).

## Aggregation and truncation

Trusted `Spec(...)` construction remains fail-fast and pays no aggregation or
resource-policy cost. Mapping and JSON boundaries collect independent failures
in canonical declaration order, followed by unexpected keys in Mapping order.
The selected `ResourcePolicy.max_errors` bounds this work. Once the budget is
reached, validation stops and the resulting exception has
`error.truncated is True`; `errors()` contains the deterministic collected
prefix. The human header includes `[truncated]`.

`truncated` means that budget enforcement terminated aggregation. It does not
claim a count of omitted failures, because Talea deliberately does not continue
traversal to discover that count. Nested aggregation and union projection
preserve the signal.

Union alternatives are a different dimension: branch failures are diagnostic
evidence required to explain why the one union path failed, so they are
collected on that failure path only. The shared node budget counts branches
actually attempted; tagged unions dispatch only to the selected branch.

## Framework response example

No framework adapter is required to produce an API-ready body:

```python
import json


def create_user(data: dict[str, object]) -> str:
    try:
        User(name=data["name"], age=data["age"])  # type: ignore[arg-type]
    except ValidationError as exc:
        return json.dumps({"errors": exc.errors()}, ensure_ascii=False)
    return json.dumps({"created": True})
```

Framework integrations can wrap the same list, map codes to localized wording,
or attach request identifiers. They should not depend on traceback text,
generated source, compiler globals, or exception message parsing.

## Performance model

Successful validators and required-only Spec constructors allocate no error
detail, safe representation, branch list, rendering state, or projection
dictionary. Error detail and safe input snapshots are created only after a
check fails. `str(error)` and `errors()` are separate measured operations, so
applications that only catch and route a failure do not pay rendering or
framework-projection cost.
