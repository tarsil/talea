# Known limitations

This is the authoritative limitations list for Talea's ongoing 0.x series.

## Current capability limits

- custom transforms, checks, serializers, and codecs are synchronous trusted
  callables; Talea does not sandbox them;
- `validate_call` supports complete synchronous and asynchronous Python
  binding, awaited-return validation, and the method/descriptor surface;
  generators, async generators, and callable instances are unsupported,
  runtime generic-function specialization is unsupported, and lost local
  deferred annotation names may be unrecoverable;
- NamedTuple, attrs, ordinary-class, and ORM-object mapping are not part of
  core; Settings has a separate concrete-Spec application boundary;
- dataclass `InitVar`, incompatible constructors, Talea method hooks, tagged
  dataclass unions, and interpretation of `dataclasses.field(metadata=...)` are
  not supported;
- JSONL, streaming JSON, and per-item streaming failure isolation are absent;
- directional Spec derivation is shallow; nested Specs, dataclasses, tagged
  branches, and TypedDicts are not implicitly rewritten;
- open generic Specs, aliases, TypedDicts, and dataclasses must be concretely
  specialized before execution;
- an arbitrary transform can make input schema projection unknowable;
- a serializer without declared `output=` makes output schema projection
  unknowable;
- nested serialization selection uses canonical field names only; aliases are
  output keys, not selector identities;
- OpenAPI's Discriminator Object can point to only the current external
  discriminator property; surrounding input Schema Objects still validate
  legacy discriminator keys, but documentation UIs may display only the
  canonical hint;
- TypedDict keys do not consume `Alias(..., legacy=...)`; migration names are
  supported by Spec fields, stdlib dataclass fields, and compatible tagged Spec
  discriminators;
- nested selection has no per-index sequence selection, mapping-key selection,
  wildcards, predicates, path expressions, or query-language callbacks;
- serializer output without `output=` is a leaf; declared output contracts use
  the normal structural selection rules;
- recursive selection requires an explicitly finite selection tree;
- heterogeneous fixed tuples require one subtree valid for every position;
- structured `set`/`frozenset` member selection is JSON-only because projected
  dictionaries cannot preserve hashability in Python output;
- dynamic `create_spec()` and `derive_spec()` results type as `type[Spec]`
  because Python cannot infer runtime field mappings;
- no automatic converters are provided for Pydantic, attrs, or foreign schema
  systems.
- plain `Contract(ArbitraryClass)` remains unsupported unless an explicit
  `Representation` annotates that position; there is no registry, discovery,
  generic Representation factory, custom format namespace, or custom error-code
  namespace;
- Representation and serializer callbacks are synchronous trusted Python;
  undeclared `@serialize` results remain opaque to nested selection and output
  schema;
- Python 3.15 uses `TypeForm` for `Contract`, `Representation.input` and
  `.output`, `create_spec` field values, and declared serializer outputs;
  Python 3.14 keeps a less precise `object` fallback without
  `typing_extensions`; static TypeForm acceptance still does not imply Talea
  runtime support for an open generic or unsupported type expression;
- Settings roots must be concrete complete Specs; attrs, dataclasses,
  TypedDicts, NamedTuples, ordinary classes, open generic origins, and partial
  derived Specs are not settings roots (supported nested values still work
  through a Spec root);
- Settings provides no `.env` parser, YAML/INI/arbitrary JSON file, remote
  secret manager, network/database source, custom source callback or registry,
  profile engine, automatic CLI, framework startup hook, watcher, async I/O,
  live mutable object, ContextVar, or global singleton;
- environment and secret names flatten only finite Spec/dataclass/TypedDict
  paths with `__`; containers, tagged unions, recursive back-edges, and
  structurally ambiguous unions use JSON textual leaves;
- a general union whose branches share one JSON shape may not textually express
  every branch type; Settings deliberately adds no coercion priority or branch
  query language;
- Settings snapshots one environment Mapping and bounded file contents per
  operation, but cannot guarantee atomicity against unrelated process, thread,
  deployment, or filesystem mutation across multiple sources;
- secret directories are flat UTF-8 sources with in-root symlink support and
  one terminal newline removal; recursive walking, binary secret values, and
  escaping symlinks are unsupported;

## Deliberate boundaries and trust model

- regex execution has no timeout and can exhibit pattern-dependent CPU cost;
- custom Mapping objects, codecs, callbacks, dataclass constructors,
  `__post_init__`, descriptors, and `__getattribute__` can execute arbitrary
  trusted application code;
- a representation loader may mutate its accepted input and Talea cannot roll
  back those application side effects; subsequent Talea validation still uses
  the values already extracted by the compiled operation;
- a representation dumper may mutate its internal value, reenter Talea, log
  secrets, or amplify a small value into large output; Talea calls it once but
  cannot roll back or resource-govern that application work;
- callbacks have no timeout or cancellation boundary, and callback CPU,
  allocation, I/O, and output size remain application-owned;
- async callable boundaries add no timeout, retry, task, or cancellation
  policy; application coroutine I/O, task creation, cleanup, and resource use
  remain application-owned;
- Talea validates each declared direction but does not guarantee
  `dump(load(value)) == value`, `load(dump(value)) == value`, or byte-for-byte
  round trips;
- `Sensitive` governs Talea-owned failures but cannot alter a dataclass's own
  generated repr; applications must use `field(repr=False)` where needed;
- resource policies govern external input, not strict trusted construction,
  output size, schema tooling, or callback/application lifecycle work;
- `ReadOnly` and `WriteOnly` do not alter ordinary source-Spec construction,
  input, or output; only explicit directional derivation changes class shape,
  and it is not authentication, authorization, or persistence protection;
- cyclic runtime graphs are rejected by external conversion and serialization;
  strict current-state validation uses active-identity cycle handling;
- no ORM-style attribute extraction or arbitrary object-to-dataclass conversion
  is performed;
- no process-global Contract, codec, dataclass, Settings, or source registry is provided.

## Python and platform constraints

- Python 3.14 or newer is required;
- Python recursion limits can still apply to declaration or trusted custom code
  outside compiled resource-governed traversal;
- recursive/local dataclass annotations must be resolvable through Python's
  normal module annotation namespace; Contract does not inspect caller frames
  to reconstruct function-local names;
- directly decorated callables can resolve a live local declaration scope, but
  a deferred function-local name cannot be recovered after that scope is gone;
- concrete path-class availability follows the running platform;
- compatibility, deprecation, and long-term support policy remain intentionally
  evolvable across the 0.x series.

## Rejected or separate concerns

Rejected core features include `Any`/`object` passthrough contracts, abstract
container conversion, process-global registries, and silent ORM attribute
extraction because they weaken or obscure the explicit contract. Settings has
its separate `talea.settings` owner; streaming protocols, framework routing,
foreign schema conversion, and additional provider protocols require their own
demonstrated owners rather than implicit expansion of `Contract` or Settings.
