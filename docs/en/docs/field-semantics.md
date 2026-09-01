# Field semantics

Every `Spec` field is keyword-only. An annotation alone declares a required
field, including when the annotation accepts `None`:

```python
from talea import Spec, field


class Required(Spec):
    value: int | None


class Defaulted(Spec):
    value: int | None = None


class Basket(Spec):
    items: list[int] = field(default_factory=list)
```

`Required()` fails because accepting `None` does not imply omission.
`Defaulted()` receives `None`. Each `Basket()` receives a new list.

## Static defaults

Talea validates static defaults when the class is declared. This is the
earliest deterministic point at which both the resolved schema and the value
exist, and it prevents an invalid declaration from manufacturing trusted
instances later. Static defaults are reused without validation when omitted;
explicit replacements are validated normally.

Lists, sets, dictionaries, and immutable containers containing them are
rejected as static defaults. Talea does not copy defaults implicitly. Use
`field(default_factory=...)` for mutable values so ownership and construction
cost are explicit.

## Default factories

`field` is a declaration function, not an instance wrapper. Its
`default_factory` is called once when the corresponding argument is omitted,
is not called for an explicit argument, and is never called merely to inspect a
class declaration. The returned value passes through the same strict validation
operations as an explicit value. One canonical validation emitter generates
both standalone validators and the operations inlined into Spec constructors;
construction does not call a Python validator function per field. A factory
exception becomes a field-located `ValidationError` with code `factory` and the
original exception retained as its cause. A returned value that fails validation
keeps the actual structural or constraint code.

When a field declares an inbound `transform`, factory output runs that
transformation before structural validation. Static defaults do not run
transforms; they must already be valid developer-provided Python values. Both
static defaults and factory results must satisfy declared field `check`
callbacks. See [Custom validation](custom-validation.md).

## Immutability and validated trust

Spec field bindings are immutable after construction. Assignment and deletion
raise `AttributeError`, including assignment to unknown attributes.
Initialization uses Talea's generated constructor to validate all values before
writing slots.

Shallow immutability is not misrepresented as permanent trust. A declaration
whose schema contains a list, set, or dictionary is validated at construction
but is canonically marked as requiring current-state revalidation at a new
boundary, including when that mutable container is nested in a tuple,
frozenset, or union. A declaration containing only transitively immutable value
schemas is permanently trusted. Referenced Spec declarations propagate their
own classification: an immutable nested Spec preserves permanent trust, while
a Spec containing a mutable value makes the containing declaration ineligible.
At each new nested validation boundary, permanently trusted references require
only nominal compatibility. Non-permanently-trusted references validate their
current canonical field state before they are accepted. This makes it
impossible for an already-invalid mutable Spec to cross a new validation
boundary solely because its Python type still matches.

Three policies were considered:

- Immutable by default: one construction validation boundary, no assignment
  compiler or assignment-time cost, and a direct trust guarantee.
- Mutable with compiled assignment validation: coherent, but introduces a
  second validation path and permanent assignment overhead for every Spec.
- Configurable mutability: possible, but adds policy and generated variants
  before a concrete consumer requires them.

Immutable-by-default bindings plus canonical permanent-trust classification win
because they preserve direct field-read performance and ordinary Python
container semantics without making a false deep-immutability claim. Talea does
not provide mutable field bindings or assignment validation.

## Canonical ownership

The immutable ordered `SpecSchema` owns each field's name, resolved schema,
required, static-default, or factory state, canonical declaration metadata,
effective custom-hook order, and the derived permanent-trust classification.
Generated constructors and standards projections consume that retained truth.
Instances contain only their field values and never reconstruct lifecycle or
metadata semantics from annotations. See [Metadata and sensitive
fields](metadata-security.md).

## Current and legacy external names

`Alias` owns the external field-name contract. Its `name` is the current
external name used for input and output. Its keyword-only `legacy` tuple lists
finite historical names accepted only by Mapping and JSON input:

```python
from typing import Annotated

from talea import Alias, Spec


class UserLookup(Spec):
    user_id: Annotated[int, Alias("userId", legacy=("id", "user_id"))]
```

This declaration can receive API v1 `{"id": 7}`, API v2
`{"user_id": 7}`, or current `{"userId": 7}` payloads. The Python
constructor and attribute remain `user_id`. Because the current external name
differs from the Python name, `user_id` is accepted externally here only
because it is explicitly listed in `legacy`; adding legacy vocabulary does not
silently broaden every alias to accept its Python spelling.

