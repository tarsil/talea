# Performance

Talea's performance architecture is straightforward:

1. resolve annotations once;
2. compile each used operation once;
3. execute specialized Python on repeated calls.

Performance claims apply to measured workloads, not every possible contract.
Simple Specs are permanent canaries because unrelated features should impose
approximately zero cost when unused.

## Workload inventory

| Task | Measures |
| --- | --- |
| `benchmark_schema` | annotation resolution and canonical schema creation |
| `benchmark_spec` | declaration, construction, failure, scaling, instance memory |
| `benchmark_composition` | nested/inherited construction and memory |
| `benchmark_validator` | strict validator compilation and execution |
| `benchmark_types` | standard types, Literal, and constraints |
| `benchmark_hooks` | transforms/checks and unhooked canaries |
| `benchmark_errors` | error detail, rendering, projection, allocations |
| `benchmark_mapping`, `benchmark_json` | external Python and JSON boundaries |
| `benchmark_serialization` | Python projection, JSON projection, encoding |
| `benchmark_recursive_generics` | specialization and recursive execution |
| `benchmark_utilities` | Contract, create_spec, introspection, replacement |
| `benchmark_metadata` | metadata and redaction |
| `benchmark_tagged` | dispatch, boundary input/output, branch scaling |
| `benchmark_recursive_named` | recursive alias and TypedDict graphs |
| `benchmark_presence` | derived declarations, partials, patching, memory |
| `benchmark_json_schema` | standards projection, scaling, output size |
| `benchmark_resources` | policy overhead and adversarial scaling |
| `benchmark_dataclasses` | dataclass boundaries, cold work, memory, generated code, and zero-tax canaries |
| `benchmark_representation` | represented strict/input/output paths, result validation, structural selection, allocation/retention, and zero-tax canaries |
| `benchmark_callables` | direct/handwritten/compiled calls, binding comparator, structures, defaults, failures, allocations, retention, and bytecode |
| `benchmark_settings` | cold/warm plans, 10/50/100-field environment/TOML loads, nesting, aliases, precedence, secrets, provenance, failures, resources, retention, concurrency, and zero-tax canaries |
| `benchmark_incremental` | lazy strict/external items, structures, failure positions, continuation, stream limits, infinite-source stopping, retention, concurrency, and zero-tax canaries |
| `benchmark_jsonl` | JSONL framing, decoded validation, limits, errors, retention, concurrency, and zero-tax canaries |
| `benchmark_namedtuple` | strict and external positional paths, JSON, output, defaults, failures, composition, generics, recursion, large arity, cold work, allocation/retention, generated code, and existing-owner canaries |

Run one task from the repository root, for example:

```console
task benchmark_spec
task benchmark_json
task benchmark_resources
task benchmark_dataclasses
task benchmark_representation
task benchmark_callables
task benchmark_settings
task benchmark_incremental
task benchmark_jsonl
task benchmark_namedtuple
```

For release review, run every permanent benchmark task listed in
`Taskfile.yaml`. Record Python version, CPU, operating system, power mode,
process load, and exact commit. Nanoseconds from one machine are not universal.

## Comparator semantics

Benchmarks include manually written Python, dataclasses, slotted dataclasses,
attrs, Pydantic, and msgspec where the dependency is available and the workload
is meaningful. "Manually written Python" means the benchmark's explicit class,
type checks, and conversions; inspect the comparator function before treating it
as equivalent.

Pydantic may coerce or reconstruct values, msgspec uses a native implementation
and may reconstruct values, while Talea can preserve identity on strict trusted
paths. Rows are comparable only when accepted input, validation strength,
conversion, output shape, and error behavior match. The benchmark scripts print
their own labels and should be read with those semantic differences in mind.

Cold declaration/schema projection and warm validation/construction are
reported separately. Memory and allocation measurements are separate from
latency. Talea does not claim to be fastest in every workload or to have zero
overhead.

### Dataclass 0.2 boundary baseline

The accepted 0.2 same-run benchmark measured common dataclass external
boundaries close to semantically equivalent handwritten Python:

| Operation | Talea median | Equivalent handwritten median | Residual |
| --- | ---: | ---: | ---: |
| Mapping to dataclass | 420.3 ns | 370.1 ns | 1.14x |
| full strict JSON to dataclass | 1.843 µs | 1.814 µs | 1.02x |

The strict JSON comparator decodes, rejects duplicate keys and non-finite
numbers, validates the complete shape, constructs the dataclass, and enforces
the same resource semantics. Bare `json.loads()` was rejected as an
inequivalent comparator because it performs only decoding. These values are a
reproducible release baseline for the permanent `benchmark_dataclasses` task,
not a universal machine-independent performance claim.

## Cold work and warm work

