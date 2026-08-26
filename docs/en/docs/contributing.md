# Contributing

Talea targets Python 3.14+ and uses Hatch, pytest, Ruff, `ty`, Zensical, and the
repository Taskfile. Changes must preserve strict semantics, single ownership,
zero required runtime dependencies, and enforced 100% line coverage.

## Set up a checkout

```console
git clone https://github.com/YOUR-USERNAME/talea.git
cd talea
python -m pip install hatch
hatch env create
hatch env create test
hatch env create docs
```

Report possible defects with a minimal reproduction, Python version, operating
system, installed package versions, traceback, expected behavior, and actual
behavior. Do not include credentials or hostile payloads containing live data.

## Development gates

Use the repository tasks rather than inventing parallel commands:

```console
task test
task coverage
task lint
task format
task mypy
task docs_test
task build
task build_with_checks
```

Run `git diff --check` before committing. Performance-sensitive changes must run
the relevant permanent benchmark tasks; release review runs all of them.

## Documentation

User-facing Markdown lives in `docs/en/docs/`. Important examples live as
normal Python programs in `docs_src/` and are included into pages with:

The source page uses the include form
`&#123;!> relative/path/to/example.py !&#125;`. Paths resolve from the Markdown
file that contains the directive.

`task docs_test` executes every `docs_src` program and verifies explicit
navigation, internal Markdown links, and the root public API inventory.
`task build` expands includes into `docs/generated/` and builds the site.

Examples must use meaningful contracts, assert important output, pass Ruff and
`ty`, and avoid network access. Do not copy a code block into Markdown when the
same substantial example can have one executable owner.

## Architecture and tests

Identify the canonical owner before editing. Schema resolution owns type
structure; declarations own fields and lifecycle; validation, input,
serialization, errors, resource policy, and standards projection consume that
truth. A new execution target must not reread annotations or recreate semantics.

Behavior changes require focused success, failure, boundary, inheritance, and
typing coverage where relevant. Performance claims require measured comparator
workloads with equivalent semantics. See [Architecture](engineering/architecture.md)
and [Performance](engineering/performance.md).

## Commits and releases

Use Conventional Commits for coherent reviewable slices. Release notes describe
user-visible additions, changes, removals, deprecations, and fixes; test-only or
mechanical documentation work does not need a user-facing release entry.

Build artifacts with `task build_with_checks`. Maintainers own versioning,
tagging, package-index publication, and support policy.
