"""Measure uncached annotation resolution with the standard library timer."""

from timeit import Timer

from talea.annotations import resolve_annotation

_ITERATIONS = 200_000
_REPEATS = 7


def measure(annotation: object) -> float:
    """Return the fastest nanoseconds per resolution across repeated samples.

    Each sample resolves the same pre-built annotation object so the result
    measures Talea's transformation rather than Python generic-alias creation.
    The minimum of seven samples limits incidental scheduler noise.
    """

    samples = Timer(lambda: resolve_annotation(annotation)).repeat(repeat=_REPEATS, number=_ITERATIONS)
    return min(samples) * 1_000_000_000 / _ITERATIONS


def main() -> None:
    """Print reproducible primitive and nested resolution baselines."""

    cases = {
        "primitive int": int,
        "nested list[dict[str, int | None]]": list[dict[str, int | None]],
    }
    print(f"Python annotation resolution ({_REPEATS} x {_ITERATIONS:,}; best sample)")
    for name, annotation in cases.items():
        print(f"{name}: {measure(annotation):.1f} ns/resolution")


if __name__ == "__main__":
    main()
