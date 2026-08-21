"""The toolchain must be one exact version, declared identically everywhere.

Three breakages in this repository came from a version resolving differently in
CI than it did locally or in the image -- ruff's rule defaults, Pillow's
``getdata`` removal, FastAPI's route flattening -- and each was fixed where it
hurt rather than where it came from. A floating tag (``python:3.14-alpine``,
``node-version: "26"``) is a standing invitation to the same divergence, and so
is one file disagreeing with another about which version is meant. CI ran the
whole suite on Python 3.13 for two phases while the image shipped 3.14, so
nothing verified the interpreter production actually runs.

Five files declare the toolchain, and they have to agree:

- ``Dockerfile`` -- the Python and Node that actually ship.
- ``.forgejo/workflows/ci.yml`` -- the Python the suite is proven on, the Node
  the bundle CI typechecks is built with, and the PostgreSQL the suite and the
  migration check run against.
- ``pyproject.toml`` -- which Pythons an install is permitted on.
- ``frontend/package.json`` -- which Node ``npm ci`` expects.
- ``docker-compose.yml`` -- the developer database, which must be the same
  PostgreSQL as CI's.

Renovate is meant to keep these in step, which is the other reason this test
exists: a grouping mis-config that bumps the Dockerfile but not the workflow
looks exactly like the drift asserted against below, and should stop a merge
rather than ship.

Every assertion is about agreement and exactness, never about a particular
number, so a deliberate upgrade means changing the declarations together and
then this stays green.
"""

import json
import re
import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
WORKFLOW = REPO / ".forgejo" / "workflows" / "ci.yml"
PYPROJECT = REPO / "pyproject.toml"
PACKAGE_JSON = REPO / "frontend" / "package.json"
COMPOSE = REPO / "docker-compose.yml"

# Python and Node release as major.minor.patch, so an exact pin has three
# components. PostgreSQL's patch is its second component (18.1), so it needs a
# shape of its own rather than being waved through by a looser pattern.
EXACT_THREE_PART = re.compile(r"\d+\.\d+\.\d+")
EXACT_TWO_PART = re.compile(r"\d+\.\d+")


