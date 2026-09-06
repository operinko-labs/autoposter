# The web UI is built here rather than committed, so the image always serves a
# bundle built from the source in this commit. The tag names an exact patch,
# not a floating major: frontend/package.json's `engines` and
# .forgejo/workflows/ci.yml's NODE_VERSION name that same patch, so the bundle
# CI typechecks is built by the same Node as the bundle that ships.
# tests/test_toolchain_versions.py fails if those three ever disagree.
FROM node:26.8.1-alpine AS frontend
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
FROM node:26.8.1-alpine AS webdev
WORKDIR /frontend

# The lazy install lives here rather than only in docker-compose.yml's
# `command:`, because a `command:` is exactly what `docker compose run --rm web
# npm run build` replaces. The named volume at /frontend/node_modules starts
# empty on a fresh clone, so both of the one-liners README's "Development"
# section documents used to die with `sh: tsc: not found` / `sh: vitest: not
# found` on the machine that most needs them to work -- the one where "a
# working Docker installation is the only requirement" is being taken at its
# word. An ENTRYPOINT is not overridden by a `command:` or by trailing
# arguments to `docker compose run`, so it happens whatever command is asked
# for.
#
# `npm ci` deletes node_modules wholesale, so it is skipped when the volume is
# already populated; `docker compose run --rm web npm ci` still forces it.
RUN printf '%s\n' \
      '#!/bin/sh' \
      'set -e' \
      '[ -d node_modules/vite ] || npm ci' \
      'exec "$@"' \
    > /usr/local/bin/webdev-entrypoint \
 && chmod +x /usr/local/bin/webdev-entrypoint
ENTRYPOINT ["/usr/local/bin/webdev-entrypoint"]

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

# 2026-08-26 -- CVE-2026-14456: openssl 3.5.7-r0 -> 3.5.8-r0. Widened
# 2026-09-05 -- CVE-2026-53612 / CVE-2026-76642: util-linux 2.42.1-r0 -> 2.42.3-r0
# (libblkid, libmount, libuuid) failed the Trivy HIGH gate on main the same way.
# A targeted list re-breaks main on every new base-package CVE, so this now
# upgrades every Alpine package the base ships; python itself is built from
# source in the official image and is not an apk package, so it is untouched.
# Remove when the pinned python:alpine base catches up (Renovate's next bump).
#
# The base is Alpine 3.24.1, whose repository already has the fixed packages;
# only the image predates them. Both stages that ship anything inherit this one,
# so patching here covers `runtime` and the `dev` stage CI runs the
# ImageMagick-gated tests in. The node stages are not in scope: `frontend`
# contributes only /frontend/dist to the final image and `webdev` is never
# reachable from `docker build .`, so neither ships an openssl layer.
#
# Deliberately its own RUN in the base stage, above every COPY. The layer is
# keyed on this file alone, so it is built once and reused by both dependent
# stages -- putting it any lower would rebuild it whenever pyproject.toml or
# src/ changed, which is the layer ordering the dev stage was reshaped to
# avoid.
RUN apk upgrade --no-cache

WORKDIR /app

# Development only. Never reachable from `docker build .`, which targets the
# last stage: `runtime` does not depend on this one, so BuildKit does not build
# it unless it is asked for by name. tests/test_dev_environment.py asserts that
# ordering, because a dev stage that became the default target would be built,
# verified and pushed as production with every existing check still passing.
#
# Installed editable, and the source is copied only so hatchling has something
# to build against: the editable install records a path pointing at /app/src,
# which docker-compose.yml then replaces with a bind mount of the host tree, so
# an edit needs no rebuild.
#
# The dependencies are installed before src/ arrives, and against a stub
# package, so that a source-only commit reuses that layer instead of resolving
# and downloading every wheel again. `--no-cache-dir` means "again" is over the
# network every time, and this stage is built by CI's "Test the
# ImageMagick-gated poster parity" step on every run: before the split it paid
# the full install whenever any file under src/ changed, which is most commits.
# hatchling needs *something* to build against, and an editable install records
# a path rather than the contents it was built from, so the stub is replaced
# wholesale by the COPY below and leaves nothing behind.
FROM pybase AS dev
COPY pyproject.toml ./
RUN mkdir -p src/autoposter \
 && touch src/autoposter/__init__.py \
 && pip install --no-cache-dir -e ".[dev]"
