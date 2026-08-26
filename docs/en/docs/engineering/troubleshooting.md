# Troubleshooting

Start with the boundary operation and the first structured error, not the
rendered exception prose. `ValidationError.errors()` exposes stable `code` and
`location` values; `ResourceLimitError` exposes `code`, `limit`, and `observed`.

## A UUID string fails in Python construction

Broken:

```python
class Payment(Spec):
    payment_id: UUID


Payment(payment_id="12345678-1234-5678-1234-567812345678")
```

The constructor is a strict Python path. A string is not a UUID object.

Correct when application code already owns the Python value:

```python
Payment(payment_id=UUID("12345678-1234-5678-1234-567812345678"))
```

Correct when JSON owns the representation:

```python
Payment.from_json(
    '{"payment_id":"12345678-1234-5678-1234-567812345678"}'
)
```

Use a field transform only when an external Python Mapping is intentionally
allowed to carry UUID text. Do not add broad conversion merely to hide a wrong
boundary call.

## `True` fails an integer field

Broken:

```python
class Revision(Spec):
    value: int


Revision(value=True)
```

Although `bool` subclasses `int` in Python, Talea primitive contracts use exact
built-in types. This prevents flags from silently entering counters and IDs.
Pass an integer or declare `bool | int` if both are genuinely valid protocol
values.

## A JSON document reports unexpected fields

Broken:

```python
class User(Spec):
    display_name: Annotated[str, Alias("displayName")]


User.from_json('{"display_name":"Ada"}')
```

Aliases replace the external field name; they do not add a second accepted
name. Correct:

```python
User.from_json('{"displayName":"Ada"}')
```

Use canonical Python names in direct construction and `copy.replace`, and the
external alias in Mapping/JSON input and aliased output. Error locations at
external boundaries use the external path.

## A transform prevents input schema generation

Broken expectation:

```python
class Amount(Spec):
    value: Decimal

    @transform("value")
    def parse_value(value: object) -> Decimal:
        return Decimal(str(value))


Amount.json_schema(mode="input")
```

An arbitrary Python callback has no statically knowable accepted domain. Talea
raises `SchemaProjectionError` rather than claim that one input shape is true.
Remove the transform and use the standard JSON Decimal representation, narrow
the callback out of the schema-owning boundary, or let the adapter document the
custom domain explicitly. Output projection remains possible when only input
was changed.

## A serializer prevents output schema generation

The inverse problem occurs when `@serialize` can return any Python value.
`json_schema(mode="output")` cannot infer its range. Keep the normal
representation, move the custom projection into an explicitly declared output
Spec, or let framework-owned documentation describe the custom output. Input
schema remains available when only output changed.

## A partial field raises `AttributeError`

Broken assumption:

```python
UserPatch = derive_spec(User, partial=True)
patch = UserPatch()
print(patch.display_name)
```

Partial omission is real absence. Talea does not fill source defaults or turn
`T` into `T | None`. Inspect presence first:

```python
if "display_name" in patch.present_fields:
    use(patch.display_name)
```

`to_dict()` and `to_json()` emit only present fields. A present `None` is valid
only if the source field already admits `None`. See [PATCH and
presence](../presence-derived-contracts.md).

## A patch validates but fails when applied

```python
patch = IntervalPatch(start=10)  # incomplete state is allowed
apply_patch(Interval(start=1, end=2), patch)  # spec_check failure
```

Multi-field whole-Spec checks do not run against incomplete partial state.
`apply_patch()` creates the complete candidate and reruns the source invariant.
Handle the resulting `ValidationError` as a failed update; do not bypass it by
merging serialized dictionaries.

## `ResourceLimitError` rejects a legitimate payload

Inspect the dimension before changing policy:

```python
try:
    Batch.from_json(body, policy=batch_policy)
except ResourceLimitError as error:
    log(error.code, error.limit, error.observed)
```

`input_size` is encoded transport bytes, `depth` is structural nesting, and
`nodes` is actual compiled schema visits. Increase the one justified dimension
for that operation after measuring the maximum legitimate shape. An error count
budget instead returns `ValidationError(truncated=True)`. Avoid disabling all
limits or weakening unrelated endpoints globally.

## A recursive declaration works but a runtime value cycles

A recursive type graph describes a finite tree of arbitrary depth. A list that
contains itself is a cyclic object identity graph and cannot be represented by
ordinary JSON. Talea reports `cycle` during governed input or output.

Replace object links with IDs, use a finite tree projection, or choose a format
with explicit reference semantics. Raising `max_depth` does not make a cycle a
valid tree.

## An open generic cannot execute

Broken:

```python
class Envelope[T](Spec):
    payload: T


Contract(Envelope)
```

`T` has no runtime contract until specialization. Correct:

```python
user_envelopes = Contract[Envelope[User]](Envelope[User])
```

Concrete specializations are cached by identity and expose concrete schema and
typing truth. The open declaration remains useful for inheritance and
introspection but cannot validate an unknown type variable.

## A discriminator is missing or unknown

```python
events.from_json('{"eventId":"..."}')
# discriminator_missing at ["type"]

events.from_json('{"type":"payment.refunded"}')
# discriminator_unknown at ["type"]
```

Every branch must declare the same required single-value Literal field and
external alias. Do not add both `kind` and `type`; use `Alias("type")` on the
canonical `kind` field. Sensitive reachability can conservatively redact the
discriminator and expected tags.

## A custom Mapping or codec exception propagates

Custom Mapping methods and JSON codecs are trusted application code. Talea
normalizes validation and decoding failures it owns, but it does not sandbox
arbitrary Python or guarantee that every user exception becomes a
`ValidationError`. Validate integration callbacks, keep them small, and apply
process-level deadlines or isolation where required.

## `create_spec()` returns `type[Spec]` to the type checker

Runtime field mappings cannot create a static constructor signature in Python
3.14. The generated class has normal Talea runtime behavior, but code requiring
precise static fields should prefer class syntax. Use `create_spec()` for
trusted configuration and framework declarations whose dynamic nature is
genuine, not as a replacement for ordinary source code.

If none of these entries matches, inspect the exact operation, exception type,
`errors()` output, aliases, and input/output mode, then reduce the contract to
the smallest failing declaration. The [error reference](../error-experience.md)
lists every stable code, and the [public API reference](../reference/api.md)
links each operation to its detailed semantics.
