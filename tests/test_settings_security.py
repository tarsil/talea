"""Exercise source collisions, limits, redaction, and failure boundaries."""

from pathlib import Path
from typing import Annotated

import pytest

from talea import Alias, ResourceLimitError, ResourcePolicy, Sensitive, Spec, ValidationError
from talea.settings import Settings, SettingsPolicy


def test_casefold_source_name_collision_rejects_at_plan_compilation() -> None:
    class Collision(Spec):
        first: Annotated[int, Alias("VALUE")]
        second: Annotated[int, Alias("value")]

    with pytest.raises(ValueError, match="source-name collision"):
        Settings(Collision)
    plan = Settings(Collision, case_sensitive=True)
    value = plan.load(environment={"VALUE": "1", "value": "2"})
    assert (value.first, value.second) == (1, 2)


def test_nested_delimiter_collision_rejects_at_plan_compilation() -> None:
    class Nested(Spec):
        value: int

    class Collision(Spec):
        nested__value: int
        nested: Nested

    with pytest.raises(ValueError, match="source-name collision"):
        Settings(Collision)


def test_casefold_current_legacy_collision_rejects_at_plan_compilation() -> None:
    class Collision(Spec):
        value: Annotated[int, Alias("VALUE", legacy=("value",))]

    with pytest.raises(ValueError, match="source-name collision"):
        Settings(Collision)


def test_current_and_legacy_environment_names_always_conflict() -> None:
    class Migrated(Spec):
        value: Annotated[int, Alias("current", legacy=("old", "older"))]

    plan = Settings(Migrated, prefix="APP_")
    for environment in (
        {"APP_CURRENT": "1", "APP_OLD": "1"},
        {"APP_OLD": "1", "APP_OLDER": "2"},
        {"APP_CURRENT": "1", "app_current": "1"},
    ):
        with pytest.raises(ValidationError) as raised:
            plan.load(environment=environment)
        assert raised.value.code == "alias_conflict"
        assert "APP_" not in repr(raised.value.errors())


def test_current_and_legacy_secret_names_conflict_without_exposing_paths(tmp_path: Path) -> None:
    class Migrated(Spec):
        value: Annotated[int, Alias("current", legacy=("old",))]

    root = tmp_path / "secrets"
    root.mkdir()
    (root / "current").write_text("1", encoding="utf-8")
    (root / "old").write_text("2", encoding="utf-8")
    with pytest.raises(ValidationError) as raised:
        Settings(Migrated, secrets=root).load()
    assert raised.value.code == "alias_conflict"
    assert str(root) not in str(raised.value)


def test_toml_case_collision_conflicts_at_load(tmp_path: Path) -> None:
    class Simple(Spec):
        value: int

    path = tmp_path / "case.toml"
    path.write_text("value = 1\nVALUE = 2\n", encoding="utf-8")
    with pytest.raises(ValidationError) as raised:
        Settings(Simple, toml=path).load()
    assert raised.value.code == "alias_conflict"


def test_environment_entry_limit_is_checked_before_lookup() -> None:
    class Simple(Spec):
        value: int = 1

    policy = SettingsPolicy(max_environment_entries=2)
    with pytest.raises(ResourceLimitError) as raised:
        Settings(Simple, policy=policy).load(environment={"A": "1", "B": "2", "C": "3"})
    assert raised.value.code == "settings_environment_entries"
    assert raised.value.observed == 3


def test_environment_requires_text_keys_and_values() -> None:
    class Simple(Spec):
        value: int = 1

    plan = Settings(Simple)
    with pytest.raises(TypeError, match="keys and values"):
        plan.load(environment={1: "1"})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="keys and values"):
        plan.load(environment={"value": 1})  # type: ignore[dict-item]


