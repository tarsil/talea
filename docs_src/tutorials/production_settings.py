"""Load a service configuration from TOML, environment, and a test override."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from talea import Alias, Sensitive, Spec
from talea.settings import Settings


class DatabaseSettings(Spec):
    host: str
    port: int = 5432
    password: Annotated[str, Sensitive()]


class HttpSettings(Spec):
    bind: str = "127.0.0.1"
    port: int = 8000


class FeatureSettings(Spec):
    audit_log: bool = False
    new_checkout: bool = False


class ServiceSettings(Spec):
    database_config: Annotated[DatabaseSettings, Alias("database", legacy=("db",))]
    http: HttpSettings
    features: FeatureSettings


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    config = root / "service.toml"
    config.write_text(
        """
[database]
host = "db.internal"
port = 5432

[http]
bind = "0.0.0.0"
port = 8000

[features]
audit_log = true
new_checkout = false
""".strip(),
        encoding="utf-8",
    )
    secrets = root / "secrets"
    secrets.mkdir()
    (secrets / "database__password").write_text("mounted-password\n", encoding="utf-8")

    loader = Settings(
        ServiceSettings,
        prefix="SERVICE_",
        toml=config,
        secrets=secrets,
    )
    loaded = loader.load(
        {"http": {"port": 9000}},
        environment={"SERVICE_DATABASE__PORT": "6432"},
        provenance=True,
    )

    assert loaded.value.database_config.host == "db.internal"  # TOML
    assert loaded.value.database_config.port == 6432  # environment
    assert loaded.value.database_config.password == "mounted-password"  # secret file
    assert loaded.value.http.bind == "0.0.0.0"  # TOML sibling survives
    assert loaded.value.http.port == 9000  # explicit override
    assert loaded.value.features.audit_log is True
    assert loaded.provenance[("database_config", "host")] == "toml"
    assert loaded.provenance[("database_config", "port")] == "environment"
    assert loaded.provenance[("database_config", "password")] == "secret"
    assert loaded.provenance[("http", "port")] == "override"
    assert "mounted-password" not in repr(loaded.value)
