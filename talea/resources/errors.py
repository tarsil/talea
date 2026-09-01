"""Report resource-policy rejection without retaining hostile input."""

from typing import Literal

__all__ = ["ResourceLimitError"]

type ResourceLimitCode = Literal[
    "input_size",
    "depth",
    "nodes",
    "settings_environment_entries",
    "settings_secret_file_bytes",
    "settings_secret_files",
    "settings_source_bytes",
    "settings_source_names",
    "settings_toml_bytes",
]


class ResourceLimitError(Exception):
    """Report that a Talea-owned input resource limit was exceeded.

    ``code``, ``limit``, and ``observed`` are stable machine-readable values.
    ``observed`` is exact when cheaply known; early transport and traversal
    termination may report only the first value known to exceed the limit.
    The exception never stores the rejected transport, container, scalar, or
    callback exception.
    """

    def __init__(self, code: ResourceLimitCode, limit: int, observed: int) -> None:
        self.code = code
        self.limit = limit
        self.observed = observed
        label = {
            "input_size": "JSON input bytes",
            "depth": "input depth",
            "nodes": "input work nodes",
            "settings_environment_entries": "settings environment entries",
            "settings_secret_file_bytes": "settings secret file bytes",
            "settings_secret_files": "settings secret files",
            "settings_source_bytes": "settings aggregate source bytes",
            "settings_source_names": "compiled settings source names",
            "settings_toml_bytes": "settings TOML bytes",
        }[code]
        super().__init__(f"{label} exceeded limit {limit} (observed {observed})")
