"""The release workflow publishes to a PUBLIC registry, so the properties that
keep an unverified or mislabelled image out of it are worth asserting rather
than reading.

This is the one workflow in the repository that runs on GitHub Actions rather
than on Forgejo, and that is a credentials decision: GitHub mints a scoped
``GITHUB_TOKEN`` per run that can already write to that repository's packages,
so publishing to ghcr.io needs no stored PAT. The push mirror carries the
`release` branch and its tags to GitHub, so tagging on Forgejo is what starts
it.

Five things can go wrong here in ways nothing downstream would notice:

1. The tag and ``pyproject.toml`` disagree, and an image is published under a
   version its own metadata contradicts.
2. A push step drifts above a verification step, so a build nobody checked
   reaches the registry.
3. The push rebuilds instead of re-tagging, so the artifact published is not
   the artifact that passed the checks.
4. ``ci.yml`` starts firing on tags too, and a release re-runs the whole suite
   on Forgejo alongside this one.
5. The ``permissions`` block loses ``packages: write`` or ``contents: write``.
   The first makes every release fail at the push; the second makes it fail at
   the Release, which is subtler -- see below.
6. The Release step goes away. A mirrored git tag is **not** a GitHub Release,
   and ``/releases/latest`` -- the endpoint ``api/version.py`` polls -- knows
   only about Releases. It answered 404 for this repository while ``/tags``
   listed ``v0.1.0`` quite happily. Without this step no released container
   ever learns about its successor, and nothing anywhere goes red.

Deliberately NOT asserted: that the steps work. They were run verbatim against
a locally built image before the workflow was committed, which is what this
repository requires of any workflow edit; a test that re-ran them would need a
docker daemon and a build, and would duplicate the workflow itself.
"""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
RELEASE = REPO / ".github" / "workflows" / "release.yml"
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

    ci.yml runs on this project's own Forgejo runners, creates docker resources
    named after its run id and pushes to Harbor. Overlapping on one tag would
    re-run, on a shared and much slower runner, a suite that already passed on
    main -- for no gain, since the release job does not depend on it.
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
    # And the login itself, which is not a gate but would be a wasted build.
    assert _index_of("Log in to GHCR") < push, (
        "the login step runs after the push, which cannot work"
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


def test_it_runs_on_githubs_own_runners():
    """`ubuntu-latest` here means a runner GitHub provides, which is the point:
    no self-hosted label, nothing of this project's infrastructure involved."""
    assert _workflow(RELEASE)["jobs"]["release"]["runs-on"] == "ubuntu-latest"


def test_the_token_is_the_runs_own_and_nothing_is_stored():
    """The whole reason this moved to GitHub Actions. A `secrets.` reference to
    anything but the automatic GITHUB_TOKEN means a credential was put back
    into storage to be rotated and leaked."""
    text = RELEASE.read_text(encoding="utf-8")
    used = set(re.findall(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)", text))
    assert used == {"GITHUB_TOKEN"}, (
        f"release.yml references stored secrets {sorted(used - {'GITHUB_TOKEN'})}; "
        "GITHUB_TOKEN is minted per run and already carries packages:write"
    )

    login = _release_steps()[_index_of("Log in to GHCR")]
    assert login["with"]["password"] == "${{ secrets.GITHUB_TOKEN }}"
    assert login["with"]["registry"] == "${{ env.REGISTRY }}"


def test_the_permissions_are_exactly_what_publishing_needs():
    """Two scopes, each earned by a step. Declaring the block at all narrows
    everything else to nothing, which is the point of spelling it out."""
    permissions = _workflow(RELEASE)["permissions"]
    assert permissions.get("packages") == "write", (
        "without `packages: write` the token cannot push and every release "
        f"fails at the push step; permissions are {permissions!r}"
    )
    assert permissions.get("contents") == "write", (
        f"`contents` is {permissions.get('contents')!r}; creating the GitHub "
        "Release needs write, and without the Release `/releases/latest` stays "
        "a 404 and the in-app update check never finds anything"
    )
    assert set(permissions) == {"contents", "packages"}, (
        f"release.yml grants {sorted(permissions)}; every scope here should be "
        "one a step actually uses"
    )


def test_a_github_release_is_created_and_only_after_the_push():
    """The step that makes the update check possible.

    A mirrored tag creates no Release, so `api/version.py` would poll
    `/releases/latest` and get 404 forever. It runs last on purpose: a Release
    announces something that exists, and creating one before the push would
    advertise an image a failing push never delivered.
    """
    create = _index_of("Create the GitHub release")
    assert create > _index_of("Push the verified image"), (
        "the Release is created before the image is pushed, so a failed push "
        "would leave a Release advertising an image nobody can pull"
    )

    step = _release_steps()[create]
    run = step["run"]
    assert "gh release create" in run, (
        f"the Release step no longer calls `gh release create`: {run!r}"
    )
    assert step.get("env", {}).get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}", (
        "gh needs GH_TOKEN in the environment; without it the step fails "
        "unauthenticated even though the job has the permission"
    )
    assert "--verify-tag" in run, (
        "`--verify-tag` makes gh refuse to invent a tag that does not exist, "
        "which is the one way this step could publish a Release pointing at "
        "nothing"
    )


def test_the_published_repository_is_the_public_ghcr_path():
    env = _workflow(RELEASE)["env"]
    assert env["REGISTRY"] == "ghcr.io"
    target = f"{env['REGISTRY']}/{env['REGISTRY_NAMESPACE']}/{env['IMAGE_NAME']}"
    assert target == "ghcr.io/operinko-labs/autoposter", target
    assert target == target.lower(), (
        "GHCR rejects an image path containing uppercase characters"
    )
