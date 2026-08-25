# Release Notes

## 0.1.0

### Added

- Added `talea.Spec` with required keyword-only fields, strict construction,
  compact slots, compile-once declaration processing, and field-aware errors.
- Added validated static defaults, `field(default_factory=...)`, mutable-default
  safety, and immutable Spec instances.
- Added nominal nested Spec fields across containers and unions, transitive
  trust classification, inherited fields, covariant field overrides, and flat
  specialized subclass construction.
- Added compact multiple inheritance for one state-bearing Spec lineage and
  empty-slotted method mixins.

### Changed

- Spec constructors now inline strict field-validation operations from the same
  compiler owner used for standalone validators, eliminating per-field Python
  validator calls during construction.
- Nested Spec references that are not permanently trusted now receive
  specialized current-state validation at each new validation boundary.

### Fixed
