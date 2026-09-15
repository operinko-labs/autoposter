"""The development environment must not be able to become the shipped one.

Every guard here exists because its failure is silent. A development stage that
becomes the default build target still passes every check in the image job --
it contains a superset of the runtime image -- and would be pushed to Harbor as
production. A vite proxy pointed at a port nothing serves looks like a working
configuration file right up until someone runs the dev server, which is how it
survived in this repository until 2026-08-21.
"""

import json
import re
from pathlib import Path

import yaml

from autoposter import main

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
COMPOSE = REPO / "docker-compose.yml"
VITE_CONFIG = REPO / "frontend" / "vite.config.ts"


def _stages() -> list[str]:
    """Every stage name, in file order; unnamed stages appear as ``""``."""
    stages = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"FROM\s+\S+(?:\s+AS\s+(\S+))?\s*$", line)
        if match:
            stages.append(match.group(1) or "")
    return stages


def test_the_runtime_stage_is_the_last_one():
    """``docker build .`` targets the last stage and CI passes no ``target:``.

    So a development stage placed after ``runtime`` becomes what
    .forgejo/workflows/ci.yml builds, verifies and pushes -- and every existing
    check in that job would still pass, because the development image contains
    everything the runtime one does plus pytest and ruff. The image would ship
    with the test toolchain in it and nothing would have complained.
    """
    stages = _stages()
    assert stages, "no FROM lines found in the Dockerfile"
    assert stages[-1] == "runtime", (
        f"the last stage in the Dockerfile is {stages[-1]!r}, not 'runtime'; "
        "`docker build .` builds the last stage and the image job passes no "
        "`target:`, so this is what would be pushed to Harbor as production"
    )


def _stage_lines(name: str) -> list[str]:
    """The Dockerfile lines belonging to the stage named ``name``, in order."""
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        match = re.match(r"FROM\s+\S+\s+AS\s+(\S+)\s*$", line)
        if start is None and match and match.group(1) == name:
            start = i + 1
            continue
        if start is not None and re.match(r"FROM\s+\S+", line):
            return lines[start:i]
    assert start is not None, f"no stage named {name!r} in the Dockerfile"
    return lines[start:]


def test_the_webdev_entrypoint_installs_and_forwards_the_command():
    """The lazy install must survive a ``command:`` override, not just a bare ``up``.

    docker-compose.yml's `web` service mounts a named volume at
    /frontend/node_modules, and README documents `docker compose run --rm web
    npm run build` / `npm test` -- both of which replace the service's
    `command:` outright. Only an ENTRYPOINT in *exec* (JSON array) form still
    runs ahead of a replaced command; a shell-form ENTRYPOINT would ignore
    whatever docker compose run passes, and no ENTRYPOINT at all is exactly
    the regression c0e7589 fixed (`sh: tsc: not found` / `sh: vitest: not
    found` against a fresh clone's empty volume). This does not run Docker: it
    proves the Dockerfile *declares* the install-then-exec structure, not that
    `npm ci` actually succeeds inside a real container.
    """
    text = "\n".join(_stage_lines("webdev"))

    entrypoint_match = re.search(r"^ENTRYPOINT\s+(\[.*\])\s*$", text, re.MULTILINE)
    assert entrypoint_match, (
        "the webdev stage declares no ENTRYPOINT; without one, `docker compose "
        "run --rm web <cmd>` replaces `command:` outright and never installs "
        "into the empty named volume"
    )
    entrypoint_argv = json.loads(entrypoint_match.group(1))
    assert isinstance(entrypoint_argv, list) and entrypoint_argv, (
        f"ENTRYPOINT {entrypoint_match.group(1)!r} must be exec (JSON array) "
        "form; shell form ignores the command docker compose run passes, so "
        "the install would run but the user's command never would"
    )
    script_path = entrypoint_argv[0]

    # The script's actual content, as written into the image by the RUN that
    # creates it -- not just that some RUN mentions the path.
    write_match = re.search(
        r"RUN printf .*?> " + re.escape(script_path), text, re.DOTALL
    )
    assert write_match, (
        f"found no `RUN printf ... > {script_path}` writing the script "
        "ENTRYPOINT points at, so its contents can't be verified"
    )
    script = "\n".join(re.findall(r"'([^']*)'", write_match.group(0)))

    assert re.search(r"^stamp=node_modules/\.package-lock\.sha256$", script, re.MULTILINE), (
        f"the entrypoint script ({script!r}) does not define a stamp file "
        "under node_modules recording the digest of the lockfile last "
        "installed from -- without it there is nothing to compare a fresh "
        "package-lock.json against"
    )
    assert re.search(r"sha256sum\s+package-lock\.json", script), (
        f"the entrypoint script ({script!r}) does not hash package-lock.json, "
        "so `npm ci` can't be keyed on whether the lockfile changed since the "
        "volume was last installed into"
    )
    assert re.search(
        r'if\s*\[\s*"\$\(cat\s+"\$stamp"[^]]*\)"\s*!=\s*"\$digest"\s*\]\s*;\s*then',
        script,
    ), (
        f"the entrypoint script ({script!r}) does not guard `npm ci` behind a "
        "comparison of the stamp against the freshly computed digest -- either "
        "the lazy install is gone, a stale volume whose lockfile changed would "
        "never reinstall (the bug this guards against: a volume seeded before "
        "a dependency was added never picks it up), or it now reinstalls "
        "unconditionally on every `up`"
    )
    guarded_block = re.search(r"if\b.*?\bfi\b", script, re.DOTALL)
    assert guarded_block and re.search(r"\bnpm ci\b", guarded_block.group(0)), (
        f"the entrypoint script ({script!r}) does not run `npm ci` inside the "
        "stamp-mismatch branch"
    )
    assert re.search(
        r'npm ci\s*\n\s*printf\s+"%s\\n"\s+"\$digest"\s*>\s*"\$stamp"', script
    ), (
        f"the entrypoint script ({script!r}) does not write the stamp "
        "immediately after `npm ci` -- writing it anywhere else risks "
        "stamping a lockfile digest whose install never happened, or never "
        "happened successfully (the script has `set -e`, so a failed `npm "
        "ci` must abort before the stamp line runs)"
    )
    last_line = script.strip().splitlines()[-1].strip()
    assert last_line == 'exec "$@"', (
        f"the entrypoint script's last line is {last_line!r}, not `exec "
        '"$@"`; without that, the command docker compose run/up passes would '
        "never actually execute"
    )


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _service(name: str) -> dict:
    services = _compose().get("services") or {}
    assert name in services, f"docker-compose.yml declares no {name!r} service"
    return services[name]


