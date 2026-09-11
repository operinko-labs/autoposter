"""An image must be stamped with what it is, and there are two stamps.

Which one a build carries is what decides whether the update check runs, so
the agreements below are load-bearing in two separate directions and no file
involved mentions the others.

**The commit stamp**, on every build:

* ``.forgejo/workflows/ci.yml`` computes the commit's short sha once, hands it
  to ``docker build`` as ``GIT_SHA``, and pushes the result to Harbor as
  ``sha-<it>``, which is the tag Flux's ImagePolicy resolves.
* ``Dockerfile`` turns that build-arg into ``AUTOPOSTER_VERSION=sha-<it>``.

**The release stamp**, only on a published release:

* ``.github/workflows/release.yml`` -- which runs on GitHub Actions, not on
  this project's Forgejo runners -- passes the tag it publishes under as
  ``RELEASE_VERSION``.
* ``Dockerfile`` turns that into ``AUTOPOSTER_RELEASE=v1.2.3``.
* ``src/autoposter/api/version.py`` prefers it, and polls GitHub for a newer
  release only when the value it reads parses as a version.

Break any one link and nothing fails loudly: the build still succeeds, the
image still pushes, the endpoint still answers. It answers ``"dev"`` forever,
or a release image silently never checks for updates because its stamp never
arrived. A silent, permanent wrong answer in the UI is precisely the failure a
suite has to catch at the seam rather than in production, which is why these
assertions are about *agreement* between the files and never about a
particular sha or version.
"""
import re
from pathlib import Path

import yaml

from autoposter.api.version import SHA_PREFIX, _version_tuple

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
WORKFLOW = REPO / ".forgejo" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "release.yml"

BUILD_ACTION = "docker/build-push-action"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _image_steps() -> list[dict]:
    steps = ((_workflow().get("jobs") or {}).get("image") or {}).get("steps") or []
    assert steps, "the workflow has no `image` job, so nothing below is verifiable"
    return steps


def _step_with_id(step_id: str) -> dict:
    matches = [s for s in _image_steps() if s.get("id") == step_id]
    assert len(matches) == 1, f"expected exactly one step with id {step_id!r}"
    return matches[0]


def _build_step() -> dict:
    matches = [
        s for s in _image_steps() if str(s.get("uses", "")).startswith(f"{BUILD_ACTION}@")
    ]
    assert len(matches) == 1, f"expected exactly one {BUILD_ACTION} step, found {matches}"
    return matches[0]


