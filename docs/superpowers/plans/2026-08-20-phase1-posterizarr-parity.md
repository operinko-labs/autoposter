# Autoposter Phase 1 — Posterizarr Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deployable service that receives Radarr/Sonarr webhooks, resolves the affected Plex items, fetches textless artwork, composites Posterizarr-identical posters/season posters/backgrounds/title cards, and writes them to `/assets` — letting Posterizarr be retired.

**Architecture:** FastAPI process with three internal modules — intake (webhooks → durable Postgres job queue), render pipeline (resolve → fetch art → composite via ImageMagick → atomic asset write), and a worker pool claiming jobs with `FOR UPDATE SKIP LOCKED`. Kometa keeps running unchanged during this phase, still reading `/assets`.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy 2.0 + asyncpg, Alembic, PostgreSQL 18, ImageMagick 7 (`magick` subprocess), httpx, Pydantic v2, pytest + pytest-asyncio, Docker.

**Spec:** `docs/superpowers/specs/2026-08-20-autoposter-design.md` (sections 1–4, 7–10). This plan covers **Phase 1 only** (spec §9.1). Badges, metadata ops, collections, scheduler, and web UI are later phases and MUST NOT be built here.

## Global Constraints

Every task's requirements implicitly include these. Values are copied verbatim from the spec and from the user's existing Posterizarr config.

- **Python 3.12+.** Type hints on all public functions. No `from __future__ import annotations` needed.
- **PostgreSQL only.** No SQLite anywhere, including caches and tests.
- **All secrets come from environment variables**, never from the YAML config file: `AUTOPOSTER_DATABASE_URL`, `AUTOPOSTER_PLEX_TOKEN`, `AUTOPOSTER_TMDB_TOKEN`, `AUTOPOSTER_TVDB_APIKEY`, `AUTOPOSTER_FANART_APIKEY`, `AUTOPOSTER_WEBHOOK_SECRET`.
- **Canvas sizes are fixed, not configurable:** poster and season poster `2000x3000`; background and title card `3840x2160`.
- **Asset extension is always `.jpg`.** Naming: `poster.jpg`, `background.jpg`, `SeasonNN.jpg`, `SNNENN.jpg` — capital `S`/`E`, zero-padded to a **minimum** of 2 digits (season 100 → `Season100`). Specials are `Season00`.
- **Asset layout** (`LibraryFolders: true`): `<assets_root>/<library>/<root_folder>/<name>.jpg`, where `<root_folder>` is the on-disk media folder basename, used verbatim with no sanitisation.
- **`text_offset` strings must carry an explicit sign** (`"+300"`, not `"300"`) — they are concatenated after `+0` to form an ImageMagick `-geometry` argument.
- **Output quality is `92%`**, applied on every `-composite` call.
- **Fonts:** `Comfortaa-Medium.ttf` (posters, seasons, backgrounds, title cards), `Colus-Regular.ttf` (collections, RTL). Ship them in `assets/fonts/`.
- **Language ladders:** poster/season/background/title-card `["xx", "en", "fi"]`; logos `["en", "fi"]`. `xx` means textless.
- **Provider order:** `["TMDB", "TVDB", "Fanart", "Plex"]`; favourite provider `TMDB`.
- **Excluded Plex libraries:** `Muskarit`, `Photos`.
- **Worker parallelism defaults to 5** (matches the current `ParallelJobs`).
- **Every asset write is atomic:** write to a temp file, then `os.replace` onto the final path.
- **All queue timestamps come from the database clock**, never from the application
  clock. `enqueue()` and `fail()` compute `run_after` with Postgres `now()`, and
  `claim()` compares against Postgres `now()`. Mixing the two makes jobs fire early
  or late by however far the app and database clocks have drifted — observed at 9.5s
  between a Windows host and its WSL2 Postgres container, and possible between pods
  in the cluster. Tests must obtain "now" via `SELECT now()` for the same reason.
- **Commit after every task.** Conventional-commit prefixes (`feat:`, `test:`, `chore:`).

### Deliberate deviations from Posterizarr

Research into Posterizarr v3.2.0 surfaced defects. These decisions are binding; do not "fix" them differently mid-implementation.

| Posterizarr behaviour | Decision | Why |
|---|---|---|
| Text that cannot fit at `minPointSize` sets `IsTruncated` and the asset is **not written at all** | **Replicate** | Prevents emitting posters Posterizarr would have skipped. Park the render as `truncated` so it is visible, not silent. |
| Logo composites emit a malformed `-geometry +0++300` (extra `+`) | **Do not replicate** — emit `+0+300` | It is a string-concatenation bug; the text branches emit the correct form. |
| `-quality` omitted on the border-only and plain-resize branches | **Not applicable** | The user's config has `AddBorder: false` and `AddOverlay: true`, so only the overlay branch is reachable. Implement the overlay branch with `-quality`. |
| Season/collection border width reuses the *poster* `borderwidth` | **Do not replicate** | Use each block's own width. Unreachable with `AddBorder: false`, but correctness is free. |
| Inner stroke composite uses `ShowOnSeasontextgravity` for every asset type | **Do not replicate** | Use each block's own gravity. Unreachable with `AddTextStroke: false`. |

### Behaviour that looks like a bug but is intentional

With the user's config (`UseLogo: true`, `UseClearlogo: true`, `LogoTextFallback: false`), a poster whose show/movie has **no clearlogo on any provider gets neither a logo nor title text** — just art plus the fade overlay. This is the current production behaviour and MUST be preserved.

---

## File Structure

```
pyproject.toml                          deps, pytest/ruff config
docker-compose.yml                      Postgres 18 for dev + tests
Dockerfile                              runtime image (python:3.12-slim + ImageMagick)
alembic.ini                             migration config
alembic/env.py                          async migration environment
alembic/versions/                       migration scripts
assets/fonts/                           Comfortaa-Medium.ttf, Colus-Regular.ttf
assets/overlays/                        overlay.png, bottom-up-fade.png, bottom-up-fade-background.png
config/autoposter.example.yaml          documented example config

src/autoposter/
  config/schema.py                      Pydantic config models + validators
  config/loader.py                      YAML load, env-var secrets
  db/base.py                            declarative Base, engine, session factory
  db/models.py                          MediaItem, Render, Job, ProviderCache, EventLog
  queue/jobs.py                         enqueue / claim / complete / fail
  queue/worker.py                       worker pool loop
  intake/arr.py                         Radarr+Sonarr payload -> RenderIntent (pure)
  intake/routes.py                      FastAPI webhook endpoints
  plex/client.py                        resolve items, read media facts
  render/naming.py                      asset path derivation
  render/textfit.py                     ImageMagick caption point-size fitting
  render/compositor.py                  magick argv construction + execution
  render/pipeline.py                    per-item orchestration
  providers/base.py                     ArtCandidate, ArtKind, provider protocol
  providers/tmdb.py                     TMDB client
  providers/tvdb.py                     TVDB v4 client
  providers/fanart.py                   Fanart.tv client
  providers/ladder.py                   cross-provider selection
  app.py                                FastAPI app factory
  main.py                               entrypoint (app + workers)

tests/
  conftest.py                           Postgres fixtures, config fixtures
  fixtures/webhooks/                    real Radarr/Sonarr JSON payloads
  fixtures/providers/                   recorded TMDB/TVDB/Fanart responses
  fixtures/golden/                      reference images harvested from /assets
  test_*.py                             one per module
```

---

## Task 1: Project scaffolding and configuration

**Files:**
- Create: `pyproject.toml`, `docker-compose.yml`, `.env.example`
- Create: `src/autoposter/__init__.py`, `src/autoposter/config/__init__.py`
- Create: `src/autoposter/config/schema.py`, `src/autoposter/config/loader.py`
- Create: `config/autoposter.example.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `load_config(path: Path) -> Config` and `Secrets.from_env() -> Secrets`, plus model classes `Config`, `TextStyle`, `ArtKindConfig`, `TitleCardConfig`, `ArtworkConfig`, `ProvidersConfig`, `PlexConfig`, `Secrets`. Every later task reads settings through `Config`.

- [ ] **Step 1: Create the project skeleton**

`pyproject.toml`:

```toml
[project]
name = "autoposter"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.9",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "plexapi>=4.15",
    "prometheus-client>=0.21",
    "structlog>=24.4",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "ruff>=0.8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/autoposter"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:18-alpine
    environment:
      POSTGRES_USER: autoposter
      POSTGRES_PASSWORD: autoposter
      POSTGRES_DB: autoposter
    ports: ["5433:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U autoposter"]
      interval: 2s
      timeout: 3s
      retries: 20
```

`.env.example`:

```bash
AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter
AUTOPOSTER_PLEX_TOKEN=changeme
AUTOPOSTER_TMDB_TOKEN=changeme
AUTOPOSTER_TVDB_APIKEY=changeme
AUTOPOSTER_FANART_APIKEY=changeme
AUTOPOSTER_WEBHOOK_SECRET=changeme
```

Create empty `src/autoposter/__init__.py` and `src/autoposter/config/__init__.py`.

- [ ] **Step 2: Write the failing config tests**

`tests/test_config.py`:

```python
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

SECRET_NAMES = (
    "DATABASE_URL", "PLEX_TOKEN", "TMDB_TOKEN",
    "TVDB_APIKEY", "FANART_APIKEY", "WEBHOOK_SECRET",
)


def test_example_config_loads():
    cfg = load_config(EXAMPLE)
    assert cfg.assets_root == Path("/assets")
    assert cfg.workers == 5
    assert cfg.providers.order == ["TMDB", "TVDB", "Fanart", "Plex"]
    assert cfg.artwork.poster.language_order == ["xx", "en", "fi"]
    assert "Muskarit" in cfg.plex.excluded_libraries


def test_poster_text_style_matches_posterizarr():
    style = load_config(EXAMPLE).artwork.poster.text
    assert style.min_point_size == 83
    assert style.max_point_size == 250
    assert style.max_width == 1200
    assert style.max_height == 485
    assert style.text_offset == "+300"
    assert style.all_caps is True
    assert style.add_stroke is False


def test_background_text_is_disabled():
    assert load_config(EXAMPLE).artwork.background.text.add_text is False


def test_title_card_has_two_text_blocks():
    tc = load_config(EXAMPLE).artwork.title_card
    assert tc.text.text_offset == "-150"
    assert tc.episode_text.text_offset == "+100"
    assert tc.season_label == "Season"
    assert tc.episode_label == "Episode"


def test_text_offset_without_sign_is_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace('"+300"', '"300"'), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="explicit sign"):
        load_config(bad)


def test_language_order_rejects_bad_codes(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("[xx, en, fi]", "[xx, english]", 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="two-letter"):
        load_config(bad)


def test_secrets_come_from_env(monkeypatch):
    for name in SECRET_NAMES:
        monkeypatch.setenv("AUTOPOSTER_" + name, "value-" + name)
    secrets = Secrets.from_env()
    assert secrets.tmdb_token == "value-TMDB_TOKEN"
    assert secrets.database_url == "value-DATABASE_URL"


def test_missing_secret_names_the_variable(monkeypatch):
    for name in SECRET_NAMES:
        monkeypatch.setenv("AUTOPOSTER_" + name, "x")
    monkeypatch.delenv("AUTOPOSTER_TMDB_TOKEN")
    with pytest.raises(RuntimeError, match="AUTOPOSTER_TMDB_TOKEN"):
        Secrets.from_env()


def test_config_version_changes_with_content(tmp_path):
    a = load_config(EXAMPLE)
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("min_point_size: 83", "min_point_size: 84"),
        encoding="utf-8",
    )
    assert a.version != load_config(changed).version
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.config.loader'`

- [ ] **Step 4: Write the config schema**

`src/autoposter/config/schema.py`:

```python
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

_LANG_RE = re.compile(r"^[a-z]{2}$")

_SECRET_ENV = {
    "database_url": "AUTOPOSTER_DATABASE_URL",
    "plex_token": "AUTOPOSTER_PLEX_TOKEN",
    "tmdb_token": "AUTOPOSTER_TMDB_TOKEN",
    "tvdb_apikey": "AUTOPOSTER_TVDB_APIKEY",
    "fanart_apikey": "AUTOPOSTER_FANART_APIKEY",
    "webhook_secret": "AUTOPOSTER_WEBHOOK_SECRET",
}


class Secrets(BaseModel):
    """Runtime secrets. Never read from the YAML config file."""

    database_url: str
    plex_token: str
    tmdb_token: str
    tvdb_apikey: str
    fanart_apikey: str
    webhook_secret: str

    @classmethod
    def from_env(cls) -> "Secrets":
        values = {}
        for field, env_name in _SECRET_ENV.items():
            value = os.environ.get(env_name)
            if not value:
                raise RuntimeError(f"required environment variable {env_name} is not set")
            values[field] = value
        return cls(**values)


class TextStyle(BaseModel):
    """One text block. Mirrors a Posterizarr *OverlayPart section."""

    font: str = "Comfortaa-Medium.ttf"
    all_caps: bool = True
    font_color: str = "white"
    min_point_size: int
    max_point_size: int
    max_width: int
    max_height: int
    text_offset: str
    gravity: str = "south"
    line_spacing: int = 0
    add_text: bool = True
    add_stroke: bool = False
    stroke_color: str = "black"
    stroke_width: int = 6

    @field_validator("text_offset")
    @classmethod
    def _must_carry_sign(cls, v: str) -> str:
        if not v.startswith(("+", "-")):
            raise ValueError(
                f"text_offset {v!r} must carry an explicit sign, e.g. '+300' — it is "
                "concatenated after '+0' to form an ImageMagick -geometry argument"
            )
        return v


class ArtKindConfig(BaseModel):
    """One artifact type: whether to build it, its art sources and its text block."""

    enabled: bool = True
    language_order: list[str] = Field(default_factory=lambda: ["xx", "en", "fi"])
    overlay_file: str
    add_overlay: bool = True
    add_border: bool = False
    border_color: str = "white"
    border_width: int = 30
    min_width: int = 0
    min_height: int = 0
    text: TextStyle | None = None

    @field_validator("language_order")
    @classmethod
    def _valid_languages(cls, v: list[str]) -> list[str]:
        for code in v:
            if code != "xx" and not _LANG_RE.match(code):
                raise ValueError(
                    f"language code {code!r} is invalid: use 'xx' for textless "
                    "or a lowercase two-letter ISO-639-1 code"
                )
        return v


class TitleCardConfig(ArtKindConfig):
    """Title cards carry two independent text blocks."""

    episode_text: TextStyle | None = None
    season_label: str = "Season"
    episode_label: str = "Episode"
    skip_words: list[str] = Field(default_factory=lambda: ["TBA"])


class ArtworkConfig(BaseModel):
    poster: ArtKindConfig
    season_poster: ArtKindConfig
    background: ArtKindConfig
    title_card: TitleCardConfig
    use_logo: bool = True
    logo_language_order: list[str] = Field(default_factory=lambda: ["en", "fi"])
    logo_text_fallback: bool = False
    output_quality: str = "92%"


class ProvidersConfig(BaseModel):
    order: list[str] = Field(default_factory=lambda: ["TMDB", "TVDB", "Fanart", "Plex"])
    favourite: str = "TMDB"
    tmdb_vote_sorting: str = "vote_average"


class PlexConfig(BaseModel):
    url: str
    excluded_libraries: list[str] = Field(default_factory=list)
    resolve_max_attempts: int = 10


class Config(BaseModel):
    assets_root: Path
    manual_assets_root: Path
    backup_root: Path
    fonts_root: Path
    overlays_root: Path
    library_folders: bool = True
    workers: int = 5
    settle_seconds: int = 30
    magick_binary: str = "magick"
    skip_tba: bool = True
    plex: PlexConfig
    providers: ProvidersConfig
    artwork: ArtworkConfig
    version: str = ""
```

- [ ] **Step 5: Write the config loader**

`src/autoposter/config/loader.py`:

```python
import hashlib
from pathlib import Path

import yaml

from autoposter.config.schema import Config


def load_config(path: Path) -> Config:
    """Load and validate the YAML config.

    ``version`` is a content hash of the file. It feeds render fingerprints, so any
    config edit that changes rendering marks the affected assets stale.
    """
    raw_bytes = Path(path).read_bytes()
    data = yaml.safe_load(raw_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config at {path} must be a YAML mapping")
    data["version"] = hashlib.sha256(raw_bytes).hexdigest()[:16]
    return Config(**data)
```

- [ ] **Step 6: Write the example config**

`config/autoposter.example.yaml` — the 1:1 translation of the existing Posterizarr config:

```yaml
assets_root: /assets
manual_assets_root: /manualassets
backup_root: /assetsbackup
fonts_root: /app/assets/fonts
overlays_root: /app/assets/overlays
library_folders: true
workers: 5
settle_seconds: 30
magick_binary: magick
skip_tba: true

plex:
  url: https://<plex-host>
  excluded_libraries: [Muskarit, Photos]
  resolve_max_attempts: 10

providers:
  order: [TMDB, TVDB, Fanart, Plex]
  favourite: TMDB
  tmdb_vote_sorting: vote_average

artwork:
  use_logo: true
  logo_language_order: [en, fi]
  logo_text_fallback: false
  output_quality: 92%

  poster:
    enabled: true
    language_order: [xx, en, fi]
    overlay_file: overlay.png
    add_overlay: true
    add_border: false
    border_color: white
    border_width: 30
    text:
      font: Comfortaa-Medium.ttf
      all_caps: true
      font_color: white
      min_point_size: 83
      max_point_size: 250
      max_width: 1200
      max_height: 485
      text_offset: "+300"
      gravity: south
      line_spacing: 0
      add_text: true
      add_stroke: false
      stroke_color: black
      stroke_width: 6

  season_poster:
    enabled: true
    language_order: [xx, en, fi]
    overlay_file: bottom-up-fade.png
    add_overlay: true
    add_border: false
    text:
      font: Comfortaa-Medium.ttf
      all_caps: true
      font_color: white
      min_point_size: 100
      max_point_size: 250
      max_width: 1200
      max_height: 485
      text_offset: "+300"
      gravity: south
      add_text: true
      add_stroke: false

  background:
    enabled: true
    language_order: [xx, en, fi]
    overlay_file: bottom-up-fade-background.png
    add_overlay: true
    add_border: false
    text:
      font: Comfortaa-Medium.ttf
      all_caps: true
      font_color: white
      min_point_size: 95
      max_point_size: 250
      max_width: 3000
      max_height: 500
      text_offset: "+200"
      gravity: south
      add_text: false
      add_stroke: false

  title_card:
    enabled: true
    language_order: [xx, en, fi]
    overlay_file: bottom-up-fade-background.png
    add_overlay: true
    add_border: false
    season_label: Season
    episode_label: Episode
    skip_words: [TBA]
    text:
      font: Comfortaa-Medium.ttf
      all_caps: true
      font_color: white
      min_point_size: 48
      max_point_size: 140
      max_width: 2500
      max_height: 300
      text_offset: "-150"
      gravity: south
      add_text: true
      add_stroke: false
    episode_text:
      font: Comfortaa-Medium.ttf
      all_caps: true
      font_color: white
      min_point_size: 48
      max_point_size: 80
      max_width: 2500
      max_height: 150
      text_offset: "+100"
      gravity: south
      add_text: true
      add_stroke: false
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS — 9 passed

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml docker-compose.yml .env.example config src/autoposter tests/test_config.py
git commit -m "feat: project scaffolding and validated YAML configuration"
```

---

## Task 2: Database models and migrations

**Files:**
- Create: `src/autoposter/db/__init__.py`, `src/autoposter/db/base.py`, `src/autoposter/db/models.py`
- Create: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/`
- Create: `tests/conftest.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `Secrets.from_env()` (Task 1).
- Produces: `Base`, `make_engine(url) -> AsyncEngine`, `make_session_factory(engine) -> async_sessionmaker`, and models `MediaItem`, `Render`, `Job`, `ProviderCache`, `EventLog`. Column names later tasks depend on: `MediaItem.rating_key/library/kind/parent_id/tmdb_id/tvdb_id/imdb_id/title/year/season_number/episode_number/root_folder/file_path`; `Render.item_id/art_kind/source_mode/provider/source_url/textless/base_sha256/fingerprint/asset_path/status/detail`; `Job.kind/payload/dedupe_key/state/attempts/run_after/claimed_by/last_error`.

- [ ] **Step 1: Write the test fixtures**

`tests/conftest.py`:

```python
import os

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from autoposter.db.base import Base

TEST_DB_URL = os.environ.get(
    "AUTOPOSTER_TEST_DATABASE_URL",
    "postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter",
)


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s
```

- [ ] **Step 2: Write the failing model tests**

