# Recursive aliases and TypedDict graphs

Talea resolves recursive PEP 695 aliases and `TypedDict` declarations into a
finite canonical graph. Annotation reflection happens while the Contract or
Spec declaration is prepared. Validation, input, serialization, and
introspection consume the same graph; none of those operations walks the
original annotation at runtime.

## Recursive aliases

A named alias may refer to itself beneath any supported container or union:

```python
from talea import Contract


type JSONValue = (
    str
    | int
    | float
    | bool
    | None
    | list[JSONValue]
    | dict[str, JSONValue]
)

json_value = Contract[JSONValue](JSONValue)
value = json_value.from_json('{"items":[1,true,null]}')

assert json_value.validate(value) is value
assert json_value.to_json(value) == '{"items":[1,true,null]}'
```

Mutual recursion is resolved by declaration identity, not by an alias's
display name. Two aliases with the same module and name remain distinct when
they are different Python declarations. A mutual back-edge points to the one
canonical declaration already being resolved instead of expanding its target
again.

## Recursive TypedDict

`TypedDict` declaration classes supply nominal graph identity even though
valid runtime values are ordinary dictionaries:

```python
from typing import NotRequired, TypedDict

from talea import Contract


class Node(TypedDict):
    value: int
    children: list[Node]
    parent: NotRequired[Node]


node = Contract[Node](Node)
root = node.from_python(
    {
        "value": 1,
        "children": [{"value": 2, "children": []}],
    }
)
```

Required, `NotRequired`, inherited, read-only, and field metadata semantics are
unchanged. `ReadOnly` remains metadata: Talea does not control mutation of a
normal dictionary after a boundary returns it.

Mutually recursive TypedDict classes work when Python can resolve their
annotations. The declarations must therefore be available through normal
Python 3.14 forward-annotation lookup when the Contract or containing Spec is
first finalized.

## Mixed alias and TypedDict graphs

Aliases and TypedDicts share one named-reference identity model:

```python
from typing import TypedDict


class TreeNode(TypedDict):
    value: int
    children: NodeList


type NodeList = list[TreeNode]
```

The alias owns its specialization identity and the TypedDict owns its class
identity. A reference does not copy metadata or fields. This is also the truth
that future JSON Schema definitions can project without reading annotations
again.

## Concrete recursive generics

Concrete recursive alias and TypedDict specializations are executable:

```python
from typing import TypedDict

from talea import Contract


type Tree[T] = T | list[Tree[T]]


class BoxNode[T](TypedDict):
    value: T
    children: list[BoxNode[T]]


integers = Contract[Tree[int]](Tree[int])
nodes = Contract[BoxNode[int]](BoxNode[int])
```

The declaration object plus concrete arguments form specialization identity.
Open generic aliases and TypedDicts remain non-executable because no concrete
schema can be compiled. Talea does not add runtime type-parameter dispatch.
Graph data is retained by the Contract or Spec that consumes it; there is no
process-global alias or TypedDict registry.

## Recursive tagged ASTs

Tagged unions retain direct discriminator selection at every recursive level:

```python
from typing import Annotated, Literal, TypedDict

from talea import Contract, Discriminator


class LiteralNode(TypedDict):
    kind: Literal["literal"]
    value: int


class AddNode(TypedDict):
    kind: Literal["add"]
    left: Expr
    right: Expr


type Expr = Annotated[
    LiteralNode | AddNode,
    Discriminator("kind"),
]

expression = Contract[Expr](Expr)
```

A known tag selects one compiled branch. Recursive support does not turn the
tagged union into a linear trial of all branches.

## Contract and Spec boundaries

Recursive named graphs support all retained Contract operations:

- `validate` checks existing Python shapes strictly and preserves identity;
- `from_python` recursively detaches mutable containers and accepts the same
  external Mapping shapes as ordinary TypedDict input;
- `from_json` feeds decoded values into that input graph;
- `to_python` returns detached mutable structures;
- `to_json` projects through the same graph before strict encoding.

The same annotations work as normal Spec fields and through `create_spec`.
There is no Spec-specific alias graph. `Spec.to_dict`, `Spec.to_json`, Mapping
input, replacement, and current-state validation consume the field's canonical
schema normally.

## Errors and sensitive values

Back-edges are transparent to locations. A failure may report
`children[2].children[0].value`; alias names and graph implementation objects
never appear as location segments. Stable codes and locations remain the
machine contract, while a named alias may improve expected-type text.

`Sensitive()` metadata remains owned by its alias or TypedDict field. A
recursive reference does not duplicate that metadata. Rejected values beneath
a sensitive recursive edge are redacted from rendering, `errors()`, retained
causes, and serialization failures under the existing metadata policy.

## Runtime cycles

A recursive type graph is not a cyclic runtime object graph. Existing strict
validation follows the recursive Spec policy: once the same object is active
through a valid recursive edge, validation does not recurse forever. External
input cannot construct a cyclic result and fails with code `cycle` at the first
repeated path. Python and JSON serialization raise `SerializationError` at that
same path.

No arbitrary depth limit is imposed in this campaign. Very deep acyclic input
therefore follows normal Python recursion behavior. Global depth and size
budgets belong to the later resource-policy owner.

## Introspection and performance

`inspect_contract` and `inspect_spec` expose frozen schema values. A named
back-edge exposes immutable kind, module, name, declaration, and concrete
arguments without recursively embedding its target. This keeps public
introspection finite and provides future schema projection with stable
definition identity.

Ordinary aliases, TypedDicts, Specs, and tagged unions contain no recursive
execution helper when their graph has no back-edge. Recursive graphs bind
schema-specialized child operations and allocate active-identity state only
during a recursive operation. Resolution cost is cold work; repeated execution
does not inspect annotations.

## Current limits

- Open generic declarations remain non-executable.
- Python must be able to resolve deferred TypedDict names through its normal
  Python 3.14 annotation namespace rules.
- Runtime cyclic input and output are rejected rather than reconstructed.
- JSON Schema, OpenAPI, PATCH/presence derivation, and global resource budgets
  are not implemented here.
