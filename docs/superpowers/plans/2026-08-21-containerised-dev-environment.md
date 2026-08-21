# Containerised Development Environment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a working Docker installation the only thing a developer needs
to run the suite, build the frontend, and run the app with hot reload.

**Architecture:** Development stages are added to the existing `Dockerfile`
(never after `runtime`, which must stay last) and driven by services added to
the existing `docker-compose.yml`. No devcontainer, no wrapper scripts. The
host working tree stays the source of truth and is bind-mounted in.

**Tech Stack:** Docker Compose v2, BuildKit multi-stage builds, PostgreSQL
18.1, Python 3.14.7 (Alpine, Q16-HDRI ImageMagick), Node 26.7.0.

**Spec:** `docs/superpowers/specs/2026-08-21-containerised-dev-environment-design.md`

## Global Constraints

- **Never `docker compose down -v`.** The developer PostgreSQL holds data in an
  anonymous volume. Use `docker compose down` or `stop` only.
- **Nothing may change the `postgres` service definition.** `git diff` on it
  must stay empty; a changed definition forces a recreate.
- **PostgreSQL stays published on host `localhost:5433`.** Containers reach it
  at `postgres:5432`.
- **Run tests as `rtk proxy python -m pytest ... -v`.** A bare
  `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`.** Signing times out here.
- **No `Co-Authored-By` trailers and no AI attribution** in commit messages.
- **`runtime` must remain the final stage in the `Dockerfile`.** CI builds with
  no `target:`, so the last stage is what is pushed to Harbor.
- **Exact version pins only.** Every `FROM <image>:` line names the same exact
  patch: `node:26.7.0-alpine`, `python:3.14.7-alpine`.
- **Concurrent agent sessions share this working tree.** `Dockerfile` and
  `docker-compose.yml` are shared root files; re-check them with `git diff`
  immediately before each commit, and re-run the affected test if they moved.

## Baseline measurements

Recorded on the host before any change, so the acceptance criteria are
falsifiable rather than aspirational:

- `magick` is **not installed** on the host.
- Host Python is **3.13.7**, while `pyproject.toml` requires `>=3.14,<3.15`.
- `rtk proxy python -m pytest tests/test_golden.py tests/test_pipeline_e2e.py
  tests/test_badge_parity.py -q -rs` → **13 passed, 5 skipped**. The 5 are
  `test_golden.py:94`, `test_golden.py:144`, `test_pipeline_e2e.py:70`,
  `test_pipeline_e2e.py:105`, `test_pipeline_e2e.py:131`.

Task 2 turns those 5 skips into passes. Nothing else in the suite changes.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `Dockerfile` | Modify: split runtime prelude into `pybase`; add `webdev` and `dev`; name and re-anchor `runtime` as last | 1 |
| `tests/test_dev_environment.py` | Create: guards for stage order, compose wiring, vite proxy target | 1, 3, 4 |
| `tests/test_toolchain_versions.py` | Modify: `_dockerfile_version` generalised to "all `FROM` lines agree" | 1 |
| `docker-compose.yml` | Modify: add `test`, `web`, `api` services and the `node_modules` volume | 2, 3, 4 |
| `frontend/vite.config.ts` | Modify: proxy target `localhost:8000` → `api:8080` | 3 |
| `.env.example` | Modify: annotate the DB URL as overridden in development | 4 |
| `README.md` | Modify: Development section becomes compose commands | 5 |

---

### Task 1: Dockerfile stages, with the ordering guarded

**Files:**
- Modify: `Dockerfile`
- Create: `tests/test_dev_environment.py`
- Modify: `tests/test_toolchain_versions.py` (the `_dockerfile_version` helper)

**Interfaces:**
- Produces: build targets `webdev`, `dev`, `pybase`, `runtime`. Tasks 2-4 use
  `target: dev` and `target: webdev` in `docker-compose.yml`.
- Produces: `tests/test_dev_environment.py` with module constants
  `REPO`, `DOCKERFILE`, `COMPOSE` reused by Tasks 3 and 4.

- [ ] **Step 1: Write the failing guard for stage order**

Create `tests/test_dev_environment.py`:

