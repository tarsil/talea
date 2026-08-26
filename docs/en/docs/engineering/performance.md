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

Run one task from the repository root, for example:

```console
task benchmark_spec
task benchmark_json
task benchmark_resources
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

## Cold work and warm work

Declaration and first-use compilation are cold costs. Repeated construction,
validation, input, and output are warm costs. A service that creates a Contract
inside every request turns cold work into a hot-path cost; retain stable
Contracts and derived Specs at module or application setup scope.

Generic specialization and dynamic declaration are also schema work. Concrete
specializations are cached weakly where identity matters, while independently
created Contracts retain their own compiled operations. `create_spec()` and
`derive_spec()` should not be driven by high-cardinality remote input.

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
