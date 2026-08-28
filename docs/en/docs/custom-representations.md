# Custom domain representations

`Representation` binds an application-owned Python type to explicit external
input and output contracts at one `Annotated` position. Use it for values such
as money, third-party identifiers, geographic coordinates, or immutable SDK
types when the internal object itself is not Talea's boundary shape.

```python
type MoneyValue = Annotated[
    Money,
    Representation(
        input=MoneyInput,
        load=load_money,
        output=MoneyOutput,
        dump=dump_money,
    ),
]
```

The resolved annotation owns one canonical `RepresentationSchema`: the strict
internal schema, optional input schema, optional output schema, and private
callback association. Strict validation, Python/JSON input, Python/JSON output,
JSON Schema, OpenAPI, introspection, and nested selection all consume that same
node. No callback registry or schema callback is involved.

## Execution contracts

Input executes in this order:

1. validate the external value against `input=`;
2. call `load` exactly once;
3. validate its result against the internal annotation;
4. return the internal value.

Output executes in this order:

1. validate the current internal value under the normal containing contract;
2. call `dump` exactly once;
3. validate its result against `output=`;
4. apply the normal detached Python or JSON projector for that output schema;
5. for `to_json()`, encode the projected JSON tree once.

A wrong dump result raises `SerializationError`; it never escapes merely
because a codec could encode it. Structured mutable callback results are
detached by normal projection. Python and JSON output remain separate compiled
operations, so standard Decimal, UUID, datetime, bytes, Enum, dataclass, Spec,
TypedDict, and container rules remain authoritative.

## One-way and bidirectional declarations

`input` and `load` form one complete pair; `output` and `dump` form another.
Declare either pair or both:

- input-only supports strict validation and external input; output compilation
  raises `SerializationError` because no output direction exists;
- output-only supports strict validation and output; external input fails
  because no input direction exists;
- bidirectional supports all six Contract operations when every nested
  representation also declares the needed direction.

There is no fallback to `repr`, `__dict__`, identity, or an arbitrary object
serializer for a missing direction.

## Composition and nested selection

A reusable represented alias can appear under Specs, dataclasses, TypedDicts,
lists, tuples, sets, frozensets, mapping values, unions, tagged branches,
concrete generics, aliases, and recursive containing graphs. The callbacks stay
attached to the represented position; containers do not consult a registry.

When `output=` is structural, nested `include` and `exclude` selection validates
against that declared output schema and compiles a direct selected projector.
Unknown output fields reject before `dump` executes. Talea validates the full
dump result, then reads only selected output fields during projection; it does
not serialize a complete dictionary and recursively post-filter it.

A field-local `@serialize` hook still overrides ordinary field output. Its
return structure is undeclared and therefore remains an opaque selection leaf.
Declared serializer output contracts are a separate future capability.

## JSON Schema, OpenAPI, and introspection

`json_schema(mode="input")` and `openapi_schema(mode="input")` project
`input=`. Output mode projects `output=`. A missing direction raises
`SchemaProjectionError`. Schema tooling never executes `load` or `dump`, and
callbacks cannot inject arbitrary schema fragments. PEP 695 alias identity
continues to own reusable definition names; callback identity never appears in
documents.

`inspect_contract()` and `inspect_spec()` expose callback-free frozen
`RepresentationInfo` projections through their `representations` tuples. Each
item contains the internal, input, and output canonical schemas plus
`has_loader` and `has_dumper`. It exposes no callable, callable name, globals,
generated source, lock, or cache.

## Relationship to other features

| Capability | What it owns |
| --- | --- |
| constraints | predicates on the schema where they are declared |
| `transform` | field-local construction preprocessing; its accepted input schema is undeclared |
| `check` | field or Spec invariant checks after strict validation |
| `@serialize` | field-local output replacement whose structure is currently opaque |
| `NewType` | static/named identity over an existing supported runtime contract |
| dataclass support | structural boundaries for the dataclass itself |
| custom `loads`/`dumps` | JSON syntax codec selection, not domain conversion |
| `Representation` | reusable arbitrary-position boundary truth for one Python type contract |

Output constraints belong inside `output=`, for example
`output=Annotated[str, Pattern(...)]`. Outer constraints still apply to the
internal schema when meaningful. Metadata such as `Title`, `Alias`, and
`Sensitive` retains its existing owner; `Representation` does not duplicate it.

## Trust, security, and round trips

Loaders and dumpers are trusted synchronous Python. Talea validates their
results and safely transports documented failures, but cannot sandbox callback
CPU, memory, I/O, mutation, reentrancy, or logging. `ResourcePolicy` governs
external input traversal, not callback work or output size. A tiny internal
value may deliberately dump a large structure.

`Sensitive` protects Talea-owned failure text, values, locations, and causes;
it cannot stop a callback from logging a secret itself. Successful represented
output is not automatically omitted. Use an explicit response shape and
application authorization for disclosure policy.

Talea guarantees that a successful loader result satisfies the internal
contract and a successful dumper result satisfies the output contract. It does
not guarantee `dump(load(x)) == x`, `load(dump(v)) == v`, byte-for-byte
reversibility, or preservation of noncanonical spelling. Canonicalization is
allowed and is demonstrated by the money and identifier examples below.

## Complete executable examples

The example covers a finance boundary, a ULID-like immutable identifier,
nested containers, Spec/dataclass/TypedDict output, asymmetric schema modes,
nested selection, one-way declarations, canonicalization, and Sensitive dump
failure.

{!> ../../../docs_src/tutorials/custom_representations.py !}

## Current limitations

Plain `Contract(ArbitraryClass)` remains unsupported unless that class has an
ordinary Talea schema such as a dataclass. Representation declarations are
explicit annotations; Talea provides no process-global discovery registry or
generic representation factory. Callbacks are synchronous and trusted. There
is no callback sandbox, output resource policy, custom format namespace, or
user-defined error-code namespace. Arbitrary transforms still make input
schema unknowable, and undeclared `@serialize` output remains structurally
opaque.
