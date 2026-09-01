# Presence and derived contracts

Talea can project a concrete `Spec` into an independent data contract with
`derive_spec()`. The same primitive owns all-fields-omittable PATCH contracts,
field selection, explicit input/output views, field omission, and their
composition.

```python
from talea import Spec, apply_patch, derive_spec


class User(Spec):
    id: int
    name: str
    active: bool = True


UserPatch = derive_spec(User, partial=True)
patch = UserPatch(name="Grace")

assert patch.present_fields == frozenset({"name"})
assert patch.to_dict() == {"name": "Grace"}

user = User(id=1, name="Ada")
updated = apply_patch(user, patch)
assert updated.to_dict() == {"id": 1, "name": "Grace", "active": True}
```

The derived class follows the normal Spec declaration, validation, input,
serialization, copy, and introspection lifecycle. It is not a subclass of the
source and has no patch-specific interpreter.

## Explicit input and output views

`mode="input"` excludes fields whose effective canonical metadata is
`ReadOnly(True)`. `mode="output"` excludes `WriteOnly(True)` fields. Ordinary
fields remain in both directions:

```python
from typing import Annotated

from talea import ReadOnly, Sensitive, WriteOnly


class Account(Spec):
    id: Annotated[int, ReadOnly()]
    email: str
    password: Annotated[str, WriteOnly(), Sensitive()]
    created_at: Annotated[str, ReadOnly()]


AccountInput = derive_spec(Account, mode="input")
AccountOutput = derive_spec(Account, mode="output")
AccountPatch = derive_spec(Account, mode="input", partial=True)
```

`AccountInput` contains `email` and `password`; `AccountOutput` contains `id`,
`email`, and `created_at`. A field marked both read-only and write-only is
excluded from both views, which permits an explicitly internal-only field.
`ReadOnly(False)` and `WriteOnly(False)` are effective false states, so those
fields remain selectable. A source without directional metadata still produces
a distinct equivalent derived class, consistent with all other derivations.

The mode determines class shape once at derivation time. It does not install an
operation guard: an input view can serialize, and an output view can be
constructed or parsed. Both are normal Specs with ordinary slots, compiled
boundaries, copying, introspection, and schema projection. The source Spec's
constructor, Mapping/JSON input, and serialization remain unchanged.

This is contract direction, not authorization. `ReadOnly` does not mean that an
actor may not change a database value, and `WriteOnly` does not itself make a
value secret. Use `Sensitive` for Talea-owned failure and repr redaction, and
apply authentication, authorization, persistence, and logging policy in the
application.

## Absence is not `None`

Partial derivation changes key presence, not the field's value contract. For a
source field `age: int`, these inputs remain distinct:

```python
class Account(Spec):
    age: int


AccountPatch = derive_spec(Account, partial=True)

omitted = AccountPatch()
assert omitted.present_fields == frozenset()

AccountPatch(age=None)  # ValidationError: None is not an int
```

Talea never implements a partial field by changing `T` into `T | None`.
Explicit `None` succeeds only when the original schema admits it:

```python
class Profile(Spec):
    biography: str | None


ProfilePatch = derive_spec(Profile, partial=True)
patch = ProfilePatch(biography=None)

assert patch.present_fields == frozenset({"biography"})
assert patch.to_dict() == {"biography": None}
assert patch.to_dict(exclude_none=True) == {}
```

`exclude_none` remains an output filter. It does not rewrite presence truth.

## Presence inspection and omitted attributes

Partial instances store one integer bitmask keyed by canonical field order.
`present_fields` projects that internal state as a new immutable `frozenset` of
canonical Python names. The mask itself is not public introspection.

An omitted slot is left unset. Access therefore raises normal `AttributeError`:

```python
patch = AccountPatch()

hasattr(patch, "age")  # False
patch.age              # AttributeError
repr(patch)             # 'AccountPartial()'
```

`repr()` includes only present fields. Present `Sensitive` fields keep Talea's
redacted representation, and omitted sensitive fields expose no value.

Ordinary Specs gain no presence slot or construction check. Their
`present_fields` result contains every canonical field because normal Spec
instances are complete.

## Defaults and factories

Partial omission preserves absence. A source static default is not materialized
and a source factory is not called solely because a partial object is created:

```python
from talea import field


class Preferences(Spec):
    theme: str = "system"
    labels: list[str] = field(default_factory=list)


PreferencesPatch = derive_spec(Preferences, partial=True)
patch = PreferencesPatch()

assert patch.to_dict() == {}
assert not hasattr(patch, "theme")
assert not hasattr(patch, "labels")
```

An explicitly supplied value equal to the source default is still present:

```python
patch = PreferencesPatch(theme="system")
assert patch.present_fields == frozenset({"theme"})
```

Non-partial pick/omit projections retain source defaults and factories for
their retained fields. Their instances are complete projected records, not
PATCH documents.

## Pick, omit, and composition

`include` and `exclude` accept canonical Python field names and are mutually
exclusive:

