# Immutable updates

Use Python's `copy.replace()` for a complete Spec when the caller knows the
fields to change:

```python
from copy import replace

updated = replace(user, name="Grace")
```

Changed values run the same transform, structural validation, and field checks
as construction. Unchanged mutable current state is revalidated; permanently
trusted values are reused. Whole-Spec checks rerun before commitment. Defaults
and factories do not rerun, and nested values are not deep-copied.

Use `apply_patch()` when changes came from a compatible partial derived Spec:

```python
from talea import apply_patch, derive_spec

UserPatch = derive_spec(User, partial=True)
patch = UserPatch.from_mapping({"name": "Grace"})
updated = apply_patch(user, patch)
```

`apply_patch()` forwards only present fields through `copy.replace()`. It
rejects a full Spec, a projection that is not partial, and a partial derived
from another source class. See [Derived and PATCH contracts](../presence-derived-contracts.md).

## Atomic validation lifecycle

Replacement never mutates the source. Changed fields run their transform,
structural validator, and field checks; unchanged mutable fields receive
current-state validation; and whole-Spec checks run against the complete
candidate. The new instance is published only after every stage succeeds.

```python
try:
    replace(interval, start=10, end=2)
except ValidationError:
    assert interval.start < interval.end
```

Factories and defaults do not rerun because replacement begins from existing
complete state. Nested mutable objects are shared by reference rather than
deep-copied, matching Python's immutable-record protocol. If a deep snapshot is
required, copy that application value explicitly and accept its cost.

## Names, aliases, typing, and performance

`copy.replace()` accepts canonical Python field names; external aliases belong
to Mapping/JSON boundaries. Unknown names fail. Python 3.14 preserves the
concrete result type but cannot statically validate arbitrary replacement
keyword value types as precisely as the generated constructor; runtime
validation remains authoritative.

Each used Spec compiles its smallest replacement operation lazily. Specs that
never use replacement have no replacement artifact. Permanently trusted
unchanged values are reused, while mutable values pay current-state validation
so prior mutation cannot bypass the contract.

Use `copy.replace()` for trusted application-selected changes and
`apply_patch()` for presence-aware external changes. Do not use replacement for
in-place mutable workflows or as a deep-copy facility. The [dynamic Spec
example](../dynamic-utilities.md#executable-dynamic-lifecycle) demonstrates
successful and failed replacement through constraints and hooks.
