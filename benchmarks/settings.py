"""Measure Settings plan creation, source loading, failures, and zero-tax canaries."""

import gc
import math
import re
import subprocess
import sys
import tempfile
import tracemalloc
import weakref
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from timeit import Timer
from typing import Annotated, NamedTuple, cast
from uuid import UUID

from talea import (
    Alias,
    Ge,
    Representation,
    ResourceLimitError,
    Sensitive,
    Spec,
    ValidationError,
    create_spec,
)
from talea.settings import Settings, SettingsPolicy

_REPEATS = 5
_HOT_ITERATIONS = 200
_COLD_ITERATIONS = 50
type Operation = Callable[[], object]
type TextDecoder = Callable[[str], object]

_INTEGER = re.compile(r"-?(?:0|[1-9]\d*)\Z")
_FLOAT = re.compile(r"-?(?:(?:0|[1-9]\d*)(?:\.\d+)?)(?:[eE][+-]?\d+)?\Z")


def measure(operation: Operation, iterations: int = _HOT_ITERATIONS) -> tuple[float, float]:
    """Return minimum and median nanoseconds for one warmed operation."""

    operation()
    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    values = [sample * 1_000_000_000 / iterations for sample in samples]
    return min(values), median(values)


def report(name: str, operation: Operation, iterations: int = _HOT_ITERATIONS) -> tuple[float, float]:
    """Print one stable settings benchmark row."""

    minimum, middle = measure(operation, iterations)
    print(f"{name:42} min={minimum:12.1f} ns/op median={middle:12.1f} ns/op")
    return minimum, middle


def allocation_report(name: str, operation: Operation, iterations: int = 100) -> None:
    """Report retained and peak traced memory for repeated completed loads."""

    operation()
    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    for _ in range(iterations):
        operation()
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{name:42} retained={retained - before:8d} B peak={peak - before:8d} B")


def model(name: str, count: int, *, defaults: bool = False) -> type[Spec]:
    """Create one retained integer settings model of a requested width."""

    fields = {f"field_{index}": int for index in range(count)}
    values = {field: index for index, field in enumerate(fields)} if defaults else None
    return create_spec(name, fields, defaults=values, module=__name__)


def environment(count: int, prefix: str = "APP_") -> dict[str, str]:
    """Return exact textual values for a generated model."""

    return {f"{prefix}FIELD_{index}": str(index) for index in range(count)}


def narrow_environment(values: Mapping[str, str], count: int, prefix: str = "APP_") -> tuple[int, ...]:
    """Retain the historical narrow, deliberately non-equivalent lower bound.

    This omits bounded acquisition, complete entry validation, case folding,
    accepted-name conflict detection, canonical Mapping construction,
    ``ResourcePolicy``, and Talea model construction.
    """

    snapshot = dict(values)
    result = []
    for index in range(count):
        text = snapshot[f"{prefix}FIELD_{index}"]
        if not text.isascii() or not text.isdecimal():
            raise ValueError
        result.append(int(text))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ManualField:
    """Describe one explicit field in the diagnostic manual comparator."""

    canonical_path: tuple[str, ...]
    external_path: tuple[str, ...]
    accepted_names: tuple[str, ...]
    decoder: TextDecoder


class EquivalentEnvironment[SettingsT: Spec]:
    """Handwritten equivalent executor for one explicit environment workload.

    The comparator deliberately shares only Talea's canonical final
    ``Spec.from_mapping`` boundary. Source acquisition, matching, accounting,
    decoding, conflict detection, and materialization are independently
    handwritten so the benchmark isolates Settings-owned execution overhead
    without weakening model validation or ``ResourcePolicy`` semantics.
    """

    __slots__ = ("_bindings", "_case_sensitive", "_model", "_policy")

    def __init__(
        self,
        model: type[SettingsT],
        fields: tuple[ManualField, ...],
        *,
        prefix: str = "",
        case_sensitive: bool = False,
        policy: SettingsPolicy | None = None,
    ) -> None:
        selected_policy = SettingsPolicy() if policy is None else policy
        bindings: dict[str, ManualField] = {}
        for field in fields:
            for accepted in field.accepted_names:
                display = prefix + "__".join((*field.external_path[:-1], accepted))
                normalized = display if case_sensitive else display.casefold()
                if normalized in bindings:
                    raise ValueError("manual source-name collision")
                bindings[normalized] = field
        self._model = model
        self._bindings = bindings
        self._case_sensitive = case_sensitive
        self._policy = selected_policy

    def load(self, environment: Mapping[str, str]) -> SettingsT:
        """Resolve one detached snapshot with the benchmarked Settings contract."""

        if not isinstance(environment, Mapping):
            raise TypeError("environment must be a Mapping")
        maximum_entries = self._policy.max_environment_entries
        observed_entries = len(environment)
        if maximum_entries is not None and observed_entries > maximum_entries:
            raise ResourceLimitError("settings_environment_entries", maximum_entries, observed_entries)
        snapshot = tuple(environment.items())
        resolved: dict[tuple[str, ...], tuple[ManualField, str, str]] = {}
        observed_bytes = 0
        maximum_bytes = self._policy.max_source_bytes
        for key, text in snapshot:
            if type(key) is not str or type(text) is not str:
                raise TypeError("environment keys and values must be str")
            normalized = key if self._case_sensitive else key.casefold()
            field = self._bindings.get(normalized)
            if field is None:
                continue
            observed_bytes += _manual_utf8_size(key) + _manual_utf8_size(text)
            if maximum_bytes is not None and observed_bytes > maximum_bytes:
                raise ResourceLimitError("settings_source_bytes", maximum_bytes, observed_bytes)
            previous = resolved.get(field.canonical_path)
            if previous is not None:
                raise ValueError(f"conflicting accepted names: {previous[1]!r}, {key!r}")
            resolved[field.canonical_path] = (field, key, text)
        external: dict[object, object] = {}
        for field, _, text in resolved.values():
            current = external
            for segment in field.external_path[:-1]:
                nested = current.get(segment)
                if not isinstance(nested, dict):
                    nested = {}
                    current[segment] = nested
                current = nested
            current[field.external_path[-1]] = field.decoder(text)
        return self._model.from_mapping(cast(dict[str, object], external), policy=self._policy.input_policy)


