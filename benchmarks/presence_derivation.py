"""Measure Campaign 16 derivation, presence-aware execution, and zero tax."""

import gc
import sys
import tracemalloc
from collections.abc import Callable
from functools import partial
from statistics import median
from timeit import Timer
from typing import Annotated
from weakref import ref

from talea import Alias, ReadOnly, Spec, WriteOnly, apply_patch, check, create_spec, derive_spec

_REPEATS = 5
_HOT_ITERATIONS = 50_000
_COLD_ITERATIONS = 500

type Operation = Callable[[], object]


class Measurement:
    """Retain minimum and median nanoseconds for one operation."""

    __slots__ = ("median", "minimum")

    def __init__(self, minimum: float, median_time: float) -> None:
        self.minimum = minimum
        self.median = median_time


def measure(operation: Operation, iterations: int = _HOT_ITERATIONS) -> Measurement:
    """Measure one operation across independent samples."""

    samples = Timer(operation).repeat(repeat=_REPEATS, number=iterations)
    nanoseconds = [sample * 1_000_000_000 / iterations for sample in samples]
    return Measurement(min(nanoseconds), median(nanoseconds))


def report(case: str, implementation: str, result: Measurement) -> None:
    """Print one stable benchmark row."""

    print(f"{case:32} {implementation:24} min={result.minimum:10.1f} ns/op median={result.median:10.1f} ns/op")


class One(Spec):
    """One-field derivation source."""

    field_0: int


class Five(Spec):
    """Five-field derivation source."""

    field_0: int
    field_1: int
    field_2: int
    field_3: int
    field_4: int


class Ten(Spec):
    """Ten-field derivation source."""

    field_0: int
    field_1: int
    field_2: int
    field_3: int
    field_4: int
    field_5: int
    field_6: int
    field_7: int
    field_8: int
    field_9: int


class Checked(Spec):
    """Source with one whole-record invariant."""

    start: int
    end: int

    @check("start", "end")
    def ordered(start: int, end: int) -> None:
        if start > end:
            raise ValueError


class Migrated(Spec):
    """Migration-bearing derivation source."""

    identifier: Annotated[int, Alias("accountId", legacy=("id", "account_id"))]


OnePatch = derive_spec(One, partial=True)
TenPatch = derive_spec(Ten, partial=True)
CheckedPatch = derive_spec(Checked, partial=True)
MigratedPatch = derive_spec(Migrated, partial=True)


def directional_source(count: int) -> type[Spec]:
    """Create one mixed-direction benchmark declaration."""

    annotations = {
        f"field_{index}": Annotated[int, ReadOnly()] if index % 2 == 0 else Annotated[int, WriteOnly()]
        for index in range(count)
    }
    return create_spec(f"Directional{count}", annotations)


DirectionalSources = {count: directional_source(count) for count in (1, 5, 10, 50)}
DirectionalTen = DirectionalSources[10]
DirectionalTenInput = derive_spec(DirectionalTen, mode="input")
DirectionalTenOutput = derive_spec(DirectionalTen, mode="output")
DirectionalTenInputPatch = derive_spec(DirectionalTen, mode="input", partial=True)
ManualInput = create_spec("ManualInput", {f"field_{index}": int for index in range(1, 10, 2)})
ManualOutput = create_spec("ManualOutput", {f"field_{index}": int for index in range(0, 10, 2)})


class HandPartial:
    """Equivalent hand-written ten-field presence-aware slotted record."""

    __slots__ = (
        "field_0",
        "field_1",
        "field_2",
        "field_3",
        "field_4",
        "field_5",
        "field_6",
        "field_7",
        "field_8",
        "field_9",
        "presence",
    )

    def __init__(self, values: dict[str, int]) -> None:
        self.presence = 0
        for index in range(10):
            name = f"field_{index}"
            if name not in values:
                continue
            value = values[name]
            if type(value) is not int:
                raise TypeError
            setattr(self, name, value)
            self.presence |= 1 << index

    def to_dict(self) -> dict[str, int]:
        """Return present fields in canonical order."""

        return {
            f"field_{index}": getattr(self, f"field_{index}") for index in range(10) if self.presence & (1 << index)
        }


def values(count: int) -> dict[str, int]:
    """Return the first ``count`` canonical values."""

    return {f"field_{index}": index for index in range(count)}


