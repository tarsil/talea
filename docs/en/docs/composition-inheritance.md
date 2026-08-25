# Composition and inheritance

## Nested Specs

A declared Spec class is a supported field type and may appear anywhere the
existing container and union schemas allow:

```python
from talea import Spec


class Address(Spec):
    city: str
    postcode: str


class User(Spec):
    identifier: int
    address: Address


class Team(Spec):
    members: list[User]
    lead: User | None = None
```

Construction accepts an existing compatible instance and retains that object.
It does not convert mappings or rebuild nested Specs. Subclasses are accepted
where a base Spec is annotated, following normal `isinstance` semantics. A
wrong member inside a container reports the field and container position, such
as `("members", 2)`.

Embedding a permanently trusted Spec performs only a nominal compatibility
check. When the referenced declaration is not permanently trusted, Talea also
validates its current declared field state at the new boundary. That validation
is specialized when the containing class is declared: it reads the known
attributes directly and uses the same compiled validation semantics as ordinary
fields. It does not reconstruct the nested object, interpret field metadata, or
recompile at runtime.

Current-state validation also runs the referenced contract's field and
cross-field custom checks. Inbound transforms are not current-state validators
and never run against a retained nested object.

A mutable nested Spec therefore succeeds while its current state is valid and
fails after normal container mutation makes that state invalid. The containing
declaration remains classified as not permanently trusted even after successful
current-state validation, because later mutation remains possible.

Trust follows the annotated nominal contract. If a subclass adds a mutable
field, that extension does not weaken an immutable base-class contract: an
instance of the subclass remains permanently valid where only the base is
required. Annotating the mutable subclass directly propagates its non-permanent
classification and requires current-state validation of its complete effective
declaration.

## Single inheritance

Subclasses inherit fields and add their own fields in declaration order:

```python
class Person(Spec):
    name: str
    active: bool = True


class Employee(Person):
    employee_id: int


employee = Employee(name="Ada", employee_id=7)
```

Construction remains keyword-only, so a required child field may follow an
inherited default. Each subclass owns one effective declaration and one flat
constructor; construction does not call the parent constructor. Inherited and
new fields use normal slots, remain immutable, and do not create an instance
dictionary.

Ordinary methods, properties, class methods, and static methods may be added or
overridden. Talea continues to own `__init__`, `__slots__`, `__setattr__`, and
`__delattr__` because those methods enforce construction and field
immutability.

## Field order and overrides

Inherited fields retain their original position. New subclass fields append in
class-body order. An override replaces the field at its inherited position.

An annotation in the subclass is a complete field declaration. Omitting an
assignment makes the override required, even when the parent field had a
default or factory. An assignment supplies a new static default, and
`field(default_factory=...)` supplies a new factory. The resulting transitions
are therefore explicit:

| Parent state | Subclass declaration | Result |
| --- | --- | --- |
| required | annotation only | required |
| required | static default | new static default |
| default | annotation only | required |
| default | static default | new static default |
| factory | annotation only | required |
| factory | static default | new static default |
| default or factory | new factory | new factory |

Field types may remain unchanged or narrow covariantly. For example,
`Person | Address` may narrow to `Person`, and a `Person` field may narrow to an
`Employee` field. Immutable tuple and frozenset members follow the same rule.
Incompatible changes such as `int` to `str`, widening a Spec subtype to its
base, or changing to a sibling Spec are rejected when the subclass is declared.
Mutable containers are invariant.

Inherited factories retain their original callable and run once for every
omitted subclass instance. Declaring a subclass does not run them. An override
replaces the inherited factory rather than layering both factories.

## Multiple inheritance

CPython cannot create a class from two independent bases that both add
non-empty slots; it raises `TypeError: multiple bases have instance lay-out
conflict`. Talea therefore supports the broadest compact policy that preserves
normal slot storage:

- one state-bearing Spec lineage may contribute fields;
- diamonds work when only one branch adds storage;
- additional Spec bases may be fieldless;
- non-Spec method mixins must declare `__slots__ = ()` and carry no instance
  state;
- two incomparable state-bearing branches are rejected before class creation.

Python's MRO selects inherited behavior. The effective field declaration is
merged in declared-base order, with the first inherited occurrence owning a
same-name field and local overrides taking precedence. Diamond fields retain a
single slot and a single canonical field entry.

Custom validation hooks use their method names as override identity and follow
the same base-first effective ordering. See
[Custom validation](custom-validation.md) for replacement and shadowing rules.