def _runtime_stage() -> str:
    """The Dockerfile from ``FROM ... AS runtime`` to the end.

    Sliced rather than searched whole: an ``ENV AUTOPOSTER_VERSION`` in the
    ``dev`` stage would satisfy a naive substring check while the image that
    actually ships carried nothing.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"^FROM .+ AS runtime$", text, re.MULTILINE)
    assert match is not None, "the Dockerfile has no `runtime` stage"
    rest = text[match.end():]
    following = re.search(r"^FROM ", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def test_the_runtime_stage_stamps_the_version_from_the_build_arg():
    stage = _runtime_stage()
    assert re.search(r"^ARG GIT_SHA", stage, re.MULTILINE), (
        "the Dockerfile's runtime stage declares no `ARG GIT_SHA`. A build-arg "
        "is scoped to the stage that declares it, so passing --build-arg "
        "GIT_SHA to a Dockerfile that never names it in `runtime` is accepted "
        "and then ignored"
    )
    assert re.search(
        r"^ENV AUTOPOSTER_VERSION=" + re.escape(SHA_PREFIX) + r"\$\{GIT_SHA\}$",
        stage,
        re.MULTILINE,
    ), (
        "the runtime stage does not set "
        f"`ENV AUTOPOSTER_VERSION={SHA_PREFIX}${{GIT_SHA}}`, so the shipped "
        "image cannot tell anyone which commit it was built from and "
        "GET /api/version reports `dev` in production"
    )


def test_the_stamp_is_the_last_layer_so_a_new_commit_rebuilds_nothing_else():
    """An ARG that changes every commit invalidates every layer below it.

    Put above the COPYs it would mean re-running `pip install` and re-copying
    the bundle on every single build -- the cache would be useless from the
    day this shipped, and the cost would look like a Docker problem rather
    than like this line.
    """
    stage = _runtime_stage()
    stamp = stage.index("ENV AUTOPOSTER_VERSION=")
    later = [
        match.group(0)
        for match in re.finditer(r"^(COPY|RUN) .+$", stage[stamp:], re.MULTILINE)
    ]
    assert not later, (
        "the runtime stage runs these after stamping AUTOPOSTER_VERSION: "
        f"{later}. The stamp changes on every commit, so each of them would be "
        "rebuilt on every commit"
    )


def test_ci_hands_the_build_the_sha_it_pushes_the_image_under():
    """The load-bearing agreement: pushed tag == `sha-` + build-arg.

    Both are written from one shell variable in the `gen_tag` step, so this
    reads that step's outputs and checks the two consumers reference them.
    """
    run = _step_with_id("gen_tag")["run"]
    outputs = dict(re.findall(r'echo "(\w+)=(\S+?)" >> "\$GITHUB_OUTPUT"', run))
    assert "tag" in outputs and "short_sha" in outputs, (
        f"the gen_tag step publishes {sorted(outputs)}; both `tag` (what the "
        "image is pushed as) and `short_sha` (what the build is stamped with) "
        "are needed, computed from the same value"
    )
    assert outputs["tag"] == SHA_PREFIX + outputs["short_sha"], (
        f"gen_tag publishes tag={outputs['tag']!r} and "
        f"short_sha={outputs['short_sha']!r}; the pushed tag must be exactly "
        f"{SHA_PREFIX!r} followed by the stamped sha, or the running version "
        "can never equal a tag in the registry and the sidebar shows an update "
        "that no deploy will ever clear"
    )

    build_args = _build_step().get("with", {}).get("build-args") or ""
    assert "GIT_SHA=${{ steps.gen_tag.outputs.short_sha }}" in build_args, (
        "the image build is not passed GIT_SHA from gen_tag's short_sha "
        f"output (build-args: {build_args!r}); the image would ship unstamped "
        "and report `dev` in production while every check here still passes"
    )


def test_the_pushed_tag_comes_from_that_same_step():
    """If the push stopped reading gen_tag's `tag` output, the assertion above
    would be comparing the build-arg against a string nothing uses."""
    pushes = [
        step for step in _image_steps() if "docker push" in str(step.get("run", ""))
    ]
    assert len(pushes) == 1, f"expected exactly one pushing step, found {len(pushes)}"
    assert "steps.gen_tag.outputs.tag" in pushes[0]["run"], (
        "the push step no longer tags the image from gen_tag's `tag` output, so "
        "the tag in the registry and the sha stamped into the image are now two "
        "independent values"
    )


def _release_steps() -> list[dict]:
    loaded = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    steps = ((loaded.get("jobs") or {}).get("release") or {}).get("steps") or []
    assert steps, "release.yml has no `release` job, so nothing below is verifiable"
    return steps


def test_the_runtime_stage_declares_and_stamps_the_release_version():
    """The stamp that decides whether a container checks for updates at all."""
    stage = _runtime_stage()
    assert re.search(r"^ARG RELEASE_VERSION", stage, re.MULTILINE), (
        "the Dockerfile's runtime stage declares no `ARG RELEASE_VERSION`. A "
        "build-arg is scoped to the stage that declares it, so the release "
        "workflow's --build-arg would be accepted and then ignored"
    )
    assert re.search(
        r"^ENV AUTOPOSTER_RELEASE=\$\{RELEASE_VERSION\}$", stage, re.MULTILINE
    ), (
        "the runtime stage does not set `ENV AUTOPOSTER_RELEASE=${RELEASE_VERSION}`, "
        "so a released image cannot tell it is a release and api/version.py "
        "never polls for a newer one"
    )


def test_a_build_with_no_release_arg_is_not_mistaken_for_a_release():
    """The default has to be empty, not a placeholder.

    `ARG RELEASE_VERSION="dev"` or similar would make every build of main look
    like a release to `_running_version`, and every Flux pod in the cluster
    would start polling GitHub and comparing a made-up version against real
    release tags.
    """
    stage = _runtime_stage()
    match = re.search(r'^ARG RELEASE_VERSION=(.*)$', stage, re.MULTILINE)
    assert match is not None, "ARG RELEASE_VERSION is declared with no default at all"
    default = match.group(1).strip().strip('"').strip("'")
    assert default == "", (
        f"ARG RELEASE_VERSION defaults to {default!r}; it must default to empty "
        "so that only the release workflow can stamp a release"
    )
    assert _version_tuple(default) is None, (
        f"the default {default!r} parses as a version, so every non-release "
        "build would report itself as a release"
    )


def test_the_release_workflow_stamps_the_tag_it_publishes_under():
    """The release equivalent of the sha agreement: what is stamped into the
    image and what it is pushed as are the same string, from one step."""
    build = [
        s for s in _release_steps()
        if str(s.get("uses", "")).startswith(f"{BUILD_ACTION}@")
    ]
    assert len(build) == 1, f"expected exactly one {BUILD_ACTION} step in release.yml"
    build_args = build[0].get("with", {}).get("build-args") or ""
    assert "RELEASE_VERSION=${{ steps.version.outputs.tag }}" in build_args, (
        "the release build is not passed RELEASE_VERSION from the version "
        f"step's `tag` output (build-args: {build_args!r}); the published image "
        "would carry no release stamp and would never check for updates"
    )
    assert "GIT_SHA=${{ steps.version.outputs.short_sha }}" in build_args, (
        "the release build no longer stamps the commit as well, so a released "
        "image cannot say which commit it was built from"
    )

    pushes = [s for s in _release_steps() if "docker push" in str(s.get("run", ""))]
    assert len(pushes) == 1, f"expected exactly one pushing step, found {len(pushes)}"
    assert "steps.version.outputs.tag" in pushes[0]["run"], (
        "the push step no longer tags the image from the version step's `tag` "
        "output, so the registry tag and the stamp inside the image are now two "
        "independent values"
    )
