"""Positive and negative static contracts for the Talea Settings domain."""

from pathlib import Path
from typing import assert_type

from talea import ResourcePolicy, Spec
from talea.settings import Settings, SettingsInfo, SettingsPolicy, SettingsResult


class ApplicationSettings(Spec):
    host: str
    port: int = 8000


plan = Settings(
    ApplicationSettings,
    prefix="APP_",
    toml=Path("app.toml"),
    secrets=Path("secrets"),
    policy=SettingsPolicy(input_policy=ResourcePolicy()),
)

assert_type(plan, Settings[ApplicationSettings])
assert_type(plan.info, SettingsInfo)
assert_type(plan.load(), ApplicationSettings)
assert_type(plan.load({"host": "localhost"}), ApplicationSettings)
assert_type(plan.load(environment={"APP_HOST": "localhost"}), ApplicationSettings)
assert_type(plan.load(provenance=True), SettingsResult[ApplicationSettings])
assert_type(plan.load(provenance=True).value, ApplicationSettings)

Settings(int)  # ty: ignore[invalid-argument-type]
Settings(ApplicationSettings, prefix=1)  # ty: ignore[invalid-argument-type]
Settings(ApplicationSettings, policy=ResourcePolicy())  # ty: ignore[invalid-argument-type]
plan.load([])  # ty: ignore[invalid-argument-type]
plan.load(environment={"APP_PORT": 8000})  # ty: ignore[invalid-argument-type]
plan.load(provenance="yes")  # ty: ignore[no-matching-overload]
