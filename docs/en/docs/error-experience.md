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

## Codes

The public vocabulary currently contains:

- structure: `type`, `literal`, `union`, `missing`, `unexpected`;
- constraints: `greater_than`, `greater_than_or_equal`, `less_than`,
  `less_than_or_equal`, `multiple_of`, `min_length`, `max_length`, `pattern`;
- custom validation: `transform`, `field_check`, `spec_check`;
- default production: `factory`.

`missing` and `unexpected` are reserved for a data-input boundary. Normal Spec
construction retains its real keyword-only Python signature, so omitted
required arguments, unknown keywords, and positional misuse currently raise
native `TypeError`, not `ValidationError`. Talea will use the structured codes
when a future mapping or serialized-data operation owns those semantics.

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
Campaign 7's `CustomValidationError` remains a compatibility subtype in
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
text rather than recursive object projections. The exact rejected Python object
remains available through `exc.value` for deliberate local debugging, but it is
not inserted raw into the framework projection.

Talea does not yet have field sensitivity metadata. Current safe representation
limits size and execution hazards; it is not a secret-redaction policy. Do not
log validation errors for secret-bearing fields without an application-level
redaction policy. The one centralized input-snapshot boundary is the future
integration point for field-aware redaction.

## Fail-fast policy

Spec construction is deliberately fail-fast across independent fields and
within a structural path. For invalid `age`, `email`, and `active` values, the
first field in canonical declaration order fails and later transforms or checks
do not run. This preserves atomic construction, the specialized constructor,
and Campaign 7-class successful performance without an error list, per-field
exception collection, or final aggregation branch on every success.

Union alternatives are a different dimension: branch failures are diagnostic
evidence required to explain why the one union path failed, so they are
collected on that failure path only. Talea does not currently expose a synthetic
aggregation constructor. A future mapping/JSON input operation can compile
collection together with structured `missing` and `unexpected` fields without
changing normal Python construction.

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
