"""Own lazy per-item Contract execution and its stream-level limits."""

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TypeVar

from talea.resources.errors import ResourceLimitError
from talea.validation.errors import ValidationError

T = TypeVar("T")


def _limit(value: int | None, name: str) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{name} must be a positive int or None")


@dataclass(frozen=True, slots=True)
class ItemPolicy:
    """Bound one incremental Contract consumption.

    Args:
        max_items: Maximum source items pulled, including valid and invalid
            items. ``None`` explicitly disables the stream record bound.
        max_invalid_items: Maximum invalid source items admitted to an
            explicit continuation callback. One structured item counts once,
            regardless of its number of validation details. ``None``
            explicitly disables this bound.

    The defaults permit large record-processing jobs while bounding accidental
    or hostile infinite consumption. These limits are operation-local and are
    distinct from :class:`talea.ResourcePolicy`, which governs traversal and
    error aggregation inside each external Python item.
    """

    max_items: int | None = 1_000_000
    max_invalid_items: int | None = 100

    def __post_init__(self) -> None:
        _limit(self.max_items, "max_items")
        _limit(self.max_invalid_items, "max_invalid_items")


DEFAULT_ITEM_POLICY = ItemPolicy()


def resolve_item_policy(policy: ItemPolicy | None) -> ItemPolicy:
    """Return the finite default or one explicit immutable override."""

    if policy is None:
        return DEFAULT_ITEM_POLICY
    if not isinstance(policy, ItemPolicy):
        raise TypeError("item_policy must be an ItemPolicy or None")
    return policy


def iter_items(
    source: Iterable[object],
    operation: Callable[[object], T],
    *,
    on_error: Callable[[int, ValidationError], None] | None,
    policy: ItemPolicy | None,
) -> Iterator[T]:
    """Validate static controls eagerly and return a lazy item iterator."""

    selected = resolve_item_policy(policy)
    if on_error is not None and not callable(on_error):
        raise TypeError("on_error must be callable or None")
    return _consume_items(source, operation, on_error, selected)


def _consume_items(
    source: Iterable[object],
    operation: Callable[[object], T],
    on_error: Callable[[int, ValidationError], None] | None,
    policy: ItemPolicy,
) -> Iterator[T]:
    invalid_items = 0
    for index, item in enumerate(source):
        if policy.max_items is not None and index >= policy.max_items:
            raise ResourceLimitError("items", policy.max_items, index + 1)
        try:
            result = operation(item)
        except ValidationError as error:
            located = error.prefixed((index,))
            if on_error is None:
                raise located from located.__cause__
            invalid_items += 1
            if policy.max_invalid_items is not None and invalid_items > policy.max_invalid_items:
                raise ResourceLimitError("invalid_items", policy.max_invalid_items, invalid_items) from None
            on_error(index, located)
            del located
        else:
            del item
            yield result
            del result
