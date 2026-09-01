"""Define the small immutable public data model for Talea Settings."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from talea.resources import ResourcePolicy
from talea.spec import Spec

__all__ = ["SettingSource", "SettingsInfo", "SettingsPolicy", "SettingsResult"]

type SettingSource = Literal["override", "environment", "secret", "toml", "default"]
type SettingPath = tuple[str, ...]


def _limit(value: int | None, name: str) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{name} must be a positive int or None")


@dataclass(frozen=True, slots=True)
class SettingsPolicy:
    """Bound source acquisition independently of Talea input traversal.

    ``input_policy`` remains the existing owner of final Mapping depth, node,
    and error budgets. The other dimensions bound work introduced only by the
    settings boundary. ``None`` explicitly disables an individual limit; the
    defaults are finite and sized for application configuration rather than
    bulk data ingestion.
    """

    max_environment_entries: int | None = 10_000
    max_override_entries: int | None = 100_000
    max_override_depth: int | None = 64
    max_override_key_bytes: int | None = 64 * 1024
    max_source_names: int | None = 10_000
    max_toml_bytes: int | None = 8 * 1024 * 1024
    max_secret_files: int | None = 256
    max_secret_file_bytes: int | None = 1024 * 1024
    max_source_bytes: int | None = 16 * 1024 * 1024
    input_policy: ResourcePolicy = field(default_factory=ResourcePolicy)

    def __post_init__(self) -> None:
        """Validate every finite limit and the delegated input policy."""

        _limit(self.max_environment_entries, "max_environment_entries")
        _limit(self.max_override_entries, "max_override_entries")
        _limit(self.max_override_depth, "max_override_depth")
        _limit(self.max_override_key_bytes, "max_override_key_bytes")
        _limit(self.max_source_names, "max_source_names")
        _limit(self.max_toml_bytes, "max_toml_bytes")
        _limit(self.max_secret_files, "max_secret_files")
        _limit(self.max_secret_file_bytes, "max_secret_file_bytes")
        _limit(self.max_source_bytes, "max_source_bytes")
        if not isinstance(self.input_policy, ResourcePolicy):
            raise TypeError("input_policy must be a ResourcePolicy")


@dataclass(frozen=True, slots=True)
class SettingsInfo:
    """Expose callback-free settings plan facts without source contents."""

    model: type[Spec]
    source_order: tuple[SettingSource, ...]
    prefix: str
    delimiter: str
    case_sensitive: bool
    environment_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SettingsResult[SettingsT: Spec]:
    """Return one immutable snapshot and its value-free leaf provenance."""

    value: SettingsT
    provenance: Mapping[SettingPath, SettingSource]

    def __post_init__(self) -> None:
        """Detach and freeze provenance supplied by the operation-local load."""

        frozen = MappingProxyType(dict(self.provenance))
        object.__setattr__(self, "provenance", frozen)
