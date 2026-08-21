# Alpine, because its ImageMagick is built Q16-HDRI — the same configuration the
# Posterizarr deployment this service replaces runs (7.1.2-29 Q16-HDRI). Debian's
# package is Q16 without HDRI, which changes internal pixel maths: the same render
# came out 0.077% different (RMSE 0.00077) from the production asset, where the
# HDRI build reproduces it byte-for-byte. HDRI also brings float-format support
# (.exr/.hdr) for hand-supplied artwork.
FROM python:3.14-alpine

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

# The package is pip-installed into site-packages while assets are copied to
# /app/assets, so the assets cannot be found relative to the module files.
ENV AUTOPOSTER_ASSETS_ROOT=/app/assets
ENV AUTOPOSTER_CONFIG=/config/autoposter.yaml
EXPOSE 8080
CMD ["sh", "-c", "alembic upgrade head && python -m autoposter.main"]