```python
PublicUser = derive_spec(User, include=("id", "name"), name="PublicUser")
EditableUser = derive_spec(
    User,
    exclude=("id",),
    partial=True,
    name="EditableUser",
)
```

Source order wins regardless of selection order. Unknown names, duplicate
names, non-string members, and simultaneous include/exclude fail at derivation
time. Selection does not rename fields. Aliases remain boundary metadata and do
not become accepted selection names.

Directional mode constrains the selectable universe before ordinary selection.
`exclude` removes additional retained fields. `include` intersects with the
direction, but explicitly requesting a directionally excluded field raises
`ValueError`; this catches a mistaken contract policy instead of silently
changing the requested shape. Include and exclude remain mutually exclusive.
Neither can resurrect a read-only input field or write-only output field.

Retained fields preserve their canonical schema, constraints, alias, metadata,
Sensitive policy, static default or factory under the selected partial policy,
field validation hooks, and serialization hook. Arbitrary application methods
and properties are not copied because they may assume source fields that the
projection does not contain.

Repeated identical calls return distinct equivalent classes. Talea does not
keep a process-global derivation cache. Applications that want stable class
identity should derive once and retain the class in their own module.

## Validation hooks and invariants

For an omitted partial field, Talea runs no transform, structural validation,
field check, or serializer. A present field follows the existing lifecycle
exactly once.

Source checks with multiple field targets do not run while a partial object is
created. They were authored for complete source state and an incomplete PATCH
does not necessarily have their inputs. Non-partial projections retain a
multi-field check only when every target remains in the projection.

Applying a patch is different: `apply_patch()` combines present values with a
complete source instance through `copy.replace`. The resulting complete object
reruns source whole-Spec checks before commitment.

```python
from talea import check


class Interval(Spec):
    start: int
    end: int

    @check("start", "end")
    def ordered(start: int, end: int) -> None:
        if start > end:
            raise ValueError("start follows end")


IntervalPatch = derive_spec(Interval, partial=True)
patch = IntervalPatch(start=10)  # complete-state check does not run here

apply_patch(Interval(start=1, end=2), patch)  # ValidationError
```

## Input and output boundaries

The direct constructor, `from_mapping()`, and `from_json()` accept omitted
partial fields without `missing` failures. Present fields remain strict,
unknown fields remain unexpected, and Mapping/JSON boundaries aggregate
independent present-field and unexpected-key failures.

Aliases mark the canonical field present:

```python
from typing import Annotated
from talea import Alias


class Contact(Spec):
    display_name: Annotated[str, Alias("displayName", legacy=("name",))]


ContactPatch = derive_spec(Contact, partial=True)
patch = ContactPatch.from_mapping({"name": "Ada"})

assert patch.present_fields == frozenset({"display_name"})
assert patch.to_dict() == {"displayName": "Ada"}
assert patch.to_dict(by_alias=False) == {"display_name": "Ada"}
```

The legacy key marks `display_name`, never `name`, as present. Supplying both
`displayName` and `name` is `alias_conflict`, even when their values match.
Applying this patch uses exact derivation provenance and forwards
`display_name` through the source replacement lifecycle.

Plain `to_dict()` and `to_json()` emit only present partial fields. Existing
`include`, `exclude`, `exclude_none`, `by_alias`, and custom codec options then
apply normally. A serializer runs only when its field is present.

## Applying patches

`apply_patch(source, patch)` is intentionally narrow:

1. the second value must be a partial class produced by `derive_spec()`;
2. its canonical source must be the exact concrete type of the source instance;
3. an output-derived partial is rejected because it may contain read-only
   source fields;
4. only present canonical fields become replacement changes;
5. Talea delegates to `copy.replace()` and the source's compiled replacer.

The operation does not serialize, merge dictionaries, call `from_mapping()`,
or replay boundary conversion. Changed fields run normal transforms,
validation, and field checks. Unchanged mutable fields receive current-state
validation. Whole-Spec checks run against the complete candidate, and no object
is returned unless every check succeeds.

A patch derived from `User` cannot apply to an unrelated `Account` merely
because their names overlap. Concrete generic identity is also exact: a patch
for `Page[User]` is not compatible with `Page[Account]`.

An input-derived partial is patch-compatible with its exact source because its
shape cannot contain effective read-only fields. A legacy partial without a
mode retains the existing metadata-only source semantics. An output-derived
partial is never patch-compatible, even if a particular instance happens not
to contain a read-only value; provenance, not field-name coincidence, owns the
decision.

An empty patch still goes through the source replacement owner. This preserves
mutable current-state and whole-Spec invariant guarantees rather than treating
an empty change set as unconditional trust.

## Inheritance, generics, recursion, and tagged unions

Derivation consumes the source's effective canonical fields and normalized
metadata. Inherited order, metadata overrides, and explicit false states are
already settled before projection; derivation does not repeat MRO or
`Annotated` interpretation.

Concrete generic specializations are supported:

