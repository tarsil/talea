"""Exercise explicit TOML and flat local-secrets acquisition boundaries."""

from pathlib import Path
from typing import Annotated

import pytest

from talea import Alias, ResourceLimitError, Sensitive, Spec, ValidationError
from talea.settings import Settings, SettingsPolicy


class FileDatabase(Spec):
    host: str
    port: int = 5432
    password: Annotated[str, Sensitive()] = "local"


class FileSettings(Spec):
    database_config: Annotated[FileDatabase, Alias("database", legacy=("db",))]
    workers: int = 2


def test_toml_typed_values_nested_tables_aliases_and_environment_override(tmp_path: Path) -> None:
    path = tmp_path / "app.toml"
    path.write_text('[db]\nhost = "toml"\nport = 5000\nworkers = 4\n', encoding="utf-8")
    # workers belongs at the root, so the nested occurrence remains an ordinary
    # unexpected-field validation failure rather than settings-specific magic.
    with pytest.raises(ValidationError):
        Settings(FileSettings, toml=path).load()

    path.write_text('workers = 4\n[db]\nhost = "toml"\nport = 5000\n', encoding="utf-8")
    value = Settings(FileSettings, prefix="APP_", toml=path).load(environment={"APP_DATABASE__PORT": "6000"})
    assert (value.database_config.host, value.database_config.port, value.workers) == ("toml", 6000, 4)


def test_toml_missing_directory_malformed_and_invalid_utf8(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(FileNotFoundError):
        Settings(FileSettings, toml=missing).load()

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(IsADirectoryError):
        Settings(FileSettings, toml=directory).load()

    malformed = tmp_path / "malformed.toml"
    malformed.write_text("not = [toml", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed settings TOML") as malformed_error:
        Settings(FileSettings, toml=malformed).load()
    assert "not =" not in repr(vars(malformed_error.value))

    binary = tmp_path / "binary.toml"
    binary.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="TOML is not valid UTF-8"):
        Settings(FileSettings, toml=binary).load()


def test_toml_limit_rejects_before_parse(tmp_path: Path) -> None:
    path = tmp_path / "large.toml"
    path.write_bytes(b"[" + b"x" * 100)
    policy = SettingsPolicy(max_toml_bytes=16)
    with pytest.raises(ResourceLimitError) as raised:
        Settings(FileSettings, toml=path, policy=policy).load()
    assert raised.value.code == "settings_toml_bytes"
    assert raised.value.observed == 17


def test_secret_file_name_content_newline_alias_and_precedence(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "db__host").write_text("secret-host\r\n", encoding="utf-8")
    (root / "database__password").write_text("  exact whitespace  \n", encoding="utf-8")
    (root / "unknown").write_text("ignored", encoding="utf-8")

    value = Settings(FileSettings, secrets=root).load()
    assert value.database_config.host == "secret-host"
    assert value.database_config.password == "  exact whitespace  "


def test_secret_multiple_lines_are_preserved_for_string_fields(tmp_path: Path) -> None:
    class Multiline(Spec):
        token: str

    root = tmp_path / "secrets"
    root.mkdir()
    (root / "token").write_text("line1\nline2\n", encoding="utf-8")
    assert Settings(Multiline, secrets=root).load().token == "line1\nline2"


def test_secret_missing_and_non_directory_paths(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Settings(FileSettings, secrets=tmp_path / "missing").load()

    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        Settings(FileSettings, secrets=file_path).load()


def test_secret_invalid_utf8_and_per_file_limit(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    secret = root / "database__password"
    secret.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="secret is not valid UTF-8"):
        Settings(FileSettings, secrets=root).load({"database": {"host": "x"}})

    secret.write_bytes(b"x" * 20)
    policy = SettingsPolicy(max_secret_file_bytes=8)
    with pytest.raises(ResourceLimitError) as raised:
        Settings(FileSettings, secrets=root, policy=policy).load({"database": {"host": "x"}})
    assert raised.value.code == "settings_secret_file_bytes"
    assert raised.value.observed == 9


def test_secret_file_count_includes_unexpected_flat_files(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    for index in range(3):
        (root / f"unknown-{index}").write_text("x", encoding="utf-8")
    policy = SettingsPolicy(max_secret_files=2)

    with pytest.raises(ResourceLimitError) as raised:
        Settings(FileSettings, secrets=root, policy=policy).load({"database": {"host": "x"}})
    assert raised.value.code == "settings_secret_files"
    assert raised.value.observed == 3


def test_kubernetes_atomic_writer_style_symlinks_are_supported(tmp_path: Path) -> None:
    root = tmp_path / "mount"
    version = root / "..2026_09_01"
    version.mkdir(parents=True)
    (version / "database__host").write_text("cluster", encoding="utf-8")
    (root / "..data").symlink_to(version.name, target_is_directory=True)
    (root / "database__host").symlink_to("..data/database__host")

    value = Settings(FileSettings, secrets=root).load()
    assert value.database_config.host == "cluster"


def test_secret_symlink_cannot_escape_explicit_root(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret", encoding="utf-8")
    (root / "database__password").symlink_to(outside)

    with pytest.raises(ValueError, match="remain within"):
        Settings(FileSettings, secrets=root).load({"database": {"host": "x"}})


def test_broken_secret_symlink_fails_atomically(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "database__host").symlink_to("missing")

    with pytest.raises(FileNotFoundError):
        Settings(FileSettings, secrets=root).load()


def test_aggregate_source_bytes_cover_toml_environment_and_secrets(tmp_path: Path) -> None:
    toml_path = tmp_path / "app.toml"
    toml_path.write_text('[database]\nhost = "toml"\n', encoding="utf-8")
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "database__password").write_text("secret", encoding="utf-8")
    policy = SettingsPolicy(max_source_bytes=40)

    with pytest.raises(ResourceLimitError) as raised:
        Settings(FileSettings, prefix="APP_", toml=toml_path, secrets=root, policy=policy).load(
            environment={"APP_DATABASE__PORT": "6000"}
        )
    assert raised.value.code == "settings_source_bytes"


def test_explicit_secret_root_may_itself_be_a_symlink(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "database__host").write_text("linked-root", encoding="utf-8")
    root = tmp_path / "root"
    root.symlink_to(actual, target_is_directory=True)

    assert Settings(FileSettings, secrets=root).load().database_config.host == "linked-root"


def test_secret_directory_does_not_recurse(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    nested = root / "database"
    nested.mkdir(parents=True)
    (nested / "host").write_text("nested", encoding="utf-8")

    with pytest.raises(ValidationError) as raised:
        Settings(FileSettings, secrets=root).load()
    assert raised.value.code == "missing"


def test_secret_validation_failure_is_redacted_even_without_sensitive_metadata(tmp_path: Path) -> None:
    class NumericSecret(Spec):
        token: int

    root = tmp_path / "secrets"
    root.mkdir()
    (root / "token").write_text("super-secret-value", encoding="utf-8")

    with pytest.raises(ValidationError) as raised:
        Settings(NumericSecret, secrets=root).load()
    assert raised.value.value == "<redacted>"
    assert "super-secret-value" not in str(raised.value)
    assert "super-secret-value" not in repr(raised.value.errors())
    assert raised.value.__cause__ is None