One payload may supply exactly one accepted name for a field. Supplying
`userId` and `id`, or `id` and `user_id`, raises `ValidationError` with code
`alias_conflict` at `userId`. Equal values still conflict: Talea does not compare
values, infer precedence, or treat tuple order as newest-to-oldest priority.
The error may expose `conflicting_names`, but it retains no conflicting values.
The same value-free rule applies to `Sensitive` fields.

Validation is identical whichever accepted name supplied the value. A bad
value under `id` receives the field's ordinary structural, constraint, or hook
code at the current external location `userId`; there is no legacy-specific
validator. Missing and unrelated keys remain `missing` and `unexpected`.
Duplicate textual JSON keys remain the decoder-owned `json_duplicate` error,
which is distinct from two different accepted names identifying one field.

Output is canonical and one-way:

```python
lookup = UserLookup(user_id=7)
assert lookup.to_dict() == {"userId": 7}
assert lookup.to_json() == '{"userId":7}'
```

Legacy names are never emitted. JSON Schema and OpenAPI likewise continue to
describe the current external property name; they do not project historical
input vocabulary. `inspect_spec()` exposes each field's `external_name`,
immutable `legacy_names`, and complete ordered `accepted_input_names`. The
order is retained for deterministic inspection and future projection only.

Talea owns accepted external-name truth, deterministic ambiguity rejection,
validation, errors, and introspection. The application owns the decision to
make a name legacy, warnings and telemetry, retirement timing, API-version
policy, semantic value migration, and source-version negotiation. Talea does
not infer migration chronology from tuple position and does not provide a
migration registry or version manager.

Current names and legacy names must be unique within one field. Across an
effective ordinary Spec declaration, current/current, current/legacy, and
legacy/legacy collisions fail during declaration so an input object cannot
identify two fields with one name. This validation naturally includes ordinary
inherited effective fields.

Legacy-name consumption currently belongs to ordinary Spec Mapping/JSON input.
Stdlib dataclass construction and tagged-union discriminator selection do not
yet consume this vocabulary. Do not rely on a legacy tuple at those boundaries;
their existing current-name behavior is unchanged.

## Practical account fields

```python
from typing import Annotated

from talea import Alias, MinLength, Sensitive


class AccountCreate(Spec):
    display_name: Annotated[str, Alias("displayName"), MinLength(1)]
    recovery_email: str | None = None
    token: Annotated[str, Sensitive()]
    labels: list[str] = field(default_factory=list)
```

`display_name` is required, externally named `displayName`, and cannot be empty.
`recovery_email` is optional in ordinary construction because it has a default,
not merely because it accepts `None`. `token` is required and redacted from
Talea-owned repr/errors. `labels` gets a per-instance mutable list.

At a JSON boundary, aliases are accepted and nested locations use external
names. `to_dict()` and `to_json()` use aliases by default. Sensitive does not
omit token from successful output; an outward response Spec should simply not
declare request credentials.

## Factory and declaration failures

```python
def broken_factory() -> list[str]:
    raise RuntimeError("configuration unavailable")


class Batch(Spec):
    labels: list[str] = field(default_factory=broken_factory)
```

Constructing `Batch()` raises a field-located `ValidationError` with code
`factory` and retains the trusted application cause. A factory returning the
wrong type receives the normal structural code instead. Sensitive factories
and callbacks discard unsafe causes where retaining them could leak the marked
value.

An invalid static default fails while the class is declared. This is a startup
or import defect, not request validation. Fix the declaration rather than
catching it inside a handler.

## Defaults in PATCH and schema

A partial derived Spec does not materialize source defaults or call factories
for absent fields. Supplying a value equal to the source default still marks it
present. This is essential for APIs where “reset to default” differs from “make
no change.”

Static defaults project to JSON Schema only when they can be represented
without running application code and do not contain Sensitive or serializer
state. Factories are never invoked for schema generation because they do not
declare one stable value.

## Error, security, and performance guidance

Field validation follows declaration order and successful construction does not
allocate rich error detail. Defaults reuse already-validated immutable values;
factories and explicit inputs pay their direct transform/validation/check work.
Mutable current state pays revalidation when it later crosses a boundary.

Do not put database lookups, network I/O, authorization, or changing business
policy in default factories or field checks. They are trusted synchronous
callbacks on object construction and are outside ResourcePolicy. Keep factories
deterministic and local; keep external effects in the application operation.