```python
"""The development environment must not be able to become the shipped one.

Every guard here exists because its failure is silent. A development stage
that becomes the default build target still passes every check in the image
job -- it contains a superset of the runtime image -- and would be pushed to
Harbor as production. A vite proxy pointed at a port nothing serves looks like
a working configuration file right up until someone runs the dev server.
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
COMPOSE = REPO / "docker-compose.yml"
VITE_CONFIG = REPO / "frontend" / "vite.config.ts"
MAIN = REPO / "src" / "autoposter" / "main.py"


def _stages() -> list[str]:
    """Every stage name, in file order; unnamed stages appear as ``""``."""
    stages = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"FROM\s+\S+(?:\s+AS\s+(\S+))?\s*$", line)
        if match:
            stages.append(match.group(1) or "")
    return stages


def test_the_runtime_stage_is_the_last_one():
    """`docker build .` targets the last stage and CI passes no `target:`.

    So a development stage placed after `runtime` becomes what
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
        "`target:', so this is what would be pushed to Harbor as production"
    )
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `rtk proxy python -m pytest tests/test_dev_environment.py -v`
Expected: FAIL — the final stage is currently unnamed, so `stages[-1]` is `""`.

- [ ] **Step 3: Generalise `_dockerfile_version` to admit repeated stages**

In `tests/test_toolchain_versions.py`, replace the whole `_dockerfile_version`
function with:

```python
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
```

Also update the module docstring's file list, replacing the `Dockerfile` bullet
with:

```
- ``Dockerfile`` -- the Python and Node that actually ship, across every stage.
```

- [ ] **Step 4: Restructure the Dockerfile**

Replace lines 1-24 of `Dockerfile` (the `frontend` stage through
`RUN npm run build`) and the `FROM python:3.14.7-alpine` line, so the file
begins:

```dockerfile
# The web UI is built here rather than committed, so the image always serves a
# bundle built from the source in this commit. The tag names an exact patch,
# not a floating major: frontend/package.json's `engines` and
# .forgejo/workflows/ci.yml's NODE_VERSION name that same patch, so the bundle
# CI typechecks is built by the same Node as the bundle that ships.
# tests/test_toolchain_versions.py fails if those three ever disagree.
FROM node:26.7.0-alpine AS frontend
WORKDIR /frontend
# The manifests alone first: a change to src/ then reuses this layer instead of
# re-installing every dependency. .npmrc belongs in this copy rather than the
# one below it: npm reads the .npmrc in the directory it installs from, so
# arriving with `COPY frontend/ ./` after `npm ci` would be too late, and the
# engine-strict rule would hold everywhere except the one build that ships.
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
# `npm ci`, never `npm install`. It installs exactly what package-lock.json
# pins and fails outright on a mismatch, where `npm install` would quietly
# resolve something newer and rewrite the lockfile -- the skew that broke this
# repository three times over. .npmrc's engine-strict makes engines.node just
# as absolute: a base image on any other patch fails here instead of warning.
RUN npm ci
COPY frontend/ ./
# `npm run build` is `tsc --noEmit && vite build`, so a type error fails the
# image build rather than shipping a bundle nothing typechecked.
RUN npm run build

# The vite dev server for `docker compose up web`. Deliberately bare: both the
# frontend/ tree and node_modules arrive at run time, one as a bind mount and
# one as a named volume, so anything copied in here would only be shadowed.
# It exists so that the Node version stays declared in this file alone, rather
# than being repeated as an `image:` tag in docker-compose.yml where nothing
# would hold it to the same patch.
FROM node:26.7.0-alpine AS webdev
WORKDIR /frontend

# Alpine, because its ImageMagick is built Q16-HDRI — the same configuration the
# Posterizarr deployment this service replaces runs (7.1.2-29 Q16-HDRI). Debian's
# package is Q16 without HDRI, which changes internal pixel maths: the same render
# came out 0.077% different (RMSE 0.00077) from the production asset, where the
# HDRI build reproduces it byte-for-byte. HDRI also brings float-format support
# (.exr/.hdr) for hand-supplied artwork.
#
# The patch is pinned, and .forgejo/workflows/ci.yml's PYTHON_VERSION and
# pyproject.toml's requires-python are held to it, so the suite is proven on
# the interpreter that actually ships rather than a neighbouring one.
#
# Split out as its own stage so `dev` inherits exactly this ImageMagick rather
# than approximating it. tests/test_golden.py and tests/test_pipeline_e2e.py
# skip without a Q16-HDRI build, which is every development machine here; from
# `dev` they run against the same build production uses.
FROM python:3.14.7-alpine AS pybase

# imagemagick's format support is split into subpackages on Alpine. jpeg is not
# optional here — every asset this service writes is a .jpg. svg is needed because
# clearlogos are sometimes SVG, and the compositor passes -density 300 for them.
RUN apk add --no-cache \
      imagemagick \
      imagemagick-jpeg \
      imagemagick-webp \
      imagemagick-svg \
      font-dejavu \
 && magick -version | grep -q HDRI \
 && magick -version

WORKDIR /app

# Development only. Never reachable from `docker build .`, which targets the
# last stage: `runtime` does not depend on this one, so BuildKit does not build
# it unless it is asked for by name.
#
# Installed editable, and the source is copied only so hatchling has something
# to build against: the editable install records a path pointing at /app/src,
# which docker-compose.yml then replaces with a bind mount of the host tree, so
# an edit needs no rebuild.
FROM pybase AS dev
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

FROM pybase AS runtime
COPY pyproject.toml ./
COPY src ./src
```