def benchmark_derivation() -> None:
    """Measure cold distinct derived-class creation policies."""

    report("partial 1 field", "cold derivation", measure(partial(derive_spec, One, partial=True), _COLD_ITERATIONS))
    report("partial 5 fields", "cold derivation", measure(partial(derive_spec, Five, partial=True), _COLD_ITERATIONS))
    report("partial 10 fields", "cold derivation", measure(partial(derive_spec, Ten, partial=True), _COLD_ITERATIONS))
    report(
        "pick 5 of 10",
        "cold derivation",
        measure(partial(derive_spec, Ten, include=tuple(values(5))), _COLD_ITERATIONS),
    )
    report(
        "omit 5 of 10",
        "cold derivation",
        measure(partial(derive_spec, Ten, exclude=tuple(values(5))), _COLD_ITERATIONS),
    )
    report(
        "partial + pick 5",
        "cold derivation",
        measure(partial(derive_spec, Ten, include=tuple(values(5)), partial=True), _COLD_ITERATIONS),
    )
    report(
        "identical repeated call",
        "distinct class policy",
        measure(partial(derive_spec, Ten, partial=True), _COLD_ITERATIONS),
    )
    report(
        "migration partial",
        "cold derivation",
        measure(partial(derive_spec, Migrated, partial=True), _COLD_ITERATIONS),
    )


def benchmark_directional_derivation() -> None:
    """Measure directional selection as declaration-time policy."""

    for count, source in DirectionalSources.items():
        report(
            f"input direction {count}",
            "cold derivation",
            measure(partial(derive_spec, source, mode="input"), _COLD_ITERATIONS),
        )
        report(
            f"output direction {count}",
            "cold derivation",
            measure(partial(derive_spec, source, mode="output"), _COLD_ITERATIONS),
        )
    report(
        "partial input 10",
        "cold derivation",
        measure(partial(derive_spec, DirectionalTen, mode="input", partial=True), _COLD_ITERATIONS),
    )
    report(
        "input + include",
        "cold derivation",
        measure(
            partial(derive_spec, DirectionalTen, mode="input", include=("field_1", "field_3")),
            _COLD_ITERATIONS,
        ),
    )
    report(
        "output + exclude",
        "cold derivation",
        measure(
            partial(derive_spec, DirectionalTen, mode="output", exclude=("field_8",)),
            _COLD_ITERATIONS,
        ),
    )


def benchmark_construction() -> None:
    """Measure supplied-field scaling against a hand-written reference."""

    for count in (0, 1, 5, 10):
        supplied = values(count)
        report(f"construct {count} present", "Talea partial", measure(partial(TenPatch, **supplied)))
        report(f"construct {count} present", "hand-written", measure(partial(HandPartial, supplied)))
    report("normal construct 1", "zero-tax canary", measure(partial(One, field_0=0)))
    report("partial construct 1", "presence-aware", measure(partial(OnePatch, field_0=0)))


def benchmark_output() -> None:
    """Measure presence projection and Python/JSON output scaling."""

    for count in (0, 1, 5, 10):
        instance = TenPatch(**values(count))
        hand = HandPartial(values(count))
        instance.to_dict()
        instance.to_json()
        report(f"to_dict {count} present", "Talea partial", measure(instance.to_dict))
        report(f"to_dict {count} present", "hand-written", measure(hand.to_dict))
        report(f"to_json {count} present", "Talea partial", measure(instance.to_json, 10_000))
    five = TenPatch(**values(5))
    report("present_fields 5", "frozenset projection", measure(lambda: five.present_fields))


