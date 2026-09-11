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
import shlex
import tomllib
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
    "assets/badges/OVERLAY-MANIFEST.sha256",
    "assets/badges/languages.json",
    "assets/collections/content_rating_cs.json",
    ".forgejo/workflows/ci.yml",
    ".forgejo/scripts/wait_for_postgres.py",
    # Runs on GitHub Actions rather than here, but tests/test_release_workflow.py
    # and tests/test_version_stamp.py both read it, so a change to it must still
    # run this suite -- the same reason the Dockerfile is on this list.
    ".github/workflows/release.yml",
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


def _jobs() -> dict[str, dict]:
    """Every job in the workflow, by id."""
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")).get("jobs") or {}


def _needs(job: dict) -> list[str]:
    """A job's ``needs``, which YAML allows as either a string or a list."""
    declared = job.get("needs") or []
    return [declared] if isinstance(declared, str) else list(declared)


def _named_step(name: str) -> dict:
    """The one step called ``name``, across all jobs."""
    matches = [step for step in _steps() if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step, found {len(matches)}"
    return matches[0]


def _steps() -> list[dict]:
    """Every step in the workflow, across all jobs."""
    return [
        step
        for job in _jobs().values()
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


def test_the_virtualenv_cache_is_keyed_on_what_decides_its_contents():
    """A venv restored on too loose a key tests packages this commit does not declare.

    The job skips ``pip install -e ".[dev]"`` outright when this cache hits, so
    whatever the key does not cover is something CI has become unable to notice
    changing. pyproject.toml holds both the dependency list and the dev extras,
    and the interpreter patch decides which wheels resolve for them, so the key
    names both. Drop the hash and a dependency bump runs against the previous
    run's packages with a green tick over it; add a ``restore-keys:`` and a
    near-miss does the same thing deliberately.

    The key's third component is the ISO week, and it is asserted for the
    opposite reason to the other two: every dependency in pyproject.toml is a
    ``>=`` floor with no lockfile, so without a time component the first save
    would freeze that resolution for as long as nobody edited the file, and CI
    would stop tracking upstream while looking exactly like this.

    The skip itself is asserted too. A cache nothing is conditioned on is a
    download that saves nothing, which would look exactly like this working.

    And the *save* is asserted to come after the step that verifies the venv
    imports. ``actions/cache``'s combined form saves in a post step that runs
    even when the job failed, so a part-built venv from a died-mid-install run
    used to be stored under this immutable key and restored, broken, forever
    after.
    """
    caches = [step for step in _steps() if step.get("id") == "venv"]
    assert len(caches) == 1, (
        "expected exactly one step with `id: venv` in .forgejo/workflows/ci.yml, "
        f"found {len(caches)}; this test and that step's `if:` consumers name it"
    )
    cache = caches[0]
    assert str(cache.get("uses", "")).startswith("actions/cache"), (
        f"the `venv` step is {cache.get('uses')!r}, not an actions/cache step"
    )

    with_block = cache.get("with") or {}
    key = str(with_block.get("key", ""))
    assert "hashFiles('pyproject.toml')" in key, (
        f"the venv cache key is {key!r}, which does not hash pyproject.toml. A "
        "dependency change would then restore the previous run's venv and the "
        "suite would pass against packages this commit does not declare"
    )
    assert "PYTHON_VERSION" in key, (
        f"the venv cache key is {key!r} and does not name the interpreter "
        "version; a Python bump would restore a venv built for the old one"
    )
    assert "restore-keys" not in with_block, (
        "the venv cache declares restore-keys, so a miss falls back to a venv "
        "built for a different dependency set instead of reinstalling"
    )

    bucket = re.search(r"steps\.(\w+)\.outputs\.\w+", key)
    assert bucket, (
        f"the venv cache key is {key!r}, which has no computed component. "
        "pyproject.toml's dependencies are all `>=` floors with no lockfile, so "
        "a key made only of declarations freezes whatever resolved on the day "
        "it was first saved -- cache entries are immutable and a hit is never "
        "rewritten. A coarse time bucket is what keeps CI tracking upstream"
    )
    producer = [step for step in _steps() if step.get("id") == bucket.group(1)]
    assert len(producer) == 1, (
        f"the venv cache key reads steps.{bucket.group(1)}.outputs, but "
        f"{len(producer)} steps carry `id: {bucket.group(1)}`; the key would "
        "then interpolate to nothing and collapse to a constant"
    )

    conditioned = [
        step
        for step in _steps()
        if "steps.venv.outputs.cache-hit" in str(step.get("if", ""))
    ]
    assert conditioned, (
        "nothing in the workflow is conditioned on steps.venv.outputs.cache-hit, "
        "so the install runs whether the cache hit or not and the cache buys "
        "nothing but a download"
    )

    test_steps = _jobs()["test"]["steps"]
    names = [str(step.get("name", "")) for step in test_steps]
    saves = [
        index
        for index, step in enumerate(test_steps)
        if str(step.get("uses", "")).startswith("actions/cache/save@")
    ]
    assert len(saves) == 1, (
        f"expected exactly one actions/cache/save step in the `test` job, found "
        f"{len(saves)}. Without a separate save the venv is stored by the cache "
        "action's post step, which runs on a failed job too -- so an install "
        "that died partway is written under this immutable key and every later "
        "run restores it broken"
    )
    verifies = [
        index
        for index, name in enumerate(names)
        if name == "Verify the virtualenv answers for this checkout"
    ]
    assert len(verifies) == 1, (
        "expected exactly one 'Verify the virtualenv answers for this checkout' "
        f"step in the `test` job, found {len(verifies)}. The save below is "
        "ordered against it by name, so a rename leaves nothing holding the "
        "save after the proof -- rename this test's expectation with it"
    )
    verify = verifies[0]
    assert saves[0] > verify, (
        f"the virtualenv is saved at step {saves[0]}, before the verification "
        f"at step {verify}. Only a venv that has been proven to import "
        "`autoposter` may be stored, because the key it is stored under can "
        "never be overwritten"
    )


def test_the_test_step_defers_the_deep_lane_only_on_pull_requests():
    """The lane split must never reach the run that publishes.

    A pull request deselects ``deep`` as well as ``imagemagick`` -- 525 of the
    4,184 tests, every ASGI-plus-database suite in tests/conftest.py's
    DEEP_SUITES bar the six that carry ``imagemagick`` too and run in their
    own step on every event. That is only safe while the other lane runs them
    *and* gates the push, which
    ``test_the_image_job_cannot_publish_past_a_failing_suite``
    below is the other half of. Narrow the non-pull-request selection too and
    CI would push an image whose integration suites nobody ran -- with every
    tick green, because the tests were deselected rather than failed.

    Asserted against the step's script rather than its name, so renaming the
    step is fine and quietly changing what it selects is not.
    """
    script = _named_step("Test")["run"]
    selections = re.findall(r'markers="([^"]*)"', script)
    assert len(selections) == 2, (
        f"the Test step assigns {len(selections)} marker selections ({selections}); "
        "this test expects the two lanes to be written out as literals so they "
        "can be read here"
    )
    assert all("not imagemagick" in selection for selection in selections), (
        f"a lane does not deselect the ImageMagick-gated tests: {selections}. "
        "They fail rather than skip under CI, so that lane would go red for a "
        "reason that is not a defect"
    )

    fast = 'markers="not imagemagick and not deep"'
    full = 'markers="not imagemagick"'
    assert fast in script and full in script, (
        f"the Test step's lanes are {selections}; expected one selection that "
        "deselects `deep` and one that does not"
    )
    assert "pull_request" in script[: script.index(fast)], (
        "the lane that deselects `deep` is not the pull-request branch, so "
        "either pull requests run everything or main runs less than everything"
    )
    assert "else" in script[script.index(fast) : script.index(full)], (
        "the two lanes are not the two branches of one conditional; read the "
        f"script again before trusting this test: {script!r}"
    )

    assert re.search(r"-n\s+(auto|\d+)", script), (
        "the Test step no longer passes -n, so the suite runs in one process "
        "again; tests/conftest.py's per-worker databases exist for this"
    )
    assert "--dist loadgroup" in script, (
        "the Test step dropped --dist loadgroup, so an `xdist_group` marker "
        "would be silently ignored rather than keeping its tests together"
    )


def test_the_image_job_cannot_publish_past_a_failing_suite():
    """``needs:`` cannot be conditional, so the ordering is expressed as gates.

    A pull request publishes nothing, so the image job may build and verify in
    parallel with the suite; a push to main may not, because the last step of
    that job pushes to Harbor. The shape is two gate jobs, exactly one of which
    can succeed per event, with the image job requiring one of them to have
    **succeeded**.

    ``== 'success'`` and not ``!= 'failure'`` is the whole of the safety here,
    and it is the assertion most worth keeping: a job whose ``needs`` failed is
    *skipped*, not failed, so a gate waiting on a red suite reports ``skipped``
    -- indistinguishable, to the weaker test, from the gate that a pull request
    skips on purpose. Written the loose way, a failing suite on main would have
    published.
    """
    jobs = _jobs()
    image = jobs["image"]
    gates = _needs(image)
    assert gates, (
        "the image job declares no `needs`, so nothing orders it after the "
        "suite on any event and a red main run would still push"
    )

    condition = str(image.get("if", ""))
    waits_for_the_suite = []
    for gate in gates:
        assert gate in jobs, f"the image job needs {gate!r}, which is not a job"
        assert f"needs.{gate}.result == 'success'" in condition, (
            f"the image job needs {gate!r} but its `if` does not require that "
            f"gate to have succeeded: {condition!r}. A gate whose own `needs` "
            "failed reports 'skipped', so anything weaker than == 'success' "
            "lets a failing suite through"
        )
        assert "-" not in gate, (
            f"the gate job id {gate!r} contains a hyphen; `needs.{gate}.result` "
            "parses as a subtraction in a workflow expression and evaluates to "
            "nothing, which fails open"
        )
        if "test" in _needs(jobs[gate]):
            waits_for_the_suite.append(gate)
        else:
            gate_if = str(jobs[gate].get("if", ""))
            assert re.search(r"==\s*'pull_request'", gate_if), (
                f"the gate {gate!r} does not wait for the `test` job and is not "
                f"restricted to pull requests ({gate_if!r}); it would open the "
                "image job on main with the suite unfinished"
            )

    assert waits_for_the_suite, (
        f"none of the image job's gates ({gates}) waits for the `test` job, so "
        "no event orders the image build after the suite"
    )

    pushes = [
        step
        for step in (image.get("steps") or [])
        if "docker push" in str(step.get("run", ""))
    ]
    assert pushes, "the image job pushes nothing; this test is then meaningless"
    for step in pushes:
        step_if = str(step.get("if", ""))
        assert "refs/heads/main" in step_if and "push" in step_if, (
            f"a step running `docker push` is conditioned on {step_if!r}, which "
            "does not restrict it to a push to main; a pull request's image "
            "job now runs without waiting for the suite, so this is what keeps "
            "an unverified image out of the registry"
        )
        # Belt and braces over the job-level `if`. The event conditions above
        # are true on a red main run too, so without this the whole protection
        # is one compound `!cancelled() && (... || ...)` expression evaluated by a
        # runner that is not GitHub's.
        assert any(
            f"needs.{gate}.result == 'success'" in step_if for gate in waits_for_the_suite
        ), (
            f"a step running `docker push` is conditioned on {step_if!r}, which "
            f"does not restate that one of {waits_for_the_suite} succeeded. The "
            "event conditions alone are satisfied by a failing run on main"
        )


def test_the_build_tag_conforms_to_the_flux_image_policys_regex():
    """The cluster's Flux ImagePolicy can only order tags shaped
    ``build-<epoch>-sha-<hex>``; a mismatch is silent -- the policy simply
    never selects an image, with no error anywhere. This full-matches a
    constructed example tag against the policy's own pattern, quoted here
    verbatim from the workflow's comment rather than retyped, so the two
    cannot drift apart unnoticed.
    """
    push_script = _named_step("Push the verified image")["run"]

    match = re.search(r'BUILD_TAG="([^"]*)"', push_script)
    assert match, (
        "the Push step no longer assigns BUILD_TAG; the Flux ImagePolicy has "
        "nothing left to order releases by"
    )
    assert match.group(1) == "build-$(date +%s)-${TAG}", (
        f"BUILD_TAG is now built as {match.group(1)!r}; the ImagePolicy's "
        "regex assumes exactly `build-<epoch>-sha-<hex>`"
    )

    # The exact, fully-anchored pattern the Flux ImagePolicy matches against.
    # Read out of the workflow's own comment rather than typed twice, so a
    # comment that drifts from the real policy fails here instead of staying
    # silently wrong.
    flux_image_policy_pattern = r"^build-(?P<ts>\d+)-sha-[a-f0-9]+$"
    assert flux_image_policy_pattern in push_script, (
        "the Push step's comment no longer quotes the Flux ImagePolicy's "
        "pattern verbatim; a reader has no way to confirm the tag shape "
        "without going and finding the policy"
    )

    gen_tag_script = _named_step("Generate image tag")["run"]

    # An allowlist of the whole SHORT_SHA pipeline, not a blacklist of ways to
    # break it: pinning this exact one-liner forecloses every transformation
    # between github.sha and SHORT_SHA at once -- `tr 'a-z' 'A-Z'`,
    # `${SHORT_SHA^^}`, `awk 'toupper'`, or anything else -- rather than
    # naming each uppercasing idiom in a negative regex and missing one.
    assert 'SHORT_SHA=$(echo "${{ github.sha }}" | cut -c1-7)' in gen_tag_script, (
        "SHORT_SHA is no longer built as "
        '`echo "${{ github.sha }}" | cut -c1-7` with nothing else in between; '
        "a git sha is lowercase hex by construction, but an inserted "
        "transformation might not preserve that, and the ImagePolicy's "
        "`[a-f0-9]+` is lowercase-only"
    )

    # The `tag` output's construction, extracted rather than retyped, so the
    # `sha-` prefix -- half of what the ImagePolicy matches on -- is pinned to
    # what the step actually emits. A rename to, say, `git-` would otherwise
    # leave this test's own hardcoded example green while the real pipeline
    # silently stopped matching the policy.
    tag_match = re.search(r'echo "tag=([^"]*)"', gen_tag_script)
    assert tag_match, (
        "the Generate image tag step no longer publishes a `tag` output via "
        '`echo "tag=..."`, which this test expects to extract from'
    )
    example_tag = tag_match.group(1).replace("${SHORT_SHA}", "4b2a34d")

    # Construct an example BUILD_TAG exactly as the two steps above do, from
    # the extracted `tag` expression plus a sample epoch and a sample
    # git-sha-shaped (lowercase hex) short sha, and full-match it.
    example_epoch = "1700000000"  # `date +%s`
    build_tag = f"build-{example_epoch}-{example_tag}"
    assert re.fullmatch(flux_image_policy_pattern, build_tag), (
        f"the constructed example {build_tag!r} does not full-match the Flux "
        f"ImagePolicy's pattern {flux_image_policy_pattern!r}"
    )


def test_the_deep_lane_is_declared_and_never_deselected_by_default():
    """``-m`` in addopts would narrow every lane at once, CI's included.

    The marker is applied by tests/conftest.py's ``pytest_collection_modifyitems``
    rather than written in the 22 files, so this is where the declaration is
    checked to still exist and still name files that do. A default ``-m`` in
    addopts is the one way the *main* lane could quietly stop running the deep
    suites while the workflow still asked for them.
    """
    ini = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    options = ini["tool"]["pytest"]["ini_options"]

    markers = options.get("markers") or []
    assert any(marker.startswith("deep:") for marker in markers), (
        f"pyproject.toml registers {markers}, with no `deep:` among them; the "
        "workflow's `-m \"... and not deep\"` would then be selecting on a "
        "marker nothing declares"
    )
    # Tokenised rather than searched for as a substring: `--maxfail=1` contains
    # `-m`, so the loose form would one day reject a perfectly legitimate
    # addopts change with a message about lane narrowing.
    assert "-m" not in shlex.split(str(options.get("addopts", ""))), (
        "pytest's addopts carries a `-m`, which every lane inherits -- "
        "including the main-branch run that is the reason deferring the deep "
        "suites on pull requests is safe"
    )

    from conftest import DEEP_SUITES

    assert DEEP_SUITES, "tests/conftest.py's DEEP_SUITES is empty; there is no deep lane"
    missing = sorted(name for name in DEEP_SUITES if not (REPO / "tests" / name).is_file())
    assert not missing, (
        f"DEEP_SUITES names files that do not exist: {missing}. They are matched "
        "on filename, so a renamed suite silently rejoins the fast lane"
    )


def test_every_api_suite_has_been_assigned_a_lane():
    """A new ``test_api_*.py`` may not pick its lane by being forgotten.

    DEEP_SUITES is checked above for naming files that exist; this is the
    other direction, and it is the one that erodes the thing the split was
    built for. Every one of these suites builds the ASGI application against a
    real database, which is the whole definition of the deep lane, so a new one
    landing in the fast lane by omission costs pull requests time with no
    signal anywhere that it happened -- nothing goes untested, the number just
    quietly stops being true.

    This does not decide the lane. It requires that somebody did, in one list
    or the other, on the pull request that adds the file.
    """
    from conftest import DEEP_SUITES, FAST_API_SUITES

    assigned = DEEP_SUITES | FAST_API_SUITES
    unassigned = sorted(
        path.name for path in (REPO / "tests").glob("test_api_*.py") if path.name not in assigned
    )
    assert not unassigned, (
        f"tests/test_api_*.py suites belong to no lane: {unassigned}. Add each "
        "to tests/conftest.py's DEEP_SUITES (it builds the ASGI app against a "
        "real database, like the other 22) or to FAST_API_SUITES (it is cheap "
        "enough to run on every pull request), but say which"
    )

    phantom = sorted(name for name in FAST_API_SUITES if not (REPO / "tests" / name).is_file())
    assert not phantom, (
        f"FAST_API_SUITES names files that do not exist: {phantom}; a renamed "
        "suite would then be exempted from the check above by a stale entry"
    )
    overlap = sorted(DEEP_SUITES & FAST_API_SUITES)
    assert not overlap, (
        f"{overlap} are in both DEEP_SUITES and FAST_API_SUITES. DEEP_SUITES "
        "wins -- the marker is applied from it -- so the fast entry is a claim "
        "about the lane that is not true"
    )


@pytest.mark.parametrize(
    "path",
    [
        "docs/design/2026-08-20-autoposter-design.md",
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
