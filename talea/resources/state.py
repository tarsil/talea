"""Own operation-local resource accounting for compiled input boundaries."""

from collections.abc import Callable

from talea.resources.errors import ResourceLimitError
from talea.resources.policy import ResourcePolicy


class _ResourceState:
    """Retain counters shared by one root input operation and its back-edges."""

    __slots__ = ("base_depth", "max_depth", "max_errors", "max_nodes", "nodes")

    def __init__(self, policy: ResourcePolicy) -> None:
        self.max_depth = policy.max_depth
        self.max_nodes = policy.max_nodes
        self.max_errors = policy.max_errors
        self.nodes = 0
        self.base_depth = 0

    def consume_node(self, relative_depth: int) -> None:
        """Charge one generated schema visit and reject the first excess."""

        depth = self.base_depth + relative_depth
        maximum_depth = self.max_depth
        if maximum_depth is not None and depth > maximum_depth:
            raise ResourceLimitError("depth", maximum_depth, depth)
        nodes = self.nodes + 1
        self.nodes = nodes
        maximum_nodes = self.max_nodes
        if maximum_nodes is not None and nodes > maximum_nodes:
            raise ResourceLimitError("nodes", maximum_nodes, nodes)

    def call_nested(self, operation: Callable[..., object], value: object, relative_depth: int) -> object:
        """Call a separately compiled back-edge at the caller's graph depth."""

        previous = self.base_depth
        self.base_depth = previous + relative_depth
        try:
            return operation(value, self)
        finally:
            self.base_depth = previous

    def error_limit_reached(self, count: int) -> bool:
        """Return whether independent failure aggregation must stop."""

        return self.max_errors is not None and count >= self.max_errors


class _UnlimitedResourceState:
    """Keep direct internal artifacts callable without boundary accounting."""

    __slots__ = ()

    @staticmethod
    def consume_node(relative_depth: int) -> None:
        del relative_depth

    def call_nested(self, operation: Callable[..., object], value: object, relative_depth: int) -> object:
        del relative_depth
        return operation(value, self)

    @staticmethod
    def error_limit_reached(count: int) -> bool:
        del count
        return False


UNLIMITED_RESOURCE_STATE = _UnlimitedResourceState()


def resource_state(policy: ResourcePolicy) -> _ResourceState:
    """Allocate the one mutable state object owned by an input operation."""

    return _ResourceState(policy)


def check_input_size(data: str | bytes | bytearray, policy: ResourcePolicy) -> None:
    """Reject an oversized JSON transport before the selected decoder runs."""

    limit = policy.max_input_bytes
    if limit is None:
        return
    if not isinstance(data, str):
        observed = len(data)
    else:
        characters = len(data)
        if characters > limit or data.isascii():
            observed = characters
        else:
            observed = 0
            for character in data:
                codepoint = ord(character)
                observed += 1 + (codepoint > 0x7F) + (codepoint > 0x7FF) + (codepoint > 0xFFFF)
                if observed > limit:
                    break
    if observed > limit:
        raise ResourceLimitError("input_size", limit, observed)