Everything from the existing `# Remove pip once the package is installed.`
comment to the end of the file is unchanged and stays in place, so `runtime`
remains the final stage.

- [ ] **Step 5: Run both test modules to verify they pass**

Run: `rtk proxy python -m pytest tests/test_dev_environment.py tests/test_toolchain_versions.py -v`
Expected: PASS, all of them.

- [ ] **Step 6: Prove the production build is unaffected**

Run:

```bash
docker build -t autoposter:plancheck . && docker run --rm autoposter:plancheck python -c "import pytest" ; echo "exit=$?"
```

Expected: the build succeeds and the `import pytest` exits **non-zero**
(`ModuleNotFoundError`). A zero exit means the default target became `dev` and
the ordering guard is not doing its job.

Then confirm the bundle is still there:

```bash
docker run --rm autoposter:plancheck ls frontend/dist/index.html
```

Expected: prints the path.

- [ ] **Step 7: Commit**

```bash
git diff --stat Dockerfile
git add Dockerfile tests/test_dev_environment.py tests/test_toolchain_versions.py
git commit --no-gpg-sign -m "Add development stages without letting one become the shipped image"
```

---

### Task 2: The `test` service

**Files:**
- Modify: `docker-compose.yml`
- Modify: `tests/test_dev_environment.py`

**Interfaces:**
- Consumes: build target `dev` from Task 1.
- Produces: service `test`, runnable as `docker compose run --rm test`.

- [ ] **Step 1: Write the failing guard**

Append to `tests/test_dev_environment.py`:

```python
def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _service(name: str) -> dict:
    services = _compose().get("services") or {}
    assert name in services, f"docker-compose.yml declares no {name!r} service"
    return services[name]


def test_development_services_build_from_the_dockerfile():
    """An `image:` tag here would be a second declaration of a pinned version.

    The whole point of the `webdev` and `dev` stages is that Node and Python
    are named once, in the Dockerfile, where tests/test_toolchain_versions.py
    already holds every stage to the same patch. A service that names an image
    directly escapes that entirely.
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


def test_the_test_service_needs_no_secrets():
    """Running the suite must need nothing but Docker.

    `api` legitimately requires credentials, as production does. If that
    requirement leaks into `test`, the claim that a fresh clone can run the
    suite stops being true and nobody finds out until a new machine tries.
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
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `rtk proxy python -m pytest tests/test_dev_environment.py -v -k service`
Expected: FAIL — "docker-compose.yml declares no 'test' service".

- [ ] **Step 3: Add the service**

Append to `docker-compose.yml`, leaving the `postgres` block untouched:

```yaml
  # The suite, on the interpreter and the ImageMagick the image ships. Needs no
  # credentials: `docker compose run --rm test` is the whole requirement.
  test:
    build:
      context: .
      target: dev
    environment:
      # tests/conftest.py defaults to the host's published 5433; inside the
      # compose network the server is reachable by service name instead.
      AUTOPOSTER_TEST_DATABASE_URL: postgresql+asyncpg://autoposter:autoposter@postgres:5432/autoposter
    volumes:
      # The editable install in the `dev` stage points at /app/src, so mounting
      # the host tree over it is what makes an edit take effect without a
      # rebuild. assets/ and config/ are read by the suite itself.
      - ./src:/app/src
      - ./tests:/app/tests
      - ./assets:/app/assets
      - ./config:/app/config
      - ./alembic:/app/alembic
      - ./alembic.ini:/app/alembic.ini
      - ./pyproject.toml:/app/pyproject.toml
    depends_on:
      postgres:
        condition: service_healthy
    command: pytest
