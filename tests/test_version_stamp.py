"""The image's version stamp must be the tag the image is pushed under.

``GET /api/version`` compares two strings for equality. That comparison is
only meaningful because three files agree, and none of them mentions the
others:

* ``.forgejo/workflows/ci.yml`` computes the commit's short sha once, hands it
  to ``docker build`` as ``GIT_SHA``, and pushes the result as ``sha-<it>``.
* ``Dockerfile`` turns that build-arg into ``AUTOPOSTER_VERSION=sha-<it>``.
* ``src/autoposter/api/version.py`` reads that variable and matches Harbor
  tags on the ``sha-`` prefix.

Break any one link and nothing fails: the build still succeeds, the image
still pushes, the endpoint still answers. It answers ``"dev"`` forever, or it
compares a tag against a differently-shaped one and reports an update that
never goes away. A silent, permanent wrong answer in the UI is precisely the
failure a suite has to catch at the seam rather than in production, which is
why these assertions are about *agreement* between the files and never about
a particular sha.
"""
import re
from pathlib import Path

import yaml

from autoposter.api.version import TAG_PREFIX

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
WORKFLOW = REPO / ".forgejo" / "workflows" / "ci.yml"

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
        r"^ENV AUTOPOSTER_VERSION=" + re.escape(TAG_PREFIX) + r"\$\{GIT_SHA\}$",
        stage,
        re.MULTILINE,
    ), (
        "the runtime stage does not set "
        f"`ENV AUTOPOSTER_VERSION={TAG_PREFIX}${{GIT_SHA}}`, so the shipped "
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
    assert outputs["tag"] == TAG_PREFIX + outputs["short_sha"], (
        f"gen_tag publishes tag={outputs['tag']!r} and "
        f"short_sha={outputs['short_sha']!r}; the pushed tag must be exactly "
        f"{TAG_PREFIX!r} followed by the stamped sha, or the running version "
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