def equivalent_integer_environment(
    model_type: type[Spec],
    count: int,
    *,
    prefix: str = "APP_",
    case_sensitive: bool = False,
    policy: SettingsPolicy | None = None,
) -> EquivalentEnvironment[Spec]:
    """Compile the explicit equivalent comparator for generated integer models."""

    fields = tuple(
        ManualField((name,), (name,), (name,), _manual_int) for name in (f"field_{index}" for index in range(count))
    )
    return EquivalentEnvironment(
        model_type,
        fields,
        prefix=prefix,
        case_sensitive=case_sensitive,
        policy=policy,
    )


def _manual_utf8_size(value: str) -> int:
    return len(value) if value.isascii() else len(value.encode("utf-8"))


def _manual_int(text: str) -> object:
    return int(text) if _INTEGER.fullmatch(text) else text


def _manual_bool(text: str) -> object:
    if text == "true":
        return True
    if text == "false":
        return False
    return text


def _manual_float(text: str) -> object:
    if _FLOAT.fullmatch(text) is None:
        return text
    value = float(text)
    return value if math.isfinite(value) else text


def _manual_optional_int(text: str) -> object:
    return None if text == "null" else _manual_int(text)


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
    value: Annotated[int, Alias("current", legacy=("historical", "ancient"))]


class SensitiveSettings(Spec):
    token: Annotated[int, Sensitive()]


class MixedNested(Spec):
    depth: int


class MixedSettings(Spec):
    name: str
    count: int
    enabled: bool
    ratio: float
    identifier: UUID
    path: Path
    optional: int | None
    constrained: Annotated[int, Ge(0)]
    nested: MixedNested
    retries: int


class DepthOne(Spec):
    value: int


class DepthTwo(Spec):
    child: DepthOne


class DepthThree(Spec):
    child: DepthTwo


class DepthFour(Spec):
    child: DepthThree


class DepthFive(Spec):
    child: DepthFour


class Point(NamedTuple):
    x: int
    y: int


class NamedTupleSettings(Spec):
    point: Point


class WrappedInteger:
    def __init__(self, value: int) -> None:
        self.value = value


class RepresentationSettings(Spec):
    value: Annotated[WrappedInteger, Representation(input=int, load=WrappedInteger)]


def mixed_comparator() -> EquivalentEnvironment[MixedSettings]:
    """Return the explicit comparator for the representative mixed model."""

    fields = (
        ManualField(("name",), ("name",), ("name",), lambda text: text),
        ManualField(("count",), ("count",), ("count",), _manual_int),
        ManualField(("enabled",), ("enabled",), ("enabled",), _manual_bool),
        ManualField(("ratio",), ("ratio",), ("ratio",), _manual_float),
        ManualField(("identifier",), ("identifier",), ("identifier",), _manual_uuid),
        ManualField(("path",), ("path",), ("path",), Path),
        ManualField(("optional",), ("optional",), ("optional",), _manual_optional_int),
        ManualField(("constrained",), ("constrained",), ("constrained",), _manual_int),
        ManualField(("nested", "depth"), ("nested", "depth"), ("depth",), _manual_int),
        ManualField(("retries",), ("retries",), ("retries",), _manual_int),
    )
    return EquivalentEnvironment(MixedSettings, fields, prefix="APP_")


