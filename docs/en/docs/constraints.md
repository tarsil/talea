# Constraints

Talea uses `typing.Annotated` as the carrier for reusable built-in constraints.
The base annotation remains the static type, while Talea canonicalizes its
validation metadata once during class declaration.

```python
from typing import Annotated

from talea import Ge, Le, MaxLength, MinLength, Spec


class Registration(Spec):
    age: Annotated[int, Ge(18), Le(130)]
    roles: Annotated[list[str], MinLength(1), MaxLength(10)]
```

Constraint objects are immutable declaration values. They do not expose a
runtime `validate()` method, and generated constructors do not loop over
constraint metadata.

## Public vocabulary

| Constraint | Supported base contracts | Meaning |
| --- | --- | --- |
| `Gt(value)` | `int`, `float`, `Decimal` | Strictly greater than |
| `Ge(value)` | `int`, `float`, `Decimal` | Greater than or equal |
| `Lt(value)` | `int`, `float`, `Decimal` | Strictly less than |
| `Le(value)` | `int`, `float`, `Decimal` | Less than or equal |
| `MultipleOf(value)` | `int`, `float`, `Decimal` | Integral multiple of a non-zero divisor |
| `MinLength(value)` | `str`, `bytes`, list/set/frozenset/dict/tuple schemas | Inclusive minimum size |
| `MaxLength(value)` | The same sized contracts | Inclusive maximum size |
| `Pattern(value)` | `str` | A regular-expression search must match |

Numeric boundary and divisor values must have the exact numeric family of the
base annotation. For example, a Decimal field uses `Ge(Decimal("0"))`, not
`Ge(0)`. Bounds and divisors must be finite; divisors cannot be zero. Lengths
must be non-negative integers and do not accept booleans.

Applying `Pattern` to an integer or `MinLength` to a UUID is a declaration
error. Talea never waits until instance construction to discover that a
built-in constraint is nonsensical for its base type.

## Normalization and contradictions

Equivalent nested `Annotated` declarations become one canonical constrained
schema. Redundant lower, upper, and length bounds reduce to the strongest
check. Duplicate `MultipleOf` and `Pattern` declarations are removed.

```python
from typing import Annotated

from talea import Ge, Le


Percentage = Annotated[int, Ge(0), Ge(10), Le(100), Le(90)]
```

`Percentage` canonicalizes to the effective range `Ge(10), Le(90)`.
Impossible ranges such as `Ge(10), Lt(10)`, discrete integer ranges such as
`Gt(10), Lt(11)`, and `MinLength(10), MaxLength(2)` fail during schema
resolution. Inclusive single-point ranges remain legal.

## Floating-point and Decimal behavior

An unconstrained strict `float` accepts NaN and positive or negative infinity
because they are float values. Ordered constraints reject NaN through their
predicate. Infinity follows ordinary ordering: positive infinity satisfies a
finite lower bound, while negative infinity satisfies a finite upper bound.
`MultipleOf` rejects non-finite float values and uses `math.remainder` with a
small divisor-relative tolerance to account for binary representation, so
`0.3` is a multiple of `0.1`.

Unconstrained Decimal values likewise retain Decimal's complete value domain.
Numeric constraints reject non-finite Decimal values before ordering or
divisibility operations. Decimal `MultipleOf` uses exact integer ratios and is
independent of the active decimal context precision.

## Strings and containers

Length checks execute after the exact base-type check and before container item
validation. A constrained list therefore compiles into an exact list check,
direct length comparisons, and the existing specialized item loop.

`Pattern` accepts a string or compiled string `re.Pattern` and uses `search`
semantics. It compiles string declarations once. Generated source binds the
compiled expression as a compiler-owned global; quotes, backslashes, and
newlines in pattern text are never interpolated as Python source.

```python
import re
from typing import Annotated

from talea import Pattern, Spec


class Product(Spec):
    code: Annotated[str, Pattern(re.compile(r"^[A-Z]{3}-\d{4}$"))]


product = Product(code="ABC-0042")
```

