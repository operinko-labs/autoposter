# Alpine, because its ImageMagick is built Q16-HDRI — the same configuration the
# Posterizarr deployment this service replaces runs (7.1.2-29 Q16-HDRI). Debian's
# package is Q16 without HDRI, which changes internal pixel maths: the same render
# came out 0.077% different (RMSE 0.00077) from the production asset, where the
# HDRI build reproduces it byte-for-byte. HDRI also brings float-format support
# (.exr/.hdr) for hand-supplied artwork.
FROM python:3.12-alpine

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
RUN pip install --no-cache-dir .

COPY assets ./assets
COPY alembic ./alembic
COPY alembic.ini ./

ENV AUTOPOSTER_CONFIG=/config/autoposter.yaml
EXPOSE 8080
CMD ["sh", "-c", "alembic upgrade head && python -m autoposter.main"]
