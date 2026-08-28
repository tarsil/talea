# Known limitations

This is the authoritative pre-1.0 limitations list.

## Current capability limits

- custom transforms, checks, serializers, and codecs are synchronous trusted
  callables; Talea does not sandbox them;
- callable-signature and return-value validation are not implemented;
- NamedTuple, attrs, ordinary-class, ORM-object, and settings-source mapping
  are not part of core;
- dataclass `InitVar`, incompatible constructors, Talea method hooks, tagged
  dataclass unions, and interpretation of `dataclasses.field(metadata=...)` are
  not supported;
- JSONL, streaming JSON, and per-item streaming failure isolation are absent;
- `ReadOnly` and `WriteOnly` are metadata and schema projection, not runtime
  input/output enforcement;
- open generic Specs, aliases, TypedDicts, and dataclasses must be concretely
  specialized before execution;
- an arbitrary transform can make input schema projection unknowable;
- an arbitrary serializer can make output schema projection unknowable;
- `include`/`exclude` serialization accepts top-level Spec field names, not
  nested selection trees;
- dynamic `create_spec()` and `derive_spec()` results type as `type[Spec]`
  because Python cannot infer runtime field mappings;
- no automatic converters are provided for Pydantic, attrs, or foreign schema
  systems.

## Deliberate boundaries and trust model

- regex execution has no timeout and can exhibit pattern-dependent CPU cost;
- custom Mapping objects, codecs, callbacks, dataclass constructors,
  `__post_init__`, descriptors, and `__getattribute__` can execute arbitrary
  trusted application code;
- `Sensitive` governs Talea-owned failures but cannot alter a dataclass's own
  generated repr; applications must use `field(repr=False)` where needed;
- resource policies govern external input, not strict trusted construction,
  output size, schema tooling, or callback/application lifecycle work;
- cyclic runtime graphs are rejected by external conversion and serialization;
  strict current-state validation uses active-identity cycle handling;
- no ORM-style attribute extraction or arbitrary object-to-dataclass conversion
  is performed;
- no process-global Contract, codec, or dataclass class registry is provided.

## Python and platform constraints

- Python 3.14 or newer is required;
- Python recursion limits can still apply to declaration or trusted custom code
  outside compiled resource-governed traversal;
- recursive/local dataclass annotations must be resolvable through Python's
  normal module annotation namespace; Contract does not inspect caller frames
  to reconstruct function-local names;
- concrete path-class availability follows the running platform;
- the package is pre-1.0, so compatibility, deprecation, and long-term support
  policy are not yet frozen.

## Rejected or separate concerns

Rejected core features include `Any`/`object` passthrough contracts, abstract
container conversion, process-global registries, and silent ORM attribute
extraction because they weaken or obscure the explicit contract. Settings,
streaming protocols, framework routing, foreign schema conversion, and custom
domain representation protocols require separate owners rather than implicit
expansion of `Contract`.