def test_source_name_limit_bounds_plan_compilation() -> None:
    class ManyAliases(Spec):
        value: Annotated[int, Alias("current", legacy=("one", "two"))]

    policy = SettingsPolicy(max_source_names=2)
    with pytest.raises(ResourceLimitError) as raised:
        Settings(ManyAliases, policy=policy)
    assert raised.value.code == "settings_source_names"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_environment_entries": 0},
        {"max_source_names": -1},
        {"max_toml_bytes": 1.0},
        {"max_secret_files": False},
        {"max_secret_file_bytes": 0},
        {"max_source_bytes": -2},
    ],
)
def test_settings_policy_requires_positive_integer_limits(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SettingsPolicy(**kwargs)  # type: ignore[arg-type]


def test_settings_policy_requires_resource_policy_owner() -> None:
    with pytest.raises(TypeError, match="ResourcePolicy"):
        SettingsPolicy(input_policy=object())  # type: ignore[arg-type]


def test_resource_policy_depth_and_nodes_apply_to_final_input() -> None:
    class Child(Spec):
        value: int

    class Root(Spec):
        child: Child

    depth = SettingsPolicy(input_policy=ResourcePolicy(max_depth=1))
    nodes = SettingsPolicy(input_policy=ResourcePolicy(max_nodes=1))
    with pytest.raises(ResourceLimitError) as depth_error:
        Settings(Root, policy=depth).load({"child": {"value": 1}})
    with pytest.raises(ResourceLimitError) as node_error:
        Settings(Root, policy=nodes).load({"child": {"value": 1}})
    assert depth_error.value.code == "depth"
    assert node_error.value.code == "nodes"


def test_sensitive_environment_and_toml_failures_do_not_retain_values(tmp_path: Path) -> None:
    class Credentials(Spec):
        token: Annotated[int, Sensitive()]

    plan = Settings(Credentials, prefix="APP_")
    with pytest.raises(ValidationError) as environment:
        plan.load(environment={"APP_TOKEN": "environment-secret"})
    assert environment.value.value == "<redacted>"
    assert "environment-secret" not in str(environment.value)

    path = tmp_path / "secret.toml"
    path.write_text('token = "toml-secret"\n', encoding="utf-8")
    with pytest.raises(ValidationError) as toml_error:
        Settings(Credentials, toml=path).load()
    assert toml_error.value.value == "<redacted>"
    assert "toml-secret" not in repr(toml_error.value.errors())


def test_secret_validation_failure_drops_plaintext_exception_state(tmp_path: Path) -> None:
    class Credentials(Spec):
        token: int

    root = tmp_path / "secrets"
    root.mkdir()
    secret = "secret-exception-sentinel"
    (root / "token").write_text(secret, encoding="utf-8")

    with pytest.raises(ValidationError) as raised:
        Settings(Credentials, secrets=root).load()

    error = raised.value
    assert error.value == "<redacted>"
    assert error.__cause__ is None
    assert error.__context__ is None
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("talea/settings/plan.py"):
            assert secret not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


def test_provenance_contains_only_canonical_paths_and_source_kinds() -> None:
    class Credentials(Spec):
        token: Annotated[str, Sensitive()]

    result = Settings(Credentials, prefix="INFRA_").load(environment={"INFRA_TOKEN": "super-secret"}, provenance=True)
    rendered = repr(dict(result.provenance))
    assert rendered == "{('token',): 'environment'}"
    assert "INFRA" not in rendered
    assert "super-secret" not in rendered


def test_unknown_environment_names_are_ignored_but_unknown_mappings_validate() -> None:
    class Simple(Spec):
        value: int = 1

    assert Settings(Simple, prefix="APP_").load(environment={"APP_UNKNOWN": "x"}).value == 1
    with pytest.raises(ValidationError) as override:
        Settings(Simple).load({"unknown": 1})
    assert override.value.code == "unexpected"


def test_non_string_override_key_remains_an_ordinary_mapping_failure() -> None:
    class Simple(Spec):
        value: int = 1

    with pytest.raises(ValidationError) as raised:
        Settings(Simple).load({1: "bad"})  # type: ignore[dict-item]
    assert raised.value.code == "unexpected"


def test_required_missing_is_distinct_from_source_and_resource_failures() -> None:
    class Required(Spec):
        value: int

    with pytest.raises(ValidationError) as raised:
        Settings(Required).load(environment={})
    assert raised.value.code == "missing"


def test_case_sensitive_environment_missing_does_not_use_platform_semantics() -> None:
    class Required(Spec):
        value: int

    with pytest.raises(ValidationError):
        Settings(Required, case_sensitive=True).load(environment={"VALUE": "1"})
    assert Settings(Required, case_sensitive=True).load(environment={"value": "1"}).value == 1


def test_nullable_nested_override_is_atomic_against_lower_environment() -> None:
    class Child(Spec):
        value: int

    class Root(Spec):
        child: Child | None

    plan = Settings(Root, prefix="APP_")
    value = plan.load({"child": None}, environment={"APP_CHILD__VALUE": "1"})
    assert value.child is None
