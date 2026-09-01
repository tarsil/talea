"""Measure Settings plan creation, source loading, failures, and zero-tax canaries."""

import gc
import subprocess
import sys
import tempfile
import tracemalloc
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from timeit import Timer
from typing import Annotated

from talea import Alias, ResourceLimitError, Sensitive, Spec, ValidationError, create_spec
from talea.settings import Settings, SettingsPolicy

_REPEATS = 5
_HOT_ITERATIONS = 200
_COLD_ITERATIONS = 50
type Operation = Callable[[], object]


def measure(operation: Operation, iterations: int = _HOT_ITERATIONS) -> tuple[float, float]:
    """Return minimum and median nanoseconds for one warmed operation."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    values = [sample * 1_000_000_000 / iterations for sample in samples]
    return min(values), median(values)


def report(name: str, operation: Operation, iterations: int = _HOT_ITERATIONS) -> None:
    """Print one stable settings benchmark row."""

    minimum, middle = measure(operation, iterations)
    print(f"{name:42} min={minimum:12.1f} ns/op median={middle:12.1f} ns/op")


def model(name: str, count: int, *, defaults: bool = False) -> type[Spec]:
    """Create one retained integer settings model of a requested width."""

    fields = {f"field_{index}": int for index in range(count)}
    values = {field: index for index, field in enumerate(fields)} if defaults else None
    return create_spec(name, fields, defaults=values, module=__name__)


def environment(count: int, prefix: str = "APP_") -> dict[str, str]:
    """Return exact textual values for a generated model."""

    return {f"{prefix}FIELD_{index}": str(index) for index in range(count)}


def hand_environment(values: Mapping[str, str], count: int, prefix: str = "APP_") -> tuple[int, ...]:
    """Apply equivalent snapshot, prefix lookup, conversion, and strict checks."""

    snapshot = dict(values)
    result = []
    for index in range(count):
        text = snapshot[f"{prefix}FIELD_{index}"]
        if not text.isascii() or not text.isdecimal():
            raise ValueError
        result.append(int(text))
    return tuple(result)


def capture(operation: Operation, expected: type[BaseException]) -> BaseException:
    """Return one expected failure for timing without rendering it."""

    try:
        operation()
    except expected as error:
        return error
    raise AssertionError("failure benchmark unexpectedly succeeded")


class Leaf(Spec):
    value: int


class Middle(Spec):
    leaf: Leaf


class Root(Spec):
    middle: Middle


class Aliased(Spec):
    value: Annotated[int, Alias("current", legacy=("historical",))]


class SensitiveSettings(Spec):
    token: Annotated[int, Sensitive()]


def benchmark_scaling() -> None:
    """Measure defaults, environment width, nesting, aliases, and lookup policy."""

    Defaults10 = model("Defaults10", 10, defaults=True)
    Environment10 = model("Environment10", 10)
    Environment50 = model("Environment50", 50)
    Environment100 = model("Environment100", 100)
    plans = {
        10: Settings(Environment10, prefix="APP_"),
        50: Settings(Environment50, prefix="APP_"),
        100: Settings(Environment100, prefix="APP_"),
    }
    report("10-field defaults only", Settings(Defaults10).load)
    for count in (10, 50, 100):
        values = environment(count)
        report(f"{count}-field environment", lambda count=count, values=values: plans[count].load(environment=values))
    nested = Settings(Root, prefix="APP_")
    report(
        "nested three-level environment",
        lambda: nested.load(environment={"APP_MIDDLE__LEAF__VALUE": "1"}),
    )
    prefixed = Settings(Environment10, prefix="SERVICE_")
    report("explicit prefix lookup", lambda: prefixed.load(environment=environment(10, "SERVICE_")))
    sensitive = Settings(SensitiveSettings, prefix="APP_")
    report("Sensitive success", lambda: sensitive.load(environment={"APP_TOKEN": "1"}))
    report(
        "case-insensitive lookup",
        lambda: plans[10].load(environment={key.lower(): value for key, value in environment(10).items()}),
    )
    exact = Settings(Environment10, prefix="APP_", case_sensitive=True)
    exact_values = {f"APP_field_{index}": str(index) for index in range(10)}
    report("case-sensitive lookup", lambda: exact.load(environment=exact_values))
    current = Settings(Aliased, prefix="APP_")
    report("current alias", lambda: current.load(environment={"APP_CURRENT": "1"}))
    report("historical alias", lambda: current.load(environment={"APP_HISTORICAL": "1"}))

    hand_values = hand_environment(environment(10), 10)
    talea_values = plans[10].load(environment=environment(10))
    if hand_values != tuple(getattr(talea_values, f"field_{index}") for index in range(10)):
        raise AssertionError("manual environment comparator is not semantically equivalent")
    report("manual equivalent 10-field", lambda: hand_environment(environment(10), 10))


def benchmark_files() -> None:
    """Measure TOML, secrets, precedence, provenance, and bounded failures."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for count in (10, 50, 100):
            Model = model(f"Toml{count}", count)
            path = root / f"{count}.toml"
            path.write_text("".join(f"field_{index} = {index}\n" for index in range(count)), encoding="utf-8")
            plan = Settings(Model, toml=path)
            report(f"TOML {count} fields", plan.load, 50)

        SecretModel = model("Secret50", 50)
        for count in (1, 10, 50):
            directory = root / f"secrets-{count}"
            directory.mkdir()
            for index in range(count):
                (directory / f"field_{index}").write_text(str(index), encoding="utf-8")
            overrides = {f"field_{index}": index for index in range(count, 50)}
            plan = Settings(SecretModel, secrets=directory)
            report(f"secret directory {count} files", lambda plan=plan, overrides=overrides: plan.load(overrides), 50)

        class Precedence(Spec):
            host: str
            port: int

        precedence_toml = root / "precedence.toml"
        precedence_toml.write_text('host = "toml"\nport = 5000\n', encoding="utf-8")
        precedence = Settings(Precedence, prefix="APP_", toml=precedence_toml)
        report(
            "TOML to env to override",
            lambda: precedence.load({"host": "override"}, environment={"APP_PORT": "6000"}),
        )
        report(
            "provenance disabled",
            lambda: precedence.load(environment={"APP_PORT": "6000"}),
        )
        report(
            "provenance enabled",
            lambda: precedence.load(environment={"APP_PORT": "6000"}, provenance=True),
        )

        oversized_toml = root / "oversized.toml"
        oversized_toml.write_bytes(b"x" * 100)
        limited_toml = Settings(Precedence, toml=oversized_toml, policy=SettingsPolicy(max_toml_bytes=8))
        report(
            "oversized TOML rejection",
            lambda: capture(limited_toml.load, ResourceLimitError),
        )
        oversized_secret = root / "oversized-secret"
        oversized_secret.mkdir()
        (oversized_secret / "port").write_bytes(b"1" * 100)
        limited_secret = Settings(
            Precedence,
            secrets=oversized_secret,
            policy=SettingsPolicy(max_secret_file_bytes=8),
        )
        report(
            "oversized secret rejection",
            lambda: capture(lambda: limited_secret.load({"host": "x"}), ResourceLimitError),
        )
        many_secrets = root / "many-secrets"
        many_secrets.mkdir()
        for index in range(10):
            (many_secrets / f"unknown-{index}").write_text("x", encoding="utf-8")
        limited_count = Settings(Precedence, secrets=many_secrets, policy=SettingsPolicy(max_secret_files=4))
        report(
            "secret count rejection",
            lambda: capture(lambda: limited_count.load({"host": "x", "port": 1}), ResourceLimitError),
        )


