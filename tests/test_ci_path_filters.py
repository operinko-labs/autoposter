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
- ``frontend/**`` — the Dockerfile builds it into the image and
  ``test_attribution_present`` asserts against the resulting bundle, so a
  frontend-only change is a change to what the container serves. Ignoring it
  would let the UI be replaced wholesale with no CI run at all.

so none of them may be ignored. This test reads the ignore list out of the
workflow and checks it against that set, rather than trusting a comment to
keep people honest.
"""

import fnmatch
import re
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
    # The web UI. `npm run build` turns these into the bundle the image serves
    # and tests/test_attribution_present.py reads, so nothing under frontend/
    # may skip CI -- including the manifests, since the lockfile is what
    # decides which versions that build resolves.
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/vite.config.ts",
    "frontend/index.html",
    "frontend/src/main.tsx",
    "frontend/src/pages/Settings.tsx",
    # tests/test_toolchain_versions.py holds the developer database to the same
    # PostgreSQL patch CI runs, so a change here is a change the suite checks.
    "docker-compose.yml",
    # tests/test_toolchain_versions.py asserts this enables the custom manager
    # that reads the workflow's `# renovate:` annotations, without which the
    # workflow's versions silently stop being updated.
    "renovate.json",
    # The harvested production poster tests/test_golden.py is byte-compared
    # against. It is the whole of the parity claim; a change to it is a change
    # to what "identical" means.
    "tests/fixtures/golden/expected_poster.jpg",
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


def test_ci_sets_the_ci_environment_variable():
    """A guard that only arms itself on a variable nothing sets is
    decoration, not enforcement.

    tests/test_attribution_present.py treats a missing frontend/dist as a
    hard `pytest.fail` only when `os.environ["CI"]` is truthy; otherwise it
    skips. If this workflow ever stops setting it -- or the runner's
    behaviour is relied on instead -- that guard goes green-with-a-skip
    rather than red the moment the "Build the frontend" step breaks, and the
    TMDB/TheTVDB attribution requirement silently stops being enforced.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    env = loaded.get("env") or {}
    assert str(env.get("CI", "")).lower() in {"1", "true", "yes"}, (
        "CI is not set to a truthy value in .forgejo/workflows/ci.yml's "
        "env block; tests/test_attribution_present.py's hard-fail guard "
        "would silently skip instead of failing on a missing frontend/dist"
    )


def _steps() -> list[dict]:
    """Every step in the workflow, across all jobs."""
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [
        step
        for job in (loaded.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    ]


def _run_scripts() -> list[str]:
    """Every `run:` script in the workflow, across all jobs."""
    return [step["run"] for step in _steps() if isinstance(step.get("run"), str)]


def test_ci_actually_executes_the_imagemagick_gated_tests():
    """The parity tests must be run, not merely deselected.

    They carry `@pytest.mark.imagemagick`, and the main pytest step
    deselects them with `-m "not imagemagick"` because no runner has a
    `magick`. That is only safe while something else selects them: drop the
    second step and CI is back to reporting green over the one claim this
    project makes -- byte-identical posters -- having rendered nothing. Which
    is what it did for the whole of this branch's history.

    `CI=true` is asserted alongside because it is what arms the guard.
    tests/conftest.py's `imagemagick` fixture fails rather than skips only
    when it is set, so a step that ran these without it would go green on a
    container that had somehow lost ImageMagick -- the exact silence this
    replaced.
    """
    deselecting = [s for s in _run_scripts() if "not imagemagick" in s]
    selecting = [s for s in _run_scripts() if re.search(r"-m\s+[\"']?imagemagick", s)]

    assert deselecting, (
        "no CI step deselects the ImageMagick-gated tests; if that is "
        "deliberate, this test and the -m flags in the workflow should go "
        "together"
    )
    assert selecting, (
        "CI deselects the ImageMagick-gated tests with -m \"not imagemagick\" "
        "but no step runs them with -m imagemagick, so the byte-identical "
        "poster parity is verified nowhere"
    )
    for script in selecting:
        assert "CI=true" in script, (
            "the step running the ImageMagick-gated tests does not pass "
            "CI=true, so tests/conftest.py's guard would skip instead of "
            "failing if ImageMagick went missing from it"
        )


def test_ci_actually_runs_the_frontend_suite():
    """The vitest suite must be executed by CI, not merely present in the tree.

    Its neighbours are all self-enforcing and this step was not. "Build the
    frontend" cannot be deleted quietly, because
    tests/test_attribution_present.py hard-fails on a missing `frontend/dist`
    once `CI` is set; the ImageMagick step has the guard above. Delete "Test
    the frontend" and roughly thirty tests stop running with everything still
    green -- including `frontend/src/main.test.tsx`, the one that catches a
    bundle containing none of the application, which is a failure this branch
    actually shipped.

    Asserted against the `run:` script rather than the step's name, so
    renaming the step is fine and removing the command is not.
    """
    running = [
        step
        for step in _steps()
        if isinstance(step.get("run"), str)
        and re.search(r"\bnpm\s+(?:run\s+)?test\b", step["run"])
    ]
    assert running, (
        "no CI step runs `npm test`, so the frontend suite -- including "
        "main.test.tsx, which is what proves the bundle contains the "
        "application at all -- is never executed"
    )
    for step in running:
        working_directory = str(step.get("working-directory", ""))
        assert "frontend" in working_directory or "frontend" in step["run"], (
            "a CI step runs `npm test` but not in frontend/, so it would "
            f"resolve no package.json and pass vacuously: {step}"
        )


def test_the_image_job_builds_the_default_target():
    """tests/test_dev_environment.py's stage-ordering test rests on this.

    `test_the_runtime_stage_is_the_last_one` is only meaningful while the image
    job builds the Dockerfile's *last* stage. Pass `target: dev` to
    docker/build-push-action and the development image -- pytest, ruff and an
    editable install included -- is what gets verified and pushed to Harbor as
    production, with that test still green and every check in the image job
    still passing, because the dev image is a superset of the runtime one.

    The premise is asserted here rather than there because it is a property of
    the workflow, which this module already parses.
    """
    build_steps = [
        step
        for step in _steps()
        if str(step.get("uses", "")).startswith("docker/build-push-action")
    ]
    assert build_steps, (
        "no docker/build-push-action step found in .forgejo/workflows/ci.yml; "
        "tests/test_dev_environment.py::test_the_runtime_stage_is_the_last_one "
        "assumes one exists and passes no target"
    )
    for step in build_steps:
        with_block = step.get("with") or {}
        assert "target" not in with_block, (
            f"the image build passes target={with_block['target']!r}, so it no "
            "longer builds the Dockerfile's last stage. "
            "tests/test_dev_environment.py::test_the_runtime_stage_is_the_last_one "
            "would stay green while a development image shipped as production"
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
