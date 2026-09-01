# Application settings

`talea.settings` loads a concrete Talea `Spec` from explicit application
configuration sources. It is a boundary around the existing model and input
machinery, not a second model hierarchy. The resulting settings value is an
ordinary immutable Spec instance: it can use the usual typing, validation,
serialization, JSON Schema, and OpenAPI operations.

Import the domain explicitly:

```python
from talea.settings import Settings
```

`import talea` does not import Settings, TOML, filesystem, or environment
machinery. No settings symbol is exported from the root package.

## When to use it

Use Settings when an application has one concrete configuration model and
needs deterministic composition of:

- an explicit Python Mapping override;
- a fresh process-environment snapshot;
- one explicit flat local-secrets directory;
- one explicit TOML file;
- normal Spec defaults and factories.

Settings does not own command-line parsing, `.env` syntax, deployment
profiles, remote secret managers, framework startup, file watching, or mutable
global configuration. Those systems may produce a Mapping or arrange the
explicit files and environment that a Settings plan consumes.

## Complete production example

{!> ../../../docs_src/tutorials/production_settings.py !}

The retained `Settings(ServiceSettings, ...)` value is an immutable source
plan. It stores the model, normalized source names, paths, prefix, case policy,
and resource policy. It never stores an environment snapshot, file contents,
secret values, a previous merge, or a loaded model.

## Deterministic precedence

The only source order is:

1. explicit override Mapping;
2. environment;
3. secrets directory;
4. TOML;
5. Spec defaults.

Precedence is applied to canonical field leaves. A higher source replaces only
the leaf it supplies. If TOML supplies `database.host` and `database.port`, and
the environment supplies only `database.port`, the TOML host survives. An
empty higher nested Mapping contributes no leaves and erases nothing.

Aliases do not create a second precedence system. Within one source, supplying
both a current and historical name is always `alias_conflict`, even when the
values compare equal. Across two sources, the higher source wins after both
names resolve to the same canonical leaf.

Defaults are not copied into the source map. The final merged Mapping is sent
to `Spec.from_mapping()`, so omitted static defaults and factories retain their
normal owner. A factory runs exactly once only when construction actually needs
it; plan creation and source inspection never call it.

## Environment

Every `load()` copies its environment input once. With
`environment=None`, the source is `os.environ`. A caller may pass a finite
`Mapping[str, str]` for tests and controlled embedding. Settings never calls
`os.reload_environ()`, never mutates `os.environ`, and never retains a live
`os._Environ` object.

The plan accepts an explicit prefix; its default is the empty string. Prefixes
are never derived from a class name or module:

```python
loader = Settings(AppConfig, prefix="APP_")
```

Nested object fields use the fixed `__` delimiter:

```text
APP_DATABASE__HOST
APP_DATABASE__PORT
```

Only Spec, dataclass, TypedDict, and optional forms of those object shapes are
flattened. There is no list index, mapping key, wildcard, escape, or query
grammar. Containers, tagged unions, and recursive back-edges are textual
leaves.

`case_sensitive=False` is the default and uses Unicode `casefold()` on both
compiled names and supplied keys, independent of platform behavior. Any
collision after normalization rejects the plan or load; differently cased
keys never gain an implicit order. `case_sensitive=True` requires the exact
declared alias/field spelling after the exact supplied prefix.

Unknown environment names are ignored. This permits a process to have
unrelated variables without treating its entire environment as the settings
schema. Known names are compiled once and looked up through one normalized
snapshot rather than scanning fields for each key.

## Textual conversion

Environment variables and secret files contain text. Conversion belongs only
to this settings boundary; `Spec.from_mapping()` remains strict and does not
begin accepting strings for integers, booleans, dates, or other Python values.
After textual syntax is decoded, normal Mapping input still owns constraints,
Representations, nested conversion, hooks, errors, and final construction.

The exact scalar policy is:

| Schema | Accepted text |
| --- | --- |
| `str` | exact text, including whitespace and the word `null` |
| `int` | JSON-style base-10 integer syntax, with no leading zero or surrounding whitespace |
| `float` | finite JSON-style decimal/exponent syntax |
| `bool` | exactly `true` or `false` |
| `Decimal` | finite decimal text |
| `UUID` | standard UUID text |
| `date`, `datetime`, `time` | their standard-library ISO formats |
| `timedelta` | Talea's ISO 8601 duration subset, such as `P2DT3H4M5.25S` |
| `bytes` | canonical padded RFC 4648 base64 |
| IP address/network/interface | standard `ipaddress` constructor text |
| path types | the corresponding pathlib constructor text |
| `Enum` and `Literal` | their exact JSON scalar representation; string members also use exact text |

For `T | None`, the exact token `null` means `None`; other text follows `T`.
For a plain `str`, `null` remains the four-character string. General unions
use JSON syntax to identify a unique JSON-shaped branch. If multiple branches
share a shape, Settings does not invent coercion priority; the decoded JSON
value proceeds to ordinary Talea union validation.

