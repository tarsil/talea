# Strictness and external boundaries

Strict Python validation answers one question: does this object already satisfy
the declared Python contract? It does not parse or guess.

| Declaration | Accepted on strict Python path | Common rejection |
| --- | --- | --- |
| `int` | exact `int` | `True`, `1.0`, `"1"` |
| `UUID` | UUID object | UUID string |
| `date` | exact date | datetime |
| `list[int]` | exact list of exact ints | tuple, booleans, strings |
| Enum class | member of that exact class | raw string or integer |

External operations answer a different question: can a representation be
converted according to a documented boundary contract and then validated?
`from_mapping()` constructs nested Specs from mappings but otherwise keeps
Python values strict. `from_json()` additionally owns schema-aware JSON
representations such as UUID strings, ISO temporal strings, Decimal strings,
and base64 bytes.

Use an explicit `@transform` when an application field has a deliberate
conversion policy. Transforms are field-local trusted callbacks, run before
structural validation, and are not visible in JSON Schema unless the input
domain remains statically knowable.

This split prevents strict construction from silently changing as JSON or API
requirements evolve. See [Input boundaries](../input-boundaries.md) for exact
representations and [Transforms and checks](../custom-validation.md) for custom
policies.

## One contract across three inputs

```python
from uuid import UUID

from talea import Spec


class Session(Spec):
    session_id: UUID
    attempts: int
```

Trusted Python construction requires the declared Python values:

```python
session = Session(
    session_id=UUID("12345678-1234-5678-1234-567812345678"),
    attempts=1,
)
```

External Mapping input may construct nested Specs from mappings, but it does
not turn UUID or integer strings into Python objects. This keeps the Python
representation contract strict even when the container itself came from an
integration.

JSON input accepts UUID text because that is Talea's declared JSON
representation and accepts a JSON number for the integer:

```python
session = Session.from_json(
    '{"session_id":"12345678-1234-5678-1234-567812345678","attempts":1}'
)
```

The JSON string is not “coerced to make validation pass.” It crosses a specific
representation boundary whose inverse is used by `to_json()` and described by
JSON Schema.

## Explicit conversion policy

Sometimes a partner's Python Mapping genuinely contains strings. A transform
can make that exception local:

```python
from talea import transform


class PartnerSession(Spec):
    session_id: UUID

    @transform("session_id")
    def parse_session_id(value: str | UUID) -> UUID:
        return value if isinstance(value, UUID) else UUID(value)
```

The callback is trusted application code. It owns rejected exceptions and may
broaden input beyond what Talea can express in input JSON Schema. Prefer a
separate partner-boundary type when this policy should not leak into ordinary
domain construction.

## Failures and debugging

A wrong strict value raises `ValidationError` at its canonical Python field. A
wrong external representation reports the external alias/path. Inspect the
stable `code`, `location`, `expected`, and `received` fields from `errors()`.
Do not retry the same operation through another boundary merely to make the
value pass; decide which representation the caller actually owns.

## Security and performance consequences

Explicit external paths are where Talea applies depth, node, and error budgets;
JSON additionally receives a transport-size limit. Trusted construction and
strict `Contract.validate()` avoid those counters because their caller already
owns Python values. This both exposes the threat boundary and keeps external
accounting off the accepted trusted hot path.

Strictness is not universally desirable. If an application product promises
broad convenient coercion from loosely typed values, Pydantic or deliberate
parsing code may fit better. Talea's choice is useful when representation
changes should be visible, narrow, and reviewable.
