# Public API reference

The root package is the normal application API. Domain modules expose additional
structural and tooling contracts deliberately; generated execution internals
are not public.

## Root exports

| API | Purpose | Main failures |
| --- | --- | --- |
| `Spec` | Immutable declared record and boundary operations | `ValidationError`, `ResourceLimitError`, `SerializationError`, `SchemaProjectionError` |
| `Contract` | Retained arbitrary annotation contract | annotation declaration errors and the same operation failures |
| `field` | Declare a default factory | declaration `TypeError` |
| `create_spec` | Build a normal Spec class from trusted runtime declarations | `TypeError` |
| `derive_spec` | Project include/exclude and partial Specs | `TypeError`, `ValueError` |
| `apply_patch` | Apply present partial fields through `copy.replace()` | `TypeError`, `ValidationError` |
| `transform` | Declare a pre-validation field transform | declaration `TypeError`; runtime `ValidationError` |
| `check` | Declare a field or whole-Spec assertion | declaration `TypeError`; runtime `ValidationError` |
| `serialize` | Declare a field output serializer | declaration `TypeError`; runtime `SerializationError` |
| `Alias` | Declare one external field name | `TypeError` and declaration collisions |
| `Discriminator` | Select a Literal-tagged union branch | tagged-union declaration errors |
| `Title`, `Description`, `Examples`, `Deprecated` | Documentation metadata | invalid marker `TypeError`/`ValueError` |
| `ReadOnly`, `WriteOnly` | Boundary classification metadata; no runtime enforcement | invalid marker `TypeError` |
| `Sensitive` | Talea-owned failure redaction metadata | invalid marker `TypeError` |
| `Gt`, `Ge`, `Lt`, `Le` | Ordered numeric constraints | declaration `TypeError`/`ValueError` |
| `MultipleOf` | Numeric divisibility constraint | declaration `TypeError`/`ValueError` |
| `MinLength`, `MaxLength`, `Pattern` | Sized and string constraints | declaration `TypeError`/`ValueError`/`re.error` |
| `ValidationError` | Structured one-or-many validation failure | — |
| `ErrorCode`, `ErrorData` | Stable codes and JSON-compatible projected detail | — |
| `ResourcePolicy`, `ResourceLimitError` | Finite external-input budgets and rejection | invalid policy `ValueError` |
| `SerializationError` | Safe output projection/encoding failure | — |
| `SchemaProjectionError` | Statically unknowable or unsupported schema projection | — |

## Spec operations

| Operation | Signature summary |
| --- | --- |
| strict construction | `ConcreteSpec(**canonical_fields)` |
| mapping input | `ConcreteSpec.from_mapping(mapping, *, policy=None)` |
| JSON input | `ConcreteSpec.from_json(data, *, loads=None, policy=None)` |
| Python output | `instance.to_dict(*, include=None, exclude=None, exclude_none=False)` |
| JSON output | `instance.to_json(*, include=None, exclude=None, exclude_none=False, dumps=None)` |
| JSON Schema | `ConcreteSpec.json_schema(*, mode="input" | "output")` |
| OpenAPI | `ConcreteSpec.openapi_schema(*, mode="input" | "output")` |

## Contract operations

`Contract.validate`, `Contract.from_python`, `Contract.from_json`,
`Contract.to_python`, `Contract.to_json`, `Contract.json_schema`, and
`Contract.openapi_schema` operate on the retained annotation. A policy supplied
to `Contract(...)` is retained; an explicit per-call input policy replaces it.

## Introspection domain

`talea.introspection` exports `FieldInfo`, `DerivationInfo`, `SpecInfo`,
`ContractInfo`, `inspect_spec`, and `inspect_contract`. See
[Introspection](introspection.md).

## Error and validation domains

`talea.errors` additionally exposes `ErrorBranchData` and `ErrorLocation`.
`talea.validation` exposes the advanced `Validator`, `compile_validator`, and
compatibility `CustomValidationError` contracts. Applications normally use the
root `ValidationError`; compiler consumers must compile only a canonical schema
and must not create a competing annotation interpreter.

## Declaration and schema domains