## Metadata interoperability

Talea-owned constraints affect validation. Other `Annotated` metadata is
ignored, not retained in the compact canonical validation schema, and never
executed. This policy avoids turning arbitrary metadata objects into accidental
validators or adding unused introspection cost. Talea-owned metadata has a
separate canonical record; see [Metadata and Sensitive](metadata-security.md).

## Inheritance and trust

A constrained override must be provably no wider than its inherited contract.
Stronger numeric and length bounds are accepted. Integer and Decimal
`MultipleOf` constraints may narrow to a divisor whose multiples are a subset
of the parent divisor. Identical float divisors and patterns are provable;
otherwise Talea rejects the override rather than assume implication.

Constraints on immutable scalar values remain permanently trustworthy.
Constrained mutable containers remain non-permanently trustworthy. If a nested
mutable Spec crosses another Talea boundary after mutation, current-state
revalidation executes its length, item, pattern, and numeric constraints.

## Failures and performance

Constraint failures retain the rejected value, exact nested location, expected
contract, stable category such as `greater_than`, `max_length`, or `pattern`,
and structured context containing the failed limit or pattern. Talea remains
fail-fast across Spec fields; [Validation errors](error-experience.md) documents
human rendering, JSON projection, and union branch diagnostics.

Unconstrained Specs contain no constraint metadata loops, regex machinery,
type adapters, or registry lookups. Each used constraint adds only its direct
Python operation to the specialized validator or constructor.

## Boundary walkthrough

The lower and upper limits are inclusive or exclusive exactly as named:

```python
from typing import Annotated

from talea import Contract, Ge, Lt


type RiskScore = Annotated[int, Ge(0), Lt(100)]
risk_scores = Contract[RiskScore](RiskScore)

assert risk_scores.validate(0) == 0
assert risk_scores.validate(99) == 99
# -1 fails with greater_than_or_equal; 100 fails with less_than.
```

For sized values, boundaries count the declared Python container or string:

```python
from talea import MaxLength, MinLength


type Tags = Annotated[list[str], MinLength(1), MaxLength(3)]
tags = Contract[Tags](Tags)

assert tags.validate(["verified"])
# [] fails min_length; four tags fail max_length.
```

Constraints compose with aliases, nested Specs, unions, TypedDict fields, and
container items. Place `Annotated` at the level being constrained:

```python
type NonEmptyText = Annotated[str, MinLength(1)]
type NonEmptyList = Annotated[list[str], MinLength(1)]

# list[NonEmptyText] constrains each string; NonEmptyList constrains the list.
```

## Inheritance narrowing example

```python
from talea import Spec


class PublicLabel(Spec):
    value: Annotated[str, MinLength(1), MaxLength(120)]


class ShortLabel(PublicLabel):
    value: Annotated[str, MinLength(3), MaxLength(40)]
```

The child accepts a subset of parent values, so substituting it does not weaken
the inherited contract. Reversing either bound is rejected at declaration.
Pattern implication is not generally decidable; Talea accepts identical
patterns but does not guess that one different regular expression narrows
another.

## Schema and debugging

Integer/float bounds and sized-container limits project to the corresponding
Draft 2020-12 keyword. The property holding `RiskScore`, for example, carries
`minimum: 0` and `exclusiveMaximum: 100`. Decimal bounds stay runtime-only
because Decimal is represented as JSON text, and float `MultipleOf` stays
runtime-only because Talea's tolerance is not JSON Schema's exact mathematical
rule.

When a declaration fails, inspect the base type and constraint value before
debugging runtime input: `Ge(0)` on Decimal should be `Ge(Decimal("0"))`, and
`MinLength` cannot apply to UUID. When a value fails, consume the stable code,
location, and `context` from `ValidationError.errors()`; the context retains the
effective normalized bound.

Do not use a structural constraint for business policy that changes with
database state, permissions, clocks, or external services. Those checks belong
in the application operation, not a supposedly stable data contract.
