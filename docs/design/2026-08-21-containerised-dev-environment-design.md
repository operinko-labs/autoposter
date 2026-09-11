# Containerised Development Environment — Design Spec

**Status:** approved 2026-08-21, not yet implemented.

## 1. Purpose

Make a working Docker installation the only thing a developer needs. Today the
README asks for Node 26.7.0 and a Python 3.14 environment on the host, and
`frontend/.npmrc` (added 2026-08-21) turned the Node requirement from a warning
into a refusal: `npm ci` exits 1 on any other patch. The primary development
machine runs Node 24.8.0, so the documented frontend flow is now unrunnable
there.

The deeper reason is the one this repository has been arguing all along. Three
breakages came from a version resolving differently in one place than another,
and a host toolchain is a declaration of that version which no test holds to
account. A host Python maintained solely so an editor can resolve imports is
the same hazard wearing a different hat.

There is a second prize. `dev` derives from the same base as the shipping
image, which carries a Q16-HDRI ImageMagick build. The byte-identical poster
tests and `test_badge_parity` **skip** on a machine without one, which is every
development machine here — they are exercised only by CI's image job. Once the
suite runs in this container they execute locally, against the same ImageMagick
configuration production uses.

## 2. Scope

**In:** running the Python suite, building and testing the frontend, and
running the application with hot reload — all inside containers.

**Out:** editor integration. No `.devcontainer/`, no language server, no
debugger wiring. The two things a devcontainer buys — a language server and a
debugger — serve a human at a keyboard, and work here is driven mostly through
Claude Code, which edits files on the host. The standard fix for bind-mount
file-watching performance on Windows is to clone the workspace into a container
volume; that is foreclosed regardless, because concurrent agent sessions
require the host working tree to remain the source of truth.

**Also out:** wrapper scripts. `docker compose` verbs are used directly, so
there is no `.ps1`/`.sh` pair to keep in step.

## 3. Constraints

- **Never `docker compose down -v`.** The developer PostgreSQL holds data and
  its volume is anonymous. Nothing in this design may force the `postgres`
  service to be recreated.
- **PostgreSQL stays reachable at `localhost:5433`** from the host, as the
  existing `ports` mapping provides. Containers reach it at `postgres:5432`.
- **Tests run as `rtk proxy python -m pytest ... -v`**; a bare `python -m
  pytest` is mangled by a shell hook.
- **Commits use `git commit --no-gpg-sign`**; signing times out here.
- Concurrent agent sessions share this working tree. `Dockerfile` and
  `docker-compose.yml` are both shared root files, and one revert has already
  cost an edit today.

## 4. Dockerfile: stage layout

The single most dangerous part of this change. `docker build .` targets the
**last** stage in the file, and `.forgejo/workflows/ci.yml` builds through
`docker/build-push-action` with no `target:`. A development stage appended
after the runtime stage would therefore become what CI builds, verifies and
pushes to Harbor — and every existing verification step would still pass,
because the development image contains a superset of the runtime one. The
failure would be silent and would ship.

So the order is fixed, and a test asserts it:

| Stage | Base | Role |
| --- | --- | --- |
| `frontend` | `node:26.7.0-alpine` | builds the bundle (unchanged) |
| `webdev` | `node:26.7.0-alpine` | bare workdir for the vite dev server (new) |
| `pybase` | `python:3.14.7-alpine` | ImageMagick Q16-HDRI apk layer, extracted from today's runtime stage |
| `dev` | `pybase` | `pip install -e ".[dev]"` (new) |
| `runtime` | `pybase` | `pip install .`, strip pip, copy the bundle — **must remain last** |

BuildKit builds only the stages its target depends on, so `runtime` does not
depend on `dev` or `webdev` and production builds pay nothing for them.

`webdev` exists rather than compose naming `image: node:26.7.0-alpine`
directly, so the Node version stays declared exactly once.

The `dev` stage installs editable (`-e`). The editable install records a path
pointing at `/app/src`, which the bind mount then replaces with live host
source — so a source edit needs no rebuild.

## 5. Compose services

`postgres` is unchanged. Three services are added.

