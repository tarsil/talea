# Metadata and sensitive fields

Talea has one declaration vocabulary for documentation, boundary
classification, and sensitive-data policy. Metadata is immutable, normalized
when a declaration is resolved, and retained beside structural validation
truth. Introspection, validation failures, serialization failures, and future
schema projection consume that same record; none re-read annotations.

## Field metadata

Use Talea markers as top-level `Annotated` metadata:

```python
from typing import Annotated

from talea import (
    Deprecated,
    Description,
    Examples,
    ReadOnly,
    Sensitive,
    Spec,
    Title,
    WriteOnly,
)


class Account(Spec):
    display_name: Annotated[
        str,
        Title("Display name"),
        Description("Customer-visible account name."),
        Examples("Ada", "Grace"),
    ]
    legacy_id: Annotated[int, Deprecated()]
    password: Annotated[str, Sensitive(), WriteOnly()]
    created_at: Annotated[str, ReadOnly()]
```

The markers mean:

| Marker | Canonical meaning | Runtime effect |
| --- | --- | --- |
| `Title(text)` | Human-facing label | None |
| `Description(text)` | Documentation description | None |
| `Examples(*values)` | Immutable documentation examples | None |
| `Deprecated()` | Deprecated classification | No warning |
| `ReadOnly()` | External-boundary classification | Stored only |
| `WriteOnly()` | External-boundary classification | Stored only |
| `Sensitive()` | Error/log safety classification | Redacts failures and `repr` |

Title, description, examples, and deprecation do not participate in
validation. Metadata-free constructors and validators have no metadata loop,
registry lookup, or runtime branch.

### Examples

Examples accept JSON-compatible scalars, finite floats, sequences, and
string-keyed mappings. Talea snapshots lists and mappings into recursively
immutable canonical values. It does not run transforms, checks, factories, or
validation merely to approve documentation examples. A future schema consumer
can project these retained values without executing application code.

### Duplicate and unknown metadata

One declaration may contain at most one marker of each Talea metadata type.
Duplicates are rejected instead of depending on incidental `Annotated` order.
An inherited value remains effective when an override does not mention that
metadata type; a local marker replaces the same inherited type.

Unknown third-party `Annotated` values remain ignored. Talea does not retain
arbitrary objects in canonical declarations or generated runtime artifacts.

## Spec metadata

Spec-level title, description, examples, and deprecation use one `metadata`
class keyword:

```python
class Customer(
    Spec,
    metadata=(
        Title("Customer"),
        Description("A customer accepted by the account API."),
        Examples({"name": "Ada"}),
    ),
):
    name: str
```

A structured `Description` wins over the class docstring. Without one, a
non-empty class docstring is captured once as the canonical description.
Talea does not duplicate the Python class name into an explicit title; callers
can use the class identity when `title` is `None`.

`ReadOnly`, `WriteOnly`, and `Sensitive` apply to fields and arbitrary
Contracts, not an entire Spec declaration. Spec-level use is rejected because
field paths own these policies.

Dynamic Specs use the same marker sequence:

```python
from talea import create_spec


GeneratedCustomer = create_spec(
    "GeneratedCustomer",
    {"name": Annotated[str, Description("Display name.")]},
    doc="Generated customer contract.",
    metadata=(Title("Generated customer"),),
)
```

There is no dynamic-only metadata representation.

## Sensitive failure policy

`Sensitive()` protects Talea-controlled public failure surfaces. For a
sensitive structural, constraint, transform, check, factory, Mapping, JSON, or
Contract failure:

- `str(exc)` renders `<redacted>`;
- `exc.errors()` projects `"input": "<redacted>"`;
- `repr(exc)` does not contain the rejected value;
- `exc.value` is `<redacted>`, not the raw object;
- `exc.received_type` still reports the original concrete type;
- Talea does not retain the raw rejected object in the exception;
- a callback or parser cause is dropped instead of attached as `__cause__`.

```python
from talea import ValidationError


class Login(Spec):
    password: Annotated[str, Sensitive()]


try:
    Login(password=123)  # type: ignore[arg-type]
except ValidationError as exc:
    assert exc.value == "<redacted>"
    assert exc.errors()[0]["input"] == "<redacted>"
```

This deliberately refines the ordinary Error Experience API. A non-sensitive
failure still retains the exact rejected object through `exc.value` and keeps
documented callback causes for debugging. Sensitive failures have no implicit
unsafe/debug escape hatch: submit the relevant non-secret state separately if
an incident needs more evidence.

### Nested paths and containers

Sensitivity follows canonical declaration structure through nested Specs,
lists, mappings, sets, unions, recursive Specs, TypedDict fields, generic
specializations, and Contract input. Static field names and list indexes remain
useful locations. Value-derived mapping keys and set members are themselves
redacted when they are beneath a sensitive declaration.

