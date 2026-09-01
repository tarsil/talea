"""Exercise explicit TOML and flat local-secrets acquisition boundaries."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, BinaryIO

import pytest

import talea.settings.plan as settings_plan
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
    assert malformed_error.value.__context__ is None

    binary = tmp_path / "binary.toml"
    binary.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="TOML is not valid UTF-8") as invalid_utf8:
        Settings(FileSettings, toml=binary).load()
    assert invalid_utf8.value.__context__ is None


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
    with pytest.raises(ValueError, match="secret is not valid UTF-8") as invalid_utf8:
        Settings(FileSettings, secrets=root).load({"database": {"host": "x"}})
    assert invalid_utf8.value.__context__ is None

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


def test_secret_file_count_bounds_directory_enumeration(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    for index in range(3):
        (root / f"directory-{index}").mkdir()
    policy = SettingsPolicy(max_secret_files=2)

    with pytest.raises(ResourceLimitError) as raised:
        Settings(FileSettings, secrets=root, policy=policy).load({"database": {"host": "x"}})
    assert raised.value.code == "settings_secret_files"
    assert raised.value.observed == 3


def test_secret_file_limit_stops_streaming_enumeration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    observed = 0

    class Entry:
        name = "unknown"

    class Directory:
        def __enter__(self) -> Directory:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> Iterator[Entry]:
            nonlocal observed
            for _ in range(1_000_000):
                observed += 1
                yield Entry()

    monkeypatch.setattr(settings_plan.os, "scandir", lambda _root: Directory())
    with pytest.raises(ResourceLimitError) as raised:
        Settings(FileSettings, secrets=root, policy=SettingsPolicy(max_secret_files=2)).load()
    assert (raised.value.code, raised.value.observed, observed) == ("settings_secret_files", 3, 3)


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


def test_secret_target_swap_cannot_escape_after_authorization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    secret = root / "database__password"
    secret.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.write_text("outside-secret-sentinel", encoding="utf-8")
    real_open = settings_plan.os.open
    swapped = False

    def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if path == "database__password" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            secret.unlink()
            secret.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(settings_plan.os, "open", swapping_open)
    with pytest.raises(OSError) as raised:
        Settings(FileSettings, secrets=root).load({"database": {"host": "x"}})
    assert swapped
    assert "outside-secret-sentinel" not in repr(raised.value)


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


def test_aggregate_source_bytes_bound_each_file_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "database__host").write_bytes(b"x" * 100)
    real_read = settings_plan._read_bounded
    read_sizes: list[int] = []

    def tracking_read(stream: BinaryIO, maximum: int | None) -> bytes:
        read_sizes.append(-1 if maximum is None else maximum + 1)
        return real_read(stream, maximum)

    monkeypatch.setattr(settings_plan, "_read_bounded", tracking_read)
    policy = SettingsPolicy(max_source_bytes=4, max_secret_file_bytes=100)

    with pytest.raises(ResourceLimitError) as raised:
        Settings(FileSettings, secrets=root, policy=policy).load()
    assert raised.value.code == "settings_source_bytes"
    assert raised.value.observed == 5
    assert read_sizes == [5]


def test_explicit_secret_root_may_itself_be_a_symlink(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "database__host").write_text("linked-root", encoding="utf-8")
    root = tmp_path / "root"
    root.symlink_to(actual, target_is_directory=True)

    assert Settings(FileSettings, secrets=root).load().database_config.host == "linked-root"


def test_secret_fallback_accepts_direct_files_and_rejects_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    direct = root / "database__host"
    direct.write_text("direct", encoding="utf-8")
    monkeypatch.setattr(settings_plan, "_SUPPORTS_SECURE_DIR_FD", False)
    assert Settings(FileSettings, secrets=root).load().database_config.host == "direct"

    direct.unlink()
    target = root / "target"
    target.write_text("linked", encoding="utf-8")
    direct.symlink_to(target.name)
    with pytest.raises(ValueError, match="descriptor-relative"):
        Settings(FileSettings, secrets=root).load()


def test_secret_fallback_detects_file_swap_before_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    secret = root / "database__host"
    secret.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.write_text("outside-secret-sentinel", encoding="utf-8")
    real_open = settings_plan.os.open
    monkeypatch.setattr(settings_plan, "_SUPPORTS_SECURE_DIR_FD", False)
    swapped = False

    def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if path == secret and not swapped:
            swapped = True
            secret.unlink()
            secret.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(settings_plan.os, "open", swapping_open)
    with pytest.raises(OSError, match="changed during acquisition"):
        Settings(FileSettings, secrets=root).load()
    assert swapped


def test_secret_descriptor_guards_identity_and_regular_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "database__host").write_text("value", encoding="utf-8")
    real_fstat = settings_plan.os.fstat

    class Changed:
        st_dev = -1
        st_ino = -1

    monkeypatch.setattr(settings_plan.os, "fstat", lambda _fd: Changed())
    with pytest.raises(OSError, match="changed during acquisition"):
        Settings(FileSettings, secrets=root).load()
    monkeypatch.setattr(settings_plan.os, "fstat", real_fstat)

    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(IsADirectoryError):
            settings_plan._bounded_secret_file(root_fd, (), 10, settings_plan._SourceBudget(10))
        directory = root / "directory"
        directory.mkdir()
        with pytest.raises(IsADirectoryError):
            settings_plan._bounded_secret_file(root_fd, ("directory",), 10, settings_plan._SourceBudget(10))
    finally:
        os.close(root_fd)


def test_fallback_verified_file_enforces_each_read_limit(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(IsADirectoryError):
        settings_plan._bounded_verified_file(
            directory,
            10,
            "settings_secret_file_bytes",
            settings_plan._SourceBudget(10),
        )

    path = tmp_path / "secret"
    path.write_bytes(b"0123456789")
    with pytest.raises(ResourceLimitError) as per_file:
        settings_plan._bounded_verified_file(
            path,
            4,
            "settings_secret_file_bytes",
            settings_plan._SourceBudget(None),
        )
    assert (per_file.value.code, per_file.value.observed) == ("settings_secret_file_bytes", 5)

    with pytest.raises(ResourceLimitError) as aggregate:
        settings_plan._bounded_verified_file(
            path,
            None,
            "settings_secret_file_bytes",
            settings_plan._SourceBudget(4),
        )
    assert (aggregate.value.code, aggregate.value.observed) == ("settings_source_bytes", 5)
    assert (
        settings_plan._bounded_verified_file(
            path,
            None,
            "settings_secret_file_bytes",
            settings_plan._SourceBudget(None),
        )
        == b"0123456789"
    )


def test_toml_aggregate_limit_and_unbounded_read_paths(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('workers = 2\n[database]\nhost = "value"\n', encoding="utf-8")
    with pytest.raises(ResourceLimitError) as raised:
        Settings(
            FileSettings,
            toml=path,
            policy=SettingsPolicy(max_toml_bytes=None, max_source_bytes=4),
        ).load()
    assert raised.value.code == "settings_source_bytes"

    value = Settings(
        FileSettings,
        toml=path,
        policy=SettingsPolicy(max_toml_bytes=None, max_source_bytes=None),
    ).load()
    assert value.database_config.host == "value"


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
