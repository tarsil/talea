# Dynamic Specs, introspection, and replacement

Campaign 12 adds three productivity surfaces around the normal Spec lifecycle:
`create_spec`, immutable public introspection, and Python-native
`copy.replace`. None creates a second model runtime.

## Dynamic Spec creation

`create_spec()` returns a normal Talea `Spec` subclass. Its ordered `fields`
mapping contains evaluated annotations; defaults and factories use separate
unambiguous mappings.

```python
from talea import create_spec


ConfiguredEvent = create_spec(
    "ConfiguredEvent",
    {
        "event_id": str,
        "attempt": int,
        "labels": list[str],
    },
    defaults={"attempt": 1},
    factories={"labels": list},
    module="application.contracts",
    doc="Event contract generated from application-owned configuration.",
)

event = ConfiguredEvent(event_id="evt-1")
```

The separate mappings avoid tuple ambiguity: `(int, 1)` may itself be a value
or part of a valid type expression in Python APIs. Each factory must be a
zero-argument callable and runs once per omitted field.

### Inheritance

The `base` must be a Spec class or a concrete generic Spec specialization.
Fields are not copied: the existing metaclass and canonical inheritance owner
compose them.

```python
from talea import Spec, create_spec


class Person(Spec):
    name: str


Employee = create_spec(
    "Employee",
    {"employee_id": int},
    base=Person,
)

employee = Employee(name="Ada", employee_id=7)
```

Fresh open generic templates are not dynamically declared in this release.
Use class syntax for a generic declaration, specialize it, then pass the
concrete specialization as `base` when needed.

### Aliases, constraints, methods, and hooks

Aliases and constraints remain ordinary `Annotated` metadata. Methods,
descriptors, `@transform`, `@check`, and `@serialize` callbacks are supplied as
normal trusted namespace entries; there is no parallel hook metadata API.

```python
from typing import Annotated

from talea import Alias, Ge, check, create_spec, serialize


@check("amount")
def nonzero(amount: int) -> None:
    if amount == 0:
        raise ValueError("amount must be non-zero")


@serialize("amount")
def as_text(amount: int) -> str:
    return str(amount)


ConfiguredPayment = create_spec(
    "ConfiguredPayment",
    {"amount": Annotated[int, Alias("amountCents"), Ge(0)]},
    namespace={"nonzero": nonzero, "as_text": as_text},
)
```

Namespace contributions are trusted application code, just like a class body.
They still pass the normal Talea signature, target, inheritance, and lifecycle
checks.

### Identity, security, and pickle

`name`, `module`, and `qualname` accept normalized Python identifiers only.
Field names use the same metaclass validation as static Specs. Names,
annotations, aliases, and hook identities are never interpolated as executable
source. String annotations are rejected: dynamic callers must provide evaluated
runtime annotations.

`create_spec` sets `__name__`, `__qualname__`, `__module__`, and `__doc__`, but
does not mutate `sys.modules` or bind the class for the caller. Standard pickle
rules therefore apply: bind a dynamic class at its declared importable module
and qualified name before expecting instances to pickle.

Python cannot infer runtime-generated fields or constructor signatures. The
return type preserves a supplied base where possible, but application typing
should not pretend a generated field exists statically.

Once created, dynamic construction, Mapping input, JSON, Python output, and
JSON output are the same compiled operations as an equivalent static Spec.
Only class definition pays the small declaration-API parsing cost.

## Public introspection

Frameworks can import immutable description values from `talea.introspection`:

```python
from talea.introspection import inspect_spec


info = inspect_spec(Employee)
for field in info.fields:
    print(field.name, field.annotation, field.required, field.alias)
```

`FieldInfo` reports the effective name, annotation, canonical schema, required
state, static default/factory, alias, and Talea constraints. `SpecInfo` reports
fields, generic parameters/origin/arguments, recursion and permanent-trust
classification, hook and serializer names, and supported operation names.
`inspect_contract()` returns `ContractInfo` with the original annotation,
canonical schema, and operations.

Description dataclasses, tuples, and canonical schema nodes are frozen.
Compiler source, validators, generated globals, locks, and mutable lifecycle
state are not exposed. Spec descriptions are weakly cached by class. An open
generic reports its free annotations and `schema=None` for fields that cannot
be canonical until specialization; its recursive and trust classifications are
therefore conservative.

This surface consumes canonical truth but is not JSON Schema. A framework can
use it for command registration, routing, documentation tooling, or dependency
analysis without coupling to private Talea declarations.

## Immutable replacement

Talea implements Python 3.14's `__replace__` protocol, so standard
`copy.replace` is the only replacement vocabulary.

```python
from copy import replace

from talea import Spec, check


class Window(Spec):
    start: int
    end: int

    @check("start", "end")
    def ordered(start: int, end: int) -> None:
        if start > end:
            raise ValueError("start must not follow end")


current = Window(start=1, end=5)
updated = replace(current, end=8)
assert current.end == 5
assert updated.end == 8
```

Replacement keywords are canonical Python field names, never external aliases.
Unknown names are rejected. Changed values run inbound transforms, structural
validation, and field checks. Whole-Spec checks always rerun. Untouched
permanently trusted values are reused directly; mutable current state is
revalidated before publication. Validation is atomic, defaults and factories
do not rerun, and unchanged mutable values remain shared by reference just as
with ordinary immutable-record replacement.

The implementation does not route through `to_dict()` and `from_mapping()`.
Each used Spec class compiles one smallest-path replacer lazily and owns it.
Ordinary Specs that never use replacement have no replacement artifact.

Current Python 3.14 typing preserves the concrete return type of
`copy.replace(spec, ...)`, but the available static protocol does not validate
arbitrary replacement keyword value types as precisely as Talea's generated
constructor signature. Runtime validation remains complete.

## Advanced framework recipe

An application can generate a normal Spec from trusted registry configuration,
then publish only immutable public metadata to its tooling layer:

```python
from talea import create_spec
from talea.introspection import inspect_spec


def build_message(name: str, configured_fields: dict[str, object]):
    message_type = create_spec(name, configured_fields, module="app.messages")
    description = inspect_spec(message_type)
    return message_type, tuple((field.name, field.required) for field in description.fields)
```

Validate the configuration before calling this function. `namespace` is a
trusted code surface, not a safe container for user-supplied callbacks.

All-fields-optional/PATCH derivation and callable validation are intentionally
not approximated by these APIs. Presence state, cross-field hook policy, and
Python signature binding need dedicated owners; see the
[release ledger](release-ledger.md).

