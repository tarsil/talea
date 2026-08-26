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
| PEP 695 `type` aliases and `NewType` | Underlying supported contract | Named identity is retained without runtime alias dispatch |
| `Annotated[A | B, Discriminator(name)]` | Required single-Literal Spec or TypedDict branches | Direct tag selection; see [Tagged unions](tagged-unions.md) |

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
mutual and concrete generic recursion. See [Recursive aliases and TypedDict
graphs](recursive-named-graphs.md).
