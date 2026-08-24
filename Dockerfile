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
FROM pybase AS dev
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

FROM pybase AS runtime
COPY pyproject.toml ./
COPY src ./src
# Remove pip once the package is installed. Nothing at runtime needs it --
# the container runs `alembic upgrade head && python -m autoposter.main` --
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
CMD ["sh", "-c", "alembic upgrade head && python -m autoposter.main"]