def test_development_services_build_from_the_dockerfile():
    """An ``image:`` tag here would be a second declaration of a pinned version.

    The whole point of the ``webdev`` and ``dev`` stages is that Node and
    Python are named once, in the Dockerfile, where
    tests/test_toolchain_versions.py already holds every stage to the same
    patch. A service naming an image directly escapes that entirely.
    """
    for name in ("test", "web", "api"):
        service = _service(name)
        assert "image" not in service, (
            f"the {name!r} service names an `image:` directly, which declares a "
            "version nothing holds to the Dockerfile's; build from a stage"
        )
        build = service.get("build")
        assert isinstance(build, dict) and build.get("target"), (
            f"the {name!r} service must build from a named Dockerfile stage"
        )


def test_the_test_service_is_behind_a_profile():
    """A bare ``docker compose up`` (or ``up -d``) must start the app, not the suite.

    The ``test`` service shares ``postgres`` with ``api``, and the suite
    truncates tables -- run alongside a live ``api`` it fights the running
    app. Compose enables a service's profiles automatically when the service
    is named on the command line, so ``docker compose run test ...`` and
    ``docker compose up test`` still work unchanged; only the profile-less
    default start excludes it.
    """
    service = _service("test")
    assert service.get("profiles") == ["test"], (
        f"the test service's profiles are {service.get('profiles')!r}, not "
        "['test']; without that, a bare `docker compose up` starts the suite "
        "against the same postgres the api service uses"
    )


def test_the_test_service_needs_no_secrets():
    """Running the suite must need nothing but Docker.

    ``api`` legitimately requires credentials, as production does. If that
    requirement leaks into ``test``, the claim that a fresh clone can run the
    suite stops being true, and nobody finds out until a new machine tries.
    """
    service = _service("test")
    assert "env_file" not in service, (
        "the test service reads an env file, so running the suite now depends "
        "on credentials a fresh clone does not have"
    )
    database = (service.get("environment") or {}).get("AUTOPOSTER_TEST_DATABASE_URL", "")
    assert "@postgres:5432/" in database, (
        f"the test service points at {database!r}; inside the compose network "
        "the database is postgres:5432, not the host's published 5433"
    )
    maintenance = (service.get("environment") or {}).get(
        "AUTOPOSTER_MAINTENANCE_DATABASE_URL", ""
    )
    assert "@postgres:5432/" in maintenance, (
        f"the test service points its maintenance connection at {maintenance!r}; "
        "inside the compose network the database is postgres:5432, not the "
        "host's published 5433 -- without this, tests/test_migrations.py's two "
        "migration guards (which catch multiple alembic heads) cannot run"
    )


