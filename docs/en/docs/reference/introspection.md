# Introspection

Framework and tooling authors can inspect finalized public truth without
accessing compiler artifacts.

```python
from talea import Contract, Spec
from talea.introspection import inspect_contract, inspect_spec


class User(Spec):
    id: int


spec_info = inspect_spec(User)
contract_info = inspect_contract(Contract(list[User]))
```

## Returned values

| Type | Important data |
| --- | --- |
| `FieldInfo` | annotation, canonical `Schema`, required/default/factory state, alias, constraints, metadata, presence |
| `DerivationInfo` | source Spec, retained/omitted fields, include/exclude selection, partial status, input/output mode |
| `SpecInfo` | fields, generic identity/arguments, recursion, hooks, serializers, metadata, trust, derivation, reachable representations |
| `ContractInfo` | annotation, canonical `Schema`, metadata, supported operation names, reachable representations |
| `RepresentationInfo` | frozen internal/input/output schemas and direction flags, with no callbacks |

For `Contract(UserDataclass)`, `ContractInfo.schema` is a frozen
`DataclassSchema`. It exposes exact dataclass type identity, immutable canonical
fields, `init`/keyword-only/default lifecycle truth, frozen transitive trust,
generic specialization identity, and finite recursive references without
exposing mutable stdlib `Field` objects.

`inspect_spec()` accepts a Spec class, including an open generic declaration.
Concrete declarations return a cached immutable `SpecInfo`; open generics expose
the declaration truth available before specialization. `inspect_contract()`
accepts a Contract instance and returns a fresh immutable description.

The canonical `Schema` graph is structural truth and is safe to read. Generated
source, compiled callables, locks, lazy publication state, codec choices, and
resource counters remain intentionally private. Tooling should not infer
semantics from class internals when a public info object or schema node provides
the answer.

`SpecInfo.representations` and `ContractInfo.representations` contain each
reachable represented contract once. Their `RepresentationInfo` values expose
`internal`, optional `input`/`output`, `has_loader`, and `has_dumper`. They do
not expose callback objects, callback names, globals, generated source, or
compiler state; mutation cannot alter runtime truth.

For a directional derived Spec, `DerivationInfo.mode` is `"input"` or
`"output"`; it is `None` for ordinary pick/omit/partial derivation. Consumers
should inspect this provenance instead of inferring semantics from names such
as `UserInput`.

## Framework adapter example

The executable example projects a normal account Spec into a smaller
framework-owned descriptor, inspects aliases, constraints, metadata, and
Sensitive state, then inspects a derived partial and an arbitrary Contract.

{!> ../../../../docs_src/recipes/framework_introspection.py !}

The framework may copy this information into route metadata, dependency
descriptions, form fields, or documentation. It should preserve the distinction
between canonical Python names and aliases, and between `required` and
`omittable`. A partial field can be non-required because it is absent; that does
not imply its value contract accepts `None`.

## Caching, open generics, and recursion

Concrete `inspect_spec()` results are weakly cached by class identity and are
frozen. A framework can retain them without worrying that another consumer will
mutate shared field truth. The cache does not keep an otherwise unreachable
dynamic class alive.

An open generic exposes its parameters and annotations, but a field whose type
depends on a free parameter has `schema=None` until specialization. Frameworks
must either display that declaration as generic or request a concrete type such
as `Page[User]`; they must not invent `Any` runtime semantics. Recursive
contracts expose finite reference nodes, so traversals should track named
identity rather than recursively expanding forever.

## Failure and extension rules

`inspect_spec()` rejects non-Spec classes and `inspect_contract()` rejects
non-Contract objects with `TypeError`. Introspection does not execute input,
serialization, callbacks, or schema projection. It also does not expose
generated source or compiled callables as public extension points.

A framework needing JSON/OpenAPI should call the projection API instead of
recreating standards semantics from `FieldInfo`. A runtime validator should use
the Spec/Contract operation or compile from public canonical Schema, not reread
`__annotations__`. These boundaries preserve one owner for each truth.