`talea.declaration` intentionally exposes `SpecField`, `SpecSchema`,
`ValidationHook`, `SerializationHook`, and `MISSING_DEFAULT` for advanced
structural consumers. `talea.schema` exposes immutable nodes:
`Schema`, `AliasSchema`, `ConstrainedSchema`, `PrimitiveSchema`,
`SpecReferenceSchema`, `TypeSchema`, `LiteralValue`, `LiteralSchema`,
`EnumSchema`, `SequenceSchema`, `MappingSchema`, `FixedTupleSchema`,
`VariadicTupleSchema`, `UnionSchema`, `DataclassField`, `DataclassSchema`,
`TypedDictField`, `TypedDictSchema`,
`NamedReferenceSchema`, `NamedSchemaIdentity`, `TaggedUnionBranch`, and
`TaggedUnionSchema`; tags `PrimitiveKind`, `SequenceKind`, and `TypeCheckMode`;
and declaration functions/errors `resolve_annotation`,
`AnnotationResolutionError`, `ConstraintDeclarationError`, and
`TaggedUnionDeclarationError`.

These domain values are public for framework tooling and architectural
extension. They are structural truth, not a generic runtime validation engine.
No current `__all__` export is classified as an accidental internal leak.

## `Spec`

Purpose: declare a named immutable record whose annotations own structural
truth. Concrete subclasses are keyword-only, slotted, and frozen after atomic
validation.

The direct constructor accepts canonical Python field names and strict Python
values. `from_mapping()` and `from_json()` accept external aliases and an
optional `ResourcePolicy`. Successful operations return the concrete subclass;
no partial object is published on failure.

```python
class User(Spec):
    id: int
    name: str


trusted = User(id=1, name="Ada")
external = User.from_json('{"id":2,"name":"Grace"}')
```

Construction and input can raise `ValidationError`; governed external input can
raise `ResourceLimitError`. Output methods can raise `ValidationError` when
mutable current state no longer satisfies the contract and
`SerializationError` when safe projection/encoding fails. Schema methods raise
`SchemaProjectionError` when a mode cannot be described honestly.

Use `Spec` for nominal records with attributes, defaults, methods, inheritance,
hooks, or whole-object invariants. Use `Contract` when a wrapper class would add
no domain meaning. Detailed semantics: [Specs](../concepts/specs.md), [fields](../field-semantics.md),
[input](../input-boundaries.md), and [serialization](../serialization.md).

## `Contract`

Signature: `Contract[T](annotation, /, *, policy=None)`.

Construction resolves a supported runtime annotation, compiles strict
validation, and retains the immutable policy. It raises
`AnnotationResolutionError` for unsupported or incomplete declarations. The
remaining operations compile lazily and are retained by that Contract:

| Method | Input | Result |
| --- | --- | --- |
| `validate(value)` | already-valid Python form | same validated root |
| `from_python(value, *, policy=None)` | external structural Python form | converted/detached `T` |
| `from_json(data, *, loads=None, policy=None)` | JSON text/bytes/bytearray | converted `T` |
| `to_python(value)` | valid `T` | detached Python representation |
| `to_json(value, *, dumps=None)` | valid `T` | JSON text |
| `json_schema(*, mode="input")` | retained annotation | fresh Draft 2020-12 document |
| `openapi_schema(*, mode="input")` | retained annotation | fresh Schema Object/components fragment |

An explicit per-call policy replaces the retained policy; it is not merged.
Contract attributes are read-only. See [Arbitrary contracts](../contracts.md)
for TypedDict, generic, recursive, tagged, and policy examples.

## derive_spec and apply_patch

```python
derive_spec(
    source,
    *,
    include=None,
    exclude=None,
    partial=False,
    name=None,
    module=None,
    qualname=None,
) -> type[Spec]
```

`source` must be a concrete Spec class. `include` and `exclude` are mutually
exclusive iterables of canonical Python field names; source declaration order
wins. Retained annotations, constraints, aliases, metadata, field-local hooks,
serializers, and applicable defaults/factories come from canonical source
truth. `partial=True` makes every retained field omittable without adding
`None` or running omitted defaults/factories.

Unknown, duplicate, non-string, or conflicting selections fail at derivation
time. Open generic origins are incomplete; specialize first. Every call returns
a distinct normal Spec class, so applications should declare and retain the
result rather than deriving per request.

```python
UserPatch = derive_spec(User, exclude=("id",), partial=True)
patch = UserPatch.from_json('{"name":"Grace"}')
updated = apply_patch(User(id=1, name="Ada"), patch)
```

