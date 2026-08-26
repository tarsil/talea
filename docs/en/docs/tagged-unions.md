# Tagged unions

Tagged unions make a payload's own required literal field select one branch.
They are explicit: ordinary unions keep their existing validation and error
semantics.

```python
from typing import Annotated, Literal

from talea import Alias, Contract, Discriminator, Spec


class CardPayment(Spec):
    kind: Annotated[Literal["card"], Alias("type")]
    number: str


class BankTransfer(Spec):
    kind: Annotated[Literal["bank"], Alias("type")]
    iban: str


type Payment = Annotated[
    CardPayment | BankTransfer,
    Discriminator("type"),
]

payment = Contract(Payment).from_json(
    '{"type":"card","number":"4242"}'
)
```

`Discriminator(name)` may name the common Python field (`kind`) or its common
external alias (`type`). The field declaration remains the only owner of both
names. The tagged schema retains the resolved canonical name, external name,
exact tags, JSON tag representations, sensitivity, and branch identities.

## Branch contract

Every branch must be a `Spec` or every branch must be a `TypedDict`. A branch
must carry the discriminator as a required key or field whose schema is one
single-value `Literal`. Talea derives the tag from that Literal; there is no
second `Tag(...)` declaration.

Supported tags are exact `str`, `int`, and `bool` values, plus Enum members
whose JSON value is one of those types. Python tags remain type-sensitive, so
`True` and `1` select different branches. Resolution also rejects tags that
are distinct in Python but collapse to the same JSON representation, such as
an `IntEnum` member with value `1` and the integer tag `1`.

All branches must resolve to the same canonical field name and external name.
Spec branch types must not be nominally overlapping. Concrete generic Spec
specializations work normally; an open generic branch is not a concrete
contract. Mixed Spec/TypedDict unions and unrelated hybrid alternatives are
rejected. `None` is the one supported outer alternative:

```python
type OptionalPayment = Annotated[
    CardPayment | BankTransfer | None,
    Discriminator("kind"),
]
```

## Dispatch and execution

Strict validation of an existing Spec instance selects its branch by nominal
identity and does not read a mapping tag. Mapping and JSON input locate the
external discriminator, validate its exact type, select one branch, and run
only that branch's compiled converter. TypedDict values necessarily dispatch
from their key because dictionaries have no nominal branch identity.

Generated dispatch uses direct comparisons for two through four branches and
a type-sensitive dictionary lookup bound directly to a compiled branch
operation for five or more branches. Both strategies avoid validating rejected
alternatives. Known-tag failures expose the selected
branch's normal structural errors without a generic union wrapper.

Serialization selects a Spec branch by nominal identity or a TypedDict branch
by its canonical tag. It then uses the selected branch's normal projection, so
aliases, nested values, standard representations, and JSON encoding remain
symmetric. A serialization hook on a discriminator field is rejected when the
tagged contract resolves because it could contradict the round-trip contract.
A hook also cannot replace an entire field whose reachable schema contains a
tagged union; that would bypass branch projection and could silently emit a
different or missing tag. Hooks on ordinary branch-body fields remain normal.

## Errors

A missing key produces `discriminator_missing`; an exact, supported tag type
with no branch produces `discriminator_unknown`. An unsupported tag type uses
the existing `type` code. All three failures point at the discriminator path,
including nested list and mapping segments.

Unknown-tag error projection includes `discriminator` and `expected_tags` as
machine-readable fields. If any branch marks the discriminator `Sensitive`,
tag failures redact the discriminator identity, received input, and expected
tag set. `ReadOnly` and `WriteOnly` remain metadata only.

## TypedDict, recursion, and introspection

TypedDict branches use the same required single-Literal rule and preserve
normal required/optional child keys, generic specialization, nested conversion,
and detached output.

Recursive Spec graphs can contain tagged unions. Forward references finalize
through the existing declaration graph and the tagged schema retains finite
Spec references rather than copying complete branch declarations.

`inspect_contract()` and Spec field introspection expose `TaggedUnionSchema`.
Its branch tuple is immutable and contains no public mutable dispatch table.
This is also the discriminator truth that a future JSON Schema/OpenAPI owner
will project; this campaign does not emit JSON Schema or OpenAPI.
