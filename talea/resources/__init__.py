"""Public immutable resource policy and bounded-failure API."""

from talea.resources.errors import ResourceLimitError
from talea.resources.policy import ResourcePolicy

__all__ = ["ResourceLimitError", "ResourcePolicy"]
