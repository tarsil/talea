"""Use deterministic settings sources in tests without touching os.environ."""

import os
from types import MappingProxyType

from talea import Spec
from talea.settings import Settings


class TestSettings(Spec):
    endpoint: str
    timeout: float = 2.0
    retries: int = 3


before = dict(os.environ)
loader = Settings(TestSettings, prefix="TEST_")
environment = MappingProxyType(
    {
        "TEST_ENDPOINT": "https://staging.example.test",
        "TEST_TIMEOUT": "1.5",
    }
)
settings = loader.load({"retries": 0}, environment=environment)

assert settings.endpoint == "https://staging.example.test"
assert settings.timeout == 1.5
assert settings.retries == 0
assert dict(os.environ) == before

# A CLI parser or dotenv library can provide an ordinary Mapping override.
# Talea does not parse argv or .env syntax and does not mutate the environment.
cli_override = {"timeout": 5.0}
from_cli = loader.load(cli_override, environment=environment)
assert from_cli.timeout == 5.0
assert settings.timeout == 1.5
