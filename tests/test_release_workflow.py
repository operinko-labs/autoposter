"""The release workflow publishes to a PUBLIC registry, so the properties that
keep an unverified or mislabelled image out of it are worth asserting rather
than reading.

Four things can go wrong here in ways nothing downstream would notice:

1. The tag and ``pyproject.toml`` disagree, and an image is published under a
   version its own metadata contradicts.
2. A push step drifts above a verification step, so a build nobody checked
   reaches the registry.
3. The push rebuilds instead of re-tagging, so the artifact published is not
   the artifact that passed the checks.
4. ``ci.yml`` starts firing on tags too, and a release re-runs the whole suite
   (or worse, races this workflow for the same docker resources).

Deliberately NOT asserted: that the steps work. They were run verbatim against
a locally built image before the workflow was committed, which is what
.superpowers notes require of any workflow edit here; a test that re-ran them
would need a docker daemon and a build, and would duplicate CI.
"""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
RELEASE = REPO / ".forgejo" / "workflows" / "release.yml"
CI = REPO / ".forgejo" / "workflows" / "ci.yml"


def _workflow(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _release_steps():
    return _workflow(RELEASE)["jobs"]["release"]["steps"]


def _index_of(prefix):
    """Position of the first step whose name starts with ``prefix``."""
    for i, step in enumerate(_release_steps()):
        if step["name"].startswith(prefix):
            return i
    raise AssertionError(
        f"no step named like {prefix!r} in release.yml; if it was renamed, this "
        "guard has stopped guarding it"
    )


def test_the_release_workflow_triggers_only_on_version_tags():
    triggers = _workflow(RELEASE)["on"]
    assert set(triggers) == {"push"}, (
        f"release.yml answers {sorted(triggers)}; a second trigger would let a "
        "release publish from something other than a tag"
    )
    push = triggers["push"]
    assert push == {"tags": ["v*"]}, (
        f"expected the push trigger to be exactly tags: ['v*'], got {push!r}. A "
        "`branches` key here would publish a release image on every commit"
    )


def test_ci_does_not_also_run_on_tags():
    """The two workflows must not both wake on a tag.

    ci.yml creates docker resources named after its run id and pushes to
    Harbor; release.yml builds and pushes to GHCR. Overlapping on one tag would
    put two builds on the runner's shared daemon for the same commit and re-run
    a suite that already passed on main.
    """
    push = _workflow(CI)["on"]["push"]
    assert "tags" not in push, (
        "ci.yml's push trigger has grown a `tags` key, so a release tag now "
        "starts both workflows"
    )
    assert push.get("branches") == ["main"], (
        f"ci.yml's push trigger is {push.get('branches')!r}, expected ['main']"
    )


def test_the_release_tag_is_checked_against_pyproject():
    """A tag that disagrees with the declared version must stop the job."""
    step = _release_steps()[_index_of("Derive the version")]
    run = step["run"]
    assert "pyproject.toml" in run, (
        "the version step no longer reads pyproject.toml, so nothing catches a "
        "tag that disagrees with the declared version"
    )
    assert 'if [ "$VERSION" != "$DECLARED" ]' in run, (
        "the tag-vs-pyproject comparison is gone from the version step"
    )
    # The refusal has to be fatal. A warning here would publish anyway.
    assert run.count("exit 1") >= 3, (
        "expected the version step to exit 1 on a malformed tag, an unreadable "
        "pyproject version, and a mismatch"
    )


def test_the_declared_version_is_readable_by_the_step_that_reads_it():
    """The sed in the workflow and pyproject.toml's actual shape must agree.

    A `version` moved under a `[tool.*]` table, or rewritten with single
    quotes, would leave the workflow reading an empty string -- caught in CI
    only at release time, which is the worst moment to find out.
    """
    run = _release_steps()[_index_of("Derive the version")]["run"]
    pattern = re.search(r"sed -n 's(.*?)p' pyproject\.toml", run)
    assert pattern, f"could not find the version-reading sed in:\n{run}"
    # Apply the workflow's own expression to the real file.
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    matches = re.findall(r'^version = "(.*)"$', text, flags=re.MULTILINE)
    assert matches, (
        "pyproject.toml has no top-level `version = \"...\"` line, so the "
        "release workflow's sed would read an empty string and refuse to publish"
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+", matches[0]), (
        f"declared version {matches[0]!r} is not MAJOR.MINOR.PATCH, so no tag "
        "this workflow accepts could ever match it"
    )


def test_nothing_is_pushed_before_the_image_is_verified():
    """Order is the whole guard. There is no `needs:` to lean on inside a job."""
    push = _index_of("Push the verified image")
    for gate in (
        "Verify the application imports",
        "Verify ImageMagick",
        "Verify the version the image reports",
        "Scan the image for vulnerabilities",
    ):
        assert _index_of(gate) < push, (
            f"{gate!r} runs after the push step, so a failing check cannot stop "
            "the image reaching a public registry"
        )
    # Credentials are checked before the build, so a missing secret costs
    # seconds rather than a full image build.
    assert _index_of("Require the GHCR credentials") < _index_of("Build the image"), (
        "the GHCR credential check moved below the build; a missing secret now "
        "wastes the whole build before failing"
    )


def test_the_build_step_loads_rather_than_pushes():
    step = _release_steps()[_index_of("Build the image")]
    with_ = step["with"]
    assert with_["load"] is True and with_["push"] is False, (
        "the build step pushes directly, which would put the image in the "
        "registry before any verification step has seen it"
    )


def test_the_pushed_image_is_the_one_that_was_built():
    """Re-tag, never rebuild: a second build can differ from the verified one."""
    run = _release_steps()[_index_of("Push the verified image")]["run"]
    assert "docker build" not in run, (
        "the push step rebuilds the image, so what is published is not what the "
        "verification steps ran against"
    )
    tagged = re.findall(r"docker tag \$\{\{ env\.REL_IMAGE \}\}", run)
    assert len(tagged) == 3, (
        f"expected three `docker tag` calls off env.REL_IMAGE, found {len(tagged)}"
    )
    for ref in ('"${TARGET}:${TAG}"', '"${TARGET}:${VERSION}"', '"${TARGET}:latest"'):
        assert f"docker push {ref}" in run, (
            f"the push step no longer publishes {ref}"
        )


def test_the_published_repository_is_the_public_ghcr_path():
    env = _workflow(RELEASE)["env"]
    assert env["REGISTRY"] == "ghcr.io"
    target = f"{env['REGISTRY']}/{env['REGISTRY_NAMESPACE']}/{env['IMAGE_NAME']}"
    assert target == "ghcr.io/operinko-labs/autoposter", target
    assert target == target.lower(), (
        "GHCR rejects an image path containing uppercase characters"
    )
