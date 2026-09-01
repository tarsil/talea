"""Model a Kubernetes-style environment and atomic-writer secret mount."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from talea import Sensitive, Spec
from talea.settings import Settings


class Database(Spec):
    host: str
    port: int
    password: Annotated[str, Sensitive()]


class Deployment(Spec):
    database: Database
    replicas: int = 2


with TemporaryDirectory() as temporary:
    mount = Path(temporary) / "mounted-secrets"
    version = mount / "..2026_09_01"
    version.mkdir(parents=True)
    (version / "database__password").write_text("first-secret\n", encoding="utf-8")
    (mount / "..data").symlink_to(version.name, target_is_directory=True)
    (mount / "database__password").symlink_to("..data/database__password")

    loader = Settings(Deployment, prefix="APP_", secrets=mount)
    config_map = {
        "APP_DATABASE__HOST": "database.default.svc",
        "APP_DATABASE__PORT": "5432",
        "APP_REPLICAS": "3",
    }
    first = loader.load(environment=config_map)
    assert first.database.password == "first-secret"
    assert first.database.host == "database.default.svc"
    assert first.replicas == 3

    # A later load is explicit and complete. Existing snapshots never change.
    (version / "database__password").write_text("second-secret\n", encoding="utf-8")
    second = loader.load(environment=config_map)
    assert first is not second
    assert first.database.password == "first-secret"
    assert second.database.password == "second-secret"
    assert "second-secret" not in repr(second)