`apply_patch(instance, patch)` requires instances and an exact source/partial
relationship. It forwards only present canonical fields through
`copy.replace()`, returns the concrete source type, and reruns changed-field and
whole-Spec validation. It raises `TypeError` for incompatible patches and
`ValidationError` for an invalid complete candidate. It does not serialize,
deep-copy, merge dictionaries, or rerun defaults. See [PATCH and
presence](../presence-derived-contracts.md).

## `ResourcePolicy` and `ResourceLimitError`

```python
ResourcePolicy(
    max_input_bytes=8 * 1024 * 1024,
    max_depth=64,
    max_nodes=100_000,
    max_errors=100,
)
```

The policy is a frozen, slotted per-operation value. Every dimension accepts a
positive exact integer or `None`; invalid values raise `ValueError`.
`max_input_bytes` applies to encoded JSON before decoding, `max_depth` counts
structural containers, `max_nodes` counts actual compiled visits, and
`max_errors` limits retained independent failures.

Size, depth, and node exhaustion raise `ResourceLimitError` with stable
`code`, `limit`, and `observed`; the exception retains no input. Error-budget
termination raises `ValidationError` with `truncated=True`. Policy does not
sandbox callbacks, codecs, Mapping methods, patterns, output, or tooling. See
[Resource and security](../resource-security.md).

## `Discriminator`

Signature: `Discriminator(name: str)`. The frozen marker belongs in
`Annotated[BranchA | BranchB, Discriminator("type")]`. `name` must be a
non-empty string and may identify the common canonical field or external alias.

Each branch must be a Spec or each must be a TypedDict, with the common field
required and declared as one exact `Literal` tag. Tags come from those fields;
there is no second tag declaration. Invalid branch shapes, collisions, mixed
families, and open generic branches raise `TaggedUnionDeclarationError` during
resolution. Runtime missing/unknown tags use stable discriminator error codes.
See [Tagged unions](../tagged-unions.md).

## `create_spec`

`create_spec(name, fields, *, defaults=None, factories=None, base=Spec,
module=None, qualname=None, doc=None, namespace=None, metadata=())` creates a
normal Spec subclass from trusted evaluated runtime annotations.

Defaults and factories are separate mappings and cannot overlap. `namespace`
may contain ordinary methods and decorated hooks but is trusted code. String
annotations are rejected, names are validated, and Talea does not install the
class in `sys.modules`; normal pickle importability rules remain the caller's
responsibility. Because runtime fields cannot become a static constructor
signature, the broad return type is deliberate. See [Dynamic
Specs](../dynamic-utilities.md).

## Declaration decorators and `field`

- `field(default_factory=...)` owns an omitted-field factory. The callable runs
  once per construction and never during schema projection.
- `@transform(field)` runs before structural validation and may deliberately
  broaden one input domain. Callback failures become transform errors.
- `@check(*fields)` asserts one field or a complete field set after structural
  validation. Callback failures become field/spec-check errors.
- `@serialize(field)` changes that field's output projection. Failures become
  `SerializationError`.

Decorator target names, signatures, inheritance, and ordering are validated at
declaration. Arbitrary callback domains can prevent honest input/output schema
projection. See [Custom validation](../custom-validation.md) and
[serialization](../serialization.md).

## Metadata and constraints

`Alias`, `Title`, `Description`, `Examples`, `Deprecated`, `ReadOnly`,
`WriteOnly`, and `Sensitive` are immutable `Annotated`/declaration markers.
Read/write markers describe schemas but do not enforce runtime access;
Sensitive redacts Talea-owned errors and repr but does not suppress successful
serialization.

`Gt`, `Ge`, `Lt`, `Le`, `MultipleOf`, `MinLength`, `MaxLength`, and `Pattern`
are immutable built-in constraints. They validate their base type and detect
contradictions during resolution. Runtime failures use stable codes and
structured context. See [Metadata](../metadata-security.md) and
[constraints](../constraints.md).

## Exceptions and machine handling

Catch `ValidationError` for contract-invalid values and consume `errors()`.
Catch `ResourceLimitError` separately for boundary budgets. Catch
`SerializationError` for output projection/codec failure and
`SchemaProjectionError` for unknowable/unsupported standards projection.
Declaration errors should normally fail startup or import rather than become
request responses.

`ErrorCode` values and `ErrorData` keys are public machine contracts. Rendered
`str(error)` is bounded human presentation and must not be parsed. The [error
reference](../error-experience.md) documents all codes, nested locations,
branches, redaction, related locations, and truncation.
