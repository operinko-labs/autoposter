"""CI must not skip a change to a file the suite actually verifies.

Filtering CI on paths is a sharp tool: get the list slightly wrong and a
change sails through with no tests run and a green tick beside it. The
non-obvious part is that several files that look like configuration or
packaging are genuinely covered here —

- ``Dockerfile`` — ``test_assets_root`` asserts it sets ``AUTOPOSTER_ASSETS_ROOT``,
  without which the container cannot find its bundled assets.
- ``pyproject.toml`` — ``test_declared_dependencies`` walks every import in
  ``src/`` against its dependency list. That is what now catches a missing
  Pillow, which once shipped to main and broke the image.
- ``config/autoposter.example.yaml`` — ``test_example_config_matches_schema``
  checks every key exists in the schema, which caught two collection toggles
  silently sitting in the wrong section.

so none of them may be ignored. This test reads the ignore list out of the
workflow and checks it against that set, rather than trusting a comment to
keep people honest.
"""

import fnmatch
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".forgejo" / "workflows" / "ci.yml"

# Files outside src/ and tests/ that the suite reads. A change to any of
# these must still run CI.
VERIFIED_NON_SOURCE = [
    "Dockerfile",
    "pyproject.toml",
    "config/autoposter.example.yaml",
    "assets/badges/MANIFEST.sha256",
    "assets/badges/languages.json",
    "assets/collections/content_rating_cs.json",
    ".forgejo/workflows/ci.yml",
    ".forgejo/scripts/wait_for_postgres.py",
    "src/autoposter/app.py",
    "tests/test_ci_path_filters.py",
    "alembic/env.py",
]


def _triggers() -> dict:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the unquoted key `on` as the boolean True; the workflow
    # quotes it, but accept either so this does not break on a reformat.
    return loaded.get("on") or loaded[True]


def _ignore_patterns() -> list[str]:
    patterns: list[str] = []
    for event in ("push", "pull_request"):
        config = _triggers().get(event) or {}
        patterns.extend(config.get("paths-ignore") or [])
    return patterns


def _matches(pattern: str, path: str) -> bool:
    # GitHub's `**` spans directory separators; fnmatch's `*` already does,
    # so collapsing `**/` to `*` is close enough for this check and errs
    # towards reporting a match.
    return fnmatch.fnmatch(path, pattern.replace("**/", "*"))


def test_the_workflow_actually_filters_something():
    """Guards the guard: if the ignore list vanished, every assertion below
    would pass trivially."""
    assert _ignore_patterns(), "no paths-ignore found; this test proves nothing"


@pytest.mark.parametrize("path", VERIFIED_NON_SOURCE)
def test_a_verified_file_is_never_skipped(path):
    offenders = [p for p in _ignore_patterns() if _matches(p, path)]
    assert not offenders, (
        f"{path} is read by the test suite but CI would skip a change to it, "
        f"matched by paths-ignore {offenders}"
    )


@pytest.mark.parametrize(
    "path",
    [
        "docs/superpowers/specs/2026-08-20-autoposter-design.md",
        "docs/research/kometa-overlays.md",
        "deploy/README.md",
        "README.md",
        ".gitignore",
    ],
)
def test_documentation_is_skipped(path):
    """The point of the filter. Nothing in the suite reads these."""
    assert any(_matches(p, path) for p in _ignore_patterns()), (
        f"{path} is documentation but would still trigger a full CI run"
    )