def _dockerfile_version(image: str) -> str:
    """The version from the one ``FROM <image>:<version>-<variant>`` line."""
    tags = re.findall(
        rf"^FROM {re.escape(image)}:(\S+)",
        DOCKERFILE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert len(tags) == 1, f"expected exactly one `FROM {image}:` line, found {tags}"
    version, _, variant = tags[0].partition("-")
    assert variant, f"`FROM {image}:{tags[0]}` names no image variant"
    return version


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _workflow_env(name: str) -> str:
    env = _workflow().get("env") or {}
    assert name in env, f"{name} is not set in the workflow's env block"
    return str(env[name])


def _action_input(action: str, key: str) -> str:
    """What one pinned action is given, with an ``env.`` reference resolved.

    Resolving matters: both version inputs are written as ``${{ env.X }}``, so a
    check that only read the env block would sail straight past someone
    hardcoding a different number in the step itself.
    """
    values = [
        str((step.get("with") or {})[key])
        for job in (_workflow().get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if str(step.get("uses", "")).startswith(f"{action}@")
    ]
    assert len(values) == 1, f"expected exactly one {action} step, found {values}"
    reference = re.fullmatch(r"\$\{\{\s*env\.(\w+)\s*\}\}", values[0])
    return _workflow_env(reference.group(1)) if reference else values[0]


def test_the_image_pins_python_to_an_exact_patch():
    version = _dockerfile_version("python")
    assert EXACT_THREE_PART.fullmatch(version), (
        f"the image ships python:{version}-alpine, which floats: a rebuild picks "
        "up whichever patch the tag points at that day, so what was tested and "
        "what ships can differ with no change to this repository"
    )


def test_ci_tests_the_python_the_image_ships():
    """The mismatch this module was written for."""
    image = _dockerfile_version("python")
    ci = _action_input("actions/setup-python", "python-version")
    assert EXACT_THREE_PART.fullmatch(ci), (
        f"CI installs Python {ci!r}, which floats; pin the exact patch so the "
        "suite is proven on one interpreter rather than whichever the runner has"
    )
    assert ci == image, (
        f"CI runs the suite on Python {ci} while the image ships {image}; "
        "nothing then tests the interpreter production runs"
    )


def test_requires_python_admits_only_the_pinned_minor():
    """A floor (``>=3.12``) quietly lets a third interpreter into the picture.

    Bounded to the shipped minor, an install anywhere else fails loudly instead
    of resolving a different set of wheels and looking fine.
    """
    major, minor, _ = _dockerfile_version("python").split(".")
    expected = f">={major}.{minor},<{major}.{int(minor) + 1}"
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"][
        "requires-python"
    ]
    assert declared.replace(" ", "") == expected, (
        f"pyproject.toml declares requires-python = {declared!r} while the image "
        f"ships Python {major}.{minor}; expected {expected!r}, so no other minor "
        "version can be installed against"
    )


def test_the_image_pins_node_to_an_exact_patch():
    version = _dockerfile_version("node")
    assert EXACT_THREE_PART.fullmatch(version), (
        f"the frontend stage builds on node:{version}-alpine, which floats: the "
        "bundle the image serves would be built by whichever patch the tag "
        "resolved to at build time"
    )


def test_ci_builds_the_frontend_with_the_node_the_image_uses():
    image = _dockerfile_version("node")
    ci = _action_input("actions/setup-node", "node-version")
    assert EXACT_THREE_PART.fullmatch(ci), (
        f"CI installs Node {ci!r}, which floats; pin the exact patch so the "
        "bundle CI typechecks and tests is built by the same Node as the image"
    )
    assert ci == image, (
        f"CI builds the frontend on Node {ci} while the image builds it on "
        f"{image}; the bundle that was tested is not the bundle that ships"
    )


def test_package_json_pins_node_exactly():
    """``engines`` is the third declaration of the same fact, and a range there
    re-opens what the other two just closed.

    Note what this does and does not buy: npm's ``engine-strict`` is off by
    default, so a mismatch is an ``EBADENGINE`` warning rather than a refusal.
    The value of pinning it is that it states one version instead of a range,
    and that this test then holds it to the one the image builds with.
    """
    image = _dockerfile_version("node")
    declared = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["engines"]["node"]
    assert declared == image, (
        f"frontend/package.json declares engines.node = {declared!r}; expected "
        f"exactly {image!r}, the Node the Dockerfile builds the bundle with"
    )


def test_the_lockfile_mirrors_the_declared_engines():
    """``npm ci`` refuses to run when package.json and the lockfile disagree,
    and the lockfile keeps its own copy of ``engines``. Editing one without the
    other breaks the image build rather than this test, which is a much later
    and much more confusing place to find out."""
    declared = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["engines"]["node"]
    lockfile = json.loads(
        (REPO / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )
    mirrored = lockfile["packages"][""]["engines"]["node"]
    assert mirrored == declared, (
        f"package-lock.json records engines.node = {mirrored!r} but package.json "
        f"declares {declared!r}; regenerate the lockfile"
    )


def test_postgres_is_one_exact_version_everywhere():
    """CI and the developer database have to be the same PostgreSQL.

    A dev database on a different patch than the one the suite and the
    migrations are verified against is the same ambiguity as a floating
    interpreter, just further from the code. The workflow starts PostgreSQL with
    `docker run` inside `run:` blocks, which nothing parses for versions, so
    they all read one env var -- and this asserts none of them went back to
    naming a tag inline.
    """
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    inline = re.findall(r"\bpostgres:(?!\$\{\{)([\w.+-]+)", workflow_text)
    assert not inline, (
        f"the workflow names a postgres tag inline ({inline}); use "
        "postgres:${{ env.POSTGRES_VERSION }}-alpine so there is one version to "
        "change and Renovate has one place to update"
    )
    assert "postgres:${{ env.POSTGRES_VERSION }}-alpine" in workflow_text, (
        "no postgres image is started in the workflow at all; this guard proves "
        "nothing as written"
    )

    version = _workflow_env("POSTGRES_VERSION")
    assert EXACT_TWO_PART.fullmatch(version), (
        f"POSTGRES_VERSION is {version!r}, which floats within the major; pin the "
        "patch so CI, the migration check and the developer database are one server"
    )

    compose_image = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"][
        "postgres"
    ]["image"]
    assert compose_image == f"postgres:{version}-alpine", (
        f"docker-compose.yml runs {compose_image} while CI runs "
        f"postgres:{version}-alpine; develop and verify against one server"
    )


def test_the_workflow_version_pins_stay_visible_to_renovate():
    """Renovate reads ``uses:``, ``container:`` and ``services:`` natively but
    not ``env:`` values, so each pin here needs a ``# renovate:`` annotation to
    be updated at all.

    Without one, Renovate bumps the Dockerfile, leaves the workflow behind, and
    every assertion above starts failing on an unrelated dependency PR. The
    annotation is what keeps this module a safety net rather than a tripwire.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    for name in ("PYTHON_VERSION", "NODE_VERSION", "POSTGRES_VERSION"):
        index = next(
            (i for i, line in enumerate(lines) if line.strip().startswith(f"{name}:")),
            None,
        )
        assert index is not None, f"{name} is not declared in the workflow"
        assert index > 0 and lines[index - 1].strip().startswith("# renovate:"), (
            f"{name} has no `# renovate:` annotation on the line above it, so "
            "Renovate cannot see or update it and it will silently fall behind "
            "the Dockerfile"
        )
