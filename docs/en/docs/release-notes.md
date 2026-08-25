# Release Notes

## 0.1.0

### Added

- Added `talea.Spec` with required keyword-only fields, strict construction,
  compact slots, compile-once declaration processing, and field-aware errors.
- Added validated static defaults, `field(default_factory=...)`, mutable-default
  safety, and immutable Spec instances.

### Changed

- Spec constructors now inline strict field-validation operations from the same
  compiler owner used for standalone validators, eliminating per-field Python
  validator calls during construction.

### Fixed