COPY src ./src
# Re-run against the real tree with the dependency graph excluded -- seconds,
# and nothing is downloaded but the build backend. It exists so this stage is
# correct whatever hatchling's editable install happens to emit: today that is
# a .pth naming /app/src, for which the stub install alone would suffice, but a
# future release emitting a finder that maps the modules it saw would otherwise
# ship an image containing only the stub.
RUN pip install --no-cache-dir -e ".[dev]" --no-deps

FROM pybase AS runtime
COPY pyproject.toml ./
COPY src ./src
# Remove pip once the package is installed. Nothing at runtime needs it --
# the container runs `python -m autoposter.boot`, which runs the migration
# itself once it has decided the deployment is configured --
# and pip is where the image's only reported vulnerabilities come from. They
# are not in anything this application imports: pip *vendors* its own
# dependencies and declares them in pip/_vendor/vendor.txt, which scanners
# read, so `msgpack==1.1.2` and `setuptools==70.3.0` get reported against the
# image even though neither is installed as a real package. Dropping pip
# removes all three findings rather than chasing versions of code we never
# call.
RUN pip install --no-cache-dir . \
 && python -m pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3.*/site-packages/pip \
           /usr/local/lib/python3.*/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

COPY assets ./assets
COPY alembic ./alembic
COPY alembic.ini ./
COPY --from=frontend /frontend/dist ./frontend/dist

# The package is pip-installed into site-packages while assets are copied to
# /app/assets, so the assets cannot be found relative to the module files.
ENV AUTOPOSTER_ASSETS_ROOT=/app/assets
# Same reasoning for the built SPA: `spa_dist()` falls back to a path relative
# to the installed module, which in this image is inside site-packages and has
# no frontend/ beside it. Without this the API starts perfectly and every probe
# passes while `/` answers 404, so the failure looks like a healthy service.
ENV AUTOPOSTER_SPA_DIST=/app/frontend/dist
ENV AUTOPOSTER_CONFIG=/config/autoposter.yaml

# The image's own name in the registry. .forgejo/workflows/ci.yml passes the
# commit's short sha here and then pushes the built image as `sha-<that>`, so
# these two strings are the same by construction -- which is the whole reason
# GET /api/version can compare what is running against what Harbor holds.
#
# Deliberately last among the ENVs and after every COPY: this layer changes on
# every commit, so anything below it would be rebuilt every time. Nothing is.
#
# A local `docker build` passes no --build-arg, so GIT_SHA is empty and this
# expands to the bare `sha-`. api/version.py treats that as `dev` rather than
# as a tag, so an unstamped build says so instead of reporting a tag the
# registry has never heard of.
ARG GIT_SHA=""
ENV AUTOPOSTER_VERSION=sha-${GIT_SHA}
EXPOSE 8080
# `exec` so python replaces sh as PID 1, rather than relying on ash's
# tail-call optimisation to make python the process that receives the
# kubelet's SIGTERM. Defensive, not a fix: without it, whether SIGTERM reaches
# python at all depends on shell-implementation behaviour this Dockerfile does
# not otherwise depend on.
#
# The migration is no longer chained here with `&&`. `autoposter.boot` runs it
# itself, after deciding that this deployment has credentials and a config
# document -- because `alembic upgrade head` raises outright without
# AUTOPOSTER_DATABASE_URL, and a container that dies in the shell can never
# serve the first-start wizard that would supply one (roadmap row 121). The
# database is not part of that decision: a configured deployment whose
# postgres is down still migrates, still fails, and still restarts.
CMD ["sh", "-c", "exec python -m autoposter.boot"]
