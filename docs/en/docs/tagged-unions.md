# Tagged unions

Tagged unions make a payload's own required literal field select one branch.
They are explicit: ordinary unions keep their existing validation and error
semantics.

```python
from typing import Annotated, Literal

from talea import Alias, Contract, Discriminator, Spec


class CardPayment(Spec):
    kind: Annotated[Literal["card"], Alias("type", legacy=("kind",))]
    number: str


class BankTransfer(Spec):
    kind: Annotated[Literal["bank"], Alias("type", legacy=("kind",))]
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

All branches must resolve to the same canonical field name, external name, and
ordered accepted input names. A branch-specific legacy vocabulary is rejected
because dispatch occurs before a branch has been selected.
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
external discriminator or exactly one of its declared legacy names, validate
its exact type, select one branch, and run only that branch's compiled
converter. TypedDict values necessarily dispatch from their key because
dictionaries have no nominal branch identity.

If more than one accepted discriminator spelling is supplied, dispatch raises
`alias_conflict` at the current external discriminator path before any branch
runs. Equal tag values still conflict. With no accepted spelling the result is
`discriminator_missing`; with one accepted spelling and an unknown tag it is
`discriminator_unknown`. Legacy names affect key lookup only—tag identity
remains exact and type-sensitive. Output always emits the current external key.

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

## JSON Schema and OpenAPI

Input branch schemas publish the complete discriminator
`accepted_input_names` vocabulary. Their per-field Draft 2020-12 `oneOf`
constraint accepts `type` or `kind` in the example above and rejects both,
while each property's literal schema preserves the unchanged tag value.
Missing and unknown tags consequently remain invalid schema shapes. Recursive
tagged graphs reuse the canonical branch definitions; legacy keys do not create
new branch identities.

Output branch schemas contain only the current external discriminator key.
OpenAPI's Discriminator Object can name one property only, so
`propertyName` is the current external key (`type` above), and `mapping` keeps
the existing tag-value-to-branch references. The referenced input branch
schemas describe legacy-key acceptance. Some UI tooling may surface only the
canonical discriminator hint; Talea does not weaken validation or invent a
vendor extension to hide that standards limitation.

## Errors

A missing key produces `discriminator_missing`; an exact, supported tag type
with no branch produces `discriminator_unknown`. An unsupported tag type uses
the existing `type` code. All three failures point at the discriminator path,
including nested list and mapping segments.

Unknown-tag error projection includes `discriminator` and `expected_tags` as
machine-readable fields. If any branch marks the discriminator `Sensitive`,
tag failures redact the discriminator identity, received input, and expected
tag set. A directional derivation of the containing Spec may omit the entire
tagged field based on that field's metadata, but it does not rewrite branches.

## TypedDict, recursion, and introspection

TypedDict branches use the same required single-Literal rule and preserve
normal required/optional child keys, generic specialization, nested conversion,
and detached output.

Recursive Spec graphs can contain tagged unions. Forward references finalize
through the existing declaration graph and the tagged schema retains finite
Spec references rather than copying complete branch declarations.

`inspect_contract()` and Spec field introspection expose `TaggedUnionSchema`.
Its immutable `accepted_input_names` is projected once from the compatible
branch fields; its branch tuple contains no public mutable dispatch table.
`json_schema()` projects `oneOf` branches from this truth.
`openapi_schema()` additionally emits a discriminator with the common external
property name and a mapping to branch components. Recursive, generic, and
TypedDict branches use the same finite definitions graph.

## Production event stream

The executable example below uses four event types, UUID and datetime boundary
representations, Decimal, aliases, a Sensitive authorization token, a generic
`EventEnvelope[T]`, strict JSON input/output, invalid tags, selected-branch
failures, JSON Schema, and an OpenAPI discriminator map.

{!> ../../../docs_src/tutorials/events.py !}

The Sensitive token illustrates an important boundary rule: Sensitive redacts
Talea-owned representation and failure snapshots, but successful serialization
still follows the declared contract. If an event projection must omit a secret,
declare a separate outward event type or explicitly exclude that field at the
Spec boundary; do not treat security metadata as an output allow-list.

Tagged dispatch is a domain fit here because `type` is a protocol-level fact.
An unknown event is not “the branch whose validation failed least”; it is an
unsupported protocol message. Direct selection also gives framework tooling a
canonical OpenAPI mapping and avoids creating diagnostics for branches the
sender never selected.

## Performance and when not to tag

Tagged dispatch runs only the selected branch. Small unions use direct
comparisons; larger unions use a retained type-sensitive lookup. Cold
resolution verifies tags and collisions once, while repeated Mapping/JSON
input avoids the trial cost and branch-error allocation of an untagged union.

Do not add a discriminator merely to optimize a union whose data has no stable
tag. The field becomes part of the external contract and must round-trip. An
ordinary union remains clearer for small value alternatives such as `int | str`
or when branch choice is genuinely structural. For recursive composition, see
the [tagged AST](recursive-generics.md#recursive-tagged-ast); for output shapes,
see [JSON Schema and OpenAPI](json-schema-openapi.md).
