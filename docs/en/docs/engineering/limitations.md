# Known limitations

This is the authoritative pre-1.0 limitations list.

- custom transforms, checks, serializers, and codecs are synchronous trusted
  callables; Talea does not sandbox them;
- callable-signature and return-value validation are not implemented;
- dataclass, NamedTuple, ordinary-class, ORM-object, and settings-source mapping
  are not part of core;
- JSONL, streaming JSON, and per-item streaming failure isolation are absent;
- `ReadOnly` and `WriteOnly` are metadata and schema projection, not runtime
  input/output enforcement;
- open generic Specs and aliases must be concretely specialized before execution;
- an arbitrary transform can make input schema projection unknowable;
- an arbitrary serializer can make output schema projection unknowable;
- regex execution has no timeout and can exhibit pattern-dependent CPU cost;
- custom Mapping objects and codecs can execute arbitrary application code;
- resource policies govern external input, not strict trusted construction,
  output size, schema tooling, or callback work;
- Python recursion limits can still apply to declaration or trusted custom code
  outside the compiled resource-governed traversal;
- cyclic runtime graphs are rejected rather than serialized;
- `include`/`exclude` serialization accepts top-level field names, not nested
  selection trees;
- dynamic `create_spec()` and `derive_spec()` results type as `type[Spec]`
  because Python cannot infer runtime field mappings;
- no automatic converters are provided for Pydantic, dataclasses, or schemas;
- the package is pre-1.0; compatibility, deprecation, and long-term support
  policy are not yet frozen.

Rejected core features include `Any`/`object` passthrough contracts, abstract
container conversion, process-global Contract/codec registries, and silent ORM
attribute extraction because they weaken or obscure the explicit contract.
