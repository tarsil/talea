"""Exercise the public Settings plan, merge, snapshot, and provenance contracts."""

import os
import subprocess
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import MappingProxyType
from typing import Annotated

import pytest
from hypothesis import given, strategies as st

from talea import Alias, Spec, ValidationError, create_spec, derive_spec, field
from talea.settings import Settings, SettingsInfo, SettingsResult


class Database(Spec):
    host: str
    port: int = 5432
    password: str = "local"


class Application(Spec):
    debug: bool = False
    database_config: Annotated[Database, Alias("database", legacy=("db",))]


def test_settings_public_api_is_domain_owned_and_root_import_is_isolated() -> None:
    import talea.settings as settings_module

    assert settings_module.__all__ == [
        "SettingSource",
        "Settings",
        "SettingsInfo",
        "SettingsPolicy",
        "SettingsResult",
    ]
    command = "import sys, talea; assert 'talea.settings' not in sys.modules; assert not hasattr(talea, 'Settings')"
    subprocess.run([sys.executable, "-c", command], check=True)


@pytest.mark.parametrize("model", [int, Spec, object])
def test_settings_requires_a_concrete_spec_root(model: object) -> None:
    with pytest.raises(TypeError, match="concrete Spec"):
        Settings(model)  # type: ignore[arg-type]


def test_open_and_concrete_generic_roots() -> None:
    class Box[ValueT](Spec):
        value: ValueT

    with pytest.raises(TypeError, match="fully specialized"):
        Settings(Box)

    plan = Settings(Box[int], prefix="BOX_")
    value = plan.load(environment={"BOX_VALUE": "4"})
    assert type(value) is Box[int]
    assert value.value == 4


def test_dynamic_and_complete_derived_roots_are_ordinary_settings_specs() -> None:
    Dynamic = create_spec("Dynamic", {"value": int}, module=__name__)
    Projected = derive_spec(Application, include=("debug",))

    assert Settings(Dynamic).load({"value": 1}).value == 1
    assert Settings(Projected).load().debug is False

    Partial = derive_spec(Application, partial=True)
    with pytest.raises(TypeError, match="partial"):
        Settings(Partial)


def test_plan_configuration_and_introspection_are_immutable() -> None:
    plan = Settings(Application, prefix="APP_", case_sensitive=True)

    assert isinstance(plan.info, SettingsInfo)
    assert plan.info.model is Application
    assert plan.info.source_order == ("override", "environment", "secret", "toml", "default")
    assert plan.info.prefix == "APP_"
    assert plan.info.delimiter == "__"
    assert plan.info.case_sensitive is True
    assert "APP_database__host" in plan.info.environment_names
    assert "APP_db__port" in plan.info.environment_names
    with pytest.raises(AttributeError, match="immutable"):
        plan._model = Database  # type: ignore[misc]
    with pytest.raises(AttributeError, match="immutable"):
        del plan._model


@pytest.mark.parametrize("prefix", [1, None])
def test_prefix_requires_text(prefix: object) -> None:
    with pytest.raises(TypeError, match="prefix"):
        Settings(Application, prefix=prefix)  # type: ignore[arg-type]


