"""Measure uncached annotation resolution with the standard library timer."""

from statistics import median
from timeit import Timer

from talea.schema import resolve_annotation

_ITERATIONS = 200_000
_REPEATS = 7


def measure(annotation: object) -> tuple[float, float]:
    """Return minimum and median nanoseconds per resolution.

    Each sample resolves the same pre-built annotation object so the result
    measures Talea's transformation rather than Python generic-alias creation.
    The minimum preserves a best-case baseline, while the median represents the
    typical sample without being dominated by a single scheduling interruption.
    """

    samples = Timer(lambda: resolve_annotation(annotation)).repeat(repeat=_REPEATS, number=_ITERATIONS)
    nanoseconds = [sample * 1_000_000_000 / _ITERATIONS for sample in samples]
    return min(nanoseconds), median(nanoseconds)


def main() -> None:
    """Print reproducible primitive and nested resolution baselines."""

    cases = {
        "primitive int": int,
        "nested list[dict[str, int | None]]": list[dict[str, int | None]],
    }
    print(f"Python annotation resolution ({_REPEATS} samples x {_ITERATIONS:,} resolutions)")
    for name, annotation in cases.items():
        minimum, median_time = measure(annotation)
        print(f"{name}: min={minimum:.1f} ns/resolution, median={median_time:.1f} ns/resolution")


if __name__ == "__main__":
    main()