```

- [ ] **Step 4: Run the guard to verify it passes**

Run: `rtk proxy python -m pytest tests/test_dev_environment.py -v`
Expected: PASS (`test_development_services_build_from_the_dockerfile` still
fails until Tasks 3 and 4 add `web` and `api` — that is expected; it goes green
at Task 4. Confirm the *other* tests pass.)

- [ ] **Step 5: Run the real suite in the container**

Run:

```bash
docker compose run --rm test pytest tests/test_golden.py tests/test_pipeline_e2e.py -q -rs
```

Expected: **5 tests that skip on the host now run and pass.** Specifically,
zero lines mentioning "requires a Q16-HDRI ImageMagick build". If they still
skip, `magick` is missing from the `dev` stage — check that `dev` derives from
`pybase` and not from `python:3.14.7-alpine` directly.

- [ ] **Step 6: Run the whole suite in the container**

Run: `docker compose run --rm test pytest -q`
Expected: PASS, with **5 fewer skips** than the host produces. Any *new*
failure is a real finding: the host has been running the suite on Python
3.13.7 against a `requires-python` of `>=3.14,<3.15`, so this is the first
execution on the correct interpreter. Record any such failure and fix it
before continuing rather than rolling it into a later task.

- [ ] **Step 7: Commit**

```bash
git diff docker-compose.yml   # confirm the postgres block is untouched
git add docker-compose.yml tests/test_dev_environment.py
git commit --no-gpg-sign -m "Run the suite on the interpreter and ImageMagick the image ships"
```

---

### Task 3: The `web` service, and the proxy port defect

**Files:**
- Modify: `docker-compose.yml`
- Modify: `frontend/vite.config.ts`
- Modify: `tests/test_dev_environment.py`

**Interfaces:**
- Consumes: build target `webdev` from Task 1; helpers `_service`, `_compose`
  from Task 2.
- Produces: service `web` on host port 5173.

- [ ] **Step 1: Write the failing guard for the proxy target**

Append to `tests/test_dev_environment.py`:

```python
def _uvicorn_port() -> int:
    """The port ``main()`` actually binds."""
    match = re.search(r"uvicorn\.run\([^)]*port=(\d+)", MAIN.read_text(encoding="utf-8"))
    assert match, "no `uvicorn.run(..., port=...)` call found in main.py"
    return int(match.group(1))


def test_the_vite_proxy_targets_the_port_the_app_binds():
    """This drifted once already and nothing noticed.

    vite.config.ts proxied to :8000 while main.py bound :8080, so the
    documented `npm run dev` hot-reload path could not have worked -- the
    config file looked entirely reasonable and the mismatch only surfaced by
    running it. Two files naming one port is a fact worth asserting.
    """
    targets = re.findall(r'"(https?://[^"]+)"', VITE_CONFIG.read_text(encoding="utf-8"))
    proxied = [t for t in targets if "://" in t]
    assert proxied, "vite.config.ts declares no proxy targets"
    port = _uvicorn_port()
    for target in proxied:
        host, _, declared = target.rpartition(":")
        assert declared.isdigit() and int(declared) == port, (
            f"vite proxies to {target} while main.py binds port {port}; the dev "
            "server would forward /api to a port nothing is listening on"
        )
        assert host.endswith("//api"), (
            f"vite proxies to {target}; inside the compose network the API is "
            "reachable as the service name `api`, not on localhost"
        )
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `rtk proxy python -m pytest tests/test_dev_environment.py::test_the_vite_proxy_targets_the_port_the_app_binds -v`
Expected: FAIL — "vite proxies to http://localhost:8000 while main.py binds port 8080".

- [ ] **Step 3: Fix the proxy target**

In `frontend/vite.config.ts`, replace the `server` block with:

```typescript
  server: {
    // The dev server runs inside the compose network (docker-compose.yml's
    // `web` service), so the API is reachable by service name. This proxied
    // localhost:8000 for a while, which no process ever listened on --
    // main.py binds 8080. tests/test_dev_environment.py now asserts the two
    // agree.
    proxy: {
      "/api": "http://api:8080",
      "/healthz": "http://api:8080",
    },
  },
```

- [ ] **Step 4: Run the guard to verify it passes**

Run: `rtk proxy python -m pytest tests/test_dev_environment.py::test_the_vite_proxy_targets_the_port_the_app_binds -v`
Expected: PASS.

- [ ] **Step 5: Add the service**

Append to `docker-compose.yml`:

