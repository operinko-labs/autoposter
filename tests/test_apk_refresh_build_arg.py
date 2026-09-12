"""The base stage's ``apk upgrade`` layer is keyed on the day, not only on the
Dockerfile's text.

Roadmap row 269's CI finding (2026-09-12): both workflows build with
``cache-from: type=gha``, and ``RUN apk upgrade`` was keyed on the Dockerfile
alone, so a runner kept serving whatever base it had cached until the file
changed. ``forgejo-runner1`` served a base from before 2026-09-11, still on
util-linux 2.42.1-r0, and the Trivy gate went red on a PR that touched
nothing under the Dockerfile -- while the same commit's parent passed on
``forgejo-runner2``. An ``ARG`` declared above the ``RUN`` is part of that
layer's cache key even when the RUN never reads it, so passing today's date
bounds any runner's staleness to one day.
"""
import re
from pathlib import Path

import yaml

# Paths only at import time, reads inside the tests: CI's ImageMagick-marked
# pytest run happens inside the dev image, which carries /app/tests but no
# /app/Dockerfile, and a module-level read there errors at COLLECTION even
# though nothing here is selected (run 477, 2026-09-12). The sibling
# ``test_version_stamp.py`` reads lazily for the same reason.
REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
CI_WORKFLOW = REPO / ".forgejo" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow):
    for job in workflow["jobs"].values():
        yield from job.get("steps", [])


def _build_steps(workflow):
    return [
        step for step in _steps(workflow)
        if "docker/build-push-action" in (step.get("uses") or "")
    ]


def test_the_dockerfile_declares_apk_refresh_directly_above_the_upgrade():
    """Directly above, in the SAME stage: an ARG in an earlier stage does not
    reach ``pybase``, and one below the RUN keys nothing."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    pybase = text.split("AS pybase", 1)[1].split("\nFROM ", 1)[0]
    match = re.search(r"^ARG APK_REFRESH[^\n]*\n(?:#[^\n]*\n)*RUN apk upgrade", pybase, re.M)
    assert match, "pybase needs `ARG APK_REFRESH` immediately above `RUN apk upgrade`"


def test_every_image_build_passes_todays_date_as_apk_refresh():
    for name, path, step_id in (
        ("ci", CI_WORKFLOW, "gen_tag"), ("release", RELEASE_WORKFLOW, "version"),
    ):
        workflow = _load(path)
        builds = _build_steps(workflow)
        assert builds, f"{name}: no build-push-action step found"
        for build in builds:
            args = build.get("with", {}).get("build-args") or ""
            assert f"APK_REFRESH=${{{{ steps.{step_id}.outputs.apk_refresh }}}}" in args, (
                f"{name}: the image build is not passed APK_REFRESH from "
                f"{step_id}'s apk_refresh output (build-args: {args!r})"
            )
        producer = next(step for step in _steps(workflow) if step.get("id") == step_id)
        assert 'apk_refresh=$(date -u +%Y-%m-%d)' in producer["run"], (
            f"{name}: step {step_id!r} does not emit today's UTC date as apk_refresh"
        )
