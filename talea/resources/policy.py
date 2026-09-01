"""Own immutable limits for Talea's untrusted input boundaries."""

from dataclasses import dataclass

__all__ = ["ResourcePolicy"]

DEFAULT_MAX_INPUT_BYTES = 8 * 1024 * 1024


def _limit(value: int | None, name: str) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{name} must be a positive int or None")


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """Bound Talea-owned work while accepting untrusted external input.

    Args:
        max_input_bytes: Maximum encoded JSON transport size. Bytes and byte
            arrays use their exact length. Text uses its UTF-8 byte length
            without allocating an encoded copy. ``None`` disables this check.
        max_depth: Maximum structural nesting. A scalar root has depth zero;
            a root container has depth one; each nested container adds one.
        max_nodes: Maximum compiled schema visits. Union alternatives count
            when attempted, while a tagged union visits only its selected
            branch. This is a work budget, not a count of distinct identities.
        max_errors: Maximum independent Mapping/JSON failures retained before
            validation stops. The resulting ``ValidationError.truncated`` is
            true when this budget terminates aggregation.

    The default limits are deliberately generous for ordinary API and message
    payloads. Passing a new policy per operation or retaining one on a
    :class:`talea.Contract` changes only that operation or Contract; Talea has
    no mutable process-global resource configuration. ``None`` may disable an
    individual dimension for a caller that enforces it elsewhere.

    The policy governs ``Spec.from_mapping``, ``Spec.from_json``,
    ``Contract.from_python``, and ``Contract.from_json``. It does not sandbox
    custom mappings, callbacks, codecs, or regular expressions, and it does not
    apply to trusted construction, strict ``Contract.validate``, output, or
    schema tooling.
    """

    max_input_bytes: int | None = DEFAULT_MAX_INPUT_BYTES
    max_depth: int | None = 64
    max_nodes: int | None = 100_000
    max_errors: int | None = 100

    def __post_init__(self) -> None:
        _limit(self.max_input_bytes, "max_input_bytes")
        _limit(self.max_depth, "max_depth")
        _limit(self.max_nodes, "max_nodes")
        _limit(self.max_errors, "max_errors")


DEFAULT_RESOURCE_POLICY = ResourcePolicy()


def resolve_policy(policy: ResourcePolicy | None) -> ResourcePolicy:
    """Return the finite default or one explicit immutable override."""

    if policy is None:
        return DEFAULT_RESOURCE_POLICY
    if not isinstance(policy, ResourcePolicy):
        raise TypeError("policy must be a ResourcePolicy or None")
    return policy