| Service | Stage | Secrets | Command |
| --- | --- | --- | --- |
| `test` | `dev` | **none** | `pytest` |
| `web` | `webdev` | **none** | install if needed, then `vite --host 0.0.0.0` |
| `api` | `dev` | `.env` required | `alembic upgrade head && uvicorn --reload` |

That `test` and `web` need no credentials is the substance of the claim that
Docker alone is enough: the suite and the frontend build are the common case
and demand nothing else. Only running the application needs credentials, which
is exactly production's posture.

- `test` reaches the database through `AUTOPOSTER_TEST_DATABASE_URL` pointed at
  `postgres:5432`, overriding the host-oriented `localhost:5433` default in
  `tests/conftest.py`.
- `api` runs migrations on start, mirroring the production `CMD`
  (`alembic upgrade head && python -m autoposter.main`), and serves with
  `--reload` against the bind-mounted source. Because `main.py` builds the app
  through `build()`, reload uses `--factory` with `autoposter.main:build`.
- `web` keeps `node_modules` in a named volume rather than on the bind mount:
  the packages carry per-platform binaries, and a host tree mounted into Alpine
  fails outright — the same reasoning `.dockerignore` already records. Because
  `npm ci` deletes `node_modules` wholesale every run, the command installs
  only when the volume is empty rather than on every `up`.
- File watching across a Windows bind mount does not deliver inotify events
  reliably, so `web` sets `CHOKIDAR_USEPOLLING`. This lives in the compose
  environment, not in `vite.config.ts`, so a committed config file does not
  carry one platform's workaround.

## 6. Secrets and configuration

`api` declares `env_file: .env`, so compose fails when the file is absent —
fail-closed, matching `Secrets.from_env()`, which raises on any of six missing
variables. A developer copies `.env.example` and fills it in.

Compose owns the wiring and `.env` owns the credentials. `.env.example` keeps
its `AUTOPOSTER_DATABASE_URL` line, because production is Kubernetes and the
line documents the URL's shape there, but it gains a comment recording that the
development stack overrides it — otherwise it reads as live configuration that
is silently ignored.

## 7. Defect fixed in passing

`frontend/vite.config.ts` proxies `/api` and `/healthz` to `localhost:8000`.
`src/autoposter/main.py` binds `0.0.0.0:8080`. The documented `npm run dev`
hot-reload path cannot have been working. The proxy is retargeted at
`http://api:8080` — the compose service name, since the vite server now always
runs inside that network.

## 8. Guards

New module `tests/test_dev_environment.py`, plus one generalisation in
`tests/test_toolchain_versions.py`. Each asserts a property this design
depends on, in the style the existing module established: about agreement and
exactness, never about a particular number.

1. **Every `FROM <image>:` line names the same exact patch.**
   `_dockerfile_version` currently asserts there is *exactly one* such line per
   image, which two `FROM node:` stages would break. Generalising to "all
   agree" is strictly stronger than what it asserts today, and is what lets
   stages multiply safely.
2. **`runtime` is the final stage.** The failure in §4 — a pushed production
   image that is quietly the development image — is invisible to every other
   check in the pipeline.
3. **The vite proxy targets the port `uvicorn.run` binds, on the compose
   service that runs the API.** This is the check that would have caught §7.
4. **Development services build from the `Dockerfile`** rather than naming an
   `image:` tag, so the pinned versions remain declared once.

## 9. Documentation

The README's Development section becomes `docker compose` commands. The
`docker run … node:26.7.0-alpine` one-liner added to the Web UI section earlier
today is superseded by `docker compose run --rm web npm run build` and is
removed.

## 10. Acceptance criteria

- `docker compose run --rm test pytest` passes with no host Python, and the
  ImageMagick parity tests **run rather than skip**.
- `docker compose run --rm web npm ci` succeeds with no host Node, on a machine
  whose Node is 24.8.0.
- `docker compose up api web` serves the SPA with hot reload, proxying to the
  API, with migrations applied.
- `docker build .` still produces the runtime image — verified by inspecting
  that the built image has no `pytest` and does have the SPA bundle.
- The existing image job's checks continue to pass unchanged.
- `git status` shows no change to the `postgres` service definition.
