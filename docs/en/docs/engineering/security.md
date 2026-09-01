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
| NamedTuple Contract | exact nominal identity, direct slot validation, positional list/tuple or JSON-array conversion, arity, generated-constructor compatibility, resource accounting | trusted annotation execution, ordinary class methods, application logging and successful output disclosure |
| nested output selection | canonical schema validation, immutable normalization, direct projection | authorization to request or disclose fields |
| represented custom values | declared input/output result validation, exact-once callback transport, Sensitive error policy | callback CPU, memory, mutation, I/O, logging, and output amplification |
| declared serializer output | complete result validation, exact-once callback transport, callback-free schema/selection discovery, Sensitive cause suppression | callback CPU, memory, mutation, reentrancy, I/O, logging, and output amplification |
| validated callables | generated-source safety, native binding shape, strict arguments and returns, Sensitive failure policy | function CPU, memory, I/O, locks, side effects, mutation, recursion, exceptions |
| application Settings | finite source names, bounded file reads, collision rejection, leaf precedence, secret-error redaction, atomic snapshot publication | process/filesystem mutation by other code, file permissions, custom Mapping behavior, deployment integrity |
| incremental Contract items | pulled-item and invalid-item limits, indexed errors, Sensitive redaction, no hidden accumulation | source I/O/lifetime, callback work and logging, explicit unbounded policy, wall-clock timeout |

The finite default policy is 8 MiB JSON transport, depth 64, 100,000 compiled
node visits, and 100 aggregated errors. It reduces Talea-owned unbounded work;
it is not a denial-of-service prevention guarantee.

## Generated code safety

Generated source comes only from compiler templates. User values are bound as
globals. Dynamic class/module/qualified names are normalized and validated;
pattern text and aliases are not inserted into source. Repository tests cover
quotes, newlines, malicious-looking names, aliases, patterns, and callback
identities.

Standards projection likewise treats current and legacy property names as
inert dictionary keys. It escapes references through the existing definition
owner, never interpolates names into JSON Pointers, rereads annotations, or
executes factories and callbacks. Migration constraints grow with the sum of
accepted names, not the product of per-field alternatives; declarations remain
trusted, but the projector does not introduce a combinatorial amplification
path. Sensitive defaults and examples retain the ordinary omission policy on
every accepted spelling.

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

NamedTuple resolution accepts only annotated `typing.NamedTuple` declarations,
not classes that merely expose `_fields`, unannotated `collections.namedtuple`
types, arbitrary tuple subclasses, or custom sequences. Warm operations consume
the frozen canonical schema and direct numeric slots; they do not reread
annotations, invoke ordinary methods, call `_asdict()`, or use a global
registry. An incompatible mutated constructor is rejected before external data
can reach it. The outer tuple does not make mutable descendants trusted, and
external positional traversal shares normal `ResourcePolicy` accounting.

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
At a `Sensitive` represented input boundary, Talea normalizes ordinary loader
exceptions into a redacted `ValidationError` with no retained cause; a
non-sensitive loader still uses `ValueError` as its declared rejection signal
and propagates other application defects.

Declared serializer output contracts prevent a callback result from drifting
from published output schema, but they do not sandbox the callback. Talea
normalizes invalid nested selectors before invoking application code and never
invokes a serializer for JSON Schema, OpenAPI, or introspection. Callback
exceptions and invalid declared results at a Sensitive boundary suppress unsafe
causes. A callback may still mutate its source, reenter serialization, return a
huge graph, or log secrets; output remains outside input `ResourcePolicy`
governance.

## Settings threat model

The explicit [Settings boundary](../settings.md) adds source acquisition work
without changing the strict Mapping owner:

