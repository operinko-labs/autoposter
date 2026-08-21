# The web UI is built here rather than committed, so the image always serves a
# bundle built from the source in this commit. The tag names an exact patch,
# not a floating major: frontend/package.json's `engines` and
# .forgejo/workflows/ci.yml's NODE_VERSION name that same patch, so the bundle
# CI typechecks is built by the same Node as the bundle that ships.
# tests/test_toolchain_versions.py fails if those three ever disagree.
FROM node:26.7.0-alpine AS frontend
WORKDIR /frontend
# The manifests alone first: a change to src/ then reuses this layer instead of
# re-installing every dependency.
COPY frontend/package.json frontend/package-lock.json ./
# `npm ci`, never `npm install`. It installs exactly what package-lock.json
# pins and fails outright on a mismatch, where `npm install` would quietly
# resolve something newer and rewrite the lockfile -- the skew that broke this
# repository three times over.
RUN npm ci
COPY frontend/ ./
# `npm run build` is `tsc --noEmit && vite build`, so a type error fails the
# image build rather than shipping a bundle nothing typechecked.
RUN npm run build

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
FROM python:3.14.7-alpine

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
EXPOSE 8080
CMD ["sh", "-c", "alembic upgrade head && python -m autoposter.main"]