def benchmark_directional_runtime() -> None:
    """Compare derived execution with manually equivalent normal Specs."""

    input_values = {f"field_{index}": index for index in range(1, 10, 2)}
    output_values = {f"field_{index}": index for index in range(0, 10, 2)}
    input_json = "{" + ",".join(f'"{name}":{value}' for name, value in input_values.items()) + "}"
    for label, derived, manual, supplied in (
        ("input", DirectionalTenInput, ManualInput, input_values),
        ("output", DirectionalTenOutput, ManualOutput, output_values),
    ):
        derived_instance = derived(**supplied)
        manual_instance = manual(**supplied)
        report(f"directional {label} construct", "derived", measure(partial(derived, **supplied)))
        report(f"directional {label} construct", "manual", measure(partial(manual, **supplied)))
        report(f"directional {label} mapping", "derived", measure(partial(derived.from_mapping, supplied)))
        report(f"directional {label} mapping", "manual", measure(partial(manual.from_mapping, supplied)))
        report(f"directional {label} to_dict", "derived", measure(derived_instance.to_dict))
        report(f"directional {label} to_dict", "manual", measure(manual_instance.to_dict))
        report(f"directional {label} to_json", "derived", measure(derived_instance.to_json, 10_000))
        report(f"directional {label} to_json", "manual", measure(manual_instance.to_json, 10_000))
    report("directional input JSON", "derived", measure(partial(DirectionalTenInput.from_json, input_json), 10_000))
    report("directional input JSON", "manual", measure(partial(ManualInput.from_json, input_json), 10_000))


def benchmark_patch() -> None:
    """Measure patch application through Talea's replacement owner."""

    source = Ten(**values(10))
    one = TenPatch(**{"field_0": 10})
    several = TenPatch(**{"field_0": 10, "field_4": 40, "field_9": 90})
    checked = Checked(start=1, end=2)
    checked_patch = CheckedPatch(**{"start": 2})
    apply_patch(source, one)
    apply_patch(checked, checked_patch)
    report("apply one field", "apply_patch", measure(partial(apply_patch, source, one)))
    report("apply three fields", "apply_patch", measure(partial(apply_patch, source, several)))
    report("apply whole check", "apply_patch", measure(partial(apply_patch, checked, checked_patch)))

    migrated_source = Migrated(identifier=1)
    migrated_current = MigratedPatch.from_mapping({"accountId": 2})
    migrated_legacy = MigratedPatch.from_mapping({"account_id": 3})
    report(
        "migration current input",
        "derived partial",
        measure(partial(MigratedPatch.from_mapping, {"accountId": 2})),
    )
    report(
        "migration legacy input",
        "derived partial",
        measure(partial(MigratedPatch.from_mapping, {"account_id": 3})),
    )
    report(
        "migration apply patch",
        "apply_patch",
        measure(partial(apply_patch, migrated_source, migrated_legacy)),
    )
    assert migrated_current.present_fields == migrated_legacy.present_fields == frozenset({"identifier"})


def released_derivation_bytes(count: int = 1_000) -> tuple[int, bool]:
    """Measure net retention and weak collection for uncached derived classes."""

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    derived = derive_spec(Ten, partial=True)
    weak = ref(derived)
    del derived
    for _ in range(count - 1):
        derive_spec(Ten, partial=True)
    gc.collect()
    after = tracemalloc.take_snapshot()
    retained = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    tracemalloc.stop()
    return retained, weak() is None


def benchmark_memory() -> None:
    """Report shallow normal/partial layout and derived-class retention."""

    normal = Ten(**values(10))
    empty = TenPatch()
    partial_five = TenPatch(**values(5))
    retained, collected = released_derivation_bytes()
    print(
        f"shallow instance normal={sys.getsizeof(normal)} B "
        f"partial-empty={sys.getsizeof(empty)} B partial-five={sys.getsizeof(partial_five)} B"
    )
    print(f"presence mask shallow={sys.getsizeof(object.__getattribute__(empty, '__talea_presence__'))} B")
    print(
        f"directional/manual shallow="
        f"{sys.getsizeof(DirectionalTenOutput(**{f'field_{index}': index for index in range(0, 10, 2)}))} B/"
        f"{sys.getsizeof(ManualOutput(**{f'field_{index}': index for index in range(0, 10, 2)}))} B "
        f"partial-input={sys.getsizeof(DirectionalTenInputPatch())} B"
    )
    print(f"1000 discarded derivations retained={retained} bytes first_collected={collected}")


def main() -> None:
    """Run all Campaign 16 permanent performance canaries."""

    print("Derived declaration")
    benchmark_derivation()
    print("\nDirectional declaration")
    benchmark_directional_derivation()
    print("\nPartial construction")
    benchmark_construction()
    print("\nPresence-aware output")
    benchmark_output()
    print("\nDirectional runtime")
    benchmark_directional_runtime()
    print("\nPatch application")
    benchmark_patch()
    print("\nMemory and retention")
    benchmark_memory()


if __name__ == "__main__":
    main()
