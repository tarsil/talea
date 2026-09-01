# Edge cases

These rules are deliberate compatibility contracts, not incidental Python
behavior.

| Case | Talea behavior |
| --- | --- |
| `bool` for `int` | Rejected; primitive checks are exact |
| `datetime` for `date` | Rejected; a timestamp is not a calendar-day contract |
| raw value for Enum | Rejected on strict Python path; JSON uses documented representation |
| `Literal[True]` and `1` | Distinct by value and runtime type |
| tuple for `list[T]` | Rejected; concrete containers preserve shape |
| unknown Mapping/JSON key | `unexpected` failure |
| current and legacy names for one field | `alias_conflict`, even when values are equal |
| duplicate JSON key | `json_duplicate` failure before field conversion |
| JSON NaN or Infinity | Rejected by the default decoder |
| mutable nested Spec | Current declared state is revalidated at a new boundary |
| cyclic input/output graph | Rejected with a finite `cycle` path |
| recursive alias | Supported when resolution reaches a finite named back-edge |
| open generic execution | Rejected; specialize first |
| partial omitted field | Attribute is absent and omitted from serialization |
| partial field set to `None` | Present only if the source field accepts `None` |
| transform in input schema | Projection fails if the pre-transform domain is unknowable |
| serializer in output schema | Projection fails if the serializer output is unknowable |
| custom codec or Mapping | Trusted caller code; exceptions may propagate as documented |
| `Sensitive` callback | Callback receives the raw value and owns its side effects |

Strict and external paths differ for standard types. See [Supported
types](../supported-types.md), [Input boundaries](../input-boundaries.md), and
[Known limitations](../engineering/limitations.md).