Lists, sets, frozensets, tuples, mappings, recursive objects, tagged unions,
and other structural leaves use JSON text. There is no CSV-like delimiter
language. JSON-native values remain strict: `list[int]` accepts `[1, 2]`, not
`["1", "2"]`. Standard JSON string representations inside containers, such
as UUID or base64 bytes, are decoded from the same canonical schema truth.

A `Representation` field is decoded according to `Representation.input`, then
the normal Mapping boundary calls its loader exactly once. Output-only
Representations cannot be loaded from Settings because the model itself has
no Mapping input direction for that position.

## TOML

Pass one explicit file with `toml=...`. Settings performs no parent search,
home-directory search, filename convention, profile selection, or multi-file
merge. A configured missing file raises `FileNotFoundError`; a directory raises
`IsADirectoryError`; invalid UTF-8 and malformed input raise value-free
`ValueError`s. The malformed error retains only its source kind and safe
line/column, never the complete TOML document retained by the stdlib decoder.

TOML is parsed only by the standard library `tomllib`. Its integers, floats,
booleans, arrays, dates, times, and nested tables remain typed Python values
and proceed directly to strict Mapping validation. They are not converted to
strings and reparsed. Current and historical aliases are recognized at each
nested object segment with the configured case policy.

The byte limit is checked with a bounded read before `tomllib` sees the input.
Concurrent application or deployment replacement of the file is not a
filesystem-transaction guarantee; one load owns the bounded bytes it read and
publishes either a complete snapshot or no value.

## Local secrets directory

Pass one explicit directory with `secrets=...`. The directory is flat:
one file name is one source name, and `__` expresses nested object fields.
The environment prefix does not apply to secret filenames. Unknown files are
ignored as values but count toward the file limit; subdirectories are not
walked.

Files are strict UTF-8 text. Settings removes exactly one terminal `\n`, or
one terminal `\r\n`, to support common mounted-secret files. It does not call
`.strip()`: leading/trailing spaces and additional lines remain data.

Symlinked files are allowed when their resolved target remains inside the
resolved explicit root. This supports Kubernetes atomic-writer layouts while
rejecting a secret filename that escapes to an arbitrary target. The explicit
root itself may be a symlink. Broken links fail the load, directories are not
recursed, and each selected target is read with a byte bound. Applications and
platforms still own concurrent filesystem mutation and deployment-level
permissions.

All validation failures from a secret-backed load are returned as a redacted
`ValidationError`, even if an application forgot to mark the target field
`Sensitive`. The returned error retains no secret content or callback cause.
`Sensitive` remains important for successful snapshots, environment/TOML/
override errors, repr behavior, and every ordinary Talea operation.

## Kubernetes and container mounts

{!> ../../../docs_src/tutorials/kubernetes_settings.py !}

The second `load()` is the reload operation. It rereads every configured
source and returns a new complete instance. The first instance remains
unchanged. There is no watcher, background task, lazy field lookup,
reload-in-place method, or global current-settings singleton.

## Provenance

`load(provenance=True)` returns `SettingsResult[T]`; otherwise it returns `T`
directly. The result holds the snapshot and an immutable Mapping from canonical
field paths to one of `override`, `environment`, `secret`, `toml`, or
`default`.

Provenance contains no values, raw Mapping, environment dictionary, file
bytes, secret contents, source object, exact environment variable, or file
path. This deliberately makes the safe baseline less detailed than a deployment
debug trace. `Settings.info` separately exposes callback-free plan facts,
including the model, prefix, delimiter, case policy, source order, and compiled
environment names; it exposes no configured paths or contents.

## Errors

Settings keeps failure ownership explicit:

| Failure | Exception |
| --- | --- |
| invalid model, prefix, case flag, policy, or partial root | `TypeError` / `ValueError` |
| source-name collision at plan creation | `ValueError` |
| current/historical or case collision within one source | `ValidationError` with `alias_conflict` |
| missing/unreadable/wrong-kind file path | normal `OSError` subtype |
| malformed TOML or invalid file UTF-8 | value-free `ValueError` |
| invalid setting value or missing field | ordinary `ValidationError` |
| source or final input budget exhaustion | `ResourceLimitError` |

Validation locations use current Talea external field paths. They never replace
those paths with environment variable names or filesystem paths. Acquisition
exceptions may contain the explicit path supplied by the application; they
never contain source values.

## Resource limits

`SettingsPolicy` owns dimensions introduced by source acquisition:

```python
SettingsPolicy(
    max_environment_entries=10_000,
    max_override_entries=100_000,
    max_override_depth=64,
    max_override_key_bytes=64 * 1024,
    max_source_names=10_000,
    max_toml_bytes=8 * 1024 * 1024,
    max_secret_files=256,  # bounds all entries enumerated from the directory
    max_secret_file_bytes=1024 * 1024,
    max_source_bytes=16 * 1024 * 1024,
    input_policy=ResourcePolicy(),
)
```

The override limits bound the settings-owned copy and source-resolution pass.
The final `input_policy` remains the existing owner of validation Mapping
depth, node, and error budgets. A caller may pass `None` for an individual
source limit only when another boundary enforces it; cyclic override mappings
are always rejected.