def benchmark_lifecycle() -> None:
    """Measure cold plans, warm loads, failures, retention, concurrency, and canaries."""

    Model = model("Lifecycle100", 100)
    values = environment(100)
    report("cold 100-field plan", lambda: Settings(Model, prefix="APP_"), _COLD_ITERATIONS)
    plan = Settings(Model, prefix="APP_")
    report("warm repeated load", lambda: plan.load(environment=values), 50)
    early = dict(values)
    early["APP_FIELD_0"] = "invalid"
    late = dict(values)
    late["APP_FIELD_99"] = "invalid"
    report("invalid early field", lambda: capture(lambda: plan.load(environment=early), ValidationError))
    report("invalid late field", lambda: capture(lambda: plan.load(environment=late), ValidationError))

    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    for _ in range(100):
        Settings(Model, prefix="APP_")
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"discarded plan retention                    retained={retained - before:8d} B peak={peak - before:8d} B")

    def concurrent() -> object:
        with ThreadPoolExecutor(max_workers=4) as executor:
            return tuple(executor.map(lambda _: plan.load(environment=values), range(8)))

    report("concurrent immutable loads", concurrent, 25)

    class Canary(Spec):
        value: int

    canary = Canary(value=1)
    report("ordinary Spec construction canary", lambda: Canary(value=1))
    report("ordinary Mapping canary", lambda: Canary.from_mapping({"value": 1}))
    report("ordinary JSON canary", lambda: Canary.from_json('{"value":1}'))
    report("ordinary serialization canary", canary.to_dict)
    subprocess.run(
        [sys.executable, "-c", "import sys,talea; assert 'talea.settings' not in sys.modules"],
        check=True,
    )
    print("root import isolation canary                 PASS")


def main() -> None:
    """Run the permanent Settings benchmark inventory."""

    benchmark_scaling()
    benchmark_files()
    benchmark_lifecycle()


if __name__ == "__main__":
    main()
