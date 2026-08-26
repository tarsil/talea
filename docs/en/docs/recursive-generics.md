# Forward references, recursion, and generics

Talea resolves recursive and generic declarations into the same canonical
schemas used by ordinary Specs. A concrete specialization compiles concrete
validators, input boundaries, and serializers; construction never looks up a
`TypeVar` or interprets a recursive schema.

## Forward references

Python 3.14 normally defers annotations, so a later declaration can be named
directly or with an explicit string:

```python
from talea import Spec


class Employee(Spec):
    manager: "Manager | None"


class Manager(Spec):
    name: str
```

Talea finalizes a declaration immediately when all names are available. If a
name is genuinely pending, Talea retains the declaration identity and resolves
the reachable graph on its first concrete operation. Construction,
`from_mapping`, `from_json`, `to_dict`, and `to_json` all cross that same
finalization boundary. There is no public rebuild or model-registry API.

An unresolved name fails with `AnnotationResolutionError`. The message names
the missing symbol, owning Spec, and field. No validator or serializer is
published before the declaration graph is resolved, so a stale partial artifact
cannot survive later resolution.

Explicit annotation strings are accepted only when their syntax is structural:
names, attributes, subscriptions, tuples, and `|` unions. The exact
`Discriminator("field")` metadata call is also accepted for recursive tagged
Spec graphs. Other function calls and executable expressions are rejected
before Python evaluates them. Prefer
normal Python 3.14 annotations and use string references only where a pending
name requires one.

## Recursive Specs

A Spec may refer to itself through any supported container or union:

```python
class Node(Spec):
    value: int
    children: list[Node]


root = Node(
    value=1,
    children=[Node(value=2, children=[])],
)
```

The canonical field schema contains a nominal reference to `Node`; it does not
copy `Node`'s fields into itself. Validators, Mapping/JSON input, and
serialization compile a deferred back edge to that same declaration identity.
This keeps recursive schemas finite and gives every execution domain one
structural truth.

Recursion also works through dictionaries, fixed and variadic tuples,
`Annotated`, constraints, aliases, and unions when the underlying annotation is
otherwise supported.

### Mutually recursive Specs

Mutual recursion uses the same graph finalization:

```python
class Folder(Spec):
    parent: "Folder | None"
    files: list["File"]


class File(Spec):
    folder: Folder
    name: str
```

Names declared in a function-local scope are supported without a process-wide
registry. Talea retains that defining local scope only while an unresolved
declaration needs it and releases it after finalization.

### Mapping and JSON input

Recursive external data is converted at every declared Spec edge:

```python
data = {
    "value": 1,
    "children": [{"value": 2, "children": []}],
}

node = Node.from_mapping(data)
same_values = Node.from_json(node.to_json())
assert same_values.to_dict() == data
```

Failures preserve the complete path. An invalid grandchild value, for example,
can report `("children", 0, "children", 0, "value")`.

### Serialization and cycle policy

`to_dict()` and `to_json()` traverse acyclic recursive values and retain their
existing alias, include/exclude, custom serializer, and JSON representation
contracts.

Talea deliberately distinguishes recursive declarations from cyclic runtime
objects. Mutable containers can be used after construction to create an object
cycle. Current-state validation handles a repeated object identity safely, but
Mapping/JSON input and serialization reject cycles:

- cyclic Mapping input raises `ValidationError` with code `"cycle"` and the
  exact back-edge location;
- cyclic `to_dict()` or `to_json()` raises `SerializationError` with the exact
  back-edge location;
- successful acyclic paths pay identity tracking only for recursive
  declarations.

Depth is otherwise governed by normal Python recursion limits. Talea does not
silently truncate a deeply nested graph.

### Trust

An immutable recursive graph, such as an optional parent link without mutable
containers, can be permanently trusted. A recursive list, dictionary, or set
keeps the declaration non-permanently-trusted because the retained container
can change. Recursive current-state validation uses operation-local identity
tracking so a runtime cycle cannot recurse without bound.

## Generic Specs

Talea uses Python 3.14 type-parameter syntax directly:

```python
class Box[T](Spec):
    value: T


integer_box = Box[int](value=1)
text_box = Box[str](value="one")
```

