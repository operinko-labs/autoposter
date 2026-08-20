FROM python:3.12-slim

# ImageMagick 7 provides the `magick` binary the compositor shells out to.
RUN apt-get update \
 && apt-get install -y --no-install-recommends imagemagick libmagickwand-dev fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

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