@pytest.mark.parametrize("prefix", ["BAD\x00", "BAD\n", "BAD\r", "BAD="])
def test_prefix_rejects_environment_control_characters(prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        Settings(Application, prefix=prefix)


def test_case_and_policy_configuration_are_explicit() -> None:
    with pytest.raises(TypeError, match="case_sensitive"):
        Settings(Application, case_sensitive=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="policy"):
        Settings(Application, policy=object())  # type: ignore[arg-type]


def test_defaults_only_and_nested_mapping_override() -> None:
    plan = Settings(Application)
    value = plan.load({"database": {"host": "localhost"}})

    assert value.to_dict() == {
        "debug": False,
        "database": {"host": "localhost", "port": 5432, "password": "local"},
    }


def test_canonical_leaf_precedence_preserves_lower_siblings(tmp_path: Path) -> None:
    toml_path = tmp_path / "app.toml"
    toml_path.write_text(
        '[database]\nhost = "toml"\nport = 5000\npassword = "toml-secret"\n',
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "database__password").write_text("mounted\n", encoding="utf-8")
    plan = Settings(Application, prefix="APP_", toml=toml_path, secrets=secrets)

    result = plan.load(
        {"database": {"host": "override"}},
        environment={"APP_DATABASE__PORT": "6000"},
        provenance=True,
    )

    assert isinstance(result, SettingsResult)
    assert result.value.database_config.host == "override"
    assert result.value.database_config.port == 6000
    assert result.value.database_config.password == "mounted"
    assert result.value.debug is False
    assert dict(result.provenance) == {
        ("database_config", "password"): "secret",
        ("database_config", "port"): "environment",
        ("database_config", "host"): "override",
        ("debug",): "default",
    }
    with pytest.raises(TypeError):
        result.provenance[("debug",)] = "override"  # type: ignore[index]


def test_empty_higher_object_does_not_erase_lower_leaf_truth(tmp_path: Path) -> None:
    path = tmp_path / "app.toml"
    path.write_text('[database]\nhost = "toml"\n', encoding="utf-8")

    value = Settings(Application, toml=path).load({"database": {}})
    assert value.database_config.host == "toml"


def test_whole_invalid_higher_value_replaces_lower_descendants(tmp_path: Path) -> None:
    path = tmp_path / "app.toml"
    path.write_text('[database]\nhost = "toml"\n', encoding="utf-8")

    with pytest.raises(ValidationError) as raised:
        Settings(Application, toml=path).load({"database": None})
    assert raised.value.location == ("database",)


def test_current_and_historical_aliases_work_in_mappings_and_across_sources(tmp_path: Path) -> None:
    path = tmp_path / "app.toml"
    path.write_text('[db]\nhost = "legacy"\n', encoding="utf-8")
    plan = Settings(Application, prefix="APP_", toml=path)

    assert plan.load(environment={"APP_DATABASE__PORT": "6001"}).database_config.host == "legacy"
    assert plan.load({"db": {"host": "override"}}).database_config.host == "override"


@pytest.mark.parametrize(
    "mapping",
    [
        {"database": {"host": "a"}, "db": {"host": "b"}},
        {"database": {"host": "a"}, "DB": {"host": "b"}},
    ],
)
def test_mapping_names_conflict_without_value_precedence(mapping: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(Application).load(mapping)
    assert raised.value.code == "alias_conflict"
    assert "input" not in raised.value.errors()[0]


def test_repeated_load_returns_new_snapshot_and_never_retains_sources() -> None:
    plan = Settings(Application, prefix="APP_")
    first_environment = {"APP_DATABASE__HOST": "first"}
    first = plan.load(environment=first_environment)
    first_environment["APP_DATABASE__HOST"] = "mutated"
    second = plan.load(environment=first_environment)

    assert first is not second
    assert first.database_config.host == "first"
    assert second.database_config.host == "mutated"


def test_failed_load_does_not_mutate_a_previous_snapshot() -> None:
    plan = Settings(Application, prefix="APP_")
    first = plan.load(environment={"APP_DATABASE__HOST": "first"})
    with pytest.raises(ValidationError):
        plan.load(environment={"APP_DATABASE__HOST": "first", "APP_DATABASE__PORT": "bad"})
    assert first.database_config.host == "first"
    assert first.database_config.port == 5432


def test_concurrent_loads_have_operation_local_state() -> None:
    plan = Settings(Application, prefix="APP_")

    def load(index: int) -> tuple[str, int]:
        value = plan.load(
            environment={
                "APP_DATABASE__HOST": f"host-{index}",
                "APP_DATABASE__PORT": str(5000 + index),
            }
        )
        return value.database_config.host, value.database_config.port

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(load, range(20))) == [(f"host-{i}", 5000 + i) for i in range(20)]


def test_process_environment_is_snapshotted_without_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DATABASE__HOST", "process")
    before = dict(os.environ)
    value = Settings(Application, prefix="APP_").load()

    assert value.database_config.host == "process"
    assert dict(os.environ) == before
    assert not hasattr(os, "reload_environ") or os.reload_environ is not None


def test_defaults_and_factories_remain_owned_by_spec_construction() -> None:
    calls = 0

    def build() -> str:
        nonlocal calls
        calls += 1
        return "factory"

    class FactorySettings(Spec):
        value: str = field(default_factory=build)

    plan = Settings(FactorySettings, prefix="APP_")
    assert calls == 0
    assert plan.load(environment={}).value == "factory"
    assert calls == 1
    assert plan.load(environment={"APP_VALUE": "provided"}).value == "provided"
    assert calls == 1


def test_override_mapping_is_consumed_per_load() -> None:
    values = {"database": {"host": "first"}}
    plan = Settings(Application)
    first = plan.load(values)
    values["database"]["host"] = "second"  # type: ignore[index]
    second = plan.load(values)
    assert (first.database_config.host, second.database_config.host) == ("first", "second")


def test_invalid_load_arguments_are_rejected() -> None:
    plan = Settings(Application)
    with pytest.raises(TypeError, match="overrides"):
        plan.load([])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="environment"):
        plan.load(environment=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="provenance"):
        plan.load({"database": {"host": "x"}}, provenance=1)  # type: ignore[arg-type]


