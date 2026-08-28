# Security architecture

Talea treats external input as hostile while treating application declarations,
callbacks, codecs, and ordinary Python execution as trusted code.

## Trust boundaries

| Surface | Talea governs | Caller still owns |
| --- | --- | --- |
| default JSON decode | size, duplicate keys, non-standard numbers, parse failures | transport deadlines and upstream limits |
| Mapping/JSON conversion | depth, node work, aggregated errors, cycles | behavior of custom Mapping implementations |
| strict construction/validation | schema correctness | whether the caller's Python object is trusted |
| callbacks and serializers | conversion of raised failures into safe Talea errors where documented | callback side effects, blocking, I/O, secret logging |
| custom codecs | Talea projection after/before codec boundary | codec safety, CPU, memory, exceptions |
| regex constraints | declaration-time compilation and safe binding | catastrophic backtracking; no timeout is provided |
| output and schema tooling | cycle rejection and explicit projection failures | output size and tooling resource budgets |
| dataclass Contract | declared stored fields, exact identity, structured boundaries | constructor, post-init, descriptors, generated repr |
| nested output selection | canonical schema validation, immutable normalization, direct projection | authorization to request or disclose fields |
| represented custom values | declared input/output result validation, exact-once callback transport, Sensitive error policy | callback CPU, memory, mutation, I/O, logging, and output amplification |

The finite default policy is 8 MiB JSON transport, depth 64, 100,000 compiled
node visits, and 100 aggregated errors. It reduces Talea-owned unbounded work;
it is not a denial-of-service prevention guarantee.

## Generated code safety

Generated source comes only from compiler templates. User values are bound as
globals. Dynamic class/module/qualified names are normalized and validated;
pattern text and aliases are not inserted into source. Repository tests cover
quotes, newlines, malicious-looking names, aliases, patterns, and callback
identities.

Nested selector keys are treated as data and validated against canonical field
truth before compilation. Caller-owned dictionaries and sets are copied into
an immutable tree before field access. There is no global selector cache; each
Spec class retains at most 32 immutable compiled plans and evicts the oldest
selected plan on overflow. Very broad or deep selectors can still consume
caller-owned output CPU and memory under normal Python limits;
`ResourcePolicy` intentionally governs hostile input, not application-owned
output.

Annotation resolution uses Python's supported annotation machinery and retained
definition namespaces. Talea does not provide an API that evaluates arbitrary
untrusted annotation strings. Application class bodies and imported modules are
trusted Python code, as they are for any annotation-driven library.

## Sensitive data

`Sensitive` redacts Talea-owned validation and serialization failures, Spec
representation, and retained callback causes under the marked boundary. It does
not omit values from successful serialization. `WriteOnly` remains distinct:
ordinary source Specs still serialize it, while an explicitly derived output
Spec structurally excludes it. Likewise, an input-derived Spec structurally
excludes `ReadOnly` fields. The derived classes are contract shapes, not access
control: the application still owns authentication, authorization, persistence
permissions, and output selection at each endpoint.

Directional selection uses normalized canonical field metadata during class
derivation. Removed fields have no constructor slot, alias, Mapping/JSON input
path, serializer hook, repr entry, introspection field, or schema property on
that derived class. Input partials may patch their exact source; output partials
are rejected by `apply_patch` so read-only values cannot re-enter through a
read-oriented view. Nested Specs are not recursively rewritten, so applications
must explicitly derive nested boundary shapes when required.

`include` and `exclude` control output projection only. They do not establish
the caller's identity, role, tenant, consent, or permission to see a field.
Applications must authorize the chosen projection before serialization;
`Sensitive` is failure-redaction metadata, not an output access-control rule.

For dataclasses, Talea cannot control the class's own generated `repr` without
mutating the application type. A sensitive dataclass field may therefore appear
in ordinary `repr(instance)` even though Talea-owned failures redact it. Use
`dataclasses.field(repr=False)` when the application representation must omit
that value. Dataclass constructors, `__post_init__`, custom `__getattribute__`,
and declared descriptors are trusted application execution, not sandboxed
input machinery.

Representation callbacks are subject to the same trust boundary. They are
synchronous and may reenter Talea, but compilation/publication locks are not
held while they execute. `ResourcePolicy` covers external input traversal, not
callback work or output size. See [Custom domain
representations](../custom-representations.md) for the full contract.

## Supply chain

`pyproject.toml` declares `dependencies = []`: Talea has zero required runtime
dependencies and production code is pure Python. Tests, benchmarks, typing,
builds, and documentation use third-party development tools. This narrows the
runtime supply-chain surface but is not a general security certification.

See [ResourcePolicy](../resource-security.md) for operation details and [Known
limitations](limitations.md) for ungoverned surfaces.

## Review concrete attacks, not only controls

The [executable hostile-input scenarios](../resource-security.md#executable-hostile-input-scenarios)
cover oversized JSON before decoding, excessive depth, exhausted work nodes,
truncated broad invalid input, secret-bearing errors, and custom Mapping code.
They demonstrate both the bounded Talea behavior and the point where arbitrary
Python remains outside the library's control.

For an application review, ask which endpoints can receive the contract, what
the largest valid shape is, whether upstream transport limits run before a
complete body is allocated, which callback/codec/pattern code executes, where
`errors()` and repr are logged, and which output type allow-lists secrets. Test
those answers with production-shaped payloads rather than relying on default
numbers alone.

Talea does not claim regulatory compliance, process isolation, deadline
enforcement, safe untrusted pickle, safe arbitrary regular expressions, or
safe execution of user callbacks. Zero required runtime dependencies narrows
one supply-chain dimension; it does not replace provenance, signed releases,
dependency review for development/build tools, or the embedding application's
threat model.