def _uvicorn_port() -> int:
    """The port ``main()`` binds when nothing in the environment says otherwise.

    The value rather than the call site: the port is read from the environment
    now (``AUTOPOSTER_HOST``/``AUTOPOSTER_PORT``, so a restarted process comes
    back where the operator reached it), and ``uvicorn.run(..., port=port)``
    holds no digit to parse. The default is what the dev stack runs on, which
    is the fact this file is about.
    """
    return main.DEFAULT_LISTEN_PORT


def test_the_vite_proxy_targets_the_port_the_app_binds():
    """This drifted once already and nothing noticed.

    vite.config.ts proxied to :8000 while main.py bound :8080, so the
    documented ``npm run dev`` hot-reload path could not have worked -- the
    config file looked entirely reasonable, and the mismatch surfaced only by
    running it. Two files naming one port is a fact worth asserting.
    """
    targets = re.findall(r'"(https?://[^"]+)"', VITE_CONFIG.read_text(encoding="utf-8"))
    assert targets, "vite.config.ts declares no proxy targets"
    port = _uvicorn_port()
    for target in targets:
        host, _, declared = target.rpartition(":")
        assert declared.isdigit() and int(declared) == port, (
            f"vite proxies to {target} while main.py binds port {port}; the dev "
            "server would forward /api to a port nothing is listening on"
        )
        assert host.endswith("//api"), (
            f"vite proxies to {target}; inside the compose network the API is "
            "reachable as the service name `api`, not on localhost"
        )


def test_the_api_service_fails_closed_without_credentials():
    """``Secrets.from_env()`` raises on any of six missing variables.

    Compose declaring the env file means the failure arrives as "no .env" at
    start-up rather than as a traceback later, and it keeps the development
    stack honest about the same thing production is: no credentials, no
    service.
    """
    env_file = _service("api").get("env_file")
    declared = env_file if isinstance(env_file, list) else [env_file]
    paths = [e if isinstance(e, str) else (e or {}).get("path") for e in declared]
    assert ".env" in paths, (
        "the api service does not declare `env_file: .env`, so it would start "
        "without credentials and fail later inside Secrets.from_env()"
    )


def test_the_env_example_leaves_the_listen_address_commented_out():
    """A listen address set for this stack is a trap, not a setting.

    The api service's compose command pins ``uvicorn --host 0.0.0.0 --port
    8080``, so AUTOPOSTER_PORT does nothing for as long as the process lives --
    until the Settings page's Restart execs a bare boot, which reads it and
    binds wherever it says while the stack still publishes 8080. Shipping the
    two names uncommented made that the default experience, so they ship
    commented and compose sets neither itself.
    """
    lines = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    environment = _service("api").get("environment") or {}
    for name in ("AUTOPOSTER_HOST", "AUTOPOSTER_PORT"):
        assert [line for line in lines if line.strip().startswith(f"{name}=")] == [], (
            f"`.env.example` sets {name} and compose passes that file into the "
            "api service; the dev command pins --port 8080, so the value takes "
            "effect only at the first Restart, after which the process binds a "
            "port the stack does not publish"
        )
        assert any(name in line for line in lines), (
            f"{name} is no longer mentioned in `.env.example`; the commented "
            "block is what tells an operator the variable exists at all"
        )
        assert name not in environment, (
            f"the api service sets {name} itself, which would override the "
            "pinned --port in its own command at the next boot"
        )


def test_the_api_service_boots_the_same_way_production_does():
    """The production CMD is ``exec python -m autoposter.boot``, and that module
    is what runs ``alembic upgrade head``.

    A development stack that skips the migration step is one where "works on
    my machine" can mean "against a schema main does not have" -- and now that
    the migration lives behind a decision, a development stack that skips the
    DECISION is one where setup mode is never exercised at all.
    """
    command = _service("api").get("command") or ""
    assert "python -m autoposter.boot" in command, (
        "the api service does not boot through autoposter.boot, while the "
        "image's CMD does; the two would drift on the one line that decides "
        "whether migrations run at all"
    )
