# Installation

Talea requires Python 3.14 or newer.

Until a package-index release is published, install from a source checkout:

```console
git clone https://github.com/tarsil/talea.git
cd talea
python -m pip install .
```

For contributor environments, enter Hatch:

```console
hatch shell
```

The installed runtime has no required third-party dependencies. The repository
environment also installs testing, typing, benchmarking, build, and
documentation tools; those are not runtime dependencies.

Verify the installation:

```console
python -c "import talea; print(talea.__version__)"
```

See [Contributing](../contributing.md) for the complete development workflow.
