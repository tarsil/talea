"""Execute documentation examples and validate the source documentation graph."""

from __future__ import annotations

import ast
import inspect
import re
import runpy
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

import yaml

import talea
import talea.declaration
import talea.errors
import talea.introspection
import talea.jsonl
import talea.schema
import talea.settings
import talea.validation

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "en" / "docs"
DOCS_SRC = ROOT / "docs_src"
CONFIG = ROOT / "mkdocs.yaml"
MARKDOWN_LINK = re.compile(r"\[[^]]+\]\((?P<target>[^)]+)\)")
FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]+`")
HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*#*\s*$", re.MULTILINE)
NON_SLUG = re.compile(r"[^\w\- ]", re.UNICODE)
CAMPAIGN_PROSE = re.compile(r"\bCampaign\s+\d+\b", re.IGNORECASE)
INCLUDE = re.compile(r"\{!>\s*(?P<path>[^!]+?)\s*!}")


def _nav_paths(value: object) -> Iterator[str]:
    if isinstance(value, str):
        if value.endswith(".md"):
            yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _nav_paths(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _nav_paths(item)


def check_navigation() -> None:
    """Require every source page to exist once in the explicit navigation."""

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    nav_paths = tuple(_nav_paths(config["nav"]))
    if len(nav_paths) != len(set(nav_paths)):
        raise RuntimeError("MkDocs navigation contains a duplicate page")
    missing = [path for path in nav_paths if not (DOCS / path).is_file()]
    if missing:
        raise RuntimeError(f"MkDocs navigation references missing pages: {missing}")
    source_paths = {str(path.relative_to(DOCS)) for path in DOCS.rglob("*.md") if path.name != "documentation-audit.md"}
    unlisted = sorted(source_paths - set(nav_paths))
    if unlisted:
        raise RuntimeError(f"source pages are absent from MkDocs navigation: {unlisted}")
    untitled = [str(path.relative_to(DOCS)) for path in DOCS.rglob("*.md") if not path.read_text().startswith("# ")]
    if untitled:
        raise RuntimeError(f"source pages do not start with a level-one title: {untitled}")


def check_internal_links() -> None:
    """Reject local Markdown links whose target page, asset, or heading is absent."""

    failures: list[str] = []
    for page in sorted(DOCS.rglob("*.md")):
        content = page.read_text(encoding="utf-8")
        prose = INLINE_CODE.sub("", FENCED_CODE.sub("", content))
        for match in MARKDOWN_LINK.finditer(prose):
            target = match.group("target").strip().split(maxsplit=1)[0].strip("<>")
            if "://" in target or target.startswith("mailto:"):
                continue
            path_text, separator, anchor = target.partition("#")
            resolved = page if not path_text else (page.parent / path_text).resolve()
            if not resolved.exists():
                failures.append(f"{page.relative_to(ROOT)} -> {target}")
                continue
            if separator and resolved.suffix == ".md":
                headings = {
                    NON_SLUG.sub("", INLINE_CODE.sub("", match.group("title"))).strip().lower().replace(" ", "-")
                    for match in HEADING.finditer(resolved.read_text(encoding="utf-8"))
                }
                if unquote(anchor) not in headings:
                    failures.append(f"{page.relative_to(ROOT)} -> {target} (missing heading)")
    if failures:
        raise RuntimeError("broken internal links:\n" + "\n".join(failures))


def check_includes() -> None:
    """Require every source include to resolve to an existing repository file."""

    failures: list[str] = []
    for page in sorted(DOCS.rglob("*.md")):
        for match in INCLUDE.finditer(page.read_text(encoding="utf-8")):
            include = match.group("path").strip()
            resolved = (page.parent / include).resolve()
            if not resolved.is_file():
                failures.append(f"{page.relative_to(ROOT)} -> {include}")
    if failures:
        raise RuntimeError("broken documentation includes:\n" + "\n".join(failures))


def check_public_api_reference() -> None:
    """Require every application and intentional domain export in the API reference."""

    api_text = (DOCS / "reference" / "api.md").read_text(encoding="utf-8")
    public_modules = (
        talea,
        talea.declaration,
        talea.errors,
        talea.introspection,
        talea.jsonl,
        talea.schema,
        talea.settings,
        talea.validation,
    )
    required = sorted({name for module in public_modules for name in module.__all__})
    missing = [name for name in required if f"`{name}`" not in api_text]
    if missing:
        raise RuntimeError(f"public API reference is missing: {missing}")
    missing_docstrings = [
        f"{module.__name__}.{name}"
        for module in public_modules
        for name in module.__all__
        if inspect.getdoc(getattr(module, name)) is None
    ]
    if missing_docstrings:
        raise RuntimeError(f"public API docstrings are missing: {missing_docstrings}")
    error_text = (DOCS / "error-experience.md").read_text(encoding="utf-8")
    missing_codes = [code.value for code in talea.ErrorCode if f"`{code.value}`" not in error_text]
    if missing_codes:
        raise RuntimeError(f"ErrorCode reference is missing: {missing_codes}")


def check_product_language() -> None:
    """Keep current product documentation free of chronology and adversarial claims."""

    failures: list[str] = []
    forbidden = ("Pydantic killer", "msgspec killer", "Pydantic is obsolete")
    for page in sorted(DOCS.rglob("*.md")):
        content = page.read_text(encoding="utf-8")
        if page.name != "release-notes.md" and CAMPAIGN_PROSE.search(content):
            failures.append(f"{page.relative_to(ROOT)} contains campaign chronology")
        for phrase in forbidden:
            if phrase.casefold() in content.casefold():
                failures.append(f"{page.relative_to(ROOT)} contains forbidden claim {phrase!r}")
    if "2026+" not in (DOCS / "getting-started" / "why-talea.md").read_text(encoding="utf-8"):
        failures.append("Why Talea does not explain the 2026+ design baseline")
    if failures:
        raise RuntimeError("product-language failures:\n" + "\n".join(failures))


def execute_examples() -> None:
    """Run every documentation source module as a complete program."""

    examples = [
        path for path in sorted(DOCS_SRC.rglob("*.py")) if path.name != "__init__.py" and not path.name.startswith("_")
    ]
    if not examples:
        raise RuntimeError("docs_src contains no executable examples")
    assertion_count = 0
    missing_assertions: list[str] = []
    for example in examples:
        tree = ast.parse(example.read_text(encoding="utf-8"), filename=str(example))
        assertions = sum(isinstance(node, ast.Assert) for node in ast.walk(tree))
        assertion_count += assertions
        if assertions == 0:
            missing_assertions.append(str(example.relative_to(ROOT)))
        runpy.run_path(str(example), run_name="__main__")
    if missing_assertions:
        raise RuntimeError(f"documentation examples contain no assertions: {missing_assertions}")
    print(f"Executed {len(examples)} documentation examples with {assertion_count} assertions")


def main() -> None:
    """Run documentation integrity checks in stable order."""

    check_navigation()
    check_internal_links()
    check_includes()
    check_public_api_reference()
    check_product_language()
    execute_examples()
    print("Documentation navigation, links, API inventory, and examples passed")


if __name__ == "__main__":
    main()