```yaml
  # The vite dev server. Needs no credentials.
  web:
    build:
      context: .
      target: webdev
    environment:
      # A bind mount from Windows does not deliver inotify events reliably, so
      # vite's watcher sees nothing and hot reload silently stops reloading.
      # This lives here rather than in vite.config.ts so a committed config
      # file does not carry one platform's workaround.
      CHOKIDAR_USEPOLLING: "true"
    volumes:
      - ./frontend:/frontend
      # Never the bind mount: these packages ship per-platform binaries, and a
      # Windows or macOS tree mounted into Alpine fails outright -- the same
      # reasoning .dockerignore already records for the build context.
      - frontend_node_modules:/frontend/node_modules
    ports:
      - "5173:5173"
    # `npm ci` deletes node_modules wholesale every run, so doing it on every
    # `up` would cost half a minute each time. Install only into an empty
    # volume; `docker compose run --rm web npm ci` forces it when a dependency
    # actually changes.
    command: sh -c "[ -d node_modules/vite ] || npm ci; npm run dev -- --host 0.0.0.0"
```

And add at the end of the file:

```yaml
volumes:
  frontend_node_modules:
```

- [ ] **Step 6: Verify the frontend builds with no host Node**

Run: `docker compose run --rm web npm ci`
Expected: exits 0. This is the acceptance criterion for the whole change on
this machine — the host runs Node 24.8.0, where `npm ci` now exits 1.

Then: `docker compose run --rm web npm run build`
Expected: emits `dist/`, exits 0.

Then: `docker compose run --rm web npm test`
Expected: the vitest suite passes.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml frontend/vite.config.ts tests/test_dev_environment.py
git commit --no-gpg-sign -m "Serve the frontend from a container and point its proxy at a live port"
```

---

### Task 4: The `api` service

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `tests/test_dev_environment.py`

**Interfaces:**
- Consumes: build target `dev` from Task 1; helpers from Task 2.
- Produces: service `api` on host port 8080, requiring `.env`.

- [ ] **Step 1: Write the failing guard**

Append to `tests/test_dev_environment.py`:

```python
def test_the_api_service_fails_closed_without_credentials():
    """`Secrets.from_env()` raises on any of six missing variables.

    Compose declaring the env file means the failure arrives as "no .env" at
    start-up rather than as a traceback thirty seconds later, and it keeps the
    development stack honest about the same thing production is: no
    credentials, no service.
    """
    service = _service("api")
    env_file = service.get("env_file")
    declared = env_file if isinstance(env_file, list) else [env_file]
    paths = [e if isinstance(e, str) else (e or {}).get("path") for e in declared]
    assert ".env" in paths, (
        "the api service does not declare `env_file: .env`, so it would start "
        "without credentials and fail later inside Secrets.from_env()"
    )


def test_the_api_service_applies_migrations_like_production_does():
    """The production CMD is `alembic upgrade head && python -m autoposter.main`.

    A development stack that skips the migration step is a development stack
    where "works on my machine" can mean "against a schema main does not have".
    """
    command = _service("api").get("command") or ""
    assert "alembic upgrade head" in command, (
        "the api service does not run migrations on start, while the image's "
        "CMD does; the two would drift on any branch that adds a revision"
    )
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `rtk proxy python -m pytest tests/test_dev_environment.py -v -k api`
Expected: FAIL — "docker-compose.yml declares no 'api' service".

- [ ] **Step 3: Add the service**

Append to `docker-compose.yml`, before the `volumes:` block:

```yaml
  # The application, with hot reload. Unlike `test` and `web`, this one needs
  # credentials -- exactly as production does. Copy .env.example to .env and
  # fill it in; compose refuses to start the service without the file, and
  # Secrets.from_env() would refuse anyway.
  api:
    build:
      context: .
      target: dev
    env_file: .env
    environment:
      # Compose owns the wiring, .env owns the credentials. Set here so the
      # host-oriented URL in .env cannot point the container at a database it
      # has no route to.
      AUTOPOSTER_DATABASE_URL: postgresql+asyncpg://autoposter:autoposter@postgres:5432/autoposter
      AUTOPOSTER_CONFIG: /app/config/autoposter.example.yaml
    volumes:
      - ./src:/app/src
      - ./assets:/app/assets
      - ./config:/app/config
      - ./alembic:/app/alembic
      - ./alembic.ini:/app/alembic.ini
      - ./pyproject.toml:/app/pyproject.toml
    ports:
      - "8080:8080"
    depends_on:
      postgres:
        condition: service_healthy
    # Mirrors the image's CMD, plus --reload against the bind-mounted source.
    # `--factory` because main.py builds the app through build() rather than
    # exposing a module-level `app`, and --reload needs an import string.
    command: >
      sh -c "alembic upgrade head &&
      uvicorn autoposter.main:build --factory --reload --host 0.0.0.0 --port 8080"
```

