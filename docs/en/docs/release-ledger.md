# Version, maturity, and support

Talea is currently version 0.3.0 and deliberately remains in the 0.x release
series. The implemented runtime is substantial, but compatibility, deprecation
windows, long-term support, and formal vulnerability-reporting governance are
not yet frozen. The 0.x series is an ongoing product stage, not an indication
that a 1.0 compatibility freeze is imminent.
Meaningful 0.4.x, 0.5.x, 0.6.x, and later 0.x releases are expected over the
coming years; there is no declared 1.0 target date.

## Implemented product surface

The release includes Specs, Contracts, strict validation, constraints,
standard-library types, defaults/factories, inheritance, recursive and generic
types, stdlib dataclass Contracts, TypedDict and PEP 695 aliases, tagged unions, Mapping/JSON input,
serialization, structured errors, metadata and redaction, presence-aware
derived/PATCH Specs, explicit input/output derived views, introspection, dynamic creation, JSON Schema Draft 2020-12,
OpenAPI 3.1-compatible projection, and finite external-input resource policies.
Talea 0.3.0 also includes root-public `Representation` contracts for explicit
custom domain types across directional input/output, standards projection,
introspection, and nested selection, plus optional declared output contracts on
field serializers.

## Deliberate boundaries

| Capability | Current disposition |
| --- | --- |
| callable argument/return validation | Not implemented; requires signature, descriptor, async, and typing policy |
| explicit ReadOnly/WriteOnly input/output Spec views | Implemented through declaration-time `derive_spec(mode=...)` selection |
| nested runtime output projection | Implemented through finite canonical-name include/exclude trees on `Spec.to_dict()` and `Spec.to_json()`; schema/OpenAPI remain unchanged |
| automatic runtime ReadOnly/WriteOnly enforcement | Deliberately absent; ordinary source-Spec behavior remains unchanged |
| NamedTuple and ordinary-class mapping | Not implemented; core is not a general object mapper |
| stdlib dataclass boundaries | Implemented through `Contract`; no ORM-style attribute extraction |
| settings/environment loading | Separate integration or package, not core |
| streaming batches and JSONL | Not implemented; materialized batches use `Contract(list[T])` |
| arbitrary annotation callbacks | Explicit per-position `Representation` contracts are implemented; no registry/discovery |
| field-local serializer output truth | Optional `@serialize(..., output=...)` contracts are implemented; undeclared hooks remain opaque |
| retained global codec/Contract registries | Rejected for core; application boundaries own retained objects |
| `Any`/`object` passthrough contracts | Rejected because they erase contract truth |
| abstract Mapping/Sequence conversion | Rejected because concrete output shape is ambiguous |
| ORM attribute extraction | Rejected for core; an integration must own lazy access and errors |
| output/schema resource governance | Caller-owned in the current threat model |

The complete operational list is on [Known limitations](engineering/limitations.md).

## Quality evidence

Repository acceptance requires tests, enforced 100% line coverage, Ruff lint
and formatting, `ty` contracts, executable documentation examples, internal
navigation/link validation, documentation and package builds, and permanent
benchmark tasks. A passing development checkout is evidence for that commit;
it is not a promise that every downstream environment is identical.

Release history belongs in [Release notes](release-notes.md). Contributor
workflow is documented in [Contributing](contributing.md).