def mixed_environment() -> dict[str, str]:
    """Return the representative mixed ten-field source snapshot."""

    return {
        "APP_NAME": "service",
        "APP_COUNT": "7",
        "APP_ENABLED": "true",
        "APP_RATIO": "1.25",
        "APP_IDENTIFIER": "12345678-1234-5678-1234-567812345678",
        "APP_PATH": "/srv/service",
        "APP_OPTIONAL": "null",
        "APP_CONSTRAINED": "3",
        "APP_NESTED__DEPTH": "2",
        "APP_RETRIES": "5",
    }


def _manual_uuid(text: str) -> object:
    try:
        return UUID(text)
    except ValueError:
        return text


def benchmark_scaling() -> None:
    """Measure defaults, environment width, nesting, aliases, and lookup policy."""

    widths = (1, 10, 50, 100)
    environment_models = {count: model(f"Environment{count}", count) for count in widths}
    default_models = {count: model(f"Defaults{count}", count, defaults=True) for count in widths}
    plans = {count: Settings(Model, prefix="APP_") for count, Model in environment_models.items()}
    for count in widths:
        Model = environment_models[count]
        report(f"cold {count}-field plan", lambda Model=Model: Settings(Model, prefix="APP_"), _COLD_ITERATIONS)
        default_plan = Settings(default_models[count])
        report(f"{count}-field defaults only", lambda default_plan=default_plan: default_plan.load(environment={}))

    report("historical 10-field defaults/process env", Settings(default_models[10]).load)
    for count in widths:
        values = environment(count)
        report(f"{count}-field environment", lambda count=count, values=values: plans[count].load(environment=values))

    nested_cases = (
        ("depth-one", Settings(DepthOne, prefix="APP_"), {"APP_VALUE": "1"}),
        ("depth-three", Settings(DepthThree, prefix="APP_"), {"APP_CHILD__CHILD__VALUE": "1"}),
        (
            "depth-five",
            Settings(DepthFive, prefix="APP_"),
            {"APP_CHILD__CHILD__CHILD__CHILD__VALUE": "1"},
        ),
    )
    for name, nested, values in nested_cases:
        report(f"nested environment {name}", lambda nested=nested, values=values: nested.load(environment=values))

    Environment10 = environment_models[10]
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
    report("first historical alias", lambda: current.load(environment={"APP_HISTORICAL": "1"}))
    report("later historical alias", lambda: current.load(environment={"APP_ANCIENT": "1"}))
    report(
        "historical alias conflict",
        lambda: capture(
            lambda: current.load(environment={"APP_HISTORICAL": "1", "APP_ANCIENT": "1"}),
            ValidationError,
        ),
    )

    override_values = {f"field_{index}": index for index in range(10)}
    report("10-field override only", lambda: plans[10].load(override_values, environment={}))
    default_environment = {f"APP_FIELD_{index}": str(index + 10) for index in range(5)}
    defaults_plan = Settings(default_models[10], prefix="APP_")
    report("environment plus defaults", lambda: defaults_plan.load(environment=default_environment))
    representation_plan = Settings(RepresentationSettings, prefix="APP_")
    report(
        "Representation-backed field",
        lambda: representation_plan.load(environment={"APP_VALUE": "1"}),
    )
    namedtuple_plan = Settings(NamedTupleSettings, prefix="APP_")
    report("NamedTuple field", lambda: namedtuple_plan.load(environment={"APP_POINT": "[1,2]"}))

    hand_values = narrow_environment(environment(10), 10)
    talea_values = plans[10].load(environment=environment(10))
    if hand_values != tuple(getattr(talea_values, f"field_{index}") for index in range(10)):
        raise AssertionError("narrow environment lower bound changed values")
    for count in widths:
        Model = environment_models[count]
        values = environment(count)
        talea = plans[count]
        equivalent = equivalent_integer_environment(Model, count)
        talea_value = talea.load(environment=values)
        manual_value = equivalent.load(values)
        if talea_value.to_dict() != manual_value.to_dict():
            raise AssertionError("equivalent environment comparator changed values")
        narrow_min, _ = report(
            f"{count}-field narrow/non-equivalent",
            lambda count=count, values=values: narrow_environment(values, count),
        )
        talea_min, _ = report(
            f"{count}-field Talea comparison",
            lambda talea=talea, values=values: talea.load(environment=values),
        )
        equivalent_min, _ = report(
            f"{count}-field equivalent manual",
            lambda equivalent=equivalent, values=values: equivalent.load(values),
        )
        print(
            f"{count}-field ratios{'':27} Talea/narrow={talea_min / narrow_min:6.2f}x "
            f"Talea/equivalent={talea_min / equivalent_min:6.2f}x"
        )

    mixed_values = mixed_environment()
    mixed_plan = Settings(MixedSettings, prefix="APP_")
    equivalent_mixed = mixed_comparator()
    if mixed_plan.load(environment=mixed_values).to_dict() != equivalent_mixed.load(mixed_values).to_dict():
        raise AssertionError("mixed equivalent comparator changed values")
    mixed_min, _ = report("representative mixed 10-field", lambda: mixed_plan.load(environment=mixed_values))
    mixed_equivalent_min, _ = report("mixed equivalent manual", lambda: equivalent_mixed.load(mixed_values))
    print(f"mixed 10-field ratio{'':24} Talea/equivalent={mixed_min / mixed_equivalent_min:6.2f}x")

    report("environment provenance disabled", lambda: plans[10].load(environment=environment(10)))
    report(
        "environment provenance enabled",
        lambda: plans[10].load(environment=environment(10), provenance=True),
    )

    crossover_plan = plans[10]
    for size in (10, 50, 100, 1_000, 10_000):
        values = environment(10)
        values.update({f"UNRELATED_{index}": "x" for index in range(size - 10)})
        iterations = 20 if size >= 1_000 else _HOT_ITERATIONS
        report(
            f"environment scan {size} entries",
            lambda values=values: crossover_plan.load(environment=values),
            iterations,
        )


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

        class FullPrecedence(Spec):
            first: str = "default"
            second: str = "default"
            third: str = "default"
            fourth: str = "default"
            fifth: str = "default"

        full_toml = root / "full.toml"
        full_toml.write_text(
            'first = "toml"\nsecond = "toml"\nthird = "toml"\nfourth = "toml"\n',
            encoding="utf-8",
        )
        full_secrets = root / "full-secrets"
        full_secrets.mkdir()
        for name in ("second", "third", "fourth"):
            (full_secrets / name).write_text("secret", encoding="utf-8")
        full = Settings(
            FullPrecedence,
            prefix="APP_",
            toml=full_toml,
            secrets=full_secrets,
        )
        report(
            "full precedence and default",
            lambda: full.load(
                {"fourth": "override"},
                environment={"APP_THIRD": "environment", "APP_FOURTH": "environment"},
            ),
            50,
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
        limited_aggregate = Settings(
            Precedence,
            prefix="APP_",
            policy=SettingsPolicy(max_source_bytes=8),
        )
        report(
            "aggregate source bytes rejection",
            lambda: capture(
                lambda: limited_aggregate.load(
                    {"port": 1},
                    environment={"APP_HOST": "source-too-large"},
                ),
                ResourceLimitError,
            ),
        )


def benchmark_lifecycle() -> None:
    """Measure cold plans, warm loads, failures, retention, concurrency, and canaries."""

    Model = model("Lifecycle100", 100)
    values = environment(100)
    plan = Settings(Model, prefix="APP_")
    report("warm repeated load", lambda: plan.load(environment=values), 50)
    early = dict(values)
    early["APP_FIELD_0"] = "invalid"
    late = dict(values)
    late["APP_FIELD_99"] = "invalid"
    report("invalid early field", lambda: capture(lambda: plan.load(environment=early), ValidationError))
    report("invalid late field", lambda: capture(lambda: plan.load(environment=late), ValidationError))
    limited_entries = Settings(Model, prefix="APP_", policy=SettingsPolicy(max_environment_entries=50))
    report(
        "environment entry rejection",
        lambda: capture(lambda: limited_entries.load(environment=values), ResourceLimitError),
    )

    StageModel = model("Stage10", 10)
    stage_values = environment(10)
    stage_mapping = {f"field_{index}": index for index in range(10)}
    stage_plan = Settings(StageModel, prefix="APP_")
    equivalent = equivalent_integer_environment(StageModel, 10)
    report("stage snapshot copy", lambda: tuple(stage_values.items()))
    report("stage casefold names", lambda: tuple(key.casefold() for key in stage_values))
    report("stage text decoding", lambda: tuple(_manual_int(text) for text in stage_values.values()))
    report("stage canonical assignment", lambda: dict(stage_mapping))
    report("stage final Spec.from_mapping", lambda: StageModel.from_mapping(stage_mapping))
    allocation_report("10-field Talea load allocations", lambda: stage_plan.load(environment=stage_values))
    allocation_report("equivalent manual allocations", lambda: equivalent.load(stage_values))

    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    for _ in range(100):
        Settings(Model, prefix="APP_")
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"discarded plan retention                    retained={retained - before:8d} B peak={peak - before:8d} B")

    class Source(dict[str, str]):
        pass

    source = Source(stage_values)
    source_reference = weakref.ref(source)
    stage_plan.load(environment=source)
    del source
    gc.collect()
    if source_reference() is not None:
        raise AssertionError("Settings retained a completed source snapshot")
    print("completed source snapshot retention          PASS")

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
