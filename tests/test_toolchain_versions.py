"""The toolchain must be one exact version, declared identically everywhere.

Three breakages in this repository came from a version resolving differently in
CI than it did locally or in the image -- ruff's rule defaults, Pillow's
``getdata`` removal, FastAPI's route flattening -- and each was fixed where it
hurt rather than where it came from. A floating tag (``python:3.14-alpine``,
``node-version: "26"``) is a standing invitation to the same divergence, and so
is one file disagreeing with another about which version is meant. CI ran the
whole suite on Python 3.13 for two phases while the image shipped 3.14, so
nothing verified the interpreter production actually runs.

Six files declare the toolchain, and they have to agree:

- ``Dockerfile`` -- the Python and Node that actually ship, across every stage.
- ``.forgejo/workflows/ci.yml`` -- the Python the suite is proven on, the Node
  the bundle CI typechecks is built with, and the PostgreSQL the suite and the
  migration check run against.
- ``pyproject.toml`` -- which Pythons an install is permitted on.
- ``frontend/package.json`` -- which Node ``npm ci`` expects.
- ``frontend/.npmrc`` -- whether ``npm ci`` enforces that or merely mentions it.
- ``docker-compose.yml`` -- the developer database, which must be the same
  PostgreSQL as CI's.

Renovate is meant to keep these in step, which is the other reason this test
exists: a grouping mis-config that bumps the Dockerfile but not the workflow
looks exactly like the drift asserted against below, and should stop a merge
rather than ship. ``renovate.json`` is therefore asserted against here too --
the manager that reads the workflow's ``# renovate:`` annotations is opt-in
while the ones that read the other four files are not, so an unconfigured
Renovate produces that drift on its very first pull request.

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
RENOVATE = REPO / "renovate.json"

# Python and Node release as major.minor.patch, so an exact pin has three
# components. PostgreSQL's patch is its second component (18.1), so it needs a
# shape of its own rather than being waved through by a looser pattern.
EXACT_THREE_PART = re.compile(r"\d+\.\d+\.\d+")
EXACT_TWO_PART = re.compile(r"\d+\.\d+")

# The preset that reads the workflow's ``# renovate:`` annotations, and the only
# thing that does. It is a preset rather than a default, so ``renovate.json``
# has to name it or the annotations are decoration.
ANNOTATION_PRESET = "customManagers:githubActionsVersions"

# That preset's ``matchStrings`` regex, transcribed from Renovate's own source
# (``lib/config/presets/internal/custom-managers.preset.ts``) with two
# mechanical changes: JavaScript's ``(?<name>...)`` group syntax becomes
# Python's ``(?P<name>...)``, and a capture group is wrapped around the
# already-present ``[A-Za-z0-9_]+?_VERSION`` atom so a match can be tied back to
# the variable it annotates. Neither changes what the pattern matches.
#
# Holding the annotations to *this* rather than to ``startswith("# renovate:")``
# is the whole point: Renovate does not error on a malformed annotation, it just
# does not match. ``datasoure=docker``, ``depname=python``, a second space
# between two fields, ``versioning=`` written before ``depName=``, or a variable
# renamed to something not ending in ``_VERSION`` all leave the comment looking
# correct while the pin silently stops being updated -- and the drift then
# surfaces one Docker bump later as a failure in a different test, pointing at
# the wrong thing.
#
# Note the field order is fixed and the separators are single spaces: after the
# mandatory ``datasource=`` and ``depName=`` come optional ``packageName=``
# (or its legacy alias ``lookupName=``), ``versioning=``, ``extractVersion=``
# and ``registryUrl=``, in that order. ``\s+`` between the comment and the
# variable allows only whitespace between them, so the annotation has to sit on
# the immediately preceding line.
GITHUB_ACTIONS_VERSIONS = re.compile(
    r"# renovate: datasource=(?P<datasource>[a-zA-Z0-9-._]+?)"
    r" depName=(?P<depName>[^\s]+?)"
    r"(?: (?:lookupName|packageName)=(?P<packageName>[^\s]+?))?"
    r"(?: versioning=(?P<versioning>[^\s]+?))?"
    r"(?: extractVersion=(?P<extractVersion>[^\s]+?))?"
    r"(?: registryUrl=(?P<registryUrl>[^\s]+?))?"
    r"\s+(?P<variable>[A-Za-z0-9_]+?_VERSION)\s*:\s*[\"']?(?P<currentValue>.+?)[\"']?\s"
)

# Which dependency each annotated variable has to name. These are the image
# names the Dockerfile and docker-compose.yml use, which is what lets one
# Renovate group resolve every location to the same string.
ANNOTATED_VERSIONS = {
    "PYTHON_VERSION": "python",
    "NODE_VERSION": "node",
    "POSTGRES_VERSION": "postgres",
}


def _dockerfile_version(image: str) -> str:
    """The version every ``FROM <image>:<version>-<variant>`` line names.

    Development stages made this plural: two ``FROM node:`` lines now exist,
    one building the bundle and one running the vite dev server. Asserting
    that they agree is strictly stronger than the previous assertion that
    there was only ever one of them, and it is what lets stages multiply
    without the pin quietly forking between them.
    """
    tags = re.findall(
        rf"^FROM {re.escape(image)}:(\S+)",
        DOCKERFILE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert tags, f"no `FROM {image}:` line in the Dockerfile"
    for tag in tags:
        assert tag.partition("-")[2], f"`FROM {image}:{tag}` names no image variant"
    versions = {tag.partition("-")[0] for tag in tags}
    assert len(versions) == 1, (
        f"the Dockerfile builds on more than one {image}: {sorted(versions)}; "
        "every stage must name the same exact patch, or what a development "
        "stage produces is not what the runtime stage ships"
    )
    return versions.pop()


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

    On its own this would buy less than it looks: npm's ``engine-strict`` is
    off by default, so a mismatch is an ``EBADENGINE`` warning rather than a
    refusal. ``frontend/.npmrc`` is what closes that, and
    ``test_npm_refuses_the_wrong_node_rather_than_warning`` below holds it
    there -- the pair is what makes this line enforceable rather than advisory.
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


def test_npm_refuses_the_wrong_node_rather_than_warning():
    """Without this the pin above is a note, not a rule.

    ``engine-strict`` defaults to false, so ``npm ci`` under a different Node
    prints ``EBADENGINE`` and then installs anyway -- node:24-alpine warned and
    resolved all 116 packages regardless. ``frontend/.npmrc`` turns that into
    exit 1, so every install of this frontend happens on the one Node the image
    builds with instead of merely being told about it.
    """
    npmrc = REPO / "frontend" / ".npmrc"
    assert npmrc.is_file(), (
        "frontend/.npmrc is missing, so npm's engine-strict is back to its "
        "default of false and engines.node is only a warning again"
    )
    settings = [
        line.split("#", 1)[0].split(";", 1)[0].strip()
        for line in npmrc.read_text(encoding="utf-8").splitlines()
    ]
    assert "engine-strict=true" in [s.replace(" ", "") for s in settings if s], (
        "frontend/.npmrc does not set engine-strict=true; engines.node is then "
        "advisory and a mismatched Node installs with a warning"
    )


def test_the_image_build_reads_the_npmrc():
    """The frontend stage copies the manifests by name, so a file added beside
    them is invisible to the install unless it is named too.

    The later ``COPY frontend/ ./`` does pick it up, but that runs *after*
    ``npm ci`` -- leaving the one build that actually ships the bundle as the
    single place the rule would not apply, which is exactly backwards.
    """
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    copied = next(
        (i for i, line in enumerate(lines) if re.match(r"COPY.*frontend/\.npmrc", line)),
        None,
    )
    installed = next(
        (i for i, line in enumerate(lines) if re.match(r"RUN\s+npm ci", line)),
        None,
    )
    assert installed is not None, "the Dockerfile no longer runs `npm ci` at all"
    assert copied is not None and copied < installed, (
        "the Dockerfile runs `npm ci` without having copied frontend/.npmrc "
        "into the frontend stage first, so the image build installs with "
        "engine-strict off while everywhere else has it on"
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
    # `\bpostgres:` alone is too loose: `\b` matches between the `-` and `p` in
    # `ci-postgres`, so the connection URL
    # `...@ci-postgres:5432/autoposter_magick` reads as an inline image tag
    # `5432`. A real image reference is the bare word `postgres` (nothing
    # word-like or a hyphen just before it) and is never immediately followed
    # by a `/`, which is exactly what a host:port has next. Excluding both
    # keeps a literal `postgres:18-alpine` caught while leaving the
    # `ci-postgres:5432` URL alone.
    inline = re.findall(r"(?<![\w-])postgres:(?!\$\{\{)([\w.+-]+)(?!/)", workflow_text)
    assert not inline, (
        f"the workflow names a postgres tag inline ({inline}); use "
        "postgres:${{ env.POSTGRES_VERSION }}-alpine so there is one version to "
        "change and Renovate has one place to update"
    )
    assert "postgres:${{ env.POSTGRES_VERSION }}-alpine" in workflow_text, (
        "no postgres image is started in the workflow at all; this guard proves nothing as written"
    )

    version = _workflow_env("POSTGRES_VERSION")
    assert EXACT_TWO_PART.fullmatch(version), (
        f"POSTGRES_VERSION is {version!r}, which floats within the major; pin the "
        "patch so CI, the migration check and the developer database are one server"
    )

    compose_image = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["postgres"][
        "image"
    ]
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
    text = WORKFLOW.read_text(encoding="utf-8")
    parsed = {m.group("variable"): m for m in GITHUB_ACTIONS_VERSIONS.finditer(text)}

    for name, dependency in ANNOTATED_VERSIONS.items():
        assert f"{name}:" in text, f"{name} is not declared in the workflow"
        assert name in parsed, (
            f"{name} has no `# renovate:` annotation Renovate would actually "
            f"parse. {ANNOTATION_PRESET} matches only "
            "`# renovate: datasource=<x> depName=<y>` -- one space between each "
            "field, that field order, and the variable on the very next line -- "
            "and it reports nothing when an annotation is malformed, so this "
            "pin would silently stop being updated and fall behind the Dockerfile"
        )
        match = parsed[name]
        assert match.group("datasource") == "docker", (
            f"{name} is annotated datasource={match.group('datasource')!r}; the "
            "Dockerfile and docker-compose.yml declare these as Docker images, "
            "and a different datasource resolves a different version string, so "
            "the locations cannot agree even when both are updated"
        )
        assert match.group("depName") == dependency, (
            f"{name} is annotated depName={match.group('depName')!r} but names "
            f"the {dependency} version; Renovate would group and bump the wrong "
            "dependency here"
        )
        assert match.group("currentValue") == _workflow_env(name), (
            f"the annotation above {name} reads a value of "
            f"{match.group('currentValue')!r} while the env block sets "
            f"{_workflow_env(name)!r}; Renovate updates what it parsed"
        )

    annotations = [
        line for line in text.splitlines() if line.strip().startswith("# renovate:")
    ]
    assert len(annotations) == len(parsed), (
        f"the workflow has {len(annotations)} `# renovate:` annotations but "
        f"{len(parsed)} of them match the shape {ANNOTATION_PRESET} requires; "
        "the rest are inert comments. Check field order, single spaces between "
        "fields, and that the variable annotated ends in _VERSION"
    )


def test_renovate_is_configured_to_read_those_annotations():
    """The annotations above do nothing on their own.

    ``customManagers:githubActionsVersions`` is a preset, not a default. Without
    it in ``extends``, no Renovate manager parses ``# renovate:`` comments at
    all -- while the dockerfile, docker-compose and npm managers, which are on
    by default, carry on bumping the other four files. That asymmetry is exactly
    the half-applied update every test above is written to catch, arriving on
    the first dependency PR rather than through anyone's mistake.
    """
    config = json.loads(RENOVATE.read_text(encoding="utf-8"))
    extends = config.get("extends") or []
    assert ANNOTATION_PRESET in extends, (
        f"renovate.json does not extend {ANNOTATION_PRESET}, so the "
        "`# renovate:` annotations in .forgejo/workflows/ci.yml are inert and "
        "the workflow's versions will fall behind the Dockerfile's"
    )

    groups = {
        rule.get("groupName"): rule
        for rule in config.get("packageRules") or []
        if rule.get("groupName")
    }
    assert groups, (
        "renovate.json defines no grouped packageRules; each declaration of a "
        "version would then be its own PR, and every one of them fails the "
        "agreement tests above"
    )
    grouped = {
        name for rule in groups.values() for name in rule.get("matchDepNames") or []
    }
    missing = set(ANNOTATED_VERSIONS.values()) - grouped
    assert not missing, (
        f"renovate.json groups no packageRule on matchDepNames for {sorted(missing)}; "
        "those declarations would be updated in separate PRs and each one alone "
        "leaves the files disagreeing. Group on the dependency name, not the "
        "datasource -- node is reported as datasource `docker` from the "
        "Dockerfile and the workflow but as `node-version` from "
        "frontend/package.json's engines"
    )