| Threat | Ownership and response |
| --- | --- |
| oversized process environment | Talea checks the snapshot entry count before known-name decoding; allocation and mutation by unrelated process code remain application/platform-owned |
| oversized or parser-amplifying TOML | Talea performs a bounded read before `tomllib`; the standard-library parser owns syntax |
| excessive secret files | Talea counts flat non-directory entries, including unknown files, before reading selected values |
| oversized secret | Talea reads at most the configured limit plus one byte before UTF-8 or schema-directed decoding |
| invalid encoding | strict UTF-8 fails the operation and publishes no snapshot |
| symlink/path confusion | flat file symlinks may support atomic-writer mounts, but every resolved target must remain beneath the resolved explicit root |
| source-name, alias, delimiter, or case-fold collision | plan collisions reject before loading; multiple accepted names within one source return `alias_conflict` without comparing values |
| secret leakage | provenance retains no values or exact file paths; validation failure from a secret-backed load is redacted and loses callback causes |
| provenance leakage | the baseline contains canonical paths and source kinds only; plan introspection contains names but no paths, snapshots, or contents |
| partial-load state | merge, buffers, provenance, and counters are operation-local; failure cannot mutate an earlier snapshot |
| concurrent source mutation | Talea snapshots the environment Mapping and reads bounded file bytes once per load; cross-source filesystem transactions are unsupported |
| hostile override Mapping | Mapping methods are trusted application execution; the final detached structure still receives ordinary depth/node/error budgets |

Source filenames and environment keys are inert data rather than generated
source. Validation locations remain canonical external field paths. A normal
acquisition `OSError` may identify the explicit application-provided path; it
never contains a setting value. Unknown environment variables and secret
filenames are ignored as values, though secret files still count toward the
flat directory limit.

Kubernetes projected Secrets and ConfigMaps commonly use visible-key symlinks
through `..data` to a version directory. Requiring final targets to remain
beneath the resolved mount root permits that layout without allowing a key to
escape the configured directory. Talea does not authenticate the provider,
lock the directory, guarantee atomic reads across files, or replace operating-
system access control.

## Incremental iterable threat model

An application-owned iterable may be infinite, huge, all-invalid, expensive,
stateful, or fail between values. `ItemPolicy.max_items` bounds pulled records
and `max_invalid_items` bounds continued invalid records. Per-item external
depth, node, and detail work remains governed independently by
`ResourcePolicy`; continuation never catches either resource failure.

Talea pulls no item speculatively, retains no prior result/error collection,
does not retry, and applies canonical Sensitive redaction before an error
reaches the callback. The callback receives no separate rejected-item argument;
ordinary non-sensitive `ValidationError` facts remain available. The iterable
and callback remain trusted application execution: either can block, allocate,
perform I/O, mutate state, or log secrets, and Talea supplies no timeout or
sandbox. Source and callback exceptions propagate unchanged. The caller owns
cursor/file/transaction cleanup and must close it explicitly when early
termination requires deterministic release.

## JSON Lines threat model

JSON Lines adds hostile transport concerns before a Python value exists.
`JsonlPolicy.max_line_bytes` bounds one record before parsing, while an
optional finite `max_total_bytes` bounds aggregate UTF-8 transport. The shared
`ItemPolicy` counts every pulled physical record and every continued malformed
or decoded-invalid record. Resource exhaustion is terminal and cannot enter a
continuation callback.

Bytes use strict UTF-8; text must be UTF-8 representable. BOMs, blank records,
multiline source units, duplicate keys, non-finite numbers, malformed syntax,
and Python's protected oversized integer conversions fail before Contract
validation. `JsonlError` stores category and safe line/column facts only: no
raw record, duplicate key, non-finite token, decoder exception, or traceback
text. Decoded failures then use ordinary Sensitive-aware `ValidationError`.

The source and both synchronous callbacks remain trusted application code.
Talea does not authenticate or open files, decompress data, own sockets, bound
blocking I/O, impose callback timeouts, or close caller resources. Explicitly
unbounded total bytes or items are an application decision. See [JSON Lines
input](../jsonl-input.md) for the complete operational contract.

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
