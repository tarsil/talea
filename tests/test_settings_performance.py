"""Prove the permanent Settings comparator matches its benchmarked contract."""

from collections.abc import Callable
from typing import Annotated

import pytest
from hypothesis import given, strategies as st

from benchmarks.settings import (
    EquivalentEnvironment,
    ManualField,
    MixedSettings,
    equivalent_integer_environment,
    mixed_comparator,
    mixed_environment,
)
from talea import Alias, ResourceLimitError, Sensitive, Spec, ValidationError
from talea.settings import Settings, SettingsPolicy


class ComparatorPair(Spec):
    field_0: int
    field_1: int


_PAIR_PLAN = Settings(ComparatorPair, prefix="APP_")
_PAIR_COMPARATOR = equivalent_integer_environment(ComparatorPair, 2)


def _accepted(operation: Callable[[], object]) -> tuple[bool, object | None]:
    try:
        value = operation()
    except (TypeError, ValueError, ValidationError, ResourceLimitError):
        return False, None
    assert isinstance(value, Spec)
    return True, value.to_dict()


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("APP_COUNT", "invalid"),
        ("APP_ENABLED", "yes"),
        ("APP_RATIO", "invalid"),
        ("APP_RATIO", "NaN"),
        ("APP_RATIO", "Infinity"),
    ],
)
def test_equivalent_mixed_comparator_rejects_like_settings(field: str, text: str) -> None:
    environment = mixed_environment()
    environment[field] = text
    plan = Settings(MixedSettings, prefix="APP_")

    assert _accepted(lambda: plan.load(environment=environment)) == _accepted(
        lambda: mixed_comparator().load(environment)
    )


def test_equivalent_mixed_comparator_matches_valid_missing_nested_and_unknown_inputs() -> None:
    comparator = mixed_comparator()
    plan = Settings(MixedSettings, prefix="APP_")
    valid = mixed_environment()
    unknown = {**valid, "APP_UNKNOWN": "ignored"}
    missing = dict(valid)
    missing.pop("APP_NESTED__DEPTH")

    for environment in (valid, unknown, missing):
        assert _accepted(lambda environment=environment: plan.load(environment=environment)) == _accepted(
            lambda environment=environment: comparator.load(environment)
        )


def test_equivalent_comparator_prefix_case_alias_conflict_and_entry_limit() -> None:
    class Aliased(Spec):
        value: Annotated[int, Alias("current", legacy=("old", "older"))]

    field = ManualField(("value",), ("current",), ("current", "old", "older"), int)
    policy = SettingsPolicy(max_environment_entries=2)
    comparator = EquivalentEnvironment(Aliased, (field,), prefix="APP_", policy=policy)
    plan = Settings(Aliased, prefix="APP_", policy=policy)
    cases = (
        {"app_current": "1"},
        {"APP_OLD": "2"},
        {"APP_OLDER": "3"},
        {"APP_CURRENT": "1", "APP_OLD": "1"},
        {"APP_CURRENT": "1", "APP_UNKNOWN": "x", "OTHER": "x"},
    )

    for environment in cases:
        assert _accepted(lambda environment=environment: plan.load(environment=environment)) == _accepted(
            lambda environment=environment: comparator.load(environment)
        )

    exact = EquivalentEnvironment(Aliased, (field,), prefix="APP_", case_sensitive=True)
    exact_plan = Settings(Aliased, prefix="APP_", case_sensitive=True)
    assert _accepted(lambda: exact_plan.load(environment={"APP_current": "4"})) == _accepted(
        lambda: exact.load({"APP_current": "4"})
    )
    assert _accepted(lambda: exact_plan.load(environment={"app_current": "4"})) == _accepted(
        lambda: exact.load({"app_current": "4"})
    )


def test_equivalent_comparator_preserves_sensitive_failure_evidence() -> None:
    class Credentials(Spec):
        token: Annotated[int, Sensitive()]

    comparator = EquivalentEnvironment(
        Credentials,
        (
            ManualField(
                ("token",),
                ("token",),
                ("token",),
                lambda text: int(text) if text.isdecimal() else text,
            ),
        ),
        prefix="APP_",
    )
    plan = Settings(Credentials, prefix="APP_")
    source = {"APP_TOKEN": "not-a-secret-to-render"}

    for operation in (lambda: plan.load(environment=source), lambda: comparator.load(source)):
        with pytest.raises((TypeError, ValueError, ValidationError)) as raised:
            operation()
        assert "not-a-secret-to-render" not in str(raised.value)
        assert "not-a-secret-to-render" not in repr(raised.value)


@given(
    st.dictionaries(
        st.sampled_from(("APP_FIELD_0", "app_field_0", "APP_FIELD_1", "app_field_1", "APP_UNKNOWN")),
        st.text(max_size=8),
        max_size=5,
    )
)
def test_equivalent_comparator_matches_random_finite_environment(environment: dict[str, str]) -> None:
    assert _accepted(lambda: _PAIR_PLAN.load(environment=environment)) == _accepted(
        lambda: _PAIR_COMPARATOR.load(environment)
    )