`tests/test_models.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from autoposter.db.models import EventLog, Job, MediaItem, Render


async def test_media_item_roundtrip(session):
    session.add(MediaItem(
        rating_key="12345", library="Movies", kind="movie",
        tmdb_id=693134, imdb_id="tt15239678",
        title="Dune: Part Two", year=2024, root_folder="Dune Part Two (2024)",
    ))
    await session.commit()
    found = (
        await session.execute(select(MediaItem).where(MediaItem.rating_key == "12345"))
    ).scalar_one()
    assert found.title == "Dune: Part Two"
    assert found.kind == "movie"


async def test_rating_key_is_unique(session):
    session.add(MediaItem(rating_key="dup", library="Movies", kind="movie", title="A"))
    await session.commit()
    session.add(MediaItem(rating_key="dup", library="Movies", kind="movie", title="B"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_episode_links_to_parent_season(session):
    show = MediaItem(rating_key="s1", library="TV Shows", kind="show", title="Severance")
    session.add(show)
    await session.flush()
    season = MediaItem(rating_key="s1s2", library="TV Shows", kind="season",
                       title="Season 2", parent_id=show.id, season_number=2)
    session.add(season)
    await session.flush()
    ep = MediaItem(rating_key="e1", library="TV Shows", kind="episode",
                   title="Who Is Alive?", parent_id=season.id,
                   season_number=2, episode_number=3)
    session.add(ep)
    await session.commit()
    assert ep.parent_id == season.id


async def test_render_defaults_to_generate_source_mode(session):
    item = MediaItem(rating_key="r1", library="Movies", kind="movie", title="X")
    session.add(item)
    await session.flush()
    render = Render(item_id=item.id, art_kind="poster",
                    asset_path="/assets/Movies/X/poster.jpg")
    session.add(render)
    await session.commit()
    assert render.source_mode == "generate"
    assert render.status == "pending"


async def test_one_render_per_item_and_art_kind(session):
    item = MediaItem(rating_key="r2", library="Movies", kind="movie", title="Y")
    session.add(item)
    await session.flush()
    session.add(Render(item_id=item.id, art_kind="poster", asset_path="/a.jpg"))
    await session.commit()
    session.add(Render(item_id=item.id, art_kind="poster", asset_path="/b.jpg"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_two_art_kinds_for_one_item_are_allowed(session):
    item = MediaItem(rating_key="r3", library="Movies", kind="movie", title="Z")
    session.add(item)
    await session.flush()
    session.add(Render(item_id=item.id, art_kind="poster", asset_path="/p.jpg"))
    session.add(Render(item_id=item.id, art_kind="background", asset_path="/b.jpg"))
    await session.commit()
    rows = (await session.execute(select(Render).where(Render.item_id == item.id))).scalars().all()
    assert len(rows) == 2


async def test_only_one_pending_job_per_dedupe_key(session):
    session.add(Job(kind="process_item", payload={"rating_key": "1"},
                    dedupe_key="process_item:1"))
    await session.commit()
    session.add(Job(kind="process_item", payload={"rating_key": "1"},
                    dedupe_key="process_item:1"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_done_job_does_not_block_a_new_pending_one(session):
    session.add(Job(kind="process_item", payload={}, dedupe_key="k", state="done"))
    await session.commit()
    session.add(Job(kind="process_item", payload={}, dedupe_key="k"))
    await session.commit()
    rows = (await session.execute(select(Job).where(Job.dedupe_key == "k"))).scalars().all()
    assert len(rows) == 2


async def test_event_log_stores_raw_payload(session):
    session.add(EventLog(source="sonarr", event_type="Download",
                         payload={"series": {"tvdbId": 371980}}))
    await session.commit()
    row = (await session.execute(select(EventLog))).scalar_one()
    assert row.payload["series"]["tvdbId"] == 371980
```

- [ ] **Step 3: Start Postgres and run the tests to verify they fail**

```bash
docker compose up -d postgres
pytest tests/test_models.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.db.base'`

- [ ] **Step 4: Write the declarative base**

`src/autoposter/db/base.py`:

```python
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, future=True, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 5: Write the models**

`src/autoposter/db/models.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autoposter.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MediaItem(Base):
    """One movie, show, season or episode, keyed by its Plex rating key."""

    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rating_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    library: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # movie|show|season|episode
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    season_number: Mapped[int | None] = mapped_column(Integer)
    episode_number: Mapped[int | None] = mapped_column(Integer)
    root_folder: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Render(Base):
    """One artifact for one item. ``fingerprint`` decides whether to re-render."""

    __tablename__ = "renders"
    __table_args__ = (UniqueConstraint("item_id", "art_kind", name="uq_render_item_kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    # poster | season_poster | background | title_card
    art_kind: Mapped[str] = mapped_column(String(24))
    # 'generate' composites our own text and fade over textless art.
    # 'verbatim' applies supplied art untouched (the MediUX seam, spec section 11).
    source_mode: Mapped[str] = mapped_column(String(16), default="generate")
    provider: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(Text)
    textless: Mapped[bool | None] = mapped_column(Boolean)
    base_sha256: Mapped[str | None] = mapped_column(String(64))
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    asset_path: Mapped[str] = mapped_column(Text)
    # pending | rendered | truncated | no_art | failed
    status: Mapped[str] = mapped_column(String(24), default="pending")
    detail: Mapped[str | None] = mapped_column(Text)
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Job(Base):
    """Durable work queue, claimed with SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (
        # Coalescing: at most one *pending* job per dedupe key. Running, done, failed
        # and parked rows are excluded, so an event arriving after work has started
        # still queues a fresh pass.
        Index(
            "uq_jobs_pending_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        Index("ix_jobs_claimable", "state", "run_after"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    # pending | running | done | failed | parked
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ProviderCache(Base):
    """Keyed provider responses with a TTL (Kometa's cache_expiration semantics)."""

    __tablename__ = "provider_cache"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EventLog(Base):
    """Every webhook received, and what it resolved to."""

    __tablename__ = "events_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))  # radarr|sonarr|tautulli|manual
    event_type: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    outcome: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS — 9 passed

- [ ] **Step 7: Set up Alembic and generate the initial migration**

Run `alembic init alembic`, then replace `alembic.ini` with:

```ini
[alembic]
script_location = alembic
prepend_sys_path = src

[loggers]
keys = root
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

and `alembic/env.py` with:

```python
import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from autoposter.db import models  # noqa: F401  (import registers the tables)
from autoposter.db.base import Base

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("AUTOPOSTER_DATABASE_URL")
    if not url:
        raise RuntimeError("AUTOPOSTER_DATABASE_URL is not set")
    return url


def _do_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_migrations)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
```

Keep the `alembic/script.py.mako` that `alembic init` generated. Then:

```bash
export AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter
alembic revision --autogenerate -m "initial schema"
```

- [ ] **Step 8: Verify the migration applies to an empty database**

```bash
docker compose down -v && docker compose up -d postgres && sleep 5 && alembic upgrade head
```

Then confirm the tables exist:

```bash
docker compose exec -T postgres psql -U autoposter -d autoposter -c "\dt"
```

Expected: `media_items`, `renders`, `jobs`, `provider_cache`, `events_log`, `alembic_version`.

- [ ] **Step 9: Commit**

```bash
git add src/autoposter/db alembic alembic.ini tests/conftest.py tests/test_models.py
git commit -m "feat: postgres schema for items, renders, jobs, cache and event log"
```

---

## Task 3: Durable job queue

**Files:**
- Create: `src/autoposter/queue/__init__.py`, `src/autoposter/queue/jobs.py`
- Test: `tests/test_queue.py`

**Interfaces:**
- Consumes: `Job` model and the `session` fixture (Task 2).
- Produces:
  - `async enqueue(session, kind: str, payload: dict, dedupe_key: str | None = None, delay_seconds: int = 0) -> int | None` — returns the new job id, or `None` when coalesced into an existing pending job.
  - `async claim(session, worker_id: str) -> Job | None`
  - `async complete(session, job_id: int) -> None`
  - `async fail(session, job_id: int, error: str) -> str` — returns the resulting state, `"pending"` or `"parked"`.
  - Constants `MAX_ATTEMPTS = 5`, `BACKOFF_BASE_SECONDS = 30`.

- [ ] **Step 1: Write the failing queue tests**

`tests/test_queue.py`:

```python
from datetime import timedelta

from sqlalchemy import func, select

from autoposter.db.models import Job
from autoposter.queue.jobs import MAX_ATTEMPTS, claim, complete, enqueue, fail


async def test_enqueue_returns_a_job_id(session):
    job_id = await enqueue(session, "process_item", {"rating_key": "1"})
    assert isinstance(job_id, int)


async def test_duplicate_pending_key_is_coalesced(session):
    first = await enqueue(session, "process_item", {"rating_key": "1"}, dedupe_key="k1")
    second = await enqueue(session, "process_item", {"rating_key": "1"}, dedupe_key="k1")
    assert first is not None
    assert second is None


async def test_key_is_reusable_once_the_job_finished(session):
    first = await enqueue(session, "process_item", {}, dedupe_key="k2")
    await complete(session, first)
    second = await enqueue(session, "process_item", {}, dedupe_key="k2")
    assert second is not None


async def test_claim_marks_running_and_counts_the_attempt(session):
    await enqueue(session, "process_item", {"rating_key": "7"})
    job = await claim(session, "worker-a")
    assert job is not None
    assert job.state == "running"
    assert job.claimed_by == "worker-a"
    assert job.attempts == 1
    assert job.payload["rating_key"] == "7"


async def test_claim_ignores_jobs_that_are_not_due(session):
    await enqueue(session, "process_item", {}, delay_seconds=3600)
    assert await claim(session, "worker-a") is None


async def test_claim_returns_none_when_queue_is_empty(session):
    assert await claim(session, "worker-a") is None


async def test_two_workers_never_claim_the_same_job(session_factory):
    async with session_factory() as setup:
        await enqueue(setup, "process_item", {"n": 1}, dedupe_key="a")
        await enqueue(setup, "process_item", {"n": 2}, dedupe_key="b")
    async with session_factory() as s1, session_factory() as s2:
        first = await claim(s1, "worker-1")
        second = await claim(s2, "worker-2")
    assert first is not None and second is not None
    assert first.id != second.id


async def test_failure_reschedules_with_backoff(session):
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    state = await fail(session, job_id, "boom")
    assert state == "pending"
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.last_error == "boom"
    # Compare against the database clock, never this process's clock: the two can
    # drift, and the queue is defined entirely in terms of the database's now().
    db_now = (await session.execute(select(func.now()))).scalar_one()
    assert job.run_after > db_now + timedelta(seconds=5)


async def test_a_job_enqueued_without_delay_is_immediately_claimable(session):
    # Regression guard for app/database clock skew: with a client-side timestamp
    # and a database clock running behind, this job would not be due yet.
    await enqueue(session, "process_item", {"rating_key": "now"})
    assert await claim(session, "worker-a") is not None


async def _make_due_now(session, job_id: int) -> None:
    """Reset a job to pending and due, using the database clock."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.state = "pending"
    job.run_after = (await session.execute(select(func.now()))).scalar_one()
    await session.commit()


async def test_job_parks_after_max_attempts(session):
    job_id = await enqueue(session, "process_item", {})
    for _ in range(MAX_ATTEMPTS - 1):
        await _make_due_now(session, job_id)
        await claim(session, "worker-a")
        assert await fail(session, job_id, "boom") == "pending"
    await _make_due_now(session, job_id)
    await claim(session, "worker-a")
    assert await fail(session, job_id, "boom") == "parked"


async def test_complete_marks_done(session):
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    await complete(session, job_id)
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job.state == "done"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_queue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.queue.jobs'`

- [ ] **Step 3: Implement the queue**

Create an empty `src/autoposter/queue/__init__.py`, then `src/autoposter/queue/jobs.py`:

```python
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import Job

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30

_CLAIM_SQL = text(
    """
    UPDATE jobs
       SET state = 'running',
           claimed_by = :worker,
           claimed_at = now(),
           attempts = attempts + 1,
           updated_at = now()
     WHERE id = (
           SELECT id
             FROM jobs
            WHERE state = 'pending'
              AND run_after <= now()
            ORDER BY run_after, id
              FOR UPDATE SKIP LOCKED
            LIMIT 1
     )
    RETURNING id
    """
)


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict,
    dedupe_key: str | None = None,
    delay_seconds: int = 0,
) -> int | None:
    """Add a job to the queue.

    When ``dedupe_key`` matches a job that is already pending, nothing is inserted
    and ``None`` is returned — that is the debounce for webhook bursts. A burst of
    events therefore produces a single pass whose delay is measured from the first
    event, which bounds latency instead of postponing work indefinitely.
    """
    # run_after is computed by Postgres, not by this process. claim() compares it
    # against the database's now(), and an app clock that drifts from the database
    # clock would otherwise make jobs run early or late by the size of the drift.
    run_after = func.now() + func.make_interval(0, 0, 0, 0, 0, 0, delay_seconds)
    stmt = insert(Job).values(
        kind=kind, payload=payload, dedupe_key=dedupe_key, run_after=run_after
    )
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["dedupe_key"], index_where=text("state = 'pending'")
        )
    result = await session.execute(stmt.returning(Job.id))
    job_id = result.scalar_one_or_none()
    await session.commit()
    return job_id


async def claim(session: AsyncSession, worker_id: str) -> Job | None:
    """Atomically take the next due job. Concurrent callers never collide."""
    result = await session.execute(_CLAIM_SQL, {"worker": worker_id})
    job_id = result.scalar_one_or_none()
    await session.commit()
    if job_id is None:
        return None
    return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def complete(session: AsyncSession, job_id: int) -> None:
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.state = "done"
    job.last_error = None
    await session.commit()


async def fail(session: AsyncSession, job_id: int, error: str) -> str:
    """Reschedule with exponential backoff, or park once attempts are exhausted."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.last_error = error
    if job.attempts >= MAX_ATTEMPTS:
        job.state = "parked"
    else:
        job.state = "pending"
        backoff = BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1))
        # Database clock again, for the same reason as enqueue().
        job.run_after = func.now() + func.make_interval(0, 0, 0, 0, 0, 0, backoff)
    await session.commit()
    return job.state
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_queue.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoposter/queue tests/test_queue.py
git commit -m "feat: durable postgres job queue with coalescing and backoff"
```

---

## Task 4: Radarr and Sonarr payload parsing

**Files:**
- Create: `src/autoposter/intake/__init__.py`, `src/autoposter/intake/arr.py`
- Create: `tests/fixtures/webhooks/*.json` (9 files, listed below)
- Test: `tests/test_arr.py`

**Interfaces:**
- Consumes: nothing (pure functions, no I/O, no database).
- Produces:
  - `RenderIntent` frozen dataclass with fields `kind: str`, `title: str`, `tmdb_id: int | None`, `tvdb_id: int | None`, `imdb_id: str | None`, `year: int | None`, `season_number: int | None`, `episode_number: int | None`, and property `dedupe_key: str`.
  - `parse_radarr(payload: dict) -> list[RenderIntent]`
  - `parse_sonarr(payload: dict) -> list[RenderIntent]`

**Why this task is pure:** every quirk below came from reading the Radarr/Sonarr webhook payload classes, and each is a real failure mode. Keeping parsing free of I/O means all of them are covered by fast tests.

Quirks that MUST be handled:

1. `eventType` is **PascalCase** (`"Download"`, `"Rename"`, `"MovieAdded"`, `"SeriesAdd"`, `"Test"`) while every other key is camelCase. Accept case-insensitively.
2. **Null fields are omitted, not sent as `null`** — `imdbId`, `mediaInfo`, `airDate` may simply be absent. Use `.get()` everywhere.
3. Zero-valued ints **are** present. Radarr's Test payload has `movie.tmdbId == 0`; treat `0` as absent.
4. **Test payloads are synthetic and must never reach ingest** — return an empty list.
5. Sonarr sends `eventType: "Download"` for **two structurally different payloads**: per-file import has `episodeFile` (singular), import-complete has `episodeFiles` (plural) plus `fileCount`. Both carry the `episodes` array, so parsing keys off `episodes` and works for both.
6. **Sonarr's Rename payload has no `episodes` array** — only `series` and `renamedEpisodeFiles`. It yields a show intent only.
7. `episodes` is an array; a season-pack import-complete carries every episode at once. Emit one episode intent each, plus one **de-duplicated** season intent per distinct season number.
8. Episode number key is `episodeNumber` (camelCase).
9. Radarr's `movie.filePath` never appears — the path is `movieFile.path`.

- [ ] **Step 1: Create the webhook fixtures**

Create these seven files under `tests/fixtures/webhooks/`. They are trimmed to the fields the parser reads, with exact key names.

`radarr_download.json`:

```json
{
  "eventType": "Download",
  "instanceName": "Radarr",
  "movie": {
    "id": 412,
    "title": "Dune: Part Two",
    "year": 2024,
    "folderPath": "/data/media/movies/Dune Part Two (2024)",
    "tmdbId": 693134,
    "imdbId": "tt15239678"
  },
  "movieFile": {
    "id": 5501,
    "relativePath": "Dune Part Two (2024) Bluray-2160p.mkv",
    "path": "/data/media/movies/Dune Part Two (2024)/Dune Part Two (2024) Bluray-2160p.mkv",
    "quality": "Bluray-2160p"
  },
  "isUpgrade": false
}
```

`radarr_rename.json`:

```json
{
  "eventType": "Rename",
  "instanceName": "Radarr",
  "movie": {
    "id": 412,
    "title": "Dune: Part Two",
    "year": 2024,
    "folderPath": "/data/media/movies/Dune Part Two (2024)",
    "tmdbId": 693134,
    "imdbId": "tt15239678"
  },
  "renamedMovieFiles": [
    {
      "id": 5501,
      "path": "/data/media/movies/Dune Part Two (2024)/Dune Part Two (2024) Bluray-2160p.mkv",
      "previousPath": "/data/media/movies/Dune Part Two (2024)/Dune.Part.Two.2024.mkv"
    }
  ]
}
```

`radarr_movie_added.json`:

```json
{
  "eventType": "MovieAdded",
  "instanceName": "Radarr",
  "movie": {
    "id": 413,
    "title": "The Substance",
    "year": 2024,
    "folderPath": "/data/media/movies/The Substance (2024)",
    "tmdbId": 933260,
    "imdbId": "tt17526714"
  },
  "addMethod": "manual"
}
```

`radarr_test.json`:

```json
{
  "eventType": "Test",
  "instanceName": "Radarr",
  "movie": {
    "id": 1,
    "title": "Test Title",
    "year": 1970,
    "folderPath": "C:\\testpath",
    "tmdbId": 0,
    "tags": ["test-tag"]
  },
  "remoteMovie": {
    "tmdbId": 1234,
    "imdbId": "5678",
    "title": "Test title",
    "year": 1970
  }
}
```

`sonarr_download_single.json`:

```json
{
  "eventType": "Download",
  "instanceName": "Sonarr",
  "series": {
    "id": 88,
    "title": "Severance",
    "path": "/data/media/tv/Severance",
    "tvdbId": 371980,
    "tmdbId": 95396,
    "imdbId": "tt11280740",
    "type": "standard",
    "year": 2022
  },
  "episodes": [
    {
      "id": 10241,
      "episodeNumber": 3,
      "seasonNumber": 2,
      "title": "Who Is Alive?",
      "airDate": "2025-01-31"
    }
  ],
  "episodeFile": {
    "id": 20304,
    "relativePath": "Season 02/Severance - S02E03 - Who Is Alive WEBDL-1080p.mkv",
    "path": "/data/media/tv/Severance/Season 02/Severance - S02E03 - Who Is Alive WEBDL-1080p.mkv",
    "quality": "WEBDL-1080p"
  },
  "isUpgrade": false
}
```

`sonarr_import_complete_seasonpack.json`:

```json
{
  "eventType": "Download",
  "instanceName": "Sonarr",
  "series": {
    "id": 88,
    "title": "Severance",
    "path": "/data/media/tv/Severance",
    "tvdbId": 371980,
    "tmdbId": 95396,
    "imdbId": "tt11280740",
    "type": "standard",
    "year": 2022
  },
  "episodes": [
    { "id": 10239, "episodeNumber": 1, "seasonNumber": 2, "title": "Hello, Ms. Cobel" },
    { "id": 10240, "episodeNumber": 2, "seasonNumber": 2, "title": "Goodbye, Mrs. Selvig" },
    { "id": 10250, "episodeNumber": 1, "seasonNumber": 3, "title": "Cold Harbor" }
  ],
  "episodeFiles": [
    { "id": 20302, "path": "/data/media/tv/Severance/Season 02/S02E01.mkv", "quality": "WEBDL-1080p" },
    { "id": 20303, "path": "/data/media/tv/Severance/Season 02/S02E02.mkv", "quality": "WEBDL-1080p" },
    { "id": 20310, "path": "/data/media/tv/Severance/Season 03/S03E01.mkv", "quality": "WEBDL-1080p" }
  ],
  "fileCount": 3,
  "destinationPath": "/data/media/tv/Severance/Season 02",
  "release": { "releaseType": "seasonPack" }
}
```

`sonarr_rename.json`:

```json
{
  "eventType": "Rename",
  "instanceName": "Sonarr",
  "series": {
    "id": 88,
    "title": "Severance",
    "path": "/data/media/tv/Severance",
    "tvdbId": 371980,
    "tmdbId": 95396,
    "type": "standard",
    "year": 2022
  },
  "renamedEpisodeFiles": [
    {
      "id": 20302,
      "path": "/data/media/tv/Severance/Season 02/Severance - S02E01 WEBDL-1080p.mkv",
      "previousPath": "/data/media/tv/Severance/Season 02/Severance.S02E01.1080p.mkv"
    }
  ]
}
```

`sonarr_series_add.json`:

```json
{
  "eventType": "SeriesAdd",
  "instanceName": "Sonarr",
  "series": {
    "id": 91,
    "title": "The Studio",
    "path": "/data/media/tv/The Studio",
    "tvdbId": 442446,
    "tmdbId": 240411,
    "imdbId": "tt29141112",
    "type": "standard",
    "year": 2025
  }
}
```

`sonarr_test.json`:

```json
{
  "eventType": "Test",
  "instanceName": "Sonarr",
  "series": {
    "id": 1,
    "title": "Test Title",
    "path": "C:\\testpath",
    "tvdbId": 1234,
    "tmdbId": 0,
    "type": "standard",
    "year": 0
  },
  "episodes": [
    { "id": 123, "episodeNumber": 1, "seasonNumber": 1, "title": "Test title" }
  ]
}
```

- [ ] **Step 2: Write the failing parser tests**

`tests/test_arr.py`:

```python
import json
from pathlib import Path

import pytest

from autoposter.intake.arr import RenderIntent, parse_radarr, parse_sonarr

FIXTURES = Path(__file__).parent / "fixtures" / "webhooks"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "fixture", ["radarr_download.json", "radarr_rename.json", "radarr_movie_added.json"]
)
def test_radarr_events_yield_one_movie_intent(fixture):
    intents = parse_radarr(load(fixture))
    assert len(intents) == 1
    assert intents[0].kind == "movie"
    assert intents[0].tmdb_id is not None


def test_radarr_download_extracts_identity():
    intent = parse_radarr(load("radarr_download.json"))[0]
    assert intent.tmdb_id == 693134
    assert intent.imdb_id == "tt15239678"
    assert intent.title == "Dune: Part Two"
    assert intent.year == 2024


def test_radarr_test_payload_is_ignored():
    assert parse_radarr(load("radarr_test.json")) == []


def test_sonarr_test_payload_is_ignored():
    assert parse_sonarr(load("sonarr_test.json")) == []


def test_sonarr_episode_import_fans_out_to_show_season_and_episode():
    intents = parse_sonarr(load("sonarr_download_single.json"))
    kinds = [i.kind for i in intents]
    assert kinds == ["show", "season", "episode"]
    show, season, episode = intents
    assert show.tvdb_id == 371980
    assert season.season_number == 2
    assert episode.season_number == 2
    assert episode.episode_number == 3
    assert episode.title == "Who Is Alive?"


def test_season_pack_emits_one_season_intent_per_distinct_season():
    intents = parse_sonarr(load("sonarr_import_complete_seasonpack.json"))
    seasons = [i.season_number for i in intents if i.kind == "season"]
    episodes = [(i.season_number, i.episode_number) for i in intents if i.kind == "episode"]
    assert sorted(seasons) == [2, 3]
    assert sorted(episodes) == [(2, 1), (2, 2), (3, 1)]
    assert len([i for i in intents if i.kind == "show"]) == 1


def test_sonarr_rename_has_no_episodes_and_yields_show_only():
    intents = parse_sonarr(load("sonarr_rename.json"))
    assert [i.kind for i in intents] == ["show"]


def test_sonarr_series_add_yields_show_only():
    intents = parse_sonarr(load("sonarr_series_add.json"))
    assert [i.kind for i in intents] == ["show"]
    assert intents[0].title == "The Studio"


def test_zero_ids_are_treated_as_missing():
    payload = load("sonarr_series_add.json")
    payload["series"]["tmdbId"] = 0
    assert parse_sonarr(payload)[0].tmdb_id is None


def test_missing_optional_keys_do_not_raise():
    payload = load("sonarr_download_single.json")
    del payload["series"]["imdbId"]
    del payload["episodes"][0]["airDate"]
    intents = parse_sonarr(payload)
    assert intents[0].imdb_id is None


def test_event_type_is_matched_case_insensitively():
    payload = load("radarr_download.json")
    payload["eventType"] = "download"
    assert len(parse_radarr(payload)) == 1


def test_unknown_event_types_are_ignored():
    payload = load("radarr_download.json")
    payload["eventType"] = "HealthRestored"
    assert parse_radarr(payload) == []


def test_dedupe_keys_are_stable_and_distinct():
    first = [i.dedupe_key for i in parse_sonarr(load("sonarr_download_single.json"))]
    second = [i.dedupe_key for i in parse_sonarr(load("sonarr_download_single.json"))]
    assert first == second
    assert len(set(first)) == 3
    assert "s02e03" in first[2]


def test_intent_is_hashable_for_set_deduplication():
    a = RenderIntent(kind="show", title="X", tvdb_id=1)
    b = RenderIntent(kind="show", title="X", tvdb_id=1)
    assert len({a, b}) == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_arr.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.intake.arr'`

- [ ] **Step 4: Implement the parser**

Create an empty `src/autoposter/intake/__init__.py`, then `src/autoposter/intake/arr.py`:

```python
from dataclasses import dataclass

RADARR_EVENTS = {"download", "rename", "movieadded"}
SONARR_EVENTS = {"download", "rename", "seriesadd"}


def _int_or_none(value: object) -> int | None:
    """Arr omits null fields but *emits* zeros. Treat 0 as absent."""
    if isinstance(value, int) and value > 0:
        return value
    return None


def _str_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


@dataclass(frozen=True)
class RenderIntent:
    """One item whose artwork may need rebuilding. Carries no Plex identity yet."""

    kind: str  # movie | show | season | episode
    title: str
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    year: int | None = None
    season_number: int | None = None
    episode_number: int | None = None

    @property
    def dedupe_key(self) -> str:
        """Stable queue key. External ids are used because the Plex rating key is
        not known until the job runs."""
        ident = (
            f"tmdb{self.tmdb_id}" if self.tmdb_id
            else f"tvdb{self.tvdb_id}" if self.tvdb_id
            else f"imdb{self.imdb_id}" if self.imdb_id
            else f"title{self.title.lower()}"
        )
        suffix = ""
        if self.season_number is not None:
            suffix = f":s{self.season_number:02d}"
            if self.episode_number is not None:
                suffix += f"e{self.episode_number:02d}"
        return f"process_item:{self.kind}:{ident}{suffix}"


def parse_radarr(payload: dict) -> list[RenderIntent]:
    """Radarr events all map to a single movie intent."""
    event = str(payload.get("eventType", "")).lower()
    if event not in RADARR_EVENTS:
        return []
    movie = payload.get("movie") or {}
    title = _str_or_none(movie.get("title"))
    if not title:
        return []
    return [
        RenderIntent(
            kind="movie",
            title=title,
            tmdb_id=_int_or_none(movie.get("tmdbId")),
            imdb_id=_str_or_none(movie.get("imdbId")),
            year=_int_or_none(movie.get("year")),
        )
    ]


def parse_sonarr(payload: dict) -> list[RenderIntent]:
    """A Sonarr event fans out to the show, each affected season, and each episode.

    A new episode can change season- and show-level artwork inputs, so all three
    levels are refreshed. Rename and SeriesAdd carry no ``episodes`` array and
    therefore yield only the show.
    """
    event = str(payload.get("eventType", "")).lower()
    if event not in SONARR_EVENTS:
        return []
    series = payload.get("series") or {}
    title = _str_or_none(series.get("title"))
    if not title:
        return []

    tvdb_id = _int_or_none(series.get("tvdbId"))
    tmdb_id = _int_or_none(series.get("tmdbId"))
    imdb_id = _str_or_none(series.get("imdbId"))
    year = _int_or_none(series.get("year"))

    intents = [
        RenderIntent(
            kind="show", title=title, tvdb_id=tvdb_id,
            tmdb_id=tmdb_id, imdb_id=imdb_id, year=year,
        )
    ]

    episodes = payload.get("episodes") or []
    seen_seasons: set[int] = set()
    episode_intents: list[RenderIntent] = []
    for episode in episodes:
        season_number = episode.get("seasonNumber")
        episode_number = episode.get("episodeNumber")
        if season_number is None or episode_number is None:
            continue
        if season_number not in seen_seasons:
            seen_seasons.add(season_number)
            intents.append(
                RenderIntent(
                    kind="season", title=title, tvdb_id=tvdb_id, tmdb_id=tmdb_id,
                    imdb_id=imdb_id, year=year, season_number=season_number,
                )
            )
        episode_intents.append(
            RenderIntent(
                kind="episode",
                title=_str_or_none(episode.get("title")) or title,
                tvdb_id=tvdb_id, tmdb_id=tmdb_id, imdb_id=imdb_id, year=year,
                season_number=season_number, episode_number=episode_number,
            )
        )
    intents.extend(episode_intents)
    return intents
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_arr.py -v`
Expected: PASS — 15 passed

- [ ] **Step 6: Commit**

```bash
git add src/autoposter/intake tests/test_arr.py tests/fixtures/webhooks
git commit -m "feat: parse Radarr and Sonarr webhooks into render intents"
```

---

## Task 5: Webhook endpoints

**Files:**
- Create: `src/autoposter/intake/routes.py`, `src/autoposter/app.py`
- Test: `tests/test_routes.py`

**Interfaces:**
- Consumes: `parse_radarr` / `parse_sonarr` / `RenderIntent` (Task 4), `enqueue` (Task 3), `EventLog` (Task 2), `Config` (Task 1).
- Produces: `create_app(config: Config, session_factory, secrets) -> FastAPI` exposing `POST /webhook/radarr`, `POST /webhook/sonarr`, and `GET /healthz`. Task 14 extends this app; it does not replace it.

**Authentication:** Radarr and Sonarr sign nothing — there is no HMAC, no timestamp, no nonce. They do send arbitrary custom headers, so the receiver requires a shared secret in `X-Autoposter-Token` compared with `secrets.compare_digest`. Requests without it get 401. Deliver a 2xx quickly: delivery is fire-and-forget with no retry queue, and the Arr notification thread blocks on the response.

- [ ] **Step 1: Write the failing endpoint tests**

`tests/test_routes.py`:

```python
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog, Job

FIXTURES = Path(__file__).parent / "fixtures" / "webhooks"
EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
TOKEN = "test-secret"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def secrets():
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret=TOKEN,
    )


@pytest_asyncio.fixture
async def client(session_factory, secrets):
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz_is_open(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_missing_token_is_rejected(client):
    response = await client.post("/webhook/radarr", json=load("radarr_download.json"))
    assert response.status_code == 401


async def test_wrong_token_is_rejected(client):
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": "nope"},
    )
    assert response.status_code == 401


async def test_radarr_download_enqueues_one_job(client, session):
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["queued"] == 1
    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].kind == "process_item"
    assert jobs[0].payload["tmdb_id"] == 693134


async def test_sonarr_episode_enqueues_three_jobs(client, session):
    response = await client.post(
        "/webhook/sonarr",
        json=load("sonarr_download_single.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.json()["queued"] == 3
    jobs = (await session.execute(select(Job))).scalars().all()
    assert {j.payload["kind"] for j in jobs} == {"show", "season", "episode"}


async def test_repeated_delivery_is_coalesced(client, session):
    payload = load("sonarr_download_single.json")
    headers = {"X-Autoposter-Token": TOKEN}
    first = await client.post("/webhook/sonarr", json=payload, headers=headers)
    second = await client.post("/webhook/sonarr", json=payload, headers=headers)
    assert first.json()["queued"] == 3
    assert second.json()["queued"] == 0
    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 3


async def test_test_payload_is_acknowledged_without_queueing(client, session):
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_test.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["queued"] == 0
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_every_delivery_is_logged(client, session):
    await client.post(
        "/webhook/radarr",
        json=load("radarr_test.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert events[0].event_type == "Test"


async def test_jobs_are_delayed_by_the_settle_window(client, session):
    await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    from datetime import datetime, timedelta, timezone

    job = (await session.execute(select(Job))).scalar_one()
    assert job.run_after > datetime.now(timezone.utc) + timedelta(seconds=20)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.app'`

- [ ] **Step 3: Implement the routes**

`src/autoposter/intake/routes.py`:

```python
import secrets as secrets_module
from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException, Request

from autoposter.db.models import EventLog
from autoposter.intake.arr import RenderIntent, parse_radarr, parse_sonarr
from autoposter.queue.jobs import enqueue

router = APIRouter()


def _authorise(request: Request, token: str | None) -> None:
    expected = request.app.state.secrets.webhook_secret
    if not token or not secrets_module.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-Autoposter-Token")


async def _ingest(request: Request, payload: dict, source: str, parser) -> dict:
    config = request.app.state.config
    session_factory = request.app.state.session_factory
    intents: list[RenderIntent] = parser(payload)

    queued = 0
    async with session_factory() as session:
        session.add(
            EventLog(
                source=source,
                event_type=payload.get("eventType"),
                payload=payload,
                outcome=f"{len(intents)} intents",
            )
        )
        await session.commit()

        for intent in intents:
            job_id = await enqueue(
                session,
                kind="process_item",
                payload=asdict(intent),
                dedupe_key=intent.dedupe_key,
                delay_seconds=config.settle_seconds,
            )
            if job_id is not None:
                queued += 1

    return {"intents": len(intents), "queued": queued}


@router.post("/webhook/radarr")
async def radarr_webhook(
    request: Request, x_autoposter_token: str | None = Header(default=None)
) -> dict:
    _authorise(request, x_autoposter_token)
    return await _ingest(request, await request.json(), "radarr", parse_radarr)


@router.post("/webhook/sonarr")
async def sonarr_webhook(
    request: Request, x_autoposter_token: str | None = Header(default=None)
) -> dict:
    _authorise(request, x_autoposter_token)
    return await _ingest(request, await request.json(), "sonarr", parse_sonarr)


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
```

`src/autoposter/app.py`:

```python
from fastapi import FastAPI

from autoposter.config.schema import Config, Secrets
from autoposter.intake.routes import router


def create_app(config: Config, session_factory, secrets: Secrets) -> FastAPI:
    app = FastAPI(title="autoposter")
    app.state.config = config
    app.state.session_factory = session_factory
    app.state.secrets = secrets
    app.include_router(router)
    return app
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_routes.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoposter/app.py src/autoposter/intake/routes.py tests/test_routes.py
git commit -m "feat: authenticated Radarr and Sonarr webhook endpoints"
```

---

## Task 6: Asset path naming

**Files:**
- Create: `src/autoposter/render/__init__.py`, `src/autoposter/render/naming.py`
- Create: `scripts/verify_asset_paths.py`
- Test: `tests/test_naming.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces:
  - `derive_root_folder(library_root: str, media_path: str, is_directory: bool) -> str`
  - `asset_path(config: Config, library: str, root_folder: str, art_kind: str, season_number: int | None = None, episode_number: int | None = None) -> Path`
  - `season_asset_name(n: int) -> str`, `episode_asset_name(s: int, e: int) -> str`

**This task carries the highest cutover risk in Phase 1.** The `/assets` tree already holds roughly 16k files written by Posterizarr. If this function computes even slightly different paths, adoption will not recognise existing assets and the system will re-render the entire library. Step 5 verifies the function against the real tree before anything depends on it.

Rules, taken from Posterizarr v3.2.0 generation code:

- Layout with `library_folders: true` is `<assets_root>/<library>/<root_folder>/<name>.jpg`.
- `root_folder` is **the on-disk media folder basename**, not a synthesised "Title (Year)" string, and receives **no sanitisation** — `[tvdb-389597]` and other bracket characters survive into the path verbatim.
- File names: `poster.jpg`, `background.jpg`, `SeasonNN.jpg`, `SNNENN.jpg`. Capital `S` and `E`, no separator, zero-padded to a **minimum** of two digits — season 100 stays `Season100`, and `S01E100` is not truncated.
- Specials are season 0 → `Season00.jpg`.
- The extension is always `.jpg`, for every artifact type. There is no configurable output format.

- [ ] **Step 1: Write the failing naming tests**

`tests/test_naming.py`:

```python
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.render.naming import (
    asset_path,
    derive_root_folder,
    episode_asset_name,
    season_asset_name,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def test_movie_root_folder_is_the_containing_directory():
    assert derive_root_folder(
        "/mnt/Media/Movies",
        "/mnt/Media/Movies/Dune Part Two (2024)/Dune Part Two Bluray-2160p.mkv",
        is_directory=False,
    ) == "Dune Part Two (2024)"


def test_movie_in_a_nested_quality_folder_uses_its_own_folder():
    assert derive_root_folder(
        "/mnt/Media/Movies",
        "/mnt/Media/Movies/4K/Dune Part Two (2024)/movie.mkv",
        is_directory=False,
    ) == "Dune Part Two (2024)"


def test_show_root_folder_is_the_series_directory():
    assert derive_root_folder(
        "/mnt/Media/TV", "/mnt/Media/TV/Severance", is_directory=True
    ) == "Severance"


def test_brackets_are_preserved_verbatim():
    assert derive_root_folder(
        "/mnt/Media/TV", "/mnt/Media/TV/Solo Leveling (2024) [tvdb-389597]", is_directory=True
    ) == "Solo Leveling (2024) [tvdb-389597]"


def test_trailing_separator_on_library_root_is_tolerated():
    assert derive_root_folder(
        "/mnt/Media/Movies/", "/mnt/Media/Movies/Heat (1995)/heat.mkv", is_directory=False
    ) == "Heat (1995)"


def test_windows_separators_are_handled():
    assert derive_root_folder(
        "D:\\Media\\Movies", "D:\\Media\\Movies\\Heat (1995)\\heat.mkv", is_directory=False
    ) == "Heat (1995)"


def test_path_outside_the_library_root_raises():
    with pytest.raises(ValueError, match="not inside"):
        derive_root_folder("/mnt/Media/Movies", "/somewhere/else/file.mkv", is_directory=False)


def test_season_names_are_zero_padded_to_two_digits():
    assert season_asset_name(1) == "Season01.jpg"
    assert season_asset_name(0) == "Season00.jpg"
    assert season_asset_name(12) == "Season12.jpg"


def test_three_digit_seasons_are_not_truncated():
    assert season_asset_name(100) == "Season100.jpg"


def test_episode_names_use_capital_s_and_e():
    assert episode_asset_name(1, 1) == "S01E01.jpg"
    assert episode_asset_name(2, 3) == "S02E03.jpg"
    assert episode_asset_name(1, 100) == "S01E100.jpg"


def test_poster_path(config):
    assert asset_path(config, "Movies", "Dune Part Two (2024)", "poster") == Path(
        "/assets/Movies/Dune Part Two (2024)/poster.jpg"
    )


def test_background_path(config):
    assert asset_path(config, "TV Shows", "Severance", "background") == Path(
        "/assets/TV Shows/Severance/background.jpg"
    )


def test_season_poster_path(config):
    assert asset_path(config, "TV Shows", "Severance", "season_poster", season_number=2) == Path(
        "/assets/TV Shows/Severance/Season02.jpg"
    )


def test_title_card_path(config):
    assert asset_path(
        config, "TV Shows", "Severance", "title_card", season_number=2, episode_number=3
    ) == Path("/assets/TV Shows/Severance/S02E03.jpg")


def test_season_poster_without_season_number_raises(config):
    with pytest.raises(ValueError, match="season_number"):
        asset_path(config, "TV Shows", "Severance", "season_poster")


def test_title_card_without_episode_number_raises(config):
    with pytest.raises(ValueError, match="episode_number"):
        asset_path(config, "TV Shows", "Severance", "title_card", season_number=2)


def test_flat_layout_when_library_folders_is_false(config):
    config.library_folders = False
    assert asset_path(config, "TV Shows", "Severance", "season_poster", season_number=2) == Path(
        "/assets/Severance_Season02.jpg"
    )
    assert asset_path(config, "Movies", "Heat (1995)", "poster") == Path("/assets/Heat (1995).jpg")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_naming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.render.naming'`

- [ ] **Step 3: Implement naming**

Create an empty `src/autoposter/render/__init__.py`, then `src/autoposter/render/naming.py`:

```python
import re
from pathlib import Path

from autoposter.config.schema import Config

_SEPARATORS = re.compile(r"[\\/]+")

# Suffixes used by the flat layout (library_folders: false). The poster has no
# suffix — it is the bare item name.
_FLAT_SUFFIX = {"poster": "", "background": "_background"}


def _split(path: str) -> list[str]:
    return [segment for segment in _SEPARATORS.split(path) if segment]


def derive_root_folder(library_root: str, media_path: str, is_directory: bool) -> str:
    """Return the on-disk folder name an item's assets are filed under.

    For movies ``media_path`` is the media *file*, so the folder is its parent.
    For shows it is the series *directory*, which is the folder itself. The name
    is used verbatim: Posterizarr applies no sanitisation, so bracket characters
    such as ``[tvdb-389597]`` are part of the real asset path on disk.
    """
    root_segments = _split(library_root)
    path_segments = _split(media_path)
    if path_segments[: len(root_segments)] != root_segments:
        raise ValueError(f"{media_path!r} is not inside library root {library_root!r}")
    relative = path_segments[len(root_segments) :]
    if not relative:
        raise ValueError(f"{media_path!r} resolves to the library root itself")
    if is_directory:
        return relative[-1]
    if len(relative) < 2:
        raise ValueError(f"{media_path!r} has no containing folder below {library_root!r}")
    return relative[-2]


def season_asset_name(season_number: int) -> str:
    """``Season01.jpg``. Specials are season 0. Padding is a minimum, not a limit."""
    return f"Season{season_number:02d}.jpg"


def episode_asset_name(season_number: int, episode_number: int) -> str:
    """``S01E01.jpg``. Capital S and E, no separator."""
    return f"S{season_number:02d}E{episode_number:02d}.jpg"


def _file_name(art_kind: str, season_number: int | None, episode_number: int | None) -> str:
    if art_kind == "poster":
        return "poster.jpg"
    if art_kind == "background":
        return "background.jpg"
    if art_kind == "season_poster":
        if season_number is None:
            raise ValueError("season_poster requires season_number")
        return season_asset_name(season_number)
    if art_kind == "title_card":
        if season_number is None:
            raise ValueError("title_card requires season_number")
        if episode_number is None:
            raise ValueError("title_card requires episode_number")
        return episode_asset_name(season_number, episode_number)
    raise ValueError(f"unknown art_kind {art_kind!r}")


def asset_path(
    config: Config,
    library: str,
    root_folder: str,
    art_kind: str,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> Path:
    """Absolute path of one artifact inside the asset tree."""
    name = _file_name(art_kind, season_number, episode_number)
    if config.library_folders:
        return Path(config.assets_root) / library / root_folder / name
    if art_kind in _FLAT_SUFFIX:
        suffix = _FLAT_SUFFIX[art_kind]
        return Path(config.assets_root) / f"{root_folder}{suffix}.jpg"
    return Path(config.assets_root) / f"{root_folder}_{name}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_naming.py -v`
Expected: PASS — 17 passed

- [ ] **Step 5: Verify the derivation against the real asset tree**

This is the step that protects the cutover. `scripts/verify_asset_paths.py` walks the live `/assets` tree read-only and reports how many existing directories the naming function would reproduce.

```python
"""Read-only check that computed asset paths match the existing Posterizarr tree.

Usage:
    python scripts/verify_asset_paths.py /assets

Exits non-zero if any library directory contains files this code would not
address, which would mean adoption re-renders instead of reusing them.
"""
import re
import sys
from pathlib import Path

KNOWN = re.compile(r"^(poster|background|Season\d{2,}|S\d{2,}E\d{2,})\.jpg$")


def main(assets_root: str) -> int:
    root = Path(assets_root)
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2

    total = 0
    unexpected: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            continue
        total += 1
        if not KNOWN.match(path.name):
            unexpected.append(path)

    print(f"scanned {total} asset files under {root}")
    print(f"unrecognised names: {len(unexpected)}")
    for path in unexpected[:25]:
        print(f"  {path}")
    if len(unexpected) > 25:
        print(f"  ... and {len(unexpected) - 25} more")
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/assets"))
```

Run it against a copy or a read-only mount of the production tree:

```bash
python scripts/verify_asset_paths.py /assets
```

Expected: exit 0. If it reports unrecognised names, **stop and reconcile the naming rules before continuing** — every mismatch is an asset adoption would miss. Record whatever you find in the plan's notes; do not adjust the regex to paper over a real difference.

- [ ] **Step 6: Commit**

```bash
git add src/autoposter/render tests/test_naming.py scripts/verify_asset_paths.py
git commit -m "feat: Posterizarr-compatible asset path naming"
```

---

## Task 7: ImageMagick text fitting

**Files:**
- Create: `src/autoposter/render/textfit.py`
- Test: `tests/test_textfit.py`

**Interfaces:**
- Consumes: `TextStyle`, `Config` (Task 1).
- Produces:
  - `FitResult` frozen dataclass with `point_size: int` and `truncated: bool`.
  - `build_fit_argv(magick: str, font_path: str, style: TextStyle, text: str) -> list[str]`
  - `fit_point_size(magick: str, font_path: str, style: TextStyle, text: str) -> FitResult`
  - `prepare_text(text: str, style: TextStyle) -> str`

**How the fit works.** There is no binary search. ImageMagick's `caption:` auto-fits text when `-size` is supplied and `-pointsize` is omitted; the chosen size is read back through `%[caption:pointsize]` and then clamped to the configured range.

**Truncation is fatal, by design.** When the fitted size falls **below** `min_point_size`, Posterizarr sets a truncation flag, skips the text, **and skips writing the asset at all**. Replicate that: `FitResult.truncated` is `True` and the caller must abandon the render, recording status `truncated`. Emitting the asset anyway would produce posters the current system never would.

**Text preparation, in this exact order:**
1. Normalise smart quotes `„ " " " " "` to `'`, and remove backticks.
2. Apply `all_caps` with `str.upper()` when configured.

- [ ] **Step 1: Write the failing tests**

`tests/test_textfit.py`:

```python
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.config.schema import TextStyle
from autoposter.render.textfit import (
    FitResult,
    build_fit_argv,
    fit_point_size,
    prepare_text,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def style() -> TextStyle:
    return load_config(EXAMPLE).artwork.poster.text


def test_fit_argv_asks_magick_for_the_caption_point_size(style):
    argv = build_fit_argv("magick", "/fonts/Comfortaa-Medium.ttf", style, "DUNE")
    assert argv[0] == "magick"
    assert "-size" in argv
    assert argv[argv.index("-size") + 1] == "1200x485"
    assert "caption:DUNE" in argv
    assert "%[caption:pointsize]" in argv
    assert argv[-1] == "info:"
    # -pointsize must be absent, otherwise magick will not auto-fit.
    assert "-pointsize" not in argv


def test_fit_argv_passes_font_and_interline_spacing(style):
    argv = build_fit_argv("magick", "/fonts/Comfortaa-Medium.ttf", style, "DUNE")
    assert argv[argv.index("-font") + 1] == "/fonts/Comfortaa-Medium.ttf"
    assert argv[argv.index("-interline-spacing") + 1] == "0"


def test_all_caps_is_applied():
    style = TextStyle(
        min_point_size=10, max_point_size=100, max_width=100, max_height=100,
        text_offset="+0", all_caps=True,
    )
    assert prepare_text("Dune: Part Two", style) == "DUNE: PART TWO"


def test_all_caps_can_be_disabled():
    style = TextStyle(
        min_point_size=10, max_point_size=100, max_width=100, max_height=100,
        text_offset="+0", all_caps=False,
    )
    assert prepare_text("Dune: Part Two", style) == "Dune: Part Two"


def test_smart_quotes_are_normalised(style):
    assert prepare_text("\u201cHeat\u201d", style) == "'HEAT'"
    assert prepare_text("\u201aHeat\u2018", style) == "'HEAT'"


def test_backticks_are_removed(style):
    assert prepare_text("He`at", style) == "HEAT"


def test_result_is_clamped_to_the_maximum(style, monkeypatch):
    monkeypatch.setattr("autoposter.render.textfit._run", lambda argv: "999")
    result = fit_point_size("magick", "/f.ttf", style, "A")
    assert result == FitResult(point_size=250, truncated=False)


def test_result_below_the_minimum_marks_truncated(style, monkeypatch):
    monkeypatch.setattr("autoposter.render.textfit._run", lambda argv: "40")
    result = fit_point_size("magick", "/f.ttf", style, "A VERY LONG TITLE INDEED")
    assert result.truncated is True
    assert result.point_size == 83


def test_result_inside_the_range_is_used_as_is(style, monkeypatch):
    monkeypatch.setattr("autoposter.render.textfit._run", lambda argv: "140")
    assert fit_point_size("magick", "/f.ttf", style, "A") == FitResult(140, False)


def test_unparseable_magick_output_raises(style, monkeypatch):
    monkeypatch.setattr("autoposter.render.textfit._run", lambda argv: "not a number")
    with pytest.raises(RuntimeError, match="point size"):
        fit_point_size("magick", "/f.ttf", style, "A")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_textfit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.render.textfit'`

- [ ] **Step 3: Implement text fitting**

`src/autoposter/render/textfit.py`:

```python
import subprocess
from dataclasses import dataclass

from autoposter.config.schema import TextStyle

_QUOTE_TRANSLATION = str.maketrans(
    {
        "\u201e": "'",  # „
        "\u201c": "'",  # "
        "\u201d": "'",  # "
        "\u2018": "'",  # '
        "\u2019": "'",  # '
        "\u201a": "'",  # ‚
        "`": "",
    }
)


@dataclass(frozen=True)
class FitResult:
    point_size: int
    truncated: bool


def prepare_text(text: str, style: TextStyle) -> str:
    """Normalise quote characters, then apply all-caps."""
    cleaned = text.translate(_QUOTE_TRANSLATION)
    return cleaned.upper() if style.all_caps else cleaned


def build_fit_argv(magick: str, font_path: str, style: TextStyle, text: str) -> list[str]:
    """Argv that makes ImageMagick report the point size it would auto-fit to.

    ``-pointsize`` is deliberately absent: supplying ``-size`` without it is what
    triggers ``caption:`` auto-fitting.
    """
    return [
        magick,
        "-size", f"{style.max_width}x{style.max_height}",
        "-font", font_path,
        "-gravity", "center",
        "-fill", "black",
        "-interline-spacing", str(style.line_spacing),
        f"caption:{text}",
        "-format", "%[caption:pointsize]",
        "info:",
    ]


def _run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def fit_point_size(magick: str, font_path: str, style: TextStyle, text: str) -> FitResult:
    """Auto-fit the text, then clamp to the configured range.

    Falling below ``min_point_size`` means the title cannot be drawn legibly. The
    caller MUST abandon the render in that case — Posterizarr writes no file at
    all, and emitting one here would produce artwork the current system never
    would.
    """
    raw = _run(build_fit_argv(magick, font_path, style, text))
    try:
        fitted = int(float(raw))
    except ValueError as exc:
        raise RuntimeError(f"could not parse point size from magick output {raw!r}") from exc

    if fitted > style.max_point_size:
        return FitResult(point_size=style.max_point_size, truncated=False)
    if fitted < style.min_point_size:
        return FitResult(point_size=style.min_point_size, truncated=True)
    return FitResult(point_size=fitted, truncated=False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_textfit.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoposter/render/textfit.py tests/test_textfit.py
git commit -m "feat: ImageMagick caption point-size fitting with truncation semantics"
```

---

## Task 8: ImageMagick compositor

**Files:**
- Create: `src/autoposter/render/compositor.py`
- Test: `tests/test_compositor.py`

**Interfaces:**
- Consumes: `TextStyle`, `ArtKindConfig`, `Config` (Task 1); `FitResult` (Task 7).
- Produces:
  - `POSTER_SIZE = "2000x3000"`, `BACKGROUND_SIZE = "3840x2160"`
  - `build_stamp_argv(magick, image) -> list[str]`
  - `build_base_argv(magick, image, canvas, overlay_path, quality, add_border, border_color, border_width) -> list[str]`
  - `build_text_argv(magick, image, style, font_path, point_size, text, quality) -> list[str]`
  - `build_logo_argv(magick, image, logo_path, style, quality) -> list[str]`
  - `run(argv: list[str]) -> None`

**Argument construction rules, taken from Posterizarr v3.2.0:**

- Every pipeline starts by stamping `-set comment "created with posterizarr"` into the JPEG. That marker is how uploads later detect already-processed artwork; keep the exact string so existing tooling and any lingering Posterizarr install agree.
- Cover-fit is `-resize "<canvas>^" -gravity center -extent "<canvas>"`. The `^` makes both dimensions meet or exceed the target; `-extent` centre-crops the overshoot.
- The overlay is read as a **second image** and applied with `-composite`. The fade PNG is full-canvas, so it must exactly match the canvas size.
- Border is `-shave WxW` followed by `-bordercolor C -border W`, which preserves the canvas size. Unreachable with the current config (`add_border: false`) but implemented.
- Text is drawn inside a parenthesised group: `-size WxH -background none -gravity <g> caption:<text> -trim +repage -extent WxH`, then composited with `-gravity <g> -geometry +0<offset>`. `-trim +repage -extent` re-flushes the text block against the box edge so the offset measures from the text, not from stray transparent margin.
- **The offset string already carries its sign** and is concatenated directly after `+0`, producing `+0+300`. Posterizarr's logo branch emits a malformed `+0++300`; per the Global Constraints table we emit the correct single-sign form.
- `(` and `)` are separate argv entries — they are ImageMagick grouping tokens, not shell syntax.
- `-quality 92%` accompanies every `-composite`.

- [ ] **Step 1: Write the failing tests**

`tests/test_compositor.py`:

```python
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.render.compositor import (
    BACKGROUND_SIZE,
    POSTER_SIZE,
    build_base_argv,
    build_logo_argv,
    build_stamp_argv,
    build_text_argv,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def test_canvas_sizes_are_fixed():
    assert POSTER_SIZE == "2000x3000"
    assert BACKGROUND_SIZE == "3840x2160"


def test_stamp_writes_the_posterizarr_comment():
    argv = build_stamp_argv("magick", "/tmp/x.jpg")
    assert argv == [
        "magick", "/tmp/x.jpg", "-set", "comment", "created with posterizarr", "/tmp/x.jpg"
    ]


def test_base_argv_cover_fits_then_composites_the_overlay():
    argv = build_base_argv(
        "magick", "/tmp/x.jpg", POSTER_SIZE, "/overlays/overlay.png", "92%",
        add_border=False, border_color="white", border_width=30,
    )
    assert argv[argv.index("-resize") + 1] == "2000x3000^"
    assert argv[argv.index("-extent") + 1] == "2000x3000"
    assert "/overlays/overlay.png" in argv
    assert "-composite" in argv
    assert argv[argv.index("-quality") + 1] == "92%"
    assert argv[0] == "magick"
    assert argv[-1] == "/tmp/x.jpg"


def test_base_argv_without_overlay_has_no_composite():
    argv = build_base_argv(
        "magick", "/tmp/x.jpg", POSTER_SIZE, None, "92%",
        add_border=False, border_color="white", border_width=30,
    )
    assert "-composite" not in argv
    assert "-resize" in argv


def test_border_shaves_then_borders_to_preserve_canvas_size():
    argv = build_base_argv(
        "magick", "/tmp/x.jpg", POSTER_SIZE, None, "92%",
        add_border=True, border_color="white", border_width=30,
    )
    assert argv[argv.index("-shave") + 1] == "30x30"
    assert argv[argv.index("-bordercolor") + 1] == "white"
    assert argv[argv.index("-border") + 1] == "30"
    assert argv.index("-shave") < argv.index("-border")


def test_text_argv_builds_a_parenthesised_caption_group(config):
    style = config.artwork.poster.text
    argv = build_text_argv(
        "magick", "/tmp/x.jpg", style, "/fonts/Comfortaa-Medium.ttf", 140, "DUNE", "92%"
    )
    assert "(" in argv and ")" in argv
    assert argv[argv.index("-pointsize") + 1] == "140"
    assert "caption:DUNE" in argv
    assert "-trim" in argv
    assert "+repage" in argv
    assert argv[argv.index("-fill") + 1] == "white"


def test_text_offset_is_concatenated_after_plus_zero(config):
    style = config.artwork.poster.text
    argv = build_text_argv(
        "magick", "/tmp/x.jpg", style, "/f.ttf", 140, "DUNE", "92%"
    )
    assert argv[argv.index("-geometry") + 1] == "+0+300"


def test_negative_text_offset_is_preserved(config):
    style = config.artwork.title_card.text  # text_offset is "-150"
    argv = build_text_argv("magick", "/tmp/x.jpg", style, "/f.ttf", 100, "EP", "92%")
    assert argv[argv.index("-geometry") + 1] == "+0-150"


def test_stroke_draws_two_captions_when_enabled(config):
    style = config.artwork.poster.text.model_copy(update={"add_stroke": True})
    argv = build_text_argv("magick", "/tmp/x.jpg", style, "/f.ttf", 140, "DUNE", "92%")
    assert argv.count("caption:DUNE") == 2
    assert argv[argv.index("-strokewidth") + 1] == "6"


def test_no_stroke_draws_one_caption(config):
    argv = build_text_argv(
        "magick", "/tmp/x.jpg", config.artwork.poster.text, "/f.ttf", 140, "DUNE", "92%"
    )
    assert argv.count("caption:DUNE") == 1
    assert "-strokewidth" not in argv


def test_logo_argv_uses_a_single_sign_geometry(config):
    argv = build_logo_argv(
        "magick", "/tmp/x.jpg", "/tmp/logo.png", config.artwork.poster.text, "92%"
    )
    # Posterizarr emits a malformed "+0++300" here; we deliberately do not.
    assert argv[argv.index("-geometry") + 1] == "+0+300"
    assert argv[argv.index("-resize") + 1] == "1200x485"
    assert "/tmp/logo.png" in argv


def test_logo_argv_adds_density_for_svg(config):
    argv = build_logo_argv(
        "magick", "/tmp/x.jpg", "/tmp/logo.svg", config.artwork.poster.text, "92%"
    )
    assert argv[argv.index("-density") + 1] == "300"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_compositor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.render.compositor'`

- [ ] **Step 3: Implement the compositor**

`src/autoposter/render/compositor.py`:

```python
import subprocess

from autoposter.config.schema import TextStyle

POSTER_SIZE = "2000x3000"
BACKGROUND_SIZE = "3840x2160"

# Provenance marker. Posterizarr writes this exact string and later greps for it
# to decide whether artwork has already been processed. Keep it byte-identical.
PROVENANCE_COMMENT = "created with posterizarr"


def build_stamp_argv(magick: str, image: str) -> list[str]:
    """Stamp the provenance comment. Always the first operation on an asset."""
    return [magick, image, "-set", "comment", PROVENANCE_COMMENT, image]


def build_base_argv(
    magick: str,
    image: str,
    canvas: str,
    overlay_path: str | None,
    quality: str,
    add_border: bool,
    border_color: str,
    border_width: int,
) -> list[str]:
    """Cover-fit the source art to the canvas, then optionally overlay and border.

    ``-resize <canvas>^`` scales so both dimensions meet or exceed the target;
    ``-gravity center -extent <canvas>`` centre-crops the overshoot.
    ``-shave`` before ``-border`` keeps the final size exactly ``canvas``.
    """
    argv = [magick, image, "-resize", f"{canvas}^", "-gravity", "center", "-extent", canvas]
    if overlay_path:
        argv += [overlay_path, "-gravity", "south", "-quality", quality, "-composite"]
    if add_border:
        argv += [
            "-shave", f"{border_width}x{border_width}",
            "-bordercolor", border_color,
            "-border", str(border_width),
        ]
    argv.append(image)
    return argv


def _caption_group(style: TextStyle, font_path: str, point_size: int, text: str) -> list[str]:
    box = f"{style.max_width}x{style.max_height}"
    common = [
        "-font", font_path,
        "-pointsize", str(point_size),
        "-size", box,
        "-background", "none",
        "-interline-spacing", str(style.line_spacing),
        "-gravity", style.gravity,
    ]
    if not style.add_stroke:
        return (
            ["("] + common + ["-fill", style.font_color, f"caption:{text}",
                              "-trim", "+repage", "-extent", box] + [")"]
        )
    # Stroke first, fill second: in a two-image list -composite puts image[1] over
    # image[0], so the stroked copy sits behind.
    stroke = ["("] + common + [
        "-fill", style.stroke_color,
        "-stroke", style.stroke_color,
        "-strokewidth", str(style.stroke_width),
        f"caption:{text}",
    ] + [")"]
    fill = ["("] + common + [
        "-fill", style.font_color, "-stroke", "none", f"caption:{text}",
    ] + [")"]
    return (
        ["(", "-size", box, "-background", "none"]
        + stroke
        + fill
        + ["-gravity", style.gravity, "-composite", "-trim", "+repage", "-extent", box, ")"]
    )


def build_text_argv(
    magick: str,
    image: str,
    style: TextStyle,
    font_path: str,
    point_size: int,
    text: str,
    quality: str,
) -> list[str]:
    """Draw one text block onto the image.

    ``style.text_offset`` already carries its sign and is concatenated after
    ``+0`` to form the geometry, e.g. ``+0+300``.
    """
    return (
        [magick, image, "-gravity", "center", "-background", "None", "-layers", "Flatten"]
        + _caption_group(style, font_path, point_size, text)
        + [
            "-gravity", style.gravity,
            "-geometry", f"+0{style.text_offset}",
            "-quality", quality,
            "-composite",
            image,
        ]
    )


def build_logo_argv(
    magick: str, image: str, logo_path: str, style: TextStyle, quality: str
) -> list[str]:
    """Composite a clearlogo in place of the title text.

    The logo is fitted to the same box as the text block and placed at the same
    gravity and offset.
    """
    group = ["(", "-background", "none"]
    if logo_path.lower().endswith(".svg"):
        group += ["-density", "300"]
    group += [logo_path, "-resize", f"{style.max_width}x{style.max_height}", ")"]
    return (
        [magick, image]
        + group
        + [
            "-gravity", style.gravity,
            "-geometry", f"+0{style.text_offset}",
            "-quality", quality,
            "-composite",
            image,
        ]
    )


def run(argv: list[str]) -> None:
    """Execute a magick command, raising with stderr attached on failure."""
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"magick failed ({result.returncode}): {' '.join(argv)}\n{result.stderr.strip()}"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_compositor.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Add a golden-image test against real ImageMagick**

This is the check that "visually indistinguishable" is true rather than hoped for. Pick one movie whose current `/assets` poster you can copy, together with the textless source art it was built from, into `tests/fixtures/golden/`.

`tests/test_golden.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.render.compositor import (
    POSTER_SIZE, build_base_argv, build_stamp_argv, build_text_argv, run,
)
from autoposter.render.textfit import fit_point_size, prepare_text

GOLDEN = Path(__file__).parent / "fixtures" / "golden"
EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

pytestmark = pytest.mark.skipif(
    shutil.which("magick") is None or not GOLDEN.exists(),
    reason="requires ImageMagick and harvested golden fixtures",
)


def _rmse(a: Path, b: Path) -> float:
    """Root-mean-square difference between two images, as reported by magick."""
    result = subprocess.run(
        ["magick", "compare", "-metric", "RMSE", str(a), str(b), "null:"],
        capture_output=True, text=True,
    )
    # magick writes "1234.56 (0.0188)" to stderr; the parenthesised value is normalised.
    text = result.stderr.strip()
    return float(text.split("(")[1].rstrip(")"))


def test_poster_matches_the_posterizarr_reference(tmp_path):
    config = load_config(EXAMPLE)
    style = config.artwork.poster.text
    font = str(GOLDEN / "Comfortaa-Medium.ttf")
    overlay = str(GOLDEN / "overlay.png")

    working = tmp_path / "poster.jpg"
    shutil.copy(GOLDEN / "source_textless.jpg", working)

    title = prepare_text("DUNE: PART TWO", style)
    fit = fit_point_size(config.magick_binary, font, style, title)
    assert fit.truncated is False

    run(build_stamp_argv(config.magick_binary, str(working)))
    run(build_base_argv(
        config.magick_binary, str(working), POSTER_SIZE, overlay,
        config.artwork.output_quality, False, "white", 30,
    ))
    run(build_text_argv(
        config.magick_binary, str(working), style, font,
        fit.point_size, title, config.artwork.output_quality,
    ))

    assert _rmse(working, GOLDEN / "expected_poster.jpg") < 0.02
```

Harvest the fixtures before running:

```bash
mkdir -p tests/fixtures/golden
cp "/assets/Movies/Dune Part Two (2024)/poster.jpg" tests/fixtures/golden/expected_poster.jpg
cp /path/to/the/textless/source.jpg tests/fixtures/golden/source_textless.jpg
cp assets/overlays/overlay.png assets/fonts/Comfortaa-Medium.ttf tests/fixtures/golden/
pytest tests/test_golden.py -v
```

Expected: PASS. A normalised RMSE under 0.02 means the images are visually identical; JPEG re-encoding alone accounts for a small non-zero value. If it fails, compare the argv this code builds against the constraints table above before changing the tolerance — **do not loosen the threshold to make it pass**.

- [ ] **Step 6: Commit**

```bash
git add src/autoposter/render/compositor.py tests/test_compositor.py tests/test_golden.py
git commit -m "feat: ImageMagick compositor with golden-image parity test"
```

---

## Task 9: Artwork provider clients

**Files:**
- Create: `src/autoposter/providers/__init__.py`, `src/autoposter/providers/base.py`
- Create: `src/autoposter/providers/tmdb.py`, `src/autoposter/providers/tvdb.py`, `src/autoposter/providers/fanart.py`
- Create: `tests/fixtures/providers/*.json`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: `Secrets` (Task 1).
- Produces:
  - `ArtKind` string constants: `POSTER`, `BACKGROUND`, `SEASON_POSTER`, `TITLE_CARD`, `LOGO`.
  - `ArtCandidate` frozen dataclass: `provider: str`, `url: str`, `language: str | None`, `width: int | None`, `height: int | None`, `score: float`, `includes_text: bool | None`, and property `is_textless: bool`.
  - `parse_tmdb_images(payload: dict, art_kind: str) -> list[ArtCandidate]`
  - `parse_tvdb_artworks(payload: dict, art_kind: str, is_movie: bool) -> list[ArtCandidate]`
  - `parse_fanart(payload: dict, art_kind: str, is_movie: bool, season_number: int | None) -> list[ArtCandidate]`
  - `ArtRequest` frozen dataclass: `art_kind: str`, `is_movie: bool`, `tmdb_id`, `tvdb_id`, `imdb_id`, `season_number`, `episode_number`, `season_id` (all optional).
  - `TMDBClient`, `TVDBClient`, `FanartClient`, each with `name: str` and `async fetch(request: ArtRequest) -> list[ArtCandidate]`. **All three share this one signature** so the ladder can treat them interchangeably; a provider whose required identifier is absent returns `[]` rather than raising.

**Per-provider facts that drive the code.** Each provider expresses "textless" differently, and getting this wrong silently degrades every poster:

| Provider | Textless signal | Language format | Quality signal |
|---|---|---|---|
| TMDB | `iso_639_1` is `null` (also accept `""` and `"xx"` defensively) | ISO-639-1, two letters | `vote_average` + `vote_count` |
| TVDB | **`includesText: false`** — a real boolean, the only authoritative flag of the three | ISO-639-2/T, **three letters**: `eng`, `fin` | `score` |
| Fanart | `lang == "00"` (explicitly tagged textless) or `lang == ""` (never tagged) | two letters | `likes` (string integer) |

Other essentials:

- **TMDB auth:** the configured token is a JWT — that is a v4 API Read Access Token and must be sent as `Authorization: Bearer <token>`, never as `api_key`. It works against all `/3/...` endpoints.
- **TMDB query:** request `include_image_language=null,en,fi` — the literal four-character string `null` is how TMDB expresses "no language tag".
- **TMDB image URLs:** `https://image.tmdb.org/t/p/original` + `file_path`.
- **TMDB arrays by endpoint:** movie and series `/images` return `posters`/`backdrops`/`logos`; season `/images` returns `posters` only; episode `/images` returns `stills` only.
- **TVDB auth:** `POST /v4/login` with `{"apikey": ...}` returns a token valid for one month; send it as a bearer. Artwork URLs are already absolute.
- **TVDB artwork type ids:** 2 series poster, 3 series background, 7 season poster, 11 and 12 episode screencaps, 14 movie poster, 15 movie background, 23 series clearlogo, 25 movie clearlogo. Fetch `/artwork/types` and cache it for a week rather than trusting these forever.
- **TVDB language filtering must happen client-side.** Passing `lang=` to `/series/{id}/artworks` excludes the null-language entries, which are exactly the best candidates.
- **Fanart auth:** `?api_key=` query parameter. Base URL `https://webservice.fanart.tv`, version `v3.2` (adds `width`/`height`).
- **Fanart season art:** filter `seasonposter` on `season == str(n)`; `showbackground` entries with `season == "all"` are series-wide.
- **Fanart has no episode artwork at all, and TVDB gives only one image per episode** (`image` + `imageType` on the episode record, no artworks array). **TMDB `stills` is the only real source of title cards** — reflect that in the ladder rather than retrying dead ends.

- [ ] **Step 1: Create provider fixtures**

`tests/fixtures/providers/tmdb_movie_images.json`:

```json
{
  "id": 693134,
  "posters": [
    { "file_path": "/textless.jpg", "iso_639_1": null, "width": 2000, "height": 3000,
      "aspect_ratio": 0.667, "vote_average": 5.6, "vote_count": 40 },
    { "file_path": "/english.jpg", "iso_639_1": "en", "width": 2000, "height": 3000,
      "aspect_ratio": 0.667, "vote_average": 7.4, "vote_count": 120 },
    { "file_path": "/finnish.jpg", "iso_639_1": "fi", "width": 1000, "height": 1500,
      "aspect_ratio": 0.667, "vote_average": 5.0, "vote_count": 2 },
    { "file_path": "/german.jpg", "iso_639_1": "de", "width": 2000, "height": 3000,
      "aspect_ratio": 0.667, "vote_average": 9.9, "vote_count": 1 }
  ],
  "backdrops": [
    { "file_path": "/backdrop.jpg", "iso_639_1": null, "width": 3840, "height": 2160,
      "aspect_ratio": 1.778, "vote_average": 5.3, "vote_count": 12 }
  ],
  "logos": [
    { "file_path": "/logo.png", "iso_639_1": "en", "width": 800, "height": 310,
      "aspect_ratio": 2.58, "vote_average": 5.0, "vote_count": 3 }
  ]
}
```

`tests/fixtures/providers/tmdb_episode_images.json`:

```json
{
  "id": 10241,
  "stills": [
    { "file_path": "/still.jpg", "iso_639_1": null, "width": 3840, "height": 2160,
      "aspect_ratio": 1.778, "vote_average": 5.4, "vote_count": 3 }
  ]
}
```

`tests/fixtures/providers/tvdb_series_artworks.json`:

```json
{
  "data": {
    "artworks": [
      { "id": 63280427, "image": "https://artworks.thetvdb.com/banners/a.jpg",
        "language": "eng", "type": 2, "score": 100000,
        "width": 680, "height": 1000, "includesText": true },
      { "id": 63280428, "image": "https://artworks.thetvdb.com/banners/b.jpg",
        "language": null, "type": 2, "score": 90000,
        "width": 680, "height": 1000, "includesText": false },
      { "id": 63280429, "image": "https://artworks.thetvdb.com/banners/c.jpg",
        "language": "eng", "type": 2, "score": 120000,
        "width": 680, "height": 1000, "includesText": false },
      { "id": 63280430, "image": "https://artworks.thetvdb.com/banners/d.jpg",
        "language": "eng", "type": 3, "score": 50000,
        "width": 1920, "height": 1080, "includesText": false }
    ]
  }
}
```

`tests/fixtures/providers/fanart_tv.json`:

```json
{
  "name": "Severance",
  "thetvdb_id": "371980",
  "tvposter": [
    { "id": "180539", "url": "https://assets.fanart.tv/a.jpg", "lang": "en", "likes": "3" },
    { "id": "176503", "url": "https://assets.fanart.tv/b.jpg", "lang": "00", "likes": "7" }
  ],
  "showbackground": [
    { "id": "180543", "url": "https://assets.fanart.tv/c.jpg", "lang": "", "likes": "6",
      "season": "all" }
  ],
  "seasonposter": [
    { "id": "185928", "url": "https://assets.fanart.tv/s1.jpg", "lang": "00", "likes": "2",
      "season": "1" },
    { "id": "185929", "url": "https://assets.fanart.tv/s2.jpg", "lang": "en", "likes": "5",
      "season": "2" }
  ],
  "hdtvlogo": [
    { "id": "176500", "url": "http://assets.fanart.tv/logo.png", "lang": "en", "likes": "4" }
  ]
}
```

- [ ] **Step 2: Write the failing parser tests**

`tests/test_providers.py`:

```python
import json
from pathlib import Path

from autoposter.providers.base import BACKGROUND, LOGO, POSTER, SEASON_POSTER, TITLE_CARD
from autoposter.providers.fanart import parse_fanart
from autoposter.providers.tmdb import parse_tmdb_images
from autoposter.providers.tvdb import parse_tvdb_artworks

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_tmdb_posters_are_parsed():
    candidates = parse_tmdb_images(load("tmdb_movie_images.json"), POSTER)
    assert len(candidates) == 4
    assert all(c.provider == "TMDB" for c in candidates)
    assert candidates[0].url == "https://image.tmdb.org/t/p/original/textless.jpg"


def test_tmdb_null_language_is_textless():
    candidates = parse_tmdb_images(load("tmdb_movie_images.json"), POSTER)
    textless = [c for c in candidates if c.is_textless]
    assert len(textless) == 1
    assert textless[0].language is None


def test_tmdb_backdrops_and_logos_use_their_own_arrays():
    payload = load("tmdb_movie_images.json")
    assert len(parse_tmdb_images(payload, BACKGROUND)) == 1
    assert len(parse_tmdb_images(payload, LOGO)) == 1


def test_tmdb_title_cards_come_from_stills():
    candidates = parse_tmdb_images(load("tmdb_episode_images.json"), TITLE_CARD)
    assert len(candidates) == 1
    assert candidates[0].width == 3840


def test_tvdb_filters_by_artwork_type():
    payload = load("tvdb_series_artworks.json")
    posters = parse_tvdb_artworks(payload, POSTER, is_movie=False)
    backgrounds = parse_tvdb_artworks(payload, BACKGROUND, is_movie=False)
    assert len(posters) == 3
    assert len(backgrounds) == 1


def test_tvdb_uses_the_includes_text_flag_not_the_language():
    posters = parse_tvdb_artworks(load("tvdb_series_artworks.json"), POSTER, is_movie=False)
    # The English artwork with includesText false is genuinely textless.
    english_textless = [c for c in posters if c.language == "eng" and c.is_textless]
    assert len(english_textless) == 1
    assert english_textless[0].includes_text is False


def test_tvdb_urls_are_already_absolute():
    posters = parse_tvdb_artworks(load("tvdb_series_artworks.json"), POSTER, is_movie=False)
    assert all(c.url.startswith("https://artworks.thetvdb.com/") for c in posters)


def test_fanart_zero_zero_language_is_textless():
    candidates = parse_fanart(load("fanart_tv.json"), POSTER, is_movie=False, season_number=None)
    textless = [c for c in candidates if c.is_textless]
    assert len(textless) == 1
    assert textless[0].language == "00"


def test_fanart_empty_language_is_also_textless():
    candidates = parse_fanart(
        load("fanart_tv.json"), BACKGROUND, is_movie=False, season_number=None
    )
    assert candidates[0].is_textless is True


def test_fanart_season_posters_are_filtered_by_season():
    candidates = parse_fanart(
        load("fanart_tv.json"), SEASON_POSTER, is_movie=False, season_number=1
    )
    assert len(candidates) == 1
    assert candidates[0].url.endswith("s1.jpg")


def test_fanart_likes_become_the_score():
    candidates = parse_fanart(load("fanart_tv.json"), POSTER, is_movie=False, season_number=None)
    assert {c.score for c in candidates} == {3.0, 7.0}


def test_fanart_http_urls_are_upgraded_to_https():
    candidates = parse_fanart(load("fanart_tv.json"), LOGO, is_movie=False, season_number=None)
    assert candidates[0].url.startswith("https://")


def test_fanart_returns_empty_for_missing_art_type():
    assert parse_fanart({}, POSTER, is_movie=True, season_number=None) == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.providers.base'`

- [ ] **Step 4: Write the shared provider types**

Create an empty `src/autoposter/providers/__init__.py`, then `src/autoposter/providers/base.py`:

```python
from dataclasses import dataclass

POSTER = "poster"
BACKGROUND = "background"
SEASON_POSTER = "season_poster"
TITLE_CARD = "title_card"
LOGO = "logo"

# Values that mean "this image carries no language tag" across providers.
TEXTLESS_LANGUAGE_TOKENS = {None, "", "xx", "00", "null"}


@dataclass(frozen=True)
class ArtRequest:
    """What artwork is wanted, for one item.

    Every provider takes this same object so the ladder can call them
    interchangeably; each picks the identifiers it understands and returns an
    empty list when the ones it needs are missing.
    """

    art_kind: str
    is_movie: bool
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    season_id: int | None = None


@dataclass(frozen=True)
class ArtCandidate:
    """One artwork option from one provider, before cross-provider ranking."""

    provider: str
    url: str
    language: str | None
    width: int | None
    height: int | None
    score: float
    includes_text: bool | None = None

    @property
    def is_textless(self) -> bool:
        """TVDB states textlessness outright; the others only imply it.

        ``includes_text`` is authoritative when present — an English-tagged TVDB
        artwork with ``includesText: false`` really is textless, and is a better
        pick than an untagged image that happens to have burned-in text.
        """
        if self.includes_text is not None:
            return not self.includes_text
        return self.language in TEXTLESS_LANGUAGE_TOKENS
```

- [ ] **Step 5: Write the TMDB client**

`src/autoposter/providers/tmdb.py`:

```python
import httpx

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, TITLE_CARD, ArtCandidate, ArtRequest,
)

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/original"

# Which response array holds each artwork type.
_ARRAY_FOR_KIND = {
    POSTER: "posters",
    SEASON_POSTER: "posters",
    BACKGROUND: "backdrops",
    LOGO: "logos",
    TITLE_CARD: "stills",
}


def parse_tmdb_images(payload: dict, art_kind: str) -> list[ArtCandidate]:
    """Convert a TMDB /images response into candidates.

    ``iso_639_1`` is ``null`` for untagged images; that is TMDB's only proxy for
    textlessness — it has no explicit flag.
    """
    entries = payload.get(_ARRAY_FOR_KIND[art_kind]) or []
    candidates = []
    for entry in entries:
        file_path = entry.get("file_path")
        if not file_path:
            continue
        candidates.append(
            ArtCandidate(
                provider="TMDB",
                url=f"{IMAGE_BASE}{file_path}",
                language=entry.get("iso_639_1"),
                width=entry.get("width"),
                height=entry.get("height"),
                score=_tmdb_score(entry),
            )
        )
    return candidates


def _tmdb_score(entry: dict) -> float:
    """Shrink the average towards the mean so a 10.0 from one vote cannot win.

    Bayesian shrinkage with C=3 prior votes at m=5.0.
    """
    votes = entry.get("vote_count") or 0
    average = entry.get("vote_average") or 0.0
    prior_votes, prior_mean = 3, 5.0
    return (votes * average + prior_votes * prior_mean) / (votes + prior_votes)


class TMDBClient:
    """Reads artwork from TMDB. The configured token is a v4 read access token."""

    name = "TMDB"

    def __init__(self, token: str, language_order: list[str], client: httpx.AsyncClient):
        self._token = token
        self._language_order = language_order
        self._client = client

    def _params(self) -> dict:
        # "null" is the literal token TMDB uses for images with no language tag.
        codes = ["null" if code == "xx" else code for code in self._language_order]
        return {"include_image_language": ",".join(codes)}

    def _path(self, request: ArtRequest) -> str:
        if request.is_movie:
            return f"/movie/{request.tmdb_id}/images"
        if request.art_kind == TITLE_CARD:
            return (
                f"/tv/{request.tmdb_id}/season/{request.season_number}"
                f"/episode/{request.episode_number}/images"
            )
        if request.art_kind == SEASON_POSTER:
            return f"/tv/{request.tmdb_id}/season/{request.season_number}/images"
        return f"/tv/{request.tmdb_id}/images"

    async def fetch(self, request: ArtRequest) -> list[ArtCandidate]:
        if request.tmdb_id is None:
            return []
        path = self._path(request)
        response = await self._client.get(
            f"{BASE_URL}{path}",
            params=self._params(),
            headers={"Authorization": f"Bearer {self._token}", "accept": "application/json"},
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return parse_tmdb_images(response.json(), request.art_kind)
```

- [ ] **Step 6: Write the TVDB client**

`src/autoposter/providers/tvdb.py`:

```python
import httpx

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, TITLE_CARD, ArtCandidate, ArtRequest,
)

BASE_URL = "https://api4.thetvdb.com/v4"

# Artwork type ids from /artwork/types. Refresh that endpoint weekly rather than
# trusting these numbers indefinitely.
_TYPE_IDS = {
    (POSTER, False): {2},
    (POSTER, True): {14},
    (BACKGROUND, False): {3},
    (BACKGROUND, True): {15},
    (SEASON_POSTER, False): {7},
    (TITLE_CARD, False): {11, 12},
    (LOGO, False): {23},
    (LOGO, True): {25},
}


def parse_tvdb_artworks(payload: dict, art_kind: str, is_movie: bool) -> list[ArtCandidate]:
    """Convert a TVDB extended/artworks response into candidates.

    TVDB is the only provider with an explicit ``includesText`` boolean, so it is
    carried through rather than inferred from the language.
    """
    wanted = _TYPE_IDS.get((art_kind, is_movie), set())
    data = payload.get("data") or payload
    artworks = data.get("artworks") or data.get("artwork") or []
    candidates = []
    for entry in artworks:
        if entry.get("type") not in wanted:
            continue
        url = entry.get("image")
        if not url:
            continue
        candidates.append(
            ArtCandidate(
                provider="TVDB",
                url=url,
                language=entry.get("language"),
                width=entry.get("width"),
                height=entry.get("height"),
                score=float(entry.get("score") or 0),
                includes_text=entry.get("includesText"),
            )
        )
    return candidates


class TVDBClient:
    """Reads artwork from TVDB v4. Login tokens are valid for one month."""

    name = "TVDB"

    def __init__(self, apikey: str, client: httpx.AsyncClient):
        self._apikey = apikey
        self._client = client
        self._token: str | None = None

    async def _authenticate(self) -> str:
        if self._token:
            return self._token
        response = await self._client.post(f"{BASE_URL}/login", json={"apikey": self._apikey})
        response.raise_for_status()
        self._token = response.json()["data"]["token"]
        return self._token

    async def fetch(self, request: ArtRequest) -> list[ArtCandidate]:
        if request.tvdb_id is None:
            return []
        if request.art_kind == TITLE_CARD:
            # An episode record carries a single `image` field, not an artworks
            # array, so TVDB cannot serve title cards through this path.
            return []
        token = await self._authenticate()
        headers = {"Authorization": f"Bearer {token}"}
        if request.is_movie:
            path = f"/movies/{request.tvdb_id}/extended"
        elif request.art_kind == SEASON_POSTER:
            if request.season_id is None:
                return []
            path = f"/seasons/{request.season_id}/extended"
        else:
            # Fetched without a lang filter on purpose: passing lang= would drop the
            # null-language entries, which are the best textless candidates.
            path = f"/series/{request.tvdb_id}/artworks"
        response = await self._client.get(f"{BASE_URL}{path}", headers=headers)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return parse_tvdb_artworks(response.json(), request.art_kind, request.is_movie)
```

- [ ] **Step 7: Write the Fanart client**

`src/autoposter/providers/fanart.py`:

```python
import httpx

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, ArtCandidate, ArtRequest,
)

BASE_URL = "https://webservice.fanart.tv/v3.2"

# Response keys per artwork type. Fanart has no episode artwork at all.
# Artwork kinds Fanart can serve at all. Episode artwork is absent entirely.
_SUPPORTED_KINDS = {POSTER, BACKGROUND, SEASON_POSTER, LOGO}

_KEYS = {
    (POSTER, True): ["movieposter"],
    (POSTER, False): ["tvposter"],
    (BACKGROUND, True): ["moviebackground"],
    (BACKGROUND, False): ["showbackground"],
    (SEASON_POSTER, False): ["seasonposter"],
    (LOGO, True): ["hdmovielogo", "movielogo"],
    (LOGO, False): ["hdtvlogo", "clearlogo"],
}


def parse_fanart(
    payload: dict, art_kind: str, is_movie: bool, season_number: int | None
) -> list[ArtCandidate]:
    """Convert a Fanart.tv response into candidates.

    ``lang`` is ``"00"`` when a designer explicitly tagged the image textless and
    ``""`` when it was simply never tagged; both count as no-language. Every value
    in a Fanart response is a JSON string, including the numbers.
    """
    candidates = []
    for key in _KEYS.get((art_kind, is_movie), []):
        for entry in payload.get(key) or []:
            url = entry.get("url")
            if not url:
                continue
            if art_kind == SEASON_POSTER and season_number is not None:
                if str(entry.get("season")) != str(season_number):
                    continue
            candidates.append(
                ArtCandidate(
                    provider="Fanart",
                    url=url.replace("http://", "https://", 1),
                    language=entry.get("lang"),
                    width=_int_or_none(entry.get("width")),
                    height=_int_or_none(entry.get("height")),
                    score=float(entry.get("likes") or 0),
                )
            )
    return candidates


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class FanartClient:
    """Reads artwork from Fanart.tv. Authentication is a query parameter."""

    name = "Fanart"

    def __init__(self, apikey: str, client: httpx.AsyncClient):
        self._apikey = apikey
        self._client = client

    async def fetch(self, request: ArtRequest) -> list[ArtCandidate]:
        if request.art_kind not in _SUPPORTED_KINDS:
            return []
        external_id = request.tmdb_id if request.is_movie else request.tvdb_id
        if external_id is None:
            return []
        segment = "movies" if request.is_movie else "tv"
        response = await self._client.get(
            f"{BASE_URL}/{segment}/{external_id}", params={"api_key": self._apikey}
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return parse_fanart(
            response.json(), request.art_kind, request.is_movie, request.season_number
        )
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_providers.py -v`
Expected: PASS — 13 passed

- [ ] **Step 9: Commit**

```bash
git add src/autoposter/providers tests/test_providers.py tests/fixtures/providers
git commit -m "feat: TMDB, TVDB and Fanart artwork clients"
```

---

## Task 10: Cross-provider selection ladder

**Files:**
- Create: `src/autoposter/providers/ladder.py`
- Test: `tests/test_ladder.py`

**Interfaces:**
- Consumes: `ArtCandidate` (Task 9), `ArtKindConfig`, `ProvidersConfig` (Task 1).
- Produces:
  - `language_rank(candidate: ArtCandidate, language_order: list[str]) -> int`
  - `rank_key(candidate: ArtCandidate, language_order: list[str]) -> tuple`
  - `best_candidate(candidates: list[ArtCandidate], language_order: list[str]) -> ArtCandidate | None`
  - `Selection` frozen dataclass: `candidate: ArtCandidate | None`, `is_fallback: bool`.
  - `async select_artwork(providers: list, language_order: list[str], request: ArtRequest) -> Selection`

**Selection semantics, carried over from Posterizarr's `PreferredLanguageOrder`:**

| `language_order` | prefer textless | textless only | Behaviour |
|---|---|---|---|
| `["xx"]` (single entry) | yes | yes | Textless only. Text-bearing images are discarded, and the artifact is skipped if none exists. |
| `["xx", "en", "fi"]` (xx first, more than one) | yes | no | Textless preferred; a text-bearing image is accepted as a fallback. **This is the configured behaviour.** |
| `["en", "fi"]` (xx not first) | no | no | Plain sequential language preference. |

The ladder walks `providers.order` in sequence. When a provider returns only text-bearing art while textless is preferred, that image is **parked** as a fallback and the walk continues to the next provider. Only after every provider has been tried is the parked fallback used — and only when textless-only mode is off.

Ranking is ascending on `(language_rank, text_rank, -score, -pixels)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ladder.py`:

```python
import pytest

from autoposter.providers.base import POSTER, ArtCandidate, ArtRequest
from autoposter.providers.ladder import (
    Selection, best_candidate, language_rank, select_artwork,
)

ORDER = ["xx", "en", "fi"]
REQUEST = ArtRequest(art_kind=POSTER, is_movie=True, tmdb_id=693134)


def candidate(language, score=1.0, provider="TMDB", includes_text=None, width=2000):
    return ArtCandidate(
        provider=provider, url=f"http://x/{language}-{score}-{width}.jpg",
        language=language, width=width, height=3000, score=score,
        includes_text=includes_text,
    )


def test_textless_ranks_first():
    assert language_rank(candidate(None), ORDER) == 0


def test_configured_languages_rank_in_order():
    assert language_rank(candidate("en"), ORDER) == 1
    assert language_rank(candidate("fi"), ORDER) == 2


def test_unlisted_languages_rank_last():
    assert language_rank(candidate("de"), ORDER) > 2


def test_tvdb_three_letter_codes_are_matched():
    assert language_rank(candidate("eng"), ORDER) == 1
    assert language_rank(candidate("fin"), ORDER) == 2


def test_textless_wins_over_a_higher_scored_text_image():
    best = best_candidate([candidate("en", score=9.9), candidate(None, score=1.0)], ORDER)
    assert best.language is None


def test_score_breaks_ties_within_a_language():
    best = best_candidate([candidate("en", score=4.0), candidate("en", score=8.0)], ORDER)
    assert best.score == 8.0


def test_larger_image_breaks_a_score_tie():
    best = best_candidate(
        [candidate("en", score=5.0, width=1000), candidate("en", score=5.0, width=2000)], ORDER
    )
    assert best.width == 2000


def test_tvdb_includes_text_false_beats_an_untagged_image():
    tagged_textless = candidate("eng", score=1.0, provider="TVDB", includes_text=False)
    untagged = candidate(None, score=1.0, provider="TVDB", includes_text=True)
    best = best_candidate([untagged, tagged_textless], ORDER)
    assert best.includes_text is False


def test_empty_candidate_list_returns_none():
    assert best_candidate([], ORDER) is None


class FakeProvider:
    def __init__(self, name, candidates):
        self.name = name
        self._candidates = candidates
        self.calls = 0

    async def fetch(self, request):
        self.calls += 1
        return self._candidates


async def test_first_provider_with_textless_art_wins():
    first = FakeProvider("TMDB", [candidate(None)])
    second = FakeProvider("TVDB", [candidate(None)])
    result = await select_artwork([first, second], ORDER, REQUEST)
    assert result.candidate.provider == "TMDB"
    assert second.calls == 0


async def test_text_only_art_is_parked_and_later_providers_are_tried():
    first = FakeProvider("TMDB", [candidate("en")])
    second = FakeProvider("TVDB", [candidate(None, provider="TVDB")])
    result = await select_artwork([first, second], ORDER, REQUEST)
    assert result.candidate.provider == "TVDB"
    assert result.is_fallback is False
    assert second.calls == 1


async def test_parked_fallback_is_used_when_nothing_textless_exists():
    first = FakeProvider("TMDB", [candidate("en")])
    second = FakeProvider("TVDB", [])
    result = await select_artwork([first, second], ORDER, REQUEST)
    assert result.candidate.language == "en"
    assert result.is_fallback is True


async def test_textless_only_mode_discards_text_art():
    provider = FakeProvider("TMDB", [candidate("en")])
    result = await select_artwork([provider], ["xx"], REQUEST)
    assert result.candidate is None


async def test_language_order_without_xx_takes_the_best_listed_language():
    provider = FakeProvider("TMDB", [candidate("fi", score=9.0), candidate("en", score=1.0)])
    result = await select_artwork([provider], ["en", "fi"], REQUEST)
    assert result.candidate.language == "en"


async def test_a_failing_provider_does_not_stop_the_walk():
    class Broken:
        name = "TMDB"

        async def fetch(self, request):
            raise RuntimeError("provider down")

    working = FakeProvider("TVDB", [candidate(None, provider="TVDB")])
    result = await select_artwork([Broken(), working], ORDER, REQUEST)
    assert result.candidate.provider == "TVDB"


async def test_no_art_anywhere_returns_an_empty_selection():
    result = await select_artwork([FakeProvider("TMDB", [])], ORDER, REQUEST)
    assert result == Selection(candidate=None, is_fallback=False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_ladder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.providers.ladder'`

- [ ] **Step 3: Implement the ladder**

`src/autoposter/providers/ladder.py`:

```python
import logging
from dataclasses import dataclass

from autoposter.providers.base import ArtCandidate, ArtRequest

logger = logging.getLogger(__name__)

UNRANKED = 99

# TVDB reports ISO-639-2/T three-letter codes; the config uses two-letter codes.
_THREE_TO_TWO = {"eng": "en", "fin": "fi", "deu": "de", "swe": "sv", "fra": "fr", "spa": "es"}


@dataclass(frozen=True)
class Selection:
    candidate: ArtCandidate | None
    is_fallback: bool


def _normalise(language: str | None) -> str | None:
    if language is None:
        return None
    return _THREE_TO_TWO.get(language, language)


def language_rank(candidate: ArtCandidate, language_order: list[str]) -> int:
    """Position of this candidate's language in the preference list.

    Textless art always ranks at the position of ``xx``; anything unlisted sorts
    last.
    """
    if candidate.is_textless and "xx" in language_order:
        return language_order.index("xx")
    code = _normalise(candidate.language)
    if code in language_order:
        return language_order.index(code)
    return UNRANKED


def rank_key(candidate: ArtCandidate, language_order: list[str]) -> tuple:
    """Ascending sort key: language, then explicit textlessness, then quality, then size."""
    pixels = (candidate.width or 0) * (candidate.height or 0)
    text_rank = 0 if candidate.is_textless else 1
    return (language_rank(candidate, language_order), text_rank, -candidate.score, -pixels)


def best_candidate(
    candidates: list[ArtCandidate], language_order: list[str]
) -> ArtCandidate | None:
    usable = [c for c in candidates if language_rank(c, language_order) != UNRANKED]
    if not usable:
        return None
    return sorted(usable, key=lambda c: rank_key(c, language_order))[0]


def prefers_textless(language_order: list[str]) -> bool:
    return bool(language_order) and language_order[0] == "xx"


def textless_only(language_order: list[str]) -> bool:
    return language_order == ["xx"]


async def select_artwork(
    providers: list, language_order: list[str], request: ArtRequest
) -> Selection:
    """Walk providers in order and return the best artwork.

    When textless is preferred but a provider offers only text-bearing art, that
    image is parked and the walk continues. The parked image is used only after
    every provider has been tried, and never in textless-only mode.
    """
    prefer = prefers_textless(language_order)
    only = textless_only(language_order)
    parked: ArtCandidate | None = None

    for provider in providers:
        try:
            candidates = await provider.fetch(request)
        except Exception:
            logger.warning("provider %s failed, continuing", provider.name, exc_info=True)
            continue

        choice = best_candidate(candidates, language_order)
        if choice is None:
            continue
        if prefer and not choice.is_textless:
            if only:
                continue
            if parked is None:
                parked = choice
            continue
        return Selection(candidate=choice, is_fallback=False)

    if parked is not None and not only:
        return Selection(candidate=parked, is_fallback=True)
    return Selection(candidate=None, is_fallback=False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_ladder.py -v`
Expected: PASS — 16 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoposter/providers/ladder.py tests/test_ladder.py
git commit -m "feat: textless-first cross-provider artwork selection"
```

---

## Task 11: Plex client

**Files:**
- Create: `src/autoposter/plex/__init__.py`, `src/autoposter/plex/client.py`
- Test: `tests/test_plex.py`

**Interfaces:**
- Consumes: `Config`, `Secrets` (Task 1); `derive_root_folder` (Task 6); `RenderIntent` (Task 4).
- Produces:
  - `ResolvedItem` frozen dataclass: `rating_key: str`, `library: str`, `kind: str`, `title: str`, `year: int | None`, `season_number: int | None`, `episode_number: int | None`, `root_folder: str`, `file_path: str | None`, `art_url: str | None`, `tmdb_id: int | None`, `tvdb_id: int | None`, `imdb_id: str | None`.
  - `ItemNotFound` exception.
  - `PlexClient` with `async resolve(intent: RenderIntent) -> ResolvedItem`.
  - `parse_guids(guids: list[str]) -> dict[str, str]`

**Behaviour:** when Plex has not scanned the new file yet the item will not exist. `resolve` raises `ItemNotFound`; the worker retries with backoff and finally parks the job as "waiting for Plex". This replaces Posterizarr's `FileTestOnTrigger` with something observable.

- [ ] **Step 1: Write the failing tests**

`tests/test_plex.py`:

```python
import pytest

from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound, PlexClient, parse_guids


def test_parse_guids_extracts_all_three_agents():
    guids = parse_guids(["tmdb://693134", "imdb://tt15239678", "tvdb://371980"])
    assert guids == {"tmdb": "693134", "imdb": "tt15239678", "tvdb": "371980"}


def test_parse_guids_ignores_legacy_agent_strings():
    guids = parse_guids(["com.plexapp.agents.imdb://tt123?lang=en", "tmdb://5"])
    assert guids["tmdb"] == "5"


def test_parse_guids_on_empty_input():
    assert parse_guids([]) == {}


class FakeMedia:
    def __init__(self, file_path):
        self.parts = [type("Part", (), {"file": file_path})()]


class FakeSection:
    def __init__(self, title, location, items):
        self.title = title
        self.locations = [location]
        self._items = items

    def search(self, **kwargs):
        results = []
        for item in self._items:
            guid_values = [g.id for g in item.guids]
            if any(kwargs.get("guid", "") == value for value in guid_values):
                results.append(item)
        return results


class FakeItem:
    def __init__(self, rating_key, title, year, file_path, guids, item_type="movie"):
        self.ratingKey = rating_key
        self.title = title
        self.year = year
        self.type = item_type
        self.guids = [type("Guid", (), {"id": g})() for g in guids]
        self.media = [FakeMedia(file_path)] if file_path else []
        self.thumb = f"/library/metadata/{rating_key}/thumb/1"


class FakeServer:
    def __init__(self, sections):
        self._sections = sections

    def library(self):
        return self

    def sections(self):
        return self._sections


@pytest.fixture
def server():
    movie = FakeItem(
        "12345", "Dune: Part Two", 2024,
        "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        ["tmdb://693134", "imdb://tt15239678"],
    )
    movies = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    excluded = FakeSection("Photos", "/mnt/Media/Photos", [])
    return FakeServer([movies, excluded])


async def test_resolve_finds_a_movie_by_tmdb_id(server, tmp_path):
    client = PlexClient(server=server, excluded_libraries=["Photos"])
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)
    item = await client.resolve(intent)
    assert item.rating_key == "12345"
    assert item.library == "Movies"
    assert item.root_folder == "Dune Part Two (2024)"
    assert item.imdb_id == "tt15239678"


async def test_resolve_raises_when_plex_has_not_scanned_yet(server):
    client = PlexClient(server=server, excluded_libraries=["Photos"])
    intent = RenderIntent(kind="movie", title="Unknown", tmdb_id=999999)
    with pytest.raises(ItemNotFound):
        await client.resolve(intent)


async def test_excluded_libraries_are_never_searched(server):
    client = PlexClient(server=server, excluded_libraries=["Movies", "Photos"])
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)
    with pytest.raises(ItemNotFound):
        await client.resolve(intent)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_plex.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.plex.client'`

- [ ] **Step 3: Implement the Plex client**

Create an empty `src/autoposter/plex/__init__.py`, then `src/autoposter/plex/client.py`:

```python
import asyncio
import re
from dataclasses import dataclass

from autoposter.intake.arr import RenderIntent
from autoposter.render.naming import derive_root_folder

_GUID_RE = re.compile(r"^(?:com\.plexapp\.agents\.)?(tmdb|imdb|tvdb)://([^?]+)")


class ItemNotFound(Exception):
    """Plex has no matching item — usually it has not scanned the file yet."""


@dataclass(frozen=True)
class ResolvedItem:
    rating_key: str
    library: str
    kind: str
    title: str
    year: int | None
    season_number: int | None
    episode_number: int | None
    root_folder: str
    file_path: str | None
    art_url: str | None
    tmdb_id: int | None
    tvdb_id: int | None
    imdb_id: str | None


def parse_guids(guids: list[str]) -> dict[str, str]:
    """Map Plex GUID strings to ``{agent: id}``, tolerating legacy agent prefixes."""
    parsed = {}
    for guid in guids:
        match = _GUID_RE.match(guid)
        if match:
            parsed[match.group(1)] = match.group(2)
    return parsed


def _as_int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class PlexClient:
    """Resolves render intents to Plex items.

    ``plexapi`` is synchronous, so calls run in a thread to keep the event loop free.
    """

    def __init__(self, server, excluded_libraries: list[str]):
        self._server = server
        self._excluded = set(excluded_libraries)

    def _sections(self):
        return [s for s in self._server.library().sections() if s.title not in self._excluded]

    def _search_sync(self, intent: RenderIntent):
        wanted = []
        if intent.tmdb_id:
            wanted.append(f"tmdb://{intent.tmdb_id}")
        if intent.tvdb_id:
            wanted.append(f"tvdb://{intent.tvdb_id}")
        if intent.imdb_id:
            wanted.append(f"imdb://{intent.imdb_id}")

        for section in self._sections():
            for guid in wanted:
                results = section.search(guid=guid)
                if results:
                    return section, results[0]
        return None, None

    async def resolve(self, intent: RenderIntent) -> ResolvedItem:
        section, item = await asyncio.to_thread(self._search_sync, intent)
        if item is None:
            raise ItemNotFound(
                f"no Plex item for {intent.kind} {intent.title!r} "
                f"(tmdb={intent.tmdb_id}, tvdb={intent.tvdb_id})"
            )

        guids = parse_guids([g.id for g in getattr(item, "guids", [])])
        file_path = None
        if getattr(item, "media", None):
            parts = item.media[0].parts
            if parts:
                file_path = parts[0].file

        library_root = section.locations[0]
        if intent.kind == "movie":
            if not file_path:
                raise ItemNotFound(f"Plex item {item.ratingKey} has no media parts yet")
            root_folder = derive_root_folder(library_root, file_path, is_directory=False)
        else:
            show_path = file_path or getattr(item, "locations", [library_root])[0]
            root_folder = derive_root_folder(library_root, show_path, is_directory=True)

        return ResolvedItem(
            rating_key=str(item.ratingKey),
            library=section.title,
            kind=intent.kind,
            title=item.title,
            year=getattr(item, "year", None),
            season_number=intent.season_number,
            episode_number=intent.episode_number,
            root_folder=root_folder,
            file_path=file_path,
            art_url=getattr(item, "thumb", None),
            tmdb_id=_as_int(guids.get("tmdb")) or intent.tmdb_id,
            tvdb_id=_as_int(guids.get("tvdb")) or intent.tvdb_id,
            imdb_id=guids.get("imdb") or intent.imdb_id,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_plex.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoposter/plex tests/test_plex.py
git commit -m "feat: resolve render intents to Plex items"
```

---

## Task 12: Render pipeline

**Files:**
- Create: `src/autoposter/render/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 1, 2, 6, 7, 8, 9, 10, 11.
- Produces:
  - `ART_KINDS_FOR: dict[str, list[str]]`
  - `compute_fingerprint(config_version: str, art_kind: str, source_url: str | None, base_sha256: str | None, text_inputs: list[str]) -> str`
  - `title_text_for(art_kind: str, item: ResolvedItem, config: Config) -> tuple[str | None, str | None]` — returns `(primary_text, secondary_text)`; the secondary is the "Season X • Episode Y" line, non-`None` only for title cards.
  - `manual_override_path(config, item, art_kind) -> Path | None`
  - `async render_artifact(session, config, http, item, art_kind, providers) -> Render`
  - `async process_item(session, config, http, plex, providers_by_kind, intent) -> list[Render]`

**Which artifacts each item kind produces:**

| Item kind | Artifacts |
|---|---|
| movie | `poster`, `background` |
| show | `poster`, `background` |
| season | `season_poster` |
| episode | `title_card` |

**Text content per artifact:**

- `poster` — the item title. **With `use_logo: true` and `logo_text_fallback: false`, a clearlogo replaces the title text, and when no logo exists on any provider the poster gets neither logo nor text.** That is the current production behaviour and must be preserved.
- `season_poster` — the season title from Plex.
- `background` — none; `add_text` is `false`.
- `title_card` — the episode title, plus a second line `"Season 2 • Episode 3"` built from the configured labels with a U+2022 bullet and **unpadded** numbers.

**Order of operations for one artifact:**

1. If a manual override exists at `<manual_assets_root>/<library>/<root_folder>/<name>.jpg`, use it as the base and skip provider lookup.
2. Otherwise select artwork through the ladder. No candidate → status `no_art`, no file written.
3. Download to a temp file and hash it.
4. Compute the fingerprint. Unchanged and the asset file still exists → return early, nothing re-rendered.
5. Fit the text. Truncated → status `truncated`, **no file written**.
6. Stamp the provenance comment, cover-fit and overlay, then draw text or composite the logo.
7. `os.replace` the temp file onto the final path — the only moment the asset tree changes.

- [ ] **Step 1: Write the failing tests**

`tests/test_pipeline.py`:

```python
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.plex.client import ResolvedItem
from autoposter.render.pipeline import (
    ART_KINDS_FOR, compute_fingerprint, manual_override_path, title_text_for,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def item(kind="movie", title="Dune: Part Two", season=None, episode=None, root="Dune (2024)"):
    return ResolvedItem(
        rating_key="1", library="Movies", kind=kind, title=title, year=2024,
        season_number=season, episode_number=episode, root_folder=root,
        file_path="/mnt/Media/Movies/Dune (2024)/x.mkv", art_url=None,
        tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )


def test_each_item_kind_maps_to_its_artifacts():
    assert ART_KINDS_FOR["movie"] == ["poster", "background"]
    assert ART_KINDS_FOR["show"] == ["poster", "background"]
    assert ART_KINDS_FOR["season"] == ["season_poster"]
    assert ART_KINDS_FOR["episode"] == ["title_card"]


def test_fingerprint_is_stable_for_identical_inputs():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    assert a == b


def test_fingerprint_changes_with_the_source_image():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v1", "poster", "http://x/a.jpg", "def", ["DUNE"])
    assert a != b


def test_fingerprint_changes_with_the_config_version():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v2", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    assert a != b


def test_fingerprint_changes_with_the_text():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["HEAT"])
    assert a != b


def test_poster_text_is_the_title(config):
    primary, secondary = title_text_for("poster", item(), config)
    assert primary == "Dune: Part Two"
    assert secondary is None


def test_background_has_no_text(config):
    primary, secondary = title_text_for("background", item(), config)
    assert primary is None
    assert secondary is None


def test_season_poster_text_is_the_season_title(config):
    primary, _ = title_text_for(
        "season_poster", item(kind="season", title="Season 2", season=2), config
    )
    assert primary == "Season 2"


def test_title_card_has_episode_title_and_a_numbering_line(config):
    primary, secondary = title_text_for(
        "title_card",
        item(kind="episode", title="Who Is Alive?", season=2, episode=3),
        config,
    )
    assert primary == "Who Is Alive?"
    assert secondary == "Season 2 • Episode 3"


def test_title_card_numbering_uses_unpadded_numbers(config):
    _, secondary = title_text_for(
        "title_card", item(kind="episode", title="X", season=1, episode=1), config
    )
    assert secondary == "Season 1 • Episode 1"


def test_manual_override_is_found_when_present(config, tmp_path):
    config.manual_assets_root = tmp_path
    target = tmp_path / "Movies" / "Dune (2024)"
    target.mkdir(parents=True)
    (target / "poster.jpg").write_bytes(b"x")
    assert manual_override_path(config, item(), "poster") == target / "poster.jpg"


def test_manual_override_is_none_when_absent(config, tmp_path):
    config.manual_assets_root = tmp_path
    assert manual_override_path(config, item(), "poster") is None


def test_publish_keeps_one_previous_generation(tmp_path):
    from autoposter.render.pipeline import _publish

    target = tmp_path / "assets" / "Dune (2024)" / "poster.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    working = tmp_path / "new.jpg"
    working.write_bytes(b"new")
    backup_root = tmp_path / "backup"

    _publish(working, target, backup_root)

    assert target.read_bytes() == b"new"
    assert (backup_root / "Dune (2024)" / "poster.jpg").read_bytes() == b"old"


def test_publish_without_an_existing_asset_writes_no_backup(tmp_path):
    from autoposter.render.pipeline import _publish

    target = tmp_path / "assets" / "Heat (1995)" / "poster.jpg"
    working = tmp_path / "new.jpg"
    working.write_bytes(b"new")
    backup_root = tmp_path / "backup"

    _publish(working, target, backup_root)

    assert target.read_bytes() == b"new"
    assert not backup_root.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.render.pipeline'`

- [ ] **Step 3: Implement the pipeline**

`src/autoposter/render/pipeline.py`:

```python
import hashlib
import logging
import os
import tempfile
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.schema import Config
from autoposter.db.models import MediaItem, Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers import base as art
from autoposter.providers.ladder import select_artwork
from autoposter.render import compositor, naming
from autoposter.render.textfit import fit_point_size, prepare_text

logger = logging.getLogger(__name__)

ART_KINDS_FOR = {
    "movie": ["poster", "background"],
    "show": ["poster", "background"],
    "season": ["season_poster"],
    "episode": ["title_card"],
}

_CANVAS = {
    "poster": compositor.POSTER_SIZE,
    "season_poster": compositor.POSTER_SIZE,
    "background": compositor.BACKGROUND_SIZE,
    "title_card": compositor.BACKGROUND_SIZE,
}

_BULLET = "•"


def compute_fingerprint(
    config_version: str,
    art_kind: str,
    source_url: str | None,
    base_sha256: str | None,
    text_inputs: list[str],
) -> str:
    """Hash every input that affects the finished image.

    Re-rendering happens only when this value changes, which is what turns a
    library-wide pass into a cheap comparison instead of 16k image operations.
    """
    parts = [config_version, art_kind, source_url or "", base_sha256 or "", *text_inputs]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def art_config_for(config: Config, art_kind: str):
    return getattr(config.artwork, art_kind)


def title_text_for(
    art_kind: str, item: ResolvedItem, config: Config
) -> tuple[str | None, str | None]:
    """Return ``(primary_text, secondary_text)`` for an artifact.

    The secondary line exists only for title cards. Numbers there are unpadded,
    unlike the zero-padded asset file names.
    """
    settings = art_config_for(config, art_kind)
    if settings.text is None or not settings.text.add_text:
        return None, None
    if art_kind == "title_card":
        secondary = None
        if settings.episode_text is not None and settings.episode_text.add_text:
            secondary = (
                f"{settings.season_label} {item.season_number} "
                f"{_BULLET} {settings.episode_label} {item.episode_number}"
            )
        return item.title, secondary
    return item.title, None


def manual_override_path(config: Config, item: ResolvedItem, art_kind: str) -> Path | None:
    """A hand-placed asset wins over anything fetched from a provider."""
    relative = naming.asset_path(
        config.model_copy(update={"assets_root": config.manual_assets_root}),
        item.library, item.root_folder, art_kind,
        item.season_number, item.episode_number,
    )
    return relative if relative.exists() else None


def _should_skip_title(config: Config, item: ResolvedItem, art_kind: str) -> bool:
    if art_kind != "title_card" or not config.skip_tba:
        return False
    skip_words = {word.lower() for word in art_config_for(config, art_kind).skip_words}
    return item.title.strip().lower() in skip_words


async def _download(http: httpx.AsyncClient, url: str, destination: Path) -> str:
    """Fetch artwork to ``destination`` and return its SHA-256."""
    digest = hashlib.sha256()
    async with http.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            async for chunk in response.aiter_bytes():
                digest.update(chunk)
                handle.write(chunk)
    return digest.hexdigest()


async def _upsert_media_item(session: AsyncSession, item: ResolvedItem) -> MediaItem:
    existing = (
        await session.execute(
            select(MediaItem).where(MediaItem.rating_key == item.rating_key)
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = MediaItem(rating_key=item.rating_key)
        session.add(existing)
    existing.library = item.library
    existing.kind = item.kind
    existing.title = item.title
    existing.year = item.year
    existing.season_number = item.season_number
    existing.episode_number = item.episode_number
    existing.root_folder = item.root_folder
    existing.file_path = item.file_path
    existing.tmdb_id = item.tmdb_id
    existing.tvdb_id = item.tvdb_id
    existing.imdb_id = item.imdb_id
    await session.flush()
    return existing


async def _get_or_create_render(
    session: AsyncSession, media_item: MediaItem, art_kind: str, asset_path: Path
) -> Render:
    render = (
        await session.execute(
            select(Render).where(
                Render.item_id == media_item.id, Render.art_kind == art_kind
            )
        )
    ).scalar_one_or_none()
    if render is None:
        render = Render(item_id=media_item.id, art_kind=art_kind, asset_path=str(asset_path))
        session.add(render)
        await session.flush()
    render.asset_path = str(asset_path)
    return render


async def render_artifact(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    item: ResolvedItem,
    art_kind: str,
    providers: list,
) -> Render:
    """Build one artifact. Idempotent: safe to run repeatedly for the same item."""
    settings = art_config_for(config, art_kind)
    media_item = await _upsert_media_item(session, item)
    target = naming.asset_path(
        config, item.library, item.root_folder, art_kind,
        item.season_number, item.episode_number,
    )
    render = await _get_or_create_render(session, media_item, art_kind, target)

    if not settings.enabled:
        render.status = "skipped"
        render.detail = f"{art_kind} disabled in config"
        await session.commit()
        return render

    if _should_skip_title(config, item, art_kind):
        render.status = "skipped"
        render.detail = f"title {item.title!r} matches a skip word"
        await session.commit()
        return render

    primary_text, secondary_text = title_text_for(art_kind, item, config)

    with tempfile.TemporaryDirectory() as tmpdir:
        working = Path(tmpdir) / target.name

        override = manual_override_path(config, item, art_kind)
        if override is not None:
            working.write_bytes(override.read_bytes())
            base_sha = hashlib.sha256(working.read_bytes()).hexdigest()
            source_url, provider_name, textless = str(override), "manual", None
        else:
            selection = await select_artwork(
                providers,
                settings.language_order,
                art.ArtRequest(
                    art_kind=art_kind,
                    is_movie=item.kind == "movie",
                    tmdb_id=item.tmdb_id,
                    tvdb_id=item.tvdb_id,
                    imdb_id=item.imdb_id,
                    season_number=item.season_number,
                    episode_number=item.episode_number,
                ),
            )
            if selection.candidate is None:
                render.status = "no_art"
                render.detail = f"no {art_kind} art on any provider"
                await session.commit()
                return render
            candidate = selection.candidate
            base_sha = await _download(http, candidate.url, working)
            source_url = candidate.url
            provider_name = candidate.provider
            textless = candidate.is_textless

        text_inputs = [t for t in (primary_text, secondary_text) if t]
        fingerprint = compute_fingerprint(
            config.version, art_kind, source_url, base_sha, text_inputs
        )
        if render.fingerprint == fingerprint and target.exists():
            render.status = "rendered"
            render.detail = "unchanged"
            await session.commit()
            return render

        if render.source_mode == "verbatim":
            # MediUX and similar sources ship finished art; compositing would fight
            # the designer's own title treatment (spec section 11).
            _publish(working, target, config.backup_root)
        else:
            overlay = (
                str(Path(config.overlays_root) / settings.overlay_file)
                if settings.add_overlay
                else None
            )
            compositor.run(compositor.build_stamp_argv(config.magick_binary, str(working)))
            compositor.run(
                compositor.build_base_argv(
                    config.magick_binary, str(working), _CANVAS[art_kind], overlay,
                    config.artwork.output_quality, settings.add_border,
                    settings.border_color, settings.border_width,
                )
            )
            blocks = [(settings.text, primary_text)]
            if art_kind == "title_card":
                blocks.append((settings.episode_text, secondary_text))
            for style, text in blocks:
                if style is None or not text:
                    continue
                prepared = prepare_text(text, style)
                font_path = str(Path(config.fonts_root) / style.font)
                fit = fit_point_size(config.magick_binary, font_path, style, prepared)
                if fit.truncated:
                    # Posterizarr writes no file when text cannot fit at the minimum
                    # point size. Emitting one here would produce artwork the current
                    # system never would.
                    render.status = "truncated"
                    render.detail = (
                        f"{text!r} does not fit in "
                        f"{style.max_width}x{style.max_height} at "
                        f"{style.min_point_size}pt"
                    )
                    await session.commit()
                    return render
                compositor.run(
                    compositor.build_text_argv(
                        config.magick_binary, str(working), style, font_path,
                        fit.point_size, prepared, config.artwork.output_quality,
                    )
                )
            _publish(working, target, config.backup_root)

    render.provider = provider_name
    render.source_url = source_url
    render.textless = textless
    render.base_sha256 = base_sha
    render.fingerprint = fingerprint
    render.status = "rendered"
    render.detail = None
    # Database clock, per the global constraint: the app and database clocks drift.
    render.rendered_at = func.now()
    await session.commit()
    return render


def _publish(working: Path, target: Path, backup_root: Path | None = None) -> None:
    """Move the finished image into the asset tree atomically.

    ``os.replace`` is atomic within a filesystem, so readers never observe a
    half-written asset. The staging copy lives beside the target so the rename
    does not cross a mount boundary.

    Before overwriting, the existing asset is copied into ``backup_root`` under
    the same relative path, keeping exactly one previous generation so a bad
    render can be rolled back (spec section 7).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup_root is not None and target.exists():
        backup = Path(backup_root) / target.parent.name / target.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(target.read_bytes())
    staging = target.with_name(f".{target.name}.tmp")
    staging.write_bytes(working.read_bytes())
    os.replace(staging, target)


async def process_item(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    plex,
    providers: list,
    intent: RenderIntent,
) -> list[Render]:
    """Resolve one intent and build every artifact it implies."""
    item = await plex.resolve(intent)
    results = []
    for art_kind in ART_KINDS_FOR[intent.kind]:
        results.append(
            await render_artifact(session, config, http, item, art_kind, providers)
        )
    return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS — 14 passed

- [ ] **Step 5: Add an end-to-end test with fakes**

`tests/test_pipeline_e2e.py`:

```python
import shutil
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render.pipeline import process_item

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
GOLDEN = Path(__file__).parent / "fixtures" / "golden"

pytestmark = pytest.mark.skipif(
    shutil.which("magick") is None or not GOLDEN.exists(),
    reason="requires ImageMagick and harvested golden fixtures",
)


class FakePlex:
    def __init__(self, item):
        self._item = item

    async def resolve(self, intent):
        return self._item


class FakeProvider:
    name = "TMDB"

    def __init__(self, url):
        self._url = url

    async def fetch(self, request):
        return [ArtCandidate("TMDB", self._url, None, 2000, 3000, 5.0)]


@pytest.fixture
def config(tmp_path):
    cfg = load_config(EXAMPLE)
    cfg.assets_root = tmp_path / "assets"
    cfg.manual_assets_root = tmp_path / "manual"
    cfg.fonts_root = GOLDEN
    cfg.overlays_root = GOLDEN
    return cfg


async def test_movie_intent_writes_poster_and_background(config, session, tmp_path):
    source = GOLDEN / "source_textless.jpg"

    async def handler(request):
        return httpx.Response(200, content=source.read_bytes())

    item = ResolvedItem(
        rating_key="12345", library="Movies", kind="movie", title="Dune: Part Two",
        year=2024, season_number=None, episode_number=None,
        root_folder="Dune Part Two (2024)",
        file_path="/mnt/Media/Movies/Dune Part Two (2024)/x.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        renders = await process_item(
            session, config, http, FakePlex(item),
            [FakeProvider("https://image.tmdb.org/t/p/original/x.jpg")],
            RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134),
        )

    assert {r.art_kind for r in renders} == {"poster", "background"}
    assert all(r.status == "rendered" for r in renders)
    poster = config.assets_root / "Movies" / "Dune Part Two (2024)" / "poster.jpg"
    assert poster.exists()
    assert poster.stat().st_size > 0


async def test_second_run_is_a_no_op(config, session):
    source = GOLDEN / "source_textless.jpg"

    async def handler(request):
        return httpx.Response(200, content=source.read_bytes())

    item = ResolvedItem(
        rating_key="12345", library="Movies", kind="movie", title="Dune: Part Two",
        year=2024, season_number=None, episode_number=None,
        root_folder="Dune Part Two (2024)",
        file_path="/mnt/Media/Movies/Dune Part Two (2024)/x.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)
    providers = [FakeProvider("https://image.tmdb.org/t/p/original/x.jpg")]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await process_item(session, config, http, FakePlex(item), providers, intent)
        poster = config.assets_root / "Movies" / "Dune Part Two (2024)" / "poster.jpg"
        first_mtime = poster.stat().st_mtime_ns
        renders = await process_item(session, config, http, FakePlex(item), providers, intent)

    assert poster.stat().st_mtime_ns == first_mtime
    assert all(r.detail == "unchanged" for r in renders)


async def test_no_art_records_the_reason_without_writing(config, session):
    class Empty:
        name = "TMDB"

        async def fetch(self, request):
            return []

    item = ResolvedItem(
        rating_key="99", library="Movies", kind="movie", title="Obscure Film",
        year=1970, season_number=None, episode_number=None, root_folder="Obscure (1970)",
        file_path="/mnt/Media/Movies/Obscure (1970)/x.mkv", art_url=None,
        tmdb_id=1, tvdb_id=None, imdb_id=None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http:
        renders = await process_item(
            session, config, http, FakePlex(item), [Empty()],
            RenderIntent(kind="movie", title="Obscure Film", tmdb_id=1),
        )
    assert all(r.status == "no_art" for r in renders)
    assert not (config.assets_root / "Movies" / "Obscure (1970)" / "poster.jpg").exists()
    rows = (await session.execute(select(Render))).scalars().all()
    assert {r.status for r in rows} == {"no_art"}
```

- [ ] **Step 6: Run the end-to-end tests**

Run: `pytest tests/test_pipeline_e2e.py -v`
Expected: PASS — 3 passed

- [ ] **Step 7: Commit**

```bash
git add src/autoposter/render/pipeline.py tests/test_pipeline.py tests/test_pipeline_e2e.py
git commit -m "feat: per-item render pipeline with fingerprint-based skipping"
```

---

## Task 13: Worker pool, entrypoint and container

**Files:**
- Create: `src/autoposter/queue/worker.py`, `src/autoposter/main.py`
- Create: `Dockerfile`, `deploy/README.md`
- Modify: `src/autoposter/app.py` (add `/metrics` and worker lifespan)
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `claim`/`complete`/`fail` (Task 3), `process_item` (Task 12), `ItemNotFound` (Task 11), `create_app` (Task 5).
- Produces: `async run_worker(worker_id, session_factory, config, deps, stop_event)` and `async run_workers(...)`; `main()` entrypoint.

**Retry policy:** `ItemNotFound` means Plex has not scanned the file yet, which is expected right after an import — it retries on the standard backoff and eventually parks as `waiting for Plex`. Any other exception retries the same way. Nothing is ever dropped silently: a webhook becomes either a rendered asset or a parked job visible in the failures view.

- [ ] **Step 1: Write the failing worker tests**

`tests/test_worker.py`:

```python
import asyncio
from dataclasses import asdict

import pytest
from sqlalchemy import select

from autoposter.db.models import Job
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound
from autoposter.queue.jobs import enqueue
from autoposter.queue.worker import run_once


async def test_run_once_processes_a_due_job(session):
    handled = []

    async def handler(session_, intent):
        handled.append(intent)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    assert await run_once(session, "worker-1", handler) is True
    assert handled[0].tmdb_id == 1
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "done"


async def test_run_once_returns_false_when_nothing_is_due(session):
    async def handler(session_, intent):
        raise AssertionError("should not be called")

    assert await run_once(session, "worker-1", handler) is False


async def test_item_not_found_reschedules_rather_than_failing(session):
    async def handler(session_, intent):
        raise ItemNotFound("plex has not scanned yet")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await run_once(session, "worker-1", handler)
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "pending"
    assert "scanned" in job.last_error


async def test_unexpected_errors_also_reschedule(session):
    async def handler(session_, intent):
        raise RuntimeError("provider exploded")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=2)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await run_once(session, "worker-1", handler)
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "pending"
    assert "exploded" in job.last_error


async def test_unknown_job_kinds_are_parked_not_retried(session):
    async def handler(session_, intent):
        raise AssertionError("should not be called")

    await enqueue(session, "not_a_real_kind", {})
    await run_once(session, "worker-1", handler)
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "parked"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.queue.worker'`

- [ ] **Step 3: Implement the worker**

`src/autoposter/queue/worker.py`:

```python
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import Job
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound
from autoposter.queue.jobs import claim, complete, fail

logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 2.0


async def run_once(session: AsyncSession, worker_id: str, handler) -> bool:
    """Claim and run at most one job. Returns False when nothing was due."""
    job = await claim(session, worker_id)
    if job is None:
        return False

    if job.kind != "process_item":
        # Not retryable — a rescheduled unknown kind would spin until it parks anyway.
        job.state = "parked"
        job.last_error = f"unknown job kind {job.kind!r}"
        await session.commit()
        return True

    try:
        intent = RenderIntent(**job.payload)
        await handler(session, intent)
    except ItemNotFound as exc:
        # Expected right after an import: Plex has not scanned the new file yet.
        logger.info("job %s waiting for Plex: %s", job.id, exc)
        await fail(session, job.id, str(exc))
    except Exception as exc:  # noqa: BLE001 - the queue is the error boundary
        logger.warning("job %s failed: %s", job.id, exc, exc_info=True)
        await fail(session, job.id, f"{type(exc).__name__}: {exc}")
    else:
        await complete(session, job.id)
    return True


async def run_worker(worker_id: str, session_factory, handler, stop_event: asyncio.Event) -> None:
    """Claim jobs until stopped, sleeping briefly when the queue is empty."""
    while not stop_event.is_set():
        try:
            async with session_factory() as session:
                did_work = await run_once(session, worker_id, handler)
        except Exception:
            logger.exception("worker %s loop error", worker_id)
            did_work = False
        if not did_work:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass


async def run_workers(count: int, session_factory, handler, stop_event: asyncio.Event) -> None:
    await asyncio.gather(
        *(
            run_worker(f"worker-{index}", session_factory, handler, stop_event)
            for index in range(count)
        )
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_worker.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Wire the entrypoint**

Replace `src/autoposter/app.py` with a version that starts the workers alongside the API:

```python
import asyncio
import functools
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from autoposter.config.schema import Config, Secrets
from autoposter.intake.routes import router
from autoposter.queue.worker import run_workers
from autoposter.render.pipeline import process_item

logger = logging.getLogger(__name__)


def create_app(
    config: Config, session_factory, secrets: Secrets, run_background: bool = False
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not run_background:
            yield
            return
        stop_event = asyncio.Event()
        http = httpx.AsyncClient(timeout=30.0)
        handler = functools.partial(
            _handle_intent, config=config, http=http,
            plex=app.state.plex, providers=app.state.providers,
        )
        task = asyncio.create_task(
            run_workers(config.workers, session_factory, handler, stop_event)
        )
        logger.info("started %d workers", config.workers)
        try:
            yield
        finally:
            stop_event.set()
            task.cancel()
            await http.aclose()

    app = FastAPI(title="autoposter", lifespan=lifespan)
    app.state.config = config
    app.state.session_factory = session_factory
    app.state.secrets = secrets
    app.state.plex = None
    app.state.providers = []
    app.include_router(router)

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


async def _handle_intent(session, intent, *, config, http, plex, providers):
    await process_item(session, config, http, plex, providers, intent)
```

`src/autoposter/main.py`:

```python
import logging
import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from plexapi.server import PlexServer

from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.base import make_engine, make_session_factory
from autoposter.plex.client import PlexClient
from autoposter.providers.fanart import FanartClient
from autoposter.providers.tmdb import TMDBClient
from autoposter.providers.tvdb import TVDBClient

CONFIG_PATH = Path(os.environ.get("AUTOPOSTER_CONFIG", "/config/autoposter.yaml"))


def build() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_config(CONFIG_PATH)
    secrets = Secrets.from_env()

    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)
    app = create_app(config, session_factory, secrets, run_background=True)

    http = httpx.AsyncClient(timeout=30.0)
    by_name = {
        "TMDB": TMDBClient(secrets.tmdb_token, config.artwork.poster.language_order, http),
        "TVDB": TVDBClient(secrets.tvdb_apikey, http),
        "Fanart": FanartClient(secrets.fanart_apikey, http),
    }
    app.state.providers = [by_name[name] for name in config.providers.order if name in by_name]
    app.state.plex = PlexClient(
        server=PlexServer(config.plex.url, secrets.plex_token),
        excluded_libraries=config.plex.excluded_libraries,
    )
    return app


def main() -> None:
    uvicorn.run(build(), host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Write the container image**

`Dockerfile`:

```dockerfile
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
```

Verify ImageMagick is present and version 7:

```bash
docker build -t autoposter:dev . && docker run --rm autoposter:dev magick -version
```

Expected: output beginning `Version: ImageMagick 7.`

- [ ] **Step 7: Document deployment**

`deploy/README.md` must state: the app needs `AUTOPOSTER_DATABASE_URL` pointing at a Postgres database it owns (migrations run at startup); the `/assets`, `/manualassets` and `/assetsbackup` volumes must be the same NFS mounts Posterizarr and Kometa use today; secrets come from an ExternalSecret with the six `AUTOPOSTER_*` variables; and Radarr/Sonarr must be configured with the webhook URL plus an `X-Autoposter-Token` custom header matching `AUTOPOSTER_WEBHOOK_SECRET`.

Radarr triggers to enable: On Import Complete, On Rename, On Movie Add. Sonarr: On Import Complete, On Rename, On Series Add.

- [ ] **Step 8: Run the whole suite**

```bash
docker compose up -d postgres
pytest -v
```

Expected: all tests pass. Golden-image and end-to-end tests skip automatically if ImageMagick or the harvested fixtures are absent — **do not treat a skip as a pass** for those two.

- [ ] **Step 9: Commit**

```bash
git add src/autoposter/app.py src/autoposter/main.py src/autoposter/queue/worker.py \
        Dockerfile deploy/README.md tests/test_worker.py
git commit -m "feat: worker pool, entrypoint and container image"
```

---

## Phase 1 completion criteria

Phase 1 is done when all of the following hold. Verify each by running the command and reading the output — do not infer.

1. `pytest -v` passes with zero failures, and the golden-image and end-to-end tests **ran** rather than skipped.
2. `python scripts/verify_asset_paths.py /assets` exits 0 against the production asset tree.
3. A real Radarr "On Import Complete" webhook produces a poster and background under `/assets` within the settle window, byte-comparable to what Posterizarr produces for the same title.
4. Re-delivering the same webhook writes nothing new — the second pass reports `unchanged`.
5. Stopping the pod mid-render and restarting it resumes the job rather than losing it.
6. Kometa still runs unchanged against `/assets` and produces the same overlaid output as before.

Only once 1–6 hold should Posterizarr be switched off.

## Deliberately out of scope for Phase 1

- **Tautulli intake.** Spec section 4 lists Tautulli alongside Radarr and Sonarr as a
  trigger. It is not built here, for two reasons: the Arr webhooks already cover every
  import, rename and add event that changes artwork inputs, and Tautulli sends a fully
  user-defined JSON body with no fixed schema — there is nothing to parse until a
  template is agreed. Adding it later is one parser plus one route, reusing the same
  `RenderIntent` and queue. The template to standardise on when that happens is
  documented in `deploy/README.md`.
- **Uploading artwork to Plex.** Phase 1 writes `/assets` only; Kometa continues to read
  that tree and push the overlaid result to Plex, exactly as today. Direct upload arrives
  in Phase 2 with the badge overlays, because uploading a base image without badges would
  visibly regress the library.
- **Custom Prometheus metrics.** `/metrics` is exposed and serves the default process
  collectors so the scrape target exists from day one; queue-depth and render-outcome
  counters land with the dashboard in Phase 4.

## Notes for later phases

- `Render.source_mode` is written but only `generate` is exercised in Phase 1. The `verbatim` branch in `render_artifact` is the MediUX seam described in spec section 11; it is deliberately reachable but unused.
- `item_facts` is not created in Phase 1 — it belongs to Phase 2 with the badge overlays, which is where resolution, codec, and rating values first matter.
- `provider_cache` is created but not yet read from. Phase 2 wires TTL caching in front of the provider clients.
- Episode title cards depend on TMDB `stills`; TVDB exposes only one image per episode and Fanart has no episode artwork at all. If title-card coverage proves thin, that is the reason, and MediUX sets are the remedy.

---

## Implementation deviations

The task code in this plan was written before implementation. Review found real
defects in several of those code blocks. The shipped implementation is the
authority; this section records where it diverges and why, so nobody re-derives
a fixed bug from the plan text.

**Queue timestamps come from the database clock (Tasks 2, 3, 12).**
The plan computed `run_after` and the model timestamps in Python. The
application and database clocks drift — measured at 9.5s between a Windows host
and its Postgres container, and possible between pods — which made jobs fire
early or late by the drift. `enqueue()`, `fail()` and every `created_at` /
`updated_at` / `rendered_at` now derive from Postgres `now()`. Tests obtain
"now" via `SELECT now()` for the same reason. See the Global Constraints entry.

**Webhook intake hardening (Task 5).**
Three defects in the plan's `routes.py`: `secrets.compare_digest` on `str`
raised `TypeError` for a non-ASCII token, returning 500 instead of 401 (now
compares bytes); a malformed body lost the `events_log` row entirely, which is
the one record an operator needs when something goes wrong (the body is now
logged before parsing, and an unparseable body returns 400); and the exception
guard was wide enough to report a parser bug as the sender's fault, so a
`parse_*` failure on a well-formed object now logs the structured payload and
propagates as a 500.

**Provider clients share one signature (Task 9).**
The plan gave each client a different `fetch` signature while the ladder called
them interchangeably — that could not have worked. All three now take a single
`ArtRequest`. A client whose required identifier is missing returns `[]` rather
than raising, and Fanart's all-strings `likes` field is parsed defensively.

**Plex resolution navigates to the actual item (Tasks 11, 12).**
The plan's resolver searched only by series GUID, so season and episode intents
resolved to the *show*. Title cards would have carried the show name instead of
the episode title, season posters the show name instead of "Season N", and
`SkipTBA` could never match. Worse, every season and episode of a show
collapsed into one `media_items` row and one `renders` row per art kind, so
fingerprints clobbered each other and re-delivery re-rendered everything. The
resolver now navigates show → season/episode and returns that item's rating key
and title, while `root_folder` still derives from the show's directory because
that is where the assets live. `parent_id` is populated when the parent row
exists.

Also: a library split across several mount points raised an unhandled
`ValueError`; every root is now tried and a genuine miss raises a diagnostic
`ItemNotFound`. All `plexapi` attribute access happens inside the worker thread
via a plain-data `_RawMatch`, because `plexapi` lazily reloads over HTTP and
would otherwise block the event loop.

**Posters composite a clearlogo, not title text (Task 12).**
The plan's prose said a clearlogo replaces the title text under `use_logo: true`
with `logo_text_fallback: false`, but its code never fetched or composited one —
leaving `build_logo_argv` and the entire `LOGO` provider path as dead code, and
every poster carrying text where production shows a logo. Now implemented for
the poster art kind, with all four branches covered.

**The fingerprint covers asset bytes, not just filenames (Task 12).**
Replacing `overlay.png` or a font in place, keeping the filename, left every
affected asset with an unchanged fingerprint and silently un-regenerated. The
overlay, font and logo contents are now hashed into it.

**Upserts are concurrency-safe, and backups keep library identity (Task 12).**
`_upsert_media_item` / `_get_or_create_render` used select-then-insert against
unique constraints; with five workers that raised `IntegrityError` and killed a
render pass. Both now use `ON CONFLICT`, matching `queue/jobs.py::enqueue`.
`_publish` backed up by the parent folder's *name* alone, so two libraries
sharing a folder name clobbered each other's only backup generation; it now
mirrors the asset tree relative to the assets root.

**Blocking work runs off the event loop (Tasks 12, 13).**
Every ImageMagick call and bulk file operation ran synchronously on the loop
uvicorn serves from, so `workers: 5` bought no compositing concurrency and a
season-pack import could stall health probes past a 1s liveness timeout. All of
it is now offloaded with `asyncio.to_thread`.

**Interrupted jobs are recovered (Task 13).**
The plan had no reclaim path, so a job left at `running` when the process died
was stranded forever — breaking completion criterion 5. `asyncio.CancelledError`
is a `BaseException` and slipped past `except Exception`, so graceful shutdown
stranded jobs too. Now: cancellation releases the job without charging a retry,
and `reclaim_stale()` resets claims older than 900s at startup.

**Plex is not a boot dependency (Task 13).**
Constructing `PlexServer` eagerly meant a Plex outage crashlooped the pod and
dropped webhooks. It is now lazy, so the service boots, serves `/healthz` and
queues webhooks while Plex is down. Plex connectivity failures inherit
`plex.resolve_max_attempts` rather than the generic retry cap.

**Titles are escaped before reaching ImageMagick (Tasks 7, 8).**
`caption:{text}` interpolated arbitrary titles. A leading `@` makes ImageMagick
read a local file, and `%` triggers property-escape expansion — "100% Wolf"
(2020) is a real film. Both are neutralised by a shared helper used by the
measuring and drawing paths, so the fitted size matches the drawn text.

**Smaller corrections.** `plex.resolve_max_attempts` was a declared config field
no code read. `Plex` appeared in the default provider order with no such client,
silently dropped; unknown provider names are now warned about, and `Plex` was
removed from the default. The TVDB token was cached forever with no refresh,
silently losing TVDB after a month. `ArtRequest.season_id` was never populated,
so TVDB could never serve season posters. `session.rollback()` now precedes
`fail()`, which previously stranded jobs after a database error. The unused
`structlog` dependency was dropped.

### Verification against the production deployment

Carried out read-only against the live cluster (`media` namespace, Posterizarr
pod and the shared NFS asset tree).

**Asset-path adoption — verified.** The real tree holds **18,008** image files.
Every one of them sits at the expected depth and matches the naming rules, with
**zero** unmatched names. The sweep also confirmed three decisions that had been
reasoned about rather than observed: specials exist (`Season00.jpg`), three-digit
episodes exist (`S01E100`–`S01E104`, so zero-padding had to be a minimum rather
than a fixed width), and 1,953 folder names contain braces such as
`{tmdb-940143}`, confirming the no-sanitisation rule. No folder currently
contains square brackets, which would be an ImageMagick frame-selector hazard —
and it would be harmless anyway, because every magick invocation operates on a
temp path and never on a path under the asset root.

**Command-level parity — verified.** Posterizarr's own
`ImageMagickCommands.log` was compared against the argv this code generates for
the same inputs. They match exactly, including the offset-sign concatenation
(`-geometry +0-150`) and the episode line `SEASON 3 • EPISODE 4` with unpadded
numbers and a U+2022 bullet (confirmed from the log bytes `e2 80 a2`). One
ordering difference in `-fill` was aligned so the two are byte-comparable.
`tests/test_production_parity.py` pins this using the real logged commands as
expected values.

**Pixel parity — verified.** Running this pipeline's exact command sequence
against the real source artwork reproduced the production asset
`/assets/Movies/All Souls (2023) {tmdb-940143}/poster.jpg` at **RMSE 0** — a
byte-identical 843,045-byte JPEG. `tests/test_golden.py` reproduces this from
harvested fixtures, and the full suite passes **233/233 with nothing skipped**
when run in the runtime container.

**The doubled-sign deviation — verified safe.** Production's logo composite
emits a malformed `-geometry +0++300`; this code emits `+0+300`. Compositing
the same image both ways under ImageMagick 7 gives a pixel difference of
`AE 0`, so ImageMagick ignores the extra sign and the outputs are identical.

**Caption escaping — verified.** Measured, not assumed: `caption:%%` renders one
literal `%` and `caption:%%%%` renders two, so a title like "100% Wolf" draws
correctly. A leading `@` turned out **not** to be treated as a file read by the
tested build, making that escape a harmless no-op; it is kept because the
behaviour is build- and policy-dependent.

**The runtime image is Q16-HDRI, and output is byte-identical.** Production
runs ImageMagick 7.1.2-29 **Q16-HDRI**. Debian's package is Q16 *without*
HDRI, and HDRI changes internal pixel maths: on that build the same render came
out 0.077% different (RMSE 0.00077). The image therefore uses Alpine, whose
ImageMagick is Q16-HDRI, and the build asserts `magick -version | grep HDRI`
so a base-image change cannot silently regress it. On that image the render is
**byte-identical** to the production asset — 843,045 bytes, RMSE 0. The exact
patch version does not matter (verified equal across 7.1.2-27 and 7.1.2-29);
the HDRI flag does. HDRI additionally allows float-format source art
(`.exr`/`.hdr`) to be supplied by hand.

**TVDB season resolution — verified against the live API.** A season entry's
`type` is a nested object
(`{"id": 1, "name": "Aired Order", "type": "official", "alternateName": null}`),
matching TVDB's own `SeasonType` schema, so preferring `type.type == "official"`
is correct. `/seasons/{id}/extended` really does return an `artwork` array —
the swagger omits the field — and its entries carry `id`, `image`, `language`,
`type`, `score`, `width`, `height` and `includesText`, with `type: 7` for a
season poster exactly as mapped. Note TVDB **does** require an API key: `POST
/login` takes `apikey` and returns a bearer token, and every endpoint but
`/countries` is behind `bearerAuth`.

**TMDB and Fanart parsers — verified against the live APIs.** A real TMDB
response parsed to 10 poster candidates, 2 of them textless via
`iso_639_1: null`, with the ladder correctly selecting a textless one and the
image URL correctly assembled. Fanart parsed 21 posters and 7 backgrounds for a
well-populated title, with `http://` upgraded to `https://` and language
handling intact; a sparse title correctly yielded zero rather than erroring.

### Still unverified — must be confirmed before cutover

One item remains:

1. **A real season and episode end to end against the live Plex server.** The
   title-card command sequence was matched against production output, and the
   resolver's season/episode navigation is unit-tested against fakes, but
   nothing has yet driven `resolve()` through to a rendered file for a
   non-movie against real Plex. This is the last check before cutover, and it
   writes a real asset, so it needs a deliberate go-ahead rather than being
   folded into a test run.