Declaration and first-use compilation are cold costs. Repeated construction,
validation, input, and output are warm costs. A service that creates a Contract
inside every request turns cold work into a hot-path cost; retain stable
Contracts and derived Specs at module or application setup scope.

Generic specialization and dynamic declaration are also schema work. Concrete
specializations are cached weakly where identity matters, while independently
created Contracts retain their own compiled operations. `create_spec()` and
`derive_spec()` should not be driven by high-cardinality remote input.

The NamedTuple owner measures strict validation, list/tuple conversion, JSON
array conversion, tuple/array output, default omission, generic and recursive
records, 2/5/10/50/100-slot scaling, cold Contract construction, allocations,
retained artifacts, and specialization collection separately. Its handwritten
comparators enforce exact type, arity, and slot checks and construct the same
nominal record; a bare tuple constructor or `json.loads()` is not treated as
equivalent. The benchmark also inspects generated instructions for direct
indexing and the absence of annotation reflection or `_asdict()` calls. Results
are machine-local acceptance evidence, not a cross-platform speed ranking.

The acceptance run on CPython 3.14.3, arm64 Apple M4 Pro measured these medians
with retained artifacts and three independent samples:

| NamedTuple operation | Median |
| --- | ---: |
| strict two-slot validation | 79.3 ns |
| external list construction | 1.26 us |
| external tuple construction | 1.28 us |
| JSON array construction | 2.88 us |
| Python tuple output | 145.9 ns |
| JSON array output | 1.35 us |
| omitted trailing default | 1.17 us |
| strict 50-slot validation | 707.8 ns |
| strict 100-slot validation | 1.39 us |
| cold two-slot Contract | 94.9 us |
| cold 50-slot Contract | 825.5 us |
| cold 100-slot Contract | 1.54 ms |

The equivalent manual strict two-slot validator measured 68.4 ns and the
equivalent manual list-to-record boundary measured 452.0 ns in the same run.
The latter gap includes Talea's resource state, structured failure contract,
and retained-operation call boundary; it is recorded evidence, not hidden by a
weaker comparator.

## What “near manually written Python” means

The comparator must perform the same strict checks, construct the same kind of
value, and expose the same success/failure contract. A hand-written constructor
that checks two exact fields is a useful lower-level reference for a two-field
Spec constructor. A function that merely returns `True` is not an equivalent
comparison to nested conversion with structured errors.

Talea's target is to keep accepted hot paths near equivalent specialized Python
while preserving the documented semantics. It is not a promise that generated
Python outruns every hand-tuned function or native extension. Failure paths,
schema generation, and cold declaration intentionally optimize for different
work.

## Feature cost and zero-tax canaries

Aliases, metadata, hooks, generics, tags, recursion, partial presence, and
security policy each have dedicated benchmarks plus simple-model canaries. A
feature may cost the models that use it; it should not add registry lookups,
metadata loops, policy counters, or dispatch branches to an unrelated simple
constructor.

Mutable nested values are an intentional cost boundary. If current state can
change after construction, later output or replacement must revalidate it.
Permanently trusted immutable subgraphs may preserve identity and skip repeated
deep proof.

Represented output necessarily pays for the direct dumper call, full declared
result validation, and ordinary projection. The permanent benchmark compares
scalar output with an equivalent manual strict/dump/result-check/project path,
measures structured output and selected projection separately, and reports
retained callback/artifact memory. Models without `Representation` do not enter
that machinery.

The serialization benchmark separately measures opaque hooks, declared scalar
and structured outputs, model/container/Representation outputs, JSON output,
nested include/exclude, callback lower bounds, invalid-result and callback
failures, allocations, and retained artifacts. Generated declared-hook paths
bind the callback, schema-specialized validator, and projector directly. Hooks
without `output=` and Specs without hooks contain no declared-result branch or
runtime Schema walk.

## Measure an application workload

1. Identify the exact operation: constructor, strict validation, Mapping input,
   JSON input, Python output, or JSON output.
2. Use production-shaped depth, containers, unions, aliases, and callbacks.
3. Separate cold declaration/first-use samples from steady state.
4. Measure accepted and rejected data independently; include error projection
   only if the application consumes it.
5. Record latency distributions, allocations, and resident/instance memory
   where they matter.
6. Compare equivalent semantics and retain the benchmark fixture in source.

Do not infer JSON throughput from constructor nanoseconds. Do not infer hostile
payload resilience from happy-path throughput. The resource benchmark covers
policy overhead and adversarial scaling; the security page explains why
timeouts, concurrency, regexes, and custom callbacks remain caller-owned.

## Interpreting regressions

A material change should be reproduced on the same Python build and machine,
then narrowed with profiling/allocation evidence. Noise, power management,
background load, and specialization order can distort small timings. Repair a
real regression or record an evidence-backed tradeoff; never weaken validation,
conversion, output, or errors merely to restore a number.