An enclosing sensitive field protects a nested failure even when the nested
field is not independently marked. A nested sensitive declaration remains
protected when an outer declaration adds a prefix. Talea never infers secrecy
from names such as `password`, `token`, or `secret`.

Aliases change the external location name, not the security policy:

```python
from talea import Alias


class TokenRequest(Spec):
    token: Annotated[str, Alias("access-token"), Sensitive()]
```

`from_mapping` and `from_json` report `access-token` while redacting its value.

### Transforms, checks, and causes

Talea-controlled rendering never includes callback exception text. For an
ordinary field the cause remains attached; for a sensitive field Talea drops
it because a callback can embed the secret in its message or attributes. A
whole-Spec check is sensitive when any declared target is sensitive.

Talea cannot stop application callbacks from logging, transmitting, or
otherwise exposing their arguments before raising. Sensitive metadata governs
Talea's failure objects; callback code remains trusted application code.

## Serialization

Sensitive and write-only are deliberately different:

- `Sensitive` protects errors and representation used for diagnostics.
- `WriteOnly` classifies external output policy for future adapters.

Normal `to_dict()` and `to_json()` include sensitive and write-only fields.
Campaign 13 does not silently change the round-trip contract:

```python
credentials = Login(password="correct horse")
assert credentials.to_dict() == {"password": "correct horse"}
assert "correct horse" not in repr(credentials)
```

If a serialization hook under a sensitive field fails, its
`SerializationError` keeps the field location but drops the callback cause.
Likewise, a JSON codec failure for a value graph containing sensitive metadata
does not retain the codec exception. Successful custom hooks and codecs still
receive the actual value because serialization was explicitly requested.

Read-only and write-only enforcement is deferred to the future presence and
derived-contract owner. JSON Schema words alone are not sufficient reason to
reject Talea Mapping input or omit normal output.

## Inheritance and explicit opt-out

Metadata follows field override ownership:

```python
class BaseCredential(Spec):
    value: Annotated[int | str, Sensitive(), Description("Credential value.")]


class NumericCredential(BaseCredential):
    value: int
```

`NumericCredential.value` remains sensitive and keeps its description. This
prevents an ordinary narrowing override from silently widening information
exposure. `Sensitive(False)` is the explicit field-level opt-out:

```python
class PublicIdentifier(BaseCredential):
    value: Annotated[int, Sensitive(False)]
```

`Deprecated(False)`, `ReadOnly(False)`, and `WriteOnly(False)` provide the same
explicit override form. Removing sensitivity from an alias's own identity is
not a use-site operation; declare a different alias if its security identity is
different.

## Type aliases and TypedDict

Metadata inside a PEP 695 alias belongs to the named alias identity. Metadata
outside the alias belongs to the field or Contract use site:

```python
type SecretId = Annotated[int, Description("Alias identity."), Sensitive()]

request_id = Contract(Annotated[SecretId, Title("Request identifier")])
```

The Contract's title is the use-site title; the alias description remains its
fallback identity documentation. Alias sensitivity cannot be downgraded by a
use-site `Sensitive(False)` marker.

TypedDict child annotations retain their own metadata:

```python
from typing import TypedDict


class Credentials(TypedDict):
    password: Annotated[str, Sensitive()]


contract = Contract(Credentials)
```

Strict validation, external Python conversion, and JSON input all redact a
failure at `password`. Python's `typing.ReadOnly` qualifier remains separate
TypedDict structural truth; Talea's `ReadOnly()` marker is boundary metadata.

## Introspection

`FieldInfo`, `SpecInfo`, and `ContractInfo` expose normalized title,
description, examples, deprecation, read/write, and sensitivity values where
they apply. The descriptions are frozen projections. Mutating returned example
containers or introspection dataclasses cannot change canonical declarations.

Open generic introspection exposes metadata before the field schema can be
resolved. Concrete specialization and recursive finalization retain the same
metadata without copying it per instance or per recursive expansion.

## Future schema projection

JSON Schema and OpenAPI are not implemented by this campaign. Their future
owner can consume canonical field, Spec, Contract, TypedDict, and alias
metadata directly, alongside structural Schema constraints and identities. It
must not re-read annotations or invent a second metadata record.

## Security guidance and limitations

- Mark the canonical declaration; do not rely on field-name heuristics.
- Treat successful serialization as intentional secret access.
- Do not put secrets in titles, descriptions, examples, aliases, constraint
  boundaries, or callback names. Those values are declaration truth, not
  rejected input.
- Application callbacks can leak their own arguments through logs or external
  effects; Talea only controls its own errors.
- Sensitive error objects discard raw input and causes. Capture separate safe
  diagnostic facts before crossing the validation boundary when required.
- Read-only and write-only are classification only until a dedicated derived
  contract defines presence and output enforcement.

Metadata is class/Contract-owned cold state. Instances retain only field
values, and metadata-free successful validation and serialization do not pay a
Campaign 13 runtime tax.
