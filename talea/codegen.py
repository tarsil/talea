"""Own deterministic names shared by Talea's generated execution targets."""

from collections.abc import Iterable


class _GeneratedNames:
    """Allocate compiler-owned identifiers disjoint from user-provided names."""

    __slots__ = ("counters", "reserved")

    def __init__(self, reserved: Iterable[str] = ()) -> None:
        self.counters: dict[str, int] = {}
        self.reserved = set(reserved)

    def allocate(self, purpose: str) -> str:
        """Return the next deterministic identifier for ``purpose``."""

        index = self.counters.get(purpose, 0)
        while True:
            index += 1
            candidate = f"_talea_{purpose}_{index}"
            if candidate not in self.reserved:
                self.counters[purpose] = index
                self.reserved.add(candidate)
                return candidate
