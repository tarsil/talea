# Remaining release ledger

This ledger records production capabilities considered during Campaign 12 but
not owned by its utility implementation. “Deferred” identifies a concrete
future owner; it is not a claim that the feature exists.

| Capability | Disposition | Named owner and reason |
| --- | --- | --- |
| Partial/PATCH, all-fields-optional, pick, omit, and read/write derivation | Deferred | **Presence and Derived Spec Contracts**: define omitted state, defaults/factories, aliases, generic identity, serializers, and cross-field hook policy before generating normal Specs through `create_spec`. |
| Callable argument and return validation | Deferred | **Callable Signature Contracts**: own signature binding, defaults, positional/keyword rules, variadics, descriptors, async behavior, return validation, exception policy, typing preservation, and wrapper cost using Contract as a leaf consumer. |
| Detached callable-contract objects | Rejected pending a distinct use case | The Callable Signature Contracts owner should expose one decorator vocabulary; a second overlapping object API has no demonstrated value. |
| Tagged/discriminated unions | Different owner | **Tagged Union Schema and Emission**: add canonical discriminator metadata, specialized validation/input/output emission, and later JSON Schema projection. Do not dispatch outside Schema truth. |
| Field title, description, examples, deprecated, read-only, and write-only metadata | Different owner | **Campaign 13 Canonical Metadata and JSON Schema Projection**. Runtime introspection must consume the same truth after it exists. |
| Recursive PEP 695 aliases and recursive TypedDict | Deferred | **Recursive Alias Graph**: introduce finite canonical named-reference nodes and cycle-aware input/output emission. Current resolution rejects cycles explicitly. |
| Dataclass, NamedTuple, and ordinary-class conversion | Deferred | **Structured Class Interoperability**: define construction, trust, attribute access, defaults, inheritance, and serialization without becoming an object mapper. |
| Settings and environment loading | Different product owner | **Talea Settings integration/package**, outside core validation. It must own source precedence, secrets, environment parsing, and application lifecycle. |
| Bulk iterable validation and per-item failures | Deferred | **Batch and Streaming Boundaries**. Materialized batches use `Contract(list[T])`; streams need consumption and failure-isolation policy. |
| JSONL or other streaming formats | Different owner | **Streaming Format Boundaries**. `from_json_many` is rejected because JSON arrays, concatenated JSON, and JSONL are different formats. |
| Boolean validation predicates | Deferred | **Validation Failure-Mode Emission**: measure a specialized non-exception target before adding `TypeIs` vocabulary. Catching rich `ValidationError` on every negative probe is not the implementation. |
| Result-style validation | Rejected | Duplicates the stable exception/error projection and adds branch vocabulary without evidence. |
| Annotation-level arbitrary validator callbacks | Deferred | **Shared Validation Declaration**: any callback metadata must feed canonical validation emission and error semantics, not create a second runtime. |
| Retained codec configuration | Deferred | **Codec Policy** only after measurements show material value beyond per-call `loads`/`dumps`; no global registry. |
| TypedDict `ReadOnly` runtime enforcement | Deferred | **Presence/Metadata Semantics**. Metadata is retained now; runtime values are ordinary dictionaries and assignment control is not a validation-boundary operation. |
| `Any`/`object` passthrough contracts | Rejected | They erase meaningful validation and introspection truth. |
| Abstract `Sequence`/`Mapping` conversion contracts | Rejected | Concrete container semantics remain deliberate and predictable; abstract input/output shape is ambiguous. |
| Capability `supports()` predicate | Rejected | Constructing `Contract(annotation)` is the authoritative capability check and returns precise declaration errors. |
| ORM attribute extraction | Different owner/rejected for core | An ORM integration may explicitly own lazy access and error policy; core will not silently read arbitrary attributes. |
| Settings registries and application-global Contract registries | Rejected for core | Ownership belongs to applications; core retains no global mutable registry. |