def test_mapping_proxy_override_is_supported() -> None:
    value = Settings(Application).load(MappingProxyType({"database": {"host": "proxy"}}))
    assert value.database_config.host == "proxy"


def test_schema_and_output_remain_ordinary_spec_behavior() -> None:
    before_json = Application.json_schema()
    before_openapi = Application.openapi_schema()
    value = Settings(Application).load({"database": {"host": "schema"}})

    assert value.to_dict()["database"]["host"] == "schema"  # type: ignore[index]
    assert Application.json_schema() == before_json
    assert Application.openapi_schema() == before_openapi
    assert "environment" not in repr(before_json)


def test_custom_mapping_snapshot_uses_ordinary_mapping_iteration() -> None:
    class OneShot(Mapping[str, object]):
        def __init__(self) -> None:
            self.iterations = 0

        def __getitem__(self, key: str) -> object:
            if key == "database":
                return {"host": "custom"}
            raise KeyError(key)

        def __iter__(self):
            self.iterations += 1
            return iter(("database",))

        def __len__(self) -> int:
            return 1

    source = OneShot()
    assert Settings(Application).load(source).database_config.host == "custom"
    assert source.iterations == 1


@given(
    environment_host=st.text(alphabet="abc", min_size=1, max_size=8),
    environment_port=st.integers(min_value=1, max_value=65_535),
    override_host=st.one_of(st.none(), st.text(alphabet="xyz", min_size=1, max_size=8)),
    override_port=st.one_of(st.none(), st.integers(min_value=1, max_value=65_535)),
)
def test_leaf_precedence_matches_an_independent_reference_merger(
    environment_host: str,
    environment_port: int,
    override_host: str | None,
    override_port: int | None,
) -> None:
    environment = {
        "APP_DATABASE__HOST": environment_host,
        "APP_DATABASE__PORT": str(environment_port),
    }
    nested_override: dict[str, object] = {}
    if override_host is not None:
        nested_override["host"] = override_host
    if override_port is not None:
        nested_override["port"] = override_port
    overrides = {"database": nested_override} if nested_override else None

    loaded = Settings(Application, prefix="APP_").load(overrides, environment=environment)
    expected_host = environment_host if override_host is None else override_host
    expected_port = environment_port if override_port is None else override_port
    assert (loaded.database_config.host, loaded.database_config.port) == (expected_host, expected_port)