The environment entry limit counts values as they are iterated rather than
trusting a custom Mapping's reported length. TOML and secret limits use bounded
reads; the aggregate byte counter covers TOML, selected secret contents, every
environment name, and matched environment values. Ignored environment values
are not read into that byte budget. Override Mapping methods and concurrently
mutated custom mappings remain trusted application work, while the settings
copy and final detached structure have separate finite policies.

On platforms with descriptor-relative file opening, secret symlinks may point
within the configured directory (including Kubernetes atomic-writer layouts),
and authorization is bound to the file descriptor used for reading. Platforms
without that facility accept direct regular files and reject secret symlinks.

## Testing, CLI, and dotenv interoperation

{!> ../../../docs_src/tutorials/settings_testing.py !}

A CLI library should parse arguments first and pass its Python Mapping as the
highest-priority override. A dotenv library may parse `.env` syntax in
application code and provide a Mapping in the same way. That makes the Mapping
an explicit override—it does not make Talea an owner of CLI or dotenv parsing,
file discovery, interpolation, mutation, or precedence.

## Generics, dynamic Specs, derivation, and recursion

The root must be a concrete complete Spec. A fully specialized generic such as
`ServiceSettings[int]`, a complete `derive_spec()` result, and a concrete
`create_spec()` result work through their ordinary Spec execution. An open
generic origin and a presence-aware partial derived Spec reject at plan
creation. Settings performs no runtime TypeVar inference.

Recursive Spec, dataclass, and TypedDict schemas are finite. Environment and
secret projection descends declared object paths until the first canonical
back-edge; that back-edge becomes one JSON textual leaf. It never generates an
arbitrary maximum nesting depth. TOML and Mapping overrides can carry recursive
structure normally under `ResourcePolicy`.

Tagged unions are also JSON textual leaves, or may arrive as nested values from
TOML/override sources. Settings does not create a branch query grammar. The
ordinary discriminator owner selects and validates the branch.

## Typing and performance

`Settings(AppConfig).load()` is statically `AppConfig`.
`load(provenance=True)` is `SettingsResult[AppConfig]`. Public source and policy
types contain no `Any`. Python 3.14 and the configured Python 3.15 lane check
the same API; Settings does not require a 3.15-only syntax contract.

`task benchmark_settings` measures cold plans, repeated loads, 10/50/100-field
environment and TOML models, nested fields, case policy, aliases, precedence,
secret directories, provenance, failures, resource rejection, retention,
concurrency, and ordinary Talea canaries. It also keeps two manual baselines:
the historical narrow loader and an audited equivalent-semantics loader that
shares only the final canonical `Spec.from_mapping` boundary.

The historical approximately 2.49 µs manual result is a useful direct-access
lower bound, not an equivalent Settings comparison. It omits complete source
snapshot validation, acquisition limits, case folding, accepted-name conflict
detection, canonical Mapping materialization, `ResourcePolicy`, and Spec
construction. Earlier approximately 20.85 µs and 20.35 µs Talea results used
the historical ten-integer environment workload against that weaker baseline.

On the Campaign 28H development machine, the same Talea workload measured
approximately 6.3–7.0 µs minimum after convergence, while the audited manual
executor measured approximately 7.1 µs. The representative mixed ten-field
model measured approximately 10.0 µs versus 8.3 µs. These are reproducible
development-checkout measurements rather than cross-machine promises.

The ten-integer Talea path decomposed to about 0.17 µs for the detached source
snapshot, 0.40 µs for case-normalized names, 1.43 µs for textual decoding,
0.07 µs for canonical assignment, 1.97 µs for final `Spec.from_mapping`, and
the remaining control and materialization work. The retained plan now preselects
primitive decoders, single-environment loads materialize resolved leaves
without general multi-source merge bookkeeping, and schema-proven leaf sources
use a leaf merge. Generic structured decoding, final model construction,
source limits, and full snapshot scanning remain intentional canonical owners.
A direct-probe environment path would miss whole-source type checks, entry
limits, and accepted-name conflict detection, so source-size cost remains
linear. Provenance remains pay-for-play and costs additional canonical-path
projection and immutable publication only when requested. Cold plan work is
reported separately from warm loads and filesystem I/O.

The benchmark makes no universal fastest-settings-library claim.

## Security boundaries and non-goals

Settings bounds Talea-owned source acquisition, rejects name ambiguity, keeps
source values out of provenance, redacts secret-backed validation failures,
and publishes no partial snapshot. It cannot make process environment mutation
atomic across unrelated application threads, provide filesystem transaction
semantics, sandbox custom Mapping methods or callbacks, or stop a platform
administrator from reading process/filesystem state.

There is deliberately no `.env` parser, YAML/INI/JSON configuration file,
remote secret manager, network or database source, callback source registry,
plugin system, source merge directive, deployment profile, automatic CLI,
watcher, live mutable settings object, context variable, or global singleton.
Adding such concerns would require a separate demonstrated owner rather than a
generic provider framework inside this one.