`Box[int]` and `Box[str]` are distinct concrete Spec classes. Each owns a
canonical concrete schema and specialized compiled execution. `Box[int]`
therefore keeps strict integer semantics and rejects `"1"`; there is no runtime
`TypeVar` dispatch.

Repeated `Box[int]` subscription returns the same class identity while it is in
use. The generic origin owns a weak specialization cache, so unused
specialization classes are collectible rather than permanently retained by a
global registry.

An unspecialized generic is a declaration template, not a runtime model:

```python
Box(value=1)  # TypeError: requires concrete specialization
```

### Nested and recursive generics

Generic Specs compose through containers and other generic Specs:

```python
class Page[T](Spec):
    items: list[T]


class Response[T](Spec):
    page: Page[T]


response = Response[int].from_mapping({"page": {"items": [1, 2]}})
assert type(response.page) is Page[int]
```

A recursive generic substitutes its back edge with the same concrete
specialization:

```python
class Tree[T](Spec):
    value: T
    children: list[Tree[T]]


tree = Tree[str].from_mapping(
    {"value": "root", "children": [{"value": "leaf", "children": []}]}
)
```

### Inheritance and partial binding

Generic inheritance materializes the concrete base schema before composing the
child's flat effective declaration:

```python
class Base[T](Spec):
    value: T


class Child[T](Base[T]):
    label: str


class IntegerChild(Base[int]):
    label: str
```

`Child[str]` validates a string `value`; `IntegerChild` is already concrete.
Inheriting an unspecialized generic base into a non-generic child is rejected.
Partially bound forms inside another generic declaration are retained and
completed when the outer type is specialized.

### Bounds, constraints, and type-parameter defaults

Bounds and constraints are checked when the specialization is created:

```python
class Entity(Spec):
    identifier: int


class Ref[T: Entity](Spec):
    value: T


class Choice[T: (int, str)](Spec):
    value: T


class Defaulted[T = int](Spec):
    value: T
```

An incompatible type argument raises `TypeError` before instances or artifacts
are used. Since Python subscription syntax requires something between the
brackets, `Defaulted[()]` requests all declared type-parameter defaults and is
identical to `Defaulted[int]`.

| Parameter form | Runtime support |
| --- | --- |
| `TypeVar` / `class Model[T]` | Yes |
| Bound `T: Base` | Yes |
| Constrained `T: (A, B)` | Yes |
| Default `T = Default` | Yes |
| Nested or partially bound TypeVars | Yes |
| `ParamSpec` / `**P` | Rejected |
| `TypeVarTuple` / `*Ts` | Rejected |

Only annotations already supported by Talea may be used as concrete type
arguments. Specialization does not broaden the canonical schema language.

### Defaults, factories, hooks, and serializers

Static defaults are validated against the concrete specialization. Factories
run per instance and their results are checked against the substituted schema.
`transform`, `check`, and `serialize` callbacks are inherited by each concrete
specialization and receive the specialized field values at runtime:

```python
from talea import check, field, serialize


class Produced[T](Spec):
    value: T = field(default_factory=lambda: 1)

    @check("value")
    def positive(value: T) -> None:
        if value <= 0:
            raise ValueError("positive value required")

    @serialize("value")
    def output(value: T) -> str:
        return str(value)
```

Static type checkers see the PEP 695 declaration directly: `Box[int].value` is
`int`, nested generic fields remain parameterized, and recursive fields retain
their concrete recursive type.

## Copying and pickle

`copy.copy()` and `copy.deepcopy()` preserve the concrete class without
rerunning transforms, checks, or factories. Deep copy preserves repeated and
cyclic object identities through Python's memo protocol.

Acyclic instances of importable plain and concrete generic Specs support
trusted Python pickle reconstruction. Pickle has its normal code-execution
security model and must never be used for untrusted data. Function-local Spec
classes remain subject to Python's normal importability limitation, and cyclic
Spec pickle graphs are not a Talea persistence format. Use `to_dict()` or
`to_json()` for supported untrusted-data boundaries.

## Performance model

Resolution and first specialization are cold class-level costs. Repeated
specialization is a cache lookup, and concrete construction uses the same
compiled shape as an equivalent non-generic Spec. Recursive construction,
conversion, and serialization scale with the actual traversed data. Ordinary
non-generic, non-recursive Specs retain their direct generated constructors and
do not carry recursive input/output state or current-state validators.