```python
class Page[T](Spec):
    items: list[T]
    cursor: str | None


UserPagePatch = derive_spec(Page[User], partial=True)
```

Open generic origins such as `derive_spec(Page, partial=True)` are rejected.
Dynamic derivation does not introduce runtime `TypeVar` dispatch.

Recursive fields and tagged-union fields retain their exact canonical source
schemas. A present recursive field performs its normal graph validation; an
absent field triggers no traversal. A present tagged union performs normal
discriminator dispatch; an absent field performs no discriminator lookup.
Derivation does not recursively derive nested contracts.
Thus deriving `Envelope` in output mode does not silently replace an existing
`User` field with `derive_spec(User, mode="output")`. Nested Specs, tagged
unions, and dataclasses retain their declared contract. Derive and annotate a
nested directional view explicitly when that is the intended boundary.

## Copy, pickle, and class identity

`copy.copy()`, `copy.deepcopy()`, and `copy.replace()` preserve present and
absent slots. Deep copy uses Python's memo protocol and does not materialize
absent values.

Derived classes follow normal dynamic-class pickle rules. If a derived class is
assigned to the matching module and qualified name, its partial instances
round-trip with their presence state. Local or unbound dynamic classes retain
normal Python pickling limitations. `module` and `qualname` can be supplied to
`derive_spec()` when an application owns importable class registration; Talea
does not mutate modules on the application's behalf.

## Introspection and schema projection

`inspect_spec()` exposes derived truth without compiler state:

```python
from talea.introspection import inspect_spec

info = inspect_spec(UserPatch)

assert info.presence_aware is True
assert all(field.omittable for field in info.fields)
assert info.derivation.source is User
assert info.derivation.retained_fields == ("id", "name", "active")
```

Each `FieldInfo` reports `required` and `omittable`. `SpecInfo.derivation`
reports the source, retained and omitted fields, selection policy, partial
policy, directional mode, and explicit name. JSON Schema projection therefore
produces required-key truth directly from the canonical declaration; it does
not need to inspect runtime instances or infer semantics from class names.

Derivation mode and schema projection mode are separate dimensions. Derivation
mode decides which fields exist; `json_schema(mode=...)` and
`openapi_schema(mode=...)` project the already-derived shape. Schema mode never
restores a source field removed by derivation.

TypedDict remains its own canonical structural contract. Its `Required` and
`NotRequired` keys already distinguish presence from value nullability. Talea
does not wrap TypedDict requiredness in the Spec derivation API; schema
projection consumes both owners consistently.

## Static typing and scope

Python type checkers cannot infer a new keyword constructor from a runtime
include/exclude set. `derive_spec()` therefore honestly returns `type[Spec]`.
Runtime fields still retain their exact schemas, while static code can use
`from_mapping()` or an application-declared protocol when a named dynamic
contract crosses a typed boundary. `apply_patch()` preserves the concrete type
of its complete source argument.

There is no direct `field(omittable=True)` syntax, `contract.partial()`,
automatic enforcement on ordinary source Specs, recursive directional
rewriting, TypedDict directional derivation, field renaming, or open-generic
derivation. JSON Schema/OpenAPI projection and external-input resource limits
apply to the resulting concrete Spec through their normal owners.

## Performance model

Derivation performs declaration-time work: it projects canonical source truth
and compiles a normal specialized Spec. Partial instances add one slot holding a
Python integer mask; they do not allocate a set or dictionary for presence.
Python integers support arbitrary field counts.

Normal Specs keep their existing slots and generated hot paths. Presence checks
exist only in generated constructors, input boundaries, serializers, nested
current-state validation, and replacement functions for presence-aware derived
classes. The permanent `benchmark_presence` task covers cold derivation,
0/1/5/all-present construction and serialization, `present_fields`, patch
application, memory, weak collection, and ordinary Spec zero-tax canaries.
It also covers input/output derivation at 1/5/10/50 fields, directional
include/exclude and partial composition, equivalent manual class execution,
Mapping/JSON boundaries, Python/JSON output, and instance-size equivalence.

## Complete REST PATCH example

The executable example below derives request, response, and PATCH Specs from
read/write metadata. It combines a server-owned identifier, write-only
Sensitive token, aliases, a source default, an optional field, empty and
one-field patches, explicit `None`, a default-equal value, omitted
`AttributeError`, Python/JSON projection, `apply_patch`, a failed field
constraint, a failed complete-object invariant, `copy.replace`, and input JSON
Schema.

{!> ../../../docs_src/tutorials/patches.py !}

In an HTTP adapter, decode the patch once, use `present_fields` for auditing or
authorization if needed, load the current complete object, apply the patch, and
persist only the validated result. Talea does not authorize which actor may
change a field, resolve concurrent revisions, perform database updates, or
choose response codes. Those remain application concerns.

Avoid serializing the complete source to a dictionary and merging patch output
by hand. That route loses the exact source/patch relationship, can confuse
aliases with Python names, and risks bypassing replacement validation. The
canonical `apply_patch()` owner exists to keep presence and whole-object truth
together.