- [ ] **Step 4: Annotate the DB URL in `.env.example`**

Replace the first line of `.env.example` with:

```
# Production is Kubernetes, where this is supplied as a Secret -- the line
# documents the URL's shape there. The development stack overrides it in
# docker-compose.yml, because inside the compose network the server is
# postgres:5432 rather than the host's published 5433.
AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter
```

- [ ] **Step 5: Run the guards to verify they pass**

Run: `rtk proxy python -m pytest tests/test_dev_environment.py -v`
Expected: PASS, all of them — including
`test_development_services_build_from_the_dockerfile`, which needed all three
services to exist.

- [ ] **Step 6: Verify the fail-closed behaviour, then the running stack**

First, with no `.env` present:

```bash
docker compose run --rm test pytest tests/test_dev_environment.py -q
```

Expected: **passes.** If compose instead errors about a missing env file, the
`api` service's declaration is breaking unrelated services — change its
`env_file` to the long form and re-run:

```yaml
    env_file:
      - path: .env
        required: true
```

Then create `.env` from `.env.example` (dummy values are fine — Plex is
connected lazily) and run:

```bash
docker compose up -d api
docker compose logs api --tail 20
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/healthz
```

Expected: `alembic upgrade head` runs in the logs, and `/healthz` answers
`200`.

- [ ] **Step 7: Verify hot reload end to end**

```bash
docker compose up -d web api
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/healthz
```

Expected: both `200`. The second proves the vite proxy reaches the `api`
service — the path that was broken before Task 3.

- [ ] **Step 8: Commit**

```bash
git diff docker-compose.yml   # confirm the postgres block is still untouched
git add docker-compose.yml .env.example tests/test_dev_environment.py
git commit --no-gpg-sign -m "Run the app with hot reload against the containerised database"
```

---

### Task 5: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the Development section**

Replace the existing Development section of `README.md` with:

```markdown
## Development

A working Docker installation is the only requirement. There is no host Python
or Node toolchain to install, and no version of either to keep in step with
the image.

```bash
docker compose run --rm test pytest     # the suite
docker compose run --rm web npm test    # the frontend suite
docker compose up web api               # the app, with hot reload
```

The SPA is then on `http://localhost:5173` and the API on
`http://localhost:8080`; the dev server proxies `/api` and `/healthz` to the
API container. `docker compose up api` applies migrations on start, exactly as
the image's `CMD` does.

Running the app needs credentials, as production does: copy `.env.example` to
`.env` and fill it in. The suite and the frontend need neither.

The suite runs on the interpreter and the ImageMagick build the image ships,
so the byte-identical poster tests in `tests/test_golden.py` execute rather
than skipping — they need a **Q16-HDRI** ImageMagick, which the `dev` stage
inherits from the same base the runtime image uses.

Never run `docker compose down -v`: the PostgreSQL volume is anonymous and
holds the development database.
```

- [ ] **Step 2: Remove the superseded one-liner**

In the Web UI section, delete the paragraph beginning "On a host with a
different Node, run that build in the pinned image instead" and the
`docker run ... node:26.7.0-alpine` block beneath it. Replace the sentence
introducing the local build with:

```markdown
Building it locally is `docker compose run --rm web npm run build`, which
emits `frontend/dist/`.
```

- [ ] **Step 3: Verify the documented commands actually run**

Run each of the three commands in the Development block above. Expected: each
exits 0. A documented command that has never been executed is the defect this
whole change exists to remove.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit --no-gpg-sign -m "Document the containerised development flow"
```

---

## Self-review

**Spec coverage.** §4 stage layout → Task 1. §5 services → Tasks 2-4. §6
secrets → Task 4 steps 3-4. §7 proxy defect → Task 3. §8 guards 1-2 → Task 1,
guard 3 → Task 3, guard 4 → Task 2. §9 documentation → Task 5. §10 acceptance
criteria → Task 1 step 6, Task 2 steps 5-6, Task 3 step 6, Task 4 steps 6-7.

**Known risk carried into execution.** Whether compose validates a missing
`env_file` for services other than the one being run is version-dependent and
is not assumed here: Task 4 step 6 tests it directly and gives the remedy.

**Deliberately not done.** No devcontainer, no wrapper scripts, no change to
the `postgres` service, no change to `.forgejo/workflows/ci.yml` — CI already
builds with no `target:` and Task 1 step 6 proves that still yields `runtime`.
