# Phase 2a — Metadata Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gather per-item facts (ratings, genres, studio, dates, content rating) from IMDb, TMDB and MDBList, and write them into Plex per item — replacing Kometa's `mass_*_update` operations so they can be switched off.

**Architecture:** A `facts` module fetches from three sources and persists to a new `item_facts` table; a `plex/writer` module applies them to Plex through chunked batch edits. Both hang off the existing per-item pipeline, so a webhook already scoped to one item drives them. Badge rendering is **Phase 2b** and is deliberately not in this plan — the facts written here are what the badges will later read.

**Tech Stack:** Existing Phase 1 stack (Python 3.12, async SQLAlchemy + asyncpg, PostgreSQL 18, httpx, python-plexapi), plus the IMDb non-commercial TSV datasets.

**Spec:** `docs/superpowers/specs/2026-08-20-autoposter-design.md` §4 step 7, §9.2.

## Global Constraints

Every task's requirements implicitly include these. All values were verified against the production deployment or the providers' live APIs — do not substitute guesses.

- **Everything from Phase 1 still applies:** database clock for all timestamps (`func.now()`, never `datetime.now()` — app and DB clocks drift ~9.5s here), Postgres only, `ON CONFLICT` upserts for anything concurrent, `asyncio.to_thread` for blocking work, no secrets in cache keys, tests never make real network calls.
- **Every provider request goes through the Phase 1 cache** (`providers/fetch.py::fetch_json` + `ProviderCache`, 24h default TTL). This is the single biggest win over the tool being replaced, which re-fetches ratings for all ~13,000 episodes on every library run. Here an episode's season is fetched once and reused, and IMDb needs no per-episode request at all.
- **Operations run before overlays.** Kometa's default `run_order` is `operations, metadata, collections, overlays`, and its ratings overlay reads **Plex's own fields**, not the providers. So facts must be written to Plex before Phase 2b renders badges from them. Preserve that ordering.
- **Provider → Plex field mapping** (from the user's Kometa config, reproduce exactly):

  | Source | Plex field | plexapi method |
  |---|---|---|
  | IMDb rating | `rating` (critic) | `editCriticRating` |
  | TMDB `vote_average` | `audienceRating` | `editAudienceRating` |
  | MDBList Common Sense | `contentRating` | `editContentRating` |
  | TMDB `genres[].name` | genres | `addGenre` |
  | TMDB studio | `studio` | `editStudio` |
  | TMDB release date | `originallyAvailableAt` | `editOriginallyAvailable` |

- **Studio is the FIRST array element, unsorted:** `production_companies[0].name` for movies, `networks[0].name` for shows. Kometa does no preference ordering; sorting would produce diffs.
- **Content rating is written as a bare age integer string** — `"17"`, not `"17+"`. The `+` is added at render time by the badge in Phase 2b. Gate on MDBList's `commonsense` field being truthy, then take the value from `age_rating`.
- **Ratings are stored to one decimal.** Kometa writes `f"{float(v):.1f}"` and compares the formatted string before deciding to write. Match that, or every run will look like a change.
- **MDBList lookup keys are asymmetric:** movies by **TMDB** id (`/tmdb/movie/{id}/`), shows by **TVDB** id (`/tvdb/show/{id}/`). Auth is `?apikey=`.
- **MDBList quota is 10,000/day on this account** (active patron, verified via `GET /user`), and supporter status permits ~5 req/s. The library is ~2,236 titles, so a full backfill fits comfortably. Still cache: MDBList data is slow-moving.
- **IMDb ratings come from the bulk TSV datasets, never from scraping.** `https://datasets.imdbws.com/title.ratings.tsv.gz` (~8.2 MiB, 1.7M rows) and `title.episode.tsv.gz` (~52 MiB, 9.8M rows), refreshed daily, tab-separated, `\N` for null.
- **IMDb data is licensed for personal, non-commercial use only** and requires the attribution string *"Information courtesy of IMDb (https://www.imdb.com). Used with permission."* — add it to `README.md` alongside the other provider attributions.
- **This plan writes one item at a time**, batching that item's fields into a single HTTP call. Multi-item batching is not needed here because the pipeline is event-driven. Forward guidance for Phase 3's backfill, which does walk the library: Plex puts every rating key *and* every edit in the **URL query string**, so a whole-library batch can exceed proxy URL limits before it even reaches the CPU cost. Chunk at **100** (the number Kometa hard-codes, despite its own config default of 250) and group by `(librarySectionID, libtype)` — `_validateItems` rejects mixed types.
- **`saveMultiEdits()` clears `_edits` *before* issuing the request.** A failed call loses the queued edits and leaves the section out of batch mode. Snapshot and restore before retrying.
- **Retry transport errors only.** `ReadTimeout`/`ConnectTimeout`/`ConnectionError` are retryable; `BadRequest`/`NotFound`/`Unauthorized` are not — retrying them just repeats a rejected write. Note `requests` exceptions are **not** in plexapi's hierarchy, so catching `PlexApiException` alone misses timeouts.
- **`editField` has a falsy-value footgun:** it sends `value or ''`, so a rating of `0.0` clears the field instead of setting zero. Use the raw `edit()` form when a literal zero matters.
- **Field locking is deliberate.** Every `edit*` convenience method defaults to `locked=True`, which stops Plex's agent reverting the value on refresh — desirable for fields we own. Record what we lock.
- **Batch `addLabel` merges** rather than replacing — verified empirically against the production server. Existing labels survive.

---

## File Structure

```
src/autoposter/
  facts/__init__.py
  facts/models.py          ItemFacts dataclass — the gathered truth for one item
  facts/imdb.py            IMDb TSV download, parse and lookup
  facts/tmdb_facts.py      TMDB ratings, genres, studio, dates (incl. season-bulk episodes)
  facts/mdblist.py         MDBList client, Common Sense extraction
  facts/gather.py          orchestration: intent -> ItemFacts, persisted
  plex/writer.py           applies ItemFacts to Plex, chunked and retried
  db/models.py             (modify) add the item_facts table

alembic/versions/          (new) item_facts migration

tests/
  fixtures/facts/          recorded provider responses + a trimmed TSV sample
  test_imdb_dataset.py
  test_tmdb_facts.py
  test_mdblist.py
  test_facts_gather.py
  test_plex_writer.py
```

---

## Task 1: `item_facts` table

**Files:**
- Modify: `src/autoposter/db/models.py`
- Create: `src/autoposter/facts/__init__.py`, `src/autoposter/facts/models.py`
- Create: `alembic/versions/` (autogenerated migration)
- Test: `tests/test_item_facts.py`

**Interfaces:**
- Consumes: `Base`, `MediaItem` (Phase 1 `db/models.py`).
- Produces: SQLAlchemy model `ItemFacts` and the frozen dataclass `GatheredFacts` with fields `critic_rating: float | None`, `audience_rating: float | None`, `content_rating: str | None`, `genres: list[str]`, `studio: str | None`, `originally_available: date | None`, `sources: dict[str, str]`.

The dataclass is what the gatherer returns and the writer consumes; the table is where it persists between runs so a re-render can tell whether a displayed value actually moved.

- [ ] **Step 1: Write the failing tests**

`tests/test_item_facts.py`:

```python
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from autoposter.db.models import ItemFacts, MediaItem
from autoposter.facts.models import GatheredFacts


async def _item(session, rating_key="1"):
    item = MediaItem(rating_key=rating_key, library="Movies", kind="movie", title="X")
    session.add(item)
    await session.flush()
    return item


async def test_facts_roundtrip(session):
    item = await _item(session)
    session.add(ItemFacts(
        item_id=item.id, critic_rating=4.9, audience_rating=6.3,
        content_rating="17", genres=["Horror", "Drama"], studio="A24",
        originally_available=date(2023, 5, 12),
        sources={"critic_rating": "imdb", "audience_rating": "tmdb"},
    ))
    await session.commit()
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.critic_rating == pytest.approx(4.9)
    assert row.genres == ["Horror", "Drama"]
    assert row.sources["critic_rating"] == "imdb"


async def test_one_facts_row_per_item(session):
    item = await _item(session, "dup")
    session.add(ItemFacts(item_id=item.id))
    await session.commit()
    session.add(ItemFacts(item_id=item.id))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_all_facts_are_optional(session):
    """A brand new item has no facts yet; nothing may be NOT NULL."""
    item = await _item(session, "empty")
    session.add(ItemFacts(item_id=item.id))
    await session.commit()
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.critic_rating is None
    assert row.genres == []


async def test_fetched_at_comes_from_the_database_clock(session):
    from sqlalchemy import func

    item = await _item(session, "clock")
    session.add(ItemFacts(item_id=item.id))
    await session.commit()
    row = (await session.execute(select(ItemFacts))).scalar_one()
    db_now = (await session.execute(select(func.now()))).scalar_one()
    assert abs((db_now - row.fetched_at).total_seconds()) < 1


def test_gathered_facts_is_frozen_and_defaults_empty():
    import dataclasses

    facts = GatheredFacts()
    assert facts.genres == []
    assert facts.sources == {}
    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.studio = "nope"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/test_item_facts.py -v`
Expected: FAIL — `ImportError: cannot import name 'ItemFacts'`

- [ ] **Step 3: Add the model**

Append to `src/autoposter/db/models.py`:

```python
class ItemFacts(Base):
    """Current observed truth for one item.

    Separate from ``renders`` so metadata writes and image work are
    independently triggerable — a rating can change without the artwork
    changing, and vice versa.
    """

    __tablename__ = "item_facts"
    __table_args__ = (UniqueConstraint("item_id", name="uq_item_facts_item"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    critic_rating: Mapped[float | None] = mapped_column(Float)
    audience_rating: Mapped[float | None] = mapped_column(Float)
    content_rating: Mapped[str | None] = mapped_column(String(16))
    genres: Mapped[list] = mapped_column(JSONB, default=list)
    studio: Mapped[str | None] = mapped_column(Text)
    originally_available: Mapped[date | None] = mapped_column(Date)
    # Which provider supplied each field, so a later source change is traceable.
    sources: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

Extend the imports at the top of that file to include `Date` and `Float` from `sqlalchemy`, and `date` from `datetime`.

- [ ] **Step 4: Add the dataclass**

`src/autoposter/facts/models.py` (and an empty `src/autoposter/facts/__init__.py`):

```python
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class GatheredFacts:
    """What the providers said about one item, before it is written anywhere.

    Every field is optional: a provider may have nothing for this title, and a
    missing value must never be written to Plex as an empty one.
    """

    critic_rating: float | None = None
    audience_rating: float | None = None
    content_rating: str | None = None
    genres: list[str] = field(default_factory=list)
    studio: str | None = None
    originally_available: date | None = None
    sources: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(
            (
                self.critic_rating is not None,
                self.audience_rating is not None,
                self.content_rating,
                self.genres,
                self.studio,
                self.originally_available,
            )
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/test_item_facts.py -v`
Expected: PASS — 5 passed

- [ ] **Step 6: Generate and apply the migration**

```bash
export AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter
rtk proxy python -m alembic revision --autogenerate -m "item facts"
rtk proxy python -m alembic upgrade head
```

Confirm the table and its unique constraint exist:

```bash
docker compose exec -T postgres psql -U autoposter -d autoposter -c "\d item_facts"
```

Expected: `uq_item_facts_item` UNIQUE CONSTRAINT on `(item_id)`, and `fetched_at`/`updated_at` carrying `DEFAULT now()`.

- [ ] **Step 7: Commit**

```bash
git add src/autoposter/db/models.py src/autoposter/facts alembic tests/test_item_facts.py
git commit --no-gpg-sign -m "feat: item_facts table and GatheredFacts"
```

---

## Task 2: IMDb ratings from the bulk datasets

**Files:**
- Create: `src/autoposter/facts/imdb.py`
- Create: `tests/fixtures/facts/title.ratings.sample.tsv`, `tests/fixtures/facts/title.episode.sample.tsv`
- Test: `tests/test_imdb_dataset.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure parsing plus an httpx download).
- Produces:
  - `RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"`, `EPISODES_URL = "https://datasets.imdbws.com/title.episode.tsv.gz"`
  - `parse_ratings(lines: Iterable[str], wanted: set[str] | None = None) -> dict[str, float]`
  - `parse_episodes(lines: Iterable[str], parents: set[str]) -> dict[tuple[str, int, int], str]`
  - `async download_tsv(http, url) -> Iterator[str]`
  - `async refresh(session, http, movie_ids: set[str], show_ids: set[str]) -> int`
  - `async get_rating(session, imdb_id) -> float | None`
  - `async get_episode_rating(session, show_imdb_id, season, episode) -> float | None`

**Why filtered, not wholesale.** `title.episode.tsv` is 9.8 million rows; loading all of it daily to serve ~283 shows is waste. Both parsers therefore take a filter set and keep only matching rows. The library's own ids come from `media_items`, so the working set is small — a few thousand rows rather than eleven million.

- [ ] **Step 1: Create the sample fixtures**

`tests/fixtures/facts/title.ratings.sample.tsv` — real header, real rows, tab-separated:

```
tconst	averageRating	numVotes
tt0000001	5.7	2225
tt0111161	9.3	3000000
tt15239678	8.5	500000
tt9999999	7.0	12
```

`tests/fixtures/facts/title.episode.sample.tsv`:

```
tconst	parentTconst	seasonNumber	episodeNumber
tt0031458	tt32857063	\N	\N
tt9999999	tt11280740	2	3
tt9999998	tt11280740	2	4
tt8888888	tt00000000	1	1
```

Note the `\N` row: IMDb uses it for nulls and such rows must be skipped, not crash the parser.

- [ ] **Step 2: Write the failing tests**

`tests/test_imdb_dataset.py`:

```python
from pathlib import Path

import pytest
from sqlalchemy import select

from autoposter.db.models import ImdbEpisode, ImdbRating
from autoposter.facts.imdb import (
    get_episode_rating,
    get_rating,
    parse_episodes,
    parse_ratings,
    store_episodes,
    store_ratings,
)

FIXTURES = Path(__file__).parent / "fixtures" / "facts"


def _lines(name):
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def test_parse_ratings_skips_the_header():
    ratings = parse_ratings(_lines("title.ratings.sample.tsv"))
    assert "tconst" not in ratings
    assert ratings["tt0111161"] == pytest.approx(9.3)


def test_parse_ratings_filters_to_the_wanted_set():
    ratings = parse_ratings(_lines("title.ratings.sample.tsv"), wanted={"tt0111161"})
    assert ratings == {"tt0111161": pytest.approx(9.3)}


def test_parse_ratings_without_a_filter_keeps_everything():
    assert len(parse_ratings(_lines("title.ratings.sample.tsv"))) == 4


def test_parse_episodes_keys_by_show_season_episode():
    episodes = parse_episodes(_lines("title.episode.sample.tsv"), parents={"tt11280740"})
    assert episodes[("tt11280740", 2, 3)] == "tt9999999"
    assert episodes[("tt11280740", 2, 4)] == "tt9999998"


def test_parse_episodes_skips_null_season_and_episode():
    """IMDb writes \\N for unknown values; those rows must not crash or appear."""
    episodes = parse_episodes(_lines("title.episode.sample.tsv"), parents={"tt32857063"})
    assert episodes == {}


def test_parse_episodes_ignores_other_shows():
    episodes = parse_episodes(_lines("title.episode.sample.tsv"), parents={"tt11280740"})
    assert all(key[0] == "tt11280740" for key in episodes)


async def test_store_and_read_back_a_rating(session):
    await store_ratings(session, {"tt15239678": 8.5})
    assert await get_rating(session, "tt15239678") == pytest.approx(8.5)
    assert await get_rating(session, "tt00000000") is None


async def test_storing_twice_updates_rather_than_duplicating(session):
    await store_ratings(session, {"tt15239678": 8.5})
    await store_ratings(session, {"tt15239678": 8.6})
    rows = (await session.execute(select(ImdbRating))).scalars().all()
    assert len(rows) == 1
    assert rows[0].rating == pytest.approx(8.6)


async def test_episode_rating_joins_episode_to_rating(session):
    await store_episodes(session, {("tt11280740", 2, 3): "tt9999999"})
    await store_ratings(session, {"tt9999999": 7.0})
    assert await get_episode_rating(session, "tt11280740", 2, 3) == pytest.approx(7.0)


async def test_episode_rating_is_none_when_the_episode_is_unrated(session):
    await store_episodes(session, {("tt11280740", 2, 9): "tt7777777"})
    assert await get_episode_rating(session, "tt11280740", 2, 9) is None


async def test_episode_rating_is_none_for_an_unknown_episode(session):
    assert await get_episode_rating(session, "tt11280740", 9, 9) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/test_imdb_dataset.py -v`
Expected: FAIL — `ImportError: cannot import name 'ImdbRating'`

- [ ] **Step 4: Add the two storage tables**

Append to `src/autoposter/db/models.py`:

```python
class ImdbRating(Base):
    """One IMDb title rating, from the bulk dataset.

    Only ids the library actually contains are stored, so this stays in the
    thousands rather than the 1.7 million rows the dataset carries.
    """

    __tablename__ = "imdb_ratings"

    tconst: Mapped[str] = mapped_column(String(16), primary_key=True)
    rating: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImdbEpisode(Base):
    """Maps a show's IMDb id + season + episode to the episode's own IMDb id."""

    __tablename__ = "imdb_episodes"

    parent_tconst: Mapped[str] = mapped_column(String(16), primary_key=True)
    season_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    tconst: Mapped[str] = mapped_column(String(16), index=True)
```

- [ ] **Step 5: Write the parser and store**

`src/autoposter/facts/imdb.py`:

```python
"""IMDb ratings via the bulk non-commercial datasets.

IMDb publishes these files daily and forbids scraping the site for the same
data. Use is personal and non-commercial only, and requires the attribution
carried in README.md.
"""

import csv
import gzip
import logging
from collections.abc import Iterable, Iterator

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ImdbEpisode, ImdbRating

logger = logging.getLogger(__name__)

RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
EPISODES_URL = "https://datasets.imdbws.com/title.episode.tsv.gz"

_NULL = "\\N"


def parse_ratings(lines: Iterable[str], wanted: set[str] | None = None) -> dict[str, float]:
    """``{tconst: averageRating}``, optionally filtered to ``wanted``."""
    ratings: dict[str, float] = {}
    reader = csv.reader(lines, delimiter="\t")
    next(reader, None)  # header
    for row in reader:
        if len(row) < 2:
            continue
        tconst, average = row[0], row[1]
        if wanted is not None and tconst not in wanted:
            continue
        if average == _NULL:
            continue
        try:
            ratings[tconst] = float(average)
        except ValueError:
            continue
    return ratings


def parse_episodes(lines: Iterable[str], parents: set[str]) -> dict[tuple[str, int, int], str]:
    """``{(show_tconst, season, episode): episode_tconst}`` for the given shows."""
    episodes: dict[tuple[str, int, int], str] = {}
    reader = csv.reader(lines, delimiter="\t")
    next(reader, None)
    for row in reader:
        if len(row) < 4:
            continue
        tconst, parent, season, episode = row[0], row[1], row[2], row[3]
        if parent not in parents:
            continue
        if season == _NULL or episode == _NULL:
            continue
        try:
            episodes[(parent, int(season), int(episode))] = tconst
        except ValueError:
            continue
    return episodes


async def download_tsv(http: httpx.AsyncClient, url: str) -> Iterator[str]:
    """Stream a gzipped TSV and yield decoded lines.

    The files are tens of megabytes; they are decompressed in memory rather
    than written to disk, and never cached — IMDb refreshes them daily.
    """
    response = await http.get(url, follow_redirects=True, timeout=300)
    response.raise_for_status()
    text = gzip.decompress(response.content).decode("utf-8")
    return iter(text.splitlines())


async def store_ratings(session: AsyncSession, ratings: dict[str, float]) -> None:
    for tconst, rating in ratings.items():
        await session.execute(
            insert(ImdbRating)
            .values(tconst=tconst, rating=rating)
            .on_conflict_do_update(index_elements=["tconst"], set_={"rating": rating})
        )
    await session.commit()


async def store_episodes(
    session: AsyncSession, episodes: dict[tuple[str, int, int], str]
) -> None:
    for (parent, season, episode), tconst in episodes.items():
        await session.execute(
            insert(ImdbEpisode)
            .values(
                parent_tconst=parent,
                season_number=season,
                episode_number=episode,
                tconst=tconst,
            )
            .on_conflict_do_update(
                index_elements=["parent_tconst", "season_number", "episode_number"],
                set_={"tconst": tconst},
            )
        )
    await session.commit()


async def get_rating(session: AsyncSession, imdb_id: str) -> float | None:
    return (
        await session.execute(select(ImdbRating.rating).where(ImdbRating.tconst == imdb_id))
    ).scalar_one_or_none()


async def get_episode_rating(
    session: AsyncSession, show_imdb_id: str, season: int, episode: int
) -> float | None:
    """Join the episode map to the ratings table.

    Returns ``None`` when the episode is unknown *or* known but unrated — both
    mean "no badge value", and the caller does not need to tell them apart.
    """
    tconst = (
        await session.execute(
            select(ImdbEpisode.tconst).where(
                ImdbEpisode.parent_tconst == show_imdb_id,
                ImdbEpisode.season_number == season,
                ImdbEpisode.episode_number == episode,
            )
        )
    ).scalar_one_or_none()
    if tconst is None:
        return None
    return await get_rating(session, tconst)


async def refresh(
    session: AsyncSession,
    http: httpx.AsyncClient,
    movie_ids: set[str],
    show_ids: set[str],
) -> int:
    """Reload both datasets, keeping only rows this library needs.

    Returns the number of ratings stored. Episodes are resolved first so their
    ids can be added to the ratings filter in a single pass.
    """
    episodes: dict[tuple[str, int, int], str] = {}
    if show_ids:
        episodes = parse_episodes(await download_tsv(http, EPISODES_URL), show_ids)
        await store_episodes(session, episodes)
        logger.info("imdb: kept %d episode rows for %d shows", len(episodes), len(show_ids))

    wanted = set(movie_ids) | set(episodes.values())
    ratings = parse_ratings(await download_tsv(http, RATINGS_URL), wanted) if wanted else {}
    await store_ratings(session, ratings)
    logger.info("imdb: stored %d ratings", len(ratings))
    return len(ratings)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/test_imdb_dataset.py -v`
Expected: PASS — 11 passed

- [ ] **Step 7: Generate the migration against a CLEAN database**

`tests/conftest.py` points at the same database and calls `create_all` on every
run, so autogenerating **after** running the suite produces an empty migration:
Alembic correctly sees no difference, and the result silently creates nothing on
a fresh deployment. Task 1 shipped exactly that bug, and the container runs
`alembic upgrade head` at startup, so it reaches production. Reset first, replay
the existing migrations, and only then autogenerate.

```bash
docker compose down -v && docker compose up -d postgres && sleep 8
export AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter
rtk proxy python -m alembic upgrade head
rtk proxy python -m alembic revision --autogenerate -m "imdb dataset tables"
```

Open the generated file and confirm it contains real `op.create_table` calls for
**both** `imdb_ratings` and `imdb_episodes`, including the composite primary key
on `(parent_tconst, season_number, episode_number)`. An `upgrade()` body of
`pass` means the diff was taken against a database that already had the tables —
start over from the reset. Then apply and verify:

```bash
rtk proxy python -m alembic upgrade head
docker compose exec -T postgres psql -U autoposter -d autoposter -c "\d imdb_episodes"
rtk proxy python -m pytest tests/test_migrations.py -v
```

`tests/test_migrations.py` (added in Task 1) applies migrations to its own
scratch database and fails on any drift from the models — it is the guard
against repeating this.

- [ ] **Step 8: Commit**

```bash
git add src/autoposter/facts/imdb.py src/autoposter/db/models.py alembic \
        tests/test_imdb_dataset.py tests/fixtures/facts
git commit --no-gpg-sign -m "feat: IMDb ratings from the bulk datasets"
```

---

## Task 3: TMDB facts

**Files:**
- Create: `src/autoposter/facts/tmdb_facts.py`
- Create: `tests/fixtures/facts/tmdb_movie.json`, `tests/fixtures/facts/tmdb_show.json`, `tests/fixtures/facts/tmdb_season.json`
- Test: `tests/test_tmdb_facts.py`

**Interfaces:**
- Consumes: `GatheredFacts` (Task 1); the existing `providers.cache.ProviderCache` and `providers.fetch.fetch_json` seam from Phase 1.
- Produces:
  - `parse_movie_facts(payload: dict) -> GatheredFacts`
  - `parse_show_facts(payload: dict) -> GatheredFacts`
  - `parse_season_episode_ratings(payload: dict) -> dict[int, float]` — episode number → `vote_average`
  - `TMDBFactsClient` with `async movie(tmdb_id)`, `async show(tmdb_id)`, `async season_episode_ratings(tmdb_id, season_number)`.

**Why season-bulk.** `GET /3/tv/{id}/season/{n}` returns the whole `episodes[]` array with `vote_average` on each. Fetching per episode would be ~13,000 requests for this library; per season it is ~3,000, and it does not depend on Plex's episode GUIDs carrying a `tmdb://` scheme (they do not when the TVDB agent is used).

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/facts/tmdb_movie.json` (trimmed from a real response):

```json
{
  "id": 940143,
  "title": "All Souls",
  "vote_average": 6.3,
  "vote_count": 41,
  "release_date": "2023-05-12",
  "genres": [{"id": 27, "name": "Horror"}, {"id": 18, "name": "Drama"}],
  "production_companies": [
    {"id": 1, "name": "First Studio"},
    {"id": 2, "name": "Second Studio"}
  ]
}
```

`tests/fixtures/facts/tmdb_show.json`:

```json
{
  "id": 95396,
  "name": "Severance",
  "vote_average": 8.4,
  "vote_count": 3000,
  "first_air_date": "2022-02-18",
  "genres": [{"id": 18, "name": "Drama"}, {"id": 9648, "name": "Mystery"}],
  "networks": [{"id": 2552, "name": "Apple TV+"}, {"id": 99, "name": "Other"}],
  "production_companies": [{"id": 11073, "name": "Should Not Be Used"}]
}
```

`tests/fixtures/facts/tmdb_season.json`:

```json
{
  "id": 3624,
  "season_number": 2,
  "episodes": [
    {"id": 1, "episode_number": 1, "vote_average": 7.8, "name": "Hello, Ms. Cobel"},
    {"id": 2, "episode_number": 2, "vote_average": 8.1, "name": "Goodbye, Mrs. Selvig"},
    {"id": 3, "episode_number": 3, "vote_average": 0.0, "name": "Who Is Alive?"}
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_tmdb_facts.py`:

```python
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from autoposter.facts.tmdb_facts import (
    TMDBFactsClient,
    parse_movie_facts,
    parse_season_episode_ratings,
    parse_show_facts,
)

FIXTURES = Path(__file__).parent / "fixtures" / "facts"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_movie_facts():
    facts = parse_movie_facts(load("tmdb_movie.json"))
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.genres == ["Horror", "Drama"]
    assert facts.originally_available == date(2023, 5, 12)
    assert facts.sources["audience_rating"] == "tmdb"


def test_movie_studio_is_the_first_production_company():
    """Kometa takes companies[0] with no sorting — match it exactly."""
    assert parse_movie_facts(load("tmdb_movie.json")).studio == "First Studio"


def test_show_studio_is_the_first_network_not_a_production_company():
    facts = parse_show_facts(load("tmdb_show.json"))
    assert facts.studio == "Apple TV+"


def test_show_uses_first_air_date():
    assert parse_show_facts(load("tmdb_show.json")).originally_available == date(2022, 2, 18)


def test_missing_optional_fields_do_not_raise():
    facts = parse_movie_facts({"id": 1})
    assert facts.audience_rating is None
    assert facts.genres == []
    assert facts.studio is None
    assert facts.originally_available is None


def test_empty_company_list_gives_no_studio():
    assert parse_movie_facts({"production_companies": []}).studio is None


def test_malformed_release_date_is_ignored():
    assert parse_movie_facts({"release_date": ""}).originally_available is None
    assert parse_movie_facts({"release_date": "not-a-date"}).originally_available is None


def test_season_episode_ratings_are_keyed_by_episode_number():
    ratings = parse_season_episode_ratings(load("tmdb_season.json"))
    assert ratings[1] == pytest.approx(7.8)
    assert ratings[2] == pytest.approx(8.1)


def test_zero_rating_is_treated_as_absent():
    """TMDB reports 0.0 for unrated episodes; writing that would show '0%'."""
    assert 3 not in parse_season_episode_ratings(load("tmdb_season.json"))


async def test_client_requests_the_season_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("tmdb_season.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("token", http)
        ratings = await client.season_episode_ratings(95396, 2)

    assert "/tv/95396/season/2" in seen["url"]
    assert ratings[1] == pytest.approx(7.8)


async def test_client_sends_a_bearer_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=load("tmdb_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await TMDBFactsClient("tok", http).movie(940143)

    assert seen["auth"] == "Bearer tok"


async def test_client_returns_empty_facts_on_404():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))
    ) as http:
        facts = await TMDBFactsClient("tok", http).movie(1)
    assert facts.is_empty()


async def test_repeated_season_lookups_hit_the_cache_not_the_api(session):
    """A season-pack import asks for one season repeatedly — pay once.

    This is the whole reason episode ratings are affordable per-item: without
    it, importing a 10-episode season means 10 identical TMDB requests.
    """
    from autoposter.providers.cache import ProviderCache

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("tmdb_season.json"))

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, cache=cache, cache_ttl_seconds=3600)
        first = await client.season_episode_ratings(95396, 2)
        second = await client.season_episode_ratings(95396, 2)

    assert first == second
    assert len(calls) == 1, "second lookup should have been served from the cache"


async def test_without_a_cache_every_lookup_hits_the_api():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("tmdb_season.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http)
        await client.season_episode_ratings(95396, 2)
        await client.season_episode_ratings(95396, 2)

    assert len(calls) == 2
```

`session_factory_for` is a one-line helper the implementer adds to
`tests/conftest.py`, returning a callable that yields the existing test
session so `ProviderCache` can open its own context:

```python
def session_factory_for(session):
    """Adapt the function-scoped test session to ProviderCache's factory API."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory():
        yield session

    return factory
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/test_tmdb_facts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.facts.tmdb_facts'`

- [ ] **Step 4: Implement**

`src/autoposter/facts/tmdb_facts.py`:

```python
import logging
from datetime import date, datetime

import httpx

from autoposter.facts.models import GatheredFacts
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"


def _as_date(value: object) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _rating(payload: dict) -> float | None:
    """``vote_average``, treating 0 as absent.

    TMDB reports 0.0 for titles nobody has voted on. Writing that would render
    a "0%" badge, which is worse than no badge.
    """
    value = payload.get("vote_average")
    if value is None:
        return None
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    return rating if rating > 0 else None


def _genres(payload: dict) -> list[str]:
    return [g["name"] for g in payload.get("genres") or [] if g.get("name")]


def _first_name(entries: object) -> str | None:
    """First element's name, unsorted — the rule Kometa uses."""
    if isinstance(entries, list) and entries and isinstance(entries[0], dict):
        return entries[0].get("name")
    return None


def parse_movie_facts(payload: dict) -> GatheredFacts:
    rating = _rating(payload)
    studio = _first_name(payload.get("production_companies"))
    genres = _genres(payload)
    released = _as_date(payload.get("release_date"))
    sources = {}
    if rating is not None:
        sources["audience_rating"] = "tmdb"
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", released)):
        if value:
            sources[key] = "tmdb"
    return GatheredFacts(
        audience_rating=rating,
        genres=genres,
        studio=studio,
        originally_available=released,
        sources=sources,
    )


def parse_show_facts(payload: dict) -> GatheredFacts:
    """Shows take their studio from ``networks``, not ``production_companies``."""
    rating = _rating(payload)
    studio = _first_name(payload.get("networks"))
    genres = _genres(payload)
    aired = _as_date(payload.get("first_air_date"))
    sources = {}
    if rating is not None:
        sources["audience_rating"] = "tmdb"
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", aired)):
        if value:
            sources[key] = "tmdb"
    return GatheredFacts(
        audience_rating=rating,
        genres=genres,
        studio=studio,
        originally_available=aired,
        sources=sources,
    )


def parse_season_episode_ratings(payload: dict) -> dict[int, float]:
    """``{episode_number: vote_average}`` for one season, skipping unrated."""
    ratings: dict[int, float] = {}
    for episode in payload.get("episodes") or []:
        number = episode.get("episode_number")
        rating = _rating(episode)
        if number is None or rating is None:
            continue
        ratings[int(number)] = rating
    return ratings


class TMDBFactsClient:
    """Reads ratings and descriptive metadata from TMDB.

    Separate from the artwork client because it asks different endpoints for
    different reasons; they share only the bearer token.

    Every request goes through the Phase 1 cache seam. That matters most for
    episodes: one season-pack import asks for the same season's ratings once
    per episode, and without the cache that is one API call each. With it, the
    first episode pays and the rest are free until the TTL expires.
    """

    name = "TMDB"

    def __init__(
        self,
        token: str,
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
    ):
        self._token = token
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "accept": "application/json"}

    async def _get(self, path: str) -> dict | None:
        url = f"{BASE_URL}{path}"
        return await fetch_json(
            method="GET",
            url=url,
            params=None,
            request=lambda: self._client.get(url, headers=self._headers()),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )

    async def movie(self, tmdb_id: int) -> GatheredFacts:
        payload = await self._get(f"/movie/{tmdb_id}")
        return parse_movie_facts(payload) if payload else GatheredFacts()

    async def show(self, tmdb_id: int) -> GatheredFacts:
        payload = await self._get(f"/tv/{tmdb_id}")
        return parse_show_facts(payload) if payload else GatheredFacts()

    async def season_episode_ratings(self, tmdb_id: int, season_number: int) -> dict[int, float]:
        """One request covers every episode of the season."""
        payload = await self._get(f"/tv/{tmdb_id}/season/{season_number}")
        return parse_season_episode_ratings(payload) if payload else {}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/test_tmdb_facts.py -v`
Expected: PASS — 14 passed

- [ ] **Step 6: Commit**

```bash
git add src/autoposter/facts/tmdb_facts.py tests/test_tmdb_facts.py tests/fixtures/facts
git commit --no-gpg-sign -m "feat: TMDB ratings, genres, studio and dates"
```

---

## Task 4: MDBList and Common Sense

**Files:**
- Create: `src/autoposter/facts/mdblist.py`
- Create: `tests/fixtures/facts/mdblist_movie.json`
- Test: `tests/test_mdblist.py`

**Interfaces:**
- Consumes: `GatheredFacts` (Task 1).
- Produces: `parse_content_rating(payload: dict) -> str | None`, and `MDBListClient` with `async content_rating(tmdb_id=None, tvdb_id=None, is_movie=True) -> str | None`.

**The two rules that matter.** Kometa gates on the `commonsense` field being truthy and then takes the value from `age_rating` — a title can carry an `age_rating` from a non-Common-Sense source, and using it would write a rating Kometa never would. And the value written is the **bare age integer as a string** (`"17"`); the `+` belongs to the badge, added in Phase 2b.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/facts/mdblist_movie.json`:

```json
{
  "title": "All Souls",
  "year": 2023,
  "imdbid": "tt14316486",
  "tmdbid": 940143,
  "type": "movie",
  "certification": "R",
  "commonsense": 1,
  "age_rating": 17,
  "score": 55,
  "ratings": [
    {"source": "imdb", "value": 4.9, "score": 49, "votes": 981},
    {"source": "tmdb", "value": 6.3, "score": 63, "votes": 41}
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_mdblist.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from autoposter.facts.mdblist import MDBListClient, parse_content_rating

FIXTURES = Path(__file__).parent / "fixtures" / "facts"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_content_rating_is_the_bare_age_as_a_string():
    """The '+' is added by the badge, not stored."""
    assert parse_content_rating(load("mdblist_movie.json")) == "17"


def test_absent_when_commonsense_is_falsy():
    """age_rating can come from non-Common-Sense sources; only use it when gated."""
    payload = load("mdblist_movie.json") | {"commonsense": 0}
    assert parse_content_rating(payload) is None


def test_absent_when_commonsense_key_is_missing():
    payload = {k: v for k, v in load("mdblist_movie.json").items() if k != "commonsense"}
    assert parse_content_rating(payload) is None


def test_absent_when_age_rating_is_missing():
    payload = {k: v for k, v in load("mdblist_movie.json").items() if k != "age_rating"}
    assert parse_content_rating(payload) is None


def test_age_rating_zero_is_not_treated_as_absent():
    """Age 0 is a legitimate Common Sense value and must survive."""
    payload = load("mdblist_movie.json") | {"age_rating": 0}
    assert parse_content_rating(payload) == "0"


def test_string_age_rating_is_accepted():
    payload = load("mdblist_movie.json") | {"age_rating": "13"}
    assert parse_content_rating(payload) == "13"


async def test_movies_are_looked_up_by_tmdb_id():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("mdblist_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rating = await MDBListClient("KEY", http).content_rating(tmdb_id=940143, is_movie=True)

    assert "/tmdb/movie/940143/" in seen["url"]
    assert rating == "17"


async def test_shows_are_looked_up_by_tvdb_id():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("mdblist_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await MDBListClient("KEY", http).content_rating(tvdb_id=371980, is_movie=False)

    assert "/tvdb/show/371980/" in seen["url"]


async def test_the_api_key_is_sent_as_a_query_parameter():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("mdblist_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await MDBListClient("SECRET", http).content_rating(tmdb_id=1, is_movie=True)

    assert "apikey=SECRET" in seen["url"]


async def test_missing_identifier_makes_no_request():
    def handler(request):
        raise AssertionError("should not have been called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await MDBListClient("KEY", http).content_rating(is_movie=True) is None


async def test_404_returns_none():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))
    ) as http:
        assert await MDBListClient("KEY", http).content_rating(tmdb_id=1, is_movie=True) is None


async def test_quota_exhaustion_raises_a_distinct_error():
    """MDBList answers 200 with an error body when the daily budget is gone."""
    from autoposter.facts.mdblist import MDBListLimitReached

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": "API Limit Reached!"})
        )
    ) as http:
        with pytest.raises(MDBListLimitReached):
            await MDBListClient("KEY", http).content_rating(tmdb_id=1, is_movie=True)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/test_mdblist.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.facts.mdblist'`

- [ ] **Step 4: Implement**

`src/autoposter/facts/mdblist.py`:

```python
import logging

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.mdblist.com"

# MDBList answers 200 with an error body rather than a 429 when the daily
# budget is spent. The account in use has a 10,000/day allowance.
_LIMIT_ERRORS = {"API Limit Reached!", "API Rate Limit Reached!"}


class MDBListLimitReached(Exception):
    """The daily request budget is exhausted; stop asking until tomorrow."""


def parse_content_rating(payload: dict) -> str | None:
    """Common Sense age rating as a bare string, or ``None``.

    Gated on ``commonsense`` being truthy: ``age_rating`` alone can be
    populated from other sources, and using it ungated would write values the
    tool being replaced never would. The ``+`` suffix is a rendering concern.
    """
    if not payload.get("commonsense"):
        return None
    age = payload.get("age_rating")
    if age is None or age == "":
        return None
    return str(age)


class MDBListClient:
    """Reads Common Sense age ratings.

    Movies are addressed by TMDB id and shows by TVDB id — the asymmetry is
    MDBList's, not ours.
    """

    name = "MDBList"

    def __init__(self, apikey: str, client: httpx.AsyncClient):
        self._apikey = apikey
        self._client = client

    async def content_rating(
        self,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        is_movie: bool = True,
    ) -> str | None:
        identifier = tmdb_id if is_movie else tvdb_id
        if identifier is None:
            return None
        provider = "tmdb" if is_movie else "tvdb"
        media_type = "movie" if is_movie else "show"
        response = await self._client.get(
            f"{BASE_URL}/{provider}/{media_type}/{identifier}/",
            params={"apikey": self._apikey},
            headers={"User-Agent": "autoposter"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if error in _LIMIT_ERRORS:
            raise MDBListLimitReached(error)
        if error:
            logger.warning("mdblist error for %s %s: %s", provider, identifier, error)
            return None
        return parse_content_rating(payload)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/test_mdblist.py -v`
Expected: PASS — 12 passed

- [ ] **Step 6: Commit**

```bash
git add src/autoposter/facts/mdblist.py tests/test_mdblist.py tests/fixtures/facts
git commit --no-gpg-sign -m "feat: Common Sense content ratings via MDBList"
```

---

## Task 5: Gathering facts for one item

**Files:**
- Create: `src/autoposter/facts/gather.py`
- Test: `tests/test_facts_gather.py`

**Interfaces:**
- Consumes: `GatheredFacts`, `ItemFacts` (Task 1); `imdb.get_rating` / `imdb.get_episode_rating` (Task 2); `TMDBFactsClient` (Task 3); `MDBListClient` (Task 4); `ResolvedItem` (Phase 1 `plex/client.py`).
- Produces:
  - `async gather_facts(session, item: ResolvedItem, tmdb: TMDBFactsClient, mdblist: MDBListClient) -> GatheredFacts`
  - `async persist_facts(session, media_item_id: int, facts: GatheredFacts) -> ItemFacts`
  - `format_critic(value: float | None) -> str | None` and `format_audience(value: float | None) -> str | None`

**Per-kind rules.** A movie takes all six fields. A show takes the same, minus nothing. A **season** has no facts of its own — its badge inherits the show's content rating in Phase 2b, so gathering returns empty. An **episode** takes only the two ratings: critic from the IMDb episode join, audience from the TMDB season-bulk lookup.

**Display formatting is defined here** because it decides re-rendering. Critic renders as one decimal *always*, including the trailing zero (`9.0`, not `9`). Audience is `int(value * 10)` — **truncating, not rounding** — with a literal `%`. Both match the tool being replaced; rounding instead of truncating would differ on any value carrying more than one decimal.

- [ ] **Step 1: Write the failing tests**

`tests/test_facts_gather.py`:

```python
from datetime import date

import pytest
from sqlalchemy import select

from autoposter.db.models import ItemFacts, MediaItem
from autoposter.facts.gather import (
    format_audience,
    format_critic,
    gather_facts,
    persist_facts,
)
from autoposter.facts.models import GatheredFacts
from autoposter.plex.client import ResolvedItem


def item(kind="movie", season=None, episode=None):
    return ResolvedItem(
        rating_key="1", library="Movies", kind=kind, title="X", year=2023,
        season_number=season, episode_number=episode, root_folder="X",
        file_path=None, art_url=None, tmdb_id=940143, tvdb_id=371980,
        imdb_id="tt14316486", parent_rating_key=None,
    )


class FakeTMDB:
    def __init__(self):
        self.season_calls = []

    async def movie(self, tmdb_id):
        return GatheredFacts(audience_rating=6.3, genres=["Horror"], studio="A24",
                             originally_available=date(2023, 5, 12),
                             sources={"audience_rating": "tmdb"})

    async def show(self, tmdb_id):
        return GatheredFacts(audience_rating=8.4, genres=["Drama"], studio="Apple TV+",
                             sources={"audience_rating": "tmdb"})

    async def season_episode_ratings(self, tmdb_id, season_number):
        self.season_calls.append((tmdb_id, season_number))
        return {3: 7.8}


class FakeMDBList:
    def __init__(self, value="17"):
        self.value = value
        self.calls = 0

    async def content_rating(self, tmdb_id=None, tvdb_id=None, is_movie=True):
        self.calls += 1
        return self.value


def test_critic_always_renders_one_decimal():
    assert format_critic(4.9) == "4.9"
    assert format_critic(9.0) == "9.0"
    assert format_critic(10.0) == "10.0"
    assert format_critic(None) is None


def test_audience_truncates_rather_than_rounds():
    assert format_audience(6.3) == "63%"
    assert format_audience(7.0) == "70%"
    assert format_audience(10.0) == "100%"
    assert format_audience(8.65) == "86%"   # truncation, not 87
    assert format_audience(None) is None


async def test_movie_gathers_every_field(session):
    from autoposter.facts.imdb import store_ratings

    await store_ratings(session, {"tt14316486": 4.9})
    facts = await gather_facts(session, item(), FakeTMDB(), FakeMDBList())
    assert facts.critic_rating == pytest.approx(4.9)
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.content_rating == "17"
    assert facts.genres == ["Horror"]
    assert facts.studio == "A24"
    assert facts.sources["critic_rating"] == "imdb"
    assert facts.sources["content_rating"] == "mdb_commonsense"


async def test_show_uses_the_show_endpoints(session):
    facts = await gather_facts(session, item(kind="show"), FakeTMDB(), FakeMDBList())
    assert facts.studio == "Apple TV+"
    assert facts.content_rating == "17"


async def test_season_has_no_facts_of_its_own(session):
    mdblist = FakeMDBList()
    facts = await gather_facts(session, item(kind="season", season=2), FakeTMDB(), mdblist)
    assert facts.is_empty()
    assert mdblist.calls == 0


async def test_episode_takes_only_the_two_ratings(session):
    from autoposter.facts.imdb import store_episodes, store_ratings

    await store_episodes(session, {("tt14316486", 2, 3): "tt9999999"})
    await store_ratings(session, {"tt9999999": 7.1})
    tmdb = FakeTMDB()
    facts = await gather_facts(
        session, item(kind="episode", season=2, episode=3), tmdb, FakeMDBList()
    )
    assert facts.critic_rating == pytest.approx(7.1)
    assert facts.audience_rating == pytest.approx(7.8)
    assert facts.genres == []
    assert facts.studio is None
    assert facts.content_rating is None
    assert tmdb.season_calls == [(940143, 2)]


async def test_episode_without_imdb_data_still_returns_audience(session):
    facts = await gather_facts(
        session, item(kind="episode", season=2, episode=3), FakeTMDB(), FakeMDBList()
    )
    assert facts.critic_rating is None
    assert facts.audience_rating == pytest.approx(7.8)


async def test_persist_is_idempotent(session):
    media = MediaItem(rating_key="p1", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(critic_rating=4.9))
    await persist_facts(session, media.id, GatheredFacts(critic_rating=5.1))
    rows = (await session.execute(select(ItemFacts))).scalars().all()
    assert len(rows) == 1
    assert rows[0].critic_rating == pytest.approx(5.1)


async def test_mdblist_limit_does_not_abort_the_other_facts(session):
    from autoposter.facts.mdblist import MDBListLimitReached

    class Exhausted:
        async def content_rating(self, **kwargs):
            raise MDBListLimitReached("API Limit Reached!")

    facts = await gather_facts(session, item(), FakeTMDB(), Exhausted())
    assert facts.content_rating is None
    assert facts.audience_rating == pytest.approx(6.3)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/test_facts_gather.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.facts.gather'`

- [ ] **Step 3: Implement**

`src/autoposter/facts/gather.py`:

```python
import logging
from dataclasses import replace

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemFacts
from autoposter.facts import imdb
from autoposter.facts.mdblist import MDBListLimitReached
from autoposter.facts.models import GatheredFacts
from autoposter.plex.client import ResolvedItem

logger = logging.getLogger(__name__)


def format_critic(value: float | None) -> str | None:
    """One decimal, always — ``9.0`` rather than ``9``.

    This is the string the badge will draw, so it is also what decides whether
    a re-render is needed.
    """
    return None if value is None else f"{float(value):.1f}"


def format_audience(value: float | None) -> str | None:
    """``int(value * 10)`` then a literal ``%``.

    Truncates rather than rounds, matching the tool being replaced: ``8.65``
    renders ``86%``, not ``87%``.
    """
    return None if value is None else f"{int(float(value) * 10)}%"


async def _critic_rating(session: AsyncSession, item: ResolvedItem) -> float | None:
    if not item.imdb_id:
        return None
    if item.kind == "episode":
        if item.season_number is None or item.episode_number is None:
            return None
        return await imdb.get_episode_rating(
            session, item.imdb_id, item.season_number, item.episode_number
        )
    return await imdb.get_rating(session, item.imdb_id)


async def gather_facts(
    session: AsyncSession, item: ResolvedItem, tmdb, mdblist
) -> GatheredFacts:
    """Collect everything the providers know about one item.

    A season carries no facts of its own — its badge inherits the show's
    content rating — so it returns empty rather than making pointless calls.
    """
    if item.kind == "season":
        return GatheredFacts()

    facts = GatheredFacts()
    sources: dict[str, str] = {}

    if item.kind == "episode":
        audience = None
        if item.tmdb_id and item.season_number is not None:
            ratings = await tmdb.season_episode_ratings(item.tmdb_id, item.season_number)
            audience = ratings.get(item.episode_number)
        if audience is not None:
            sources["audience_rating"] = "tmdb"
        facts = GatheredFacts(audience_rating=audience)
    elif item.tmdb_id:
        facts = await (tmdb.movie(item.tmdb_id) if item.kind == "movie"
                       else tmdb.show(item.tmdb_id))
        sources.update(facts.sources)

    critic = await _critic_rating(session, item)
    if critic is not None:
        sources["critic_rating"] = "imdb"

    content_rating = None
    if item.kind in ("movie", "show"):
        try:
            content_rating = await mdblist.content_rating(
                tmdb_id=item.tmdb_id, tvdb_id=item.tvdb_id, is_movie=item.kind == "movie"
            )
        except MDBListLimitReached:
            # Budget spent for today. Everything else we gathered is still
            # good; the content rating fills in on a later pass.
            logger.warning("mdblist daily limit reached; skipping content rating")
        if content_rating:
            sources["content_rating"] = "mdb_commonsense"

    return replace(
        facts, critic_rating=critic, content_rating=content_rating, sources=sources
    )


async def persist_facts(
    session: AsyncSession, media_item_id: int, facts: GatheredFacts
) -> ItemFacts:
    """Upsert the row, so concurrent workers on one item cannot collide."""
    values = {
        "item_id": media_item_id,
        "critic_rating": facts.critic_rating,
        "audience_rating": facts.audience_rating,
        "content_rating": facts.content_rating,
        "genres": facts.genres,
        "studio": facts.studio,
        "originally_available": facts.originally_available,
        "sources": facts.sources,
    }
    mutable = {k: v for k, v in values.items() if k != "item_id"}
    # Database clock, like every other timestamp in this project.
    mutable["fetched_at"] = func.now()
    await session.execute(
        insert(ItemFacts).values(**values).on_conflict_do_update(
            index_elements=["item_id"], set_=mutable
        )
    )
    await session.commit()
    return (
        await session.execute(select(ItemFacts).where(ItemFacts.item_id == media_item_id))
    ).scalar_one()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/test_facts_gather.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoposter/facts/gather.py tests/test_facts_gather.py
git commit --no-gpg-sign -m "feat: gather per-item facts from IMDb, TMDB and MDBList"
```

---

## Task 6: Writing facts to Plex

**Files:**
- Create: `src/autoposter/plex/writer.py`
- Test: `tests/test_plex_writer.py`

**Interfaces:**
- Consumes: `GatheredFacts` (Task 1), `format_critic`/`format_audience` (Task 5).
- Produces:
  - `plan_edits(item, facts) -> dict[str, object]` — the field/value pairs that actually differ from what Plex already holds.
  - `async apply_facts(plex_item, facts) -> dict[str, object]` — applies them in **one** HTTP call, returns what was written.
  - `WRITABLE_BY_KIND: dict[str, set[str]]`

**What this must not do.** Seasons have no `contentRating`, `studio`, `genres` or `originallyAvailableAt` — calling those raises `AttributeError`. Episodes have no genres or studio. The map above encodes that, and `plan_edits` filters by it.

**Only write what changed.** Comparing the *formatted* value against what Plex already holds avoids a no-op write on every pass, which would otherwise churn Plex's database and its change notifications for 16,000 items.

**Field locking is intentional.** Writing with `locked=True` stops Plex's metadata agent reverting these values on its next refresh. That is the desired behaviour for fields this tool owns, and it mirrors what the tool being replaced does.

- [ ] **Step 1: Write the failing tests**

`tests/test_plex_writer.py`:

```python
from datetime import date

import pytest

from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import WRITABLE_BY_KIND, apply_facts, plan_edits


class FakeItem:
    """Models the plexapi surface the writer touches."""

    def __init__(self, kind="movie", **attrs):
        self.type = kind
        self.rating = attrs.get("rating")
        self.audienceRating = attrs.get("audienceRating")
        self.contentRating = attrs.get("contentRating")
        self.studio = attrs.get("studio")
        self.originallyAvailableAt = attrs.get("originallyAvailableAt")
        self.genres = [type("G", (), {"tag": g})() for g in attrs.get("genres", [])]
        self.batched = False
        self.saved = False
        self.edits = {}

    def batchEdits(self):
        self.batched = True
        return self

    def saveEdits(self):
        self.saved = True
        return self

    def edit(self, **kwargs):
        self.edits.update(kwargs)
        return self

    def addGenre(self, genres, locked=True):
        self.edits["genres"] = genres
        return self


def test_plan_includes_changed_fields_only():
    item = FakeItem(rating=4.9, audienceRating=6.3)
    facts = GatheredFacts(critic_rating=4.9, audience_rating=7.0)
    edits = plan_edits(item, facts)
    assert "rating.value" not in edits
    assert edits["audienceRating.value"] == pytest.approx(7.0)


def test_plan_is_empty_when_nothing_changed():
    item = FakeItem(rating=4.9, audienceRating=6.3, contentRating="17")
    facts = GatheredFacts(critic_rating=4.9, audience_rating=6.3, content_rating="17")
    assert plan_edits(item, facts) == {}


def test_ratings_are_compared_on_the_formatted_value():
    """8.65 and 8.6 both render '86%', so they are not a change worth writing."""
    item = FakeItem(audienceRating=8.6)
    assert plan_edits(item, GatheredFacts(audience_rating=8.65)) == {}


def test_none_values_are_never_written():
    item = FakeItem(rating=4.9, contentRating="17")
    assert plan_edits(item, GatheredFacts()) == {}


def test_every_written_field_is_locked():
    item = FakeItem()
    edits = plan_edits(item, GatheredFacts(critic_rating=4.9, content_rating="17"))
    assert edits["rating.locked"] == 1
    assert edits["contentRating.locked"] == 1


def test_zero_rating_is_written_rather_than_clearing_the_field():
    """plexapi's editField sends `value or ''`, so 0.0 would blank the field."""
    item = FakeItem(rating=4.9)
    edits = plan_edits(item, GatheredFacts(critic_rating=0.0))
    assert edits["rating.value"] == 0.0


def test_seasons_accept_only_ratings():
    assert WRITABLE_BY_KIND["season"] == {"critic_rating", "audience_rating"}


def test_episodes_do_not_accept_genres_or_studio():
    assert "genres" not in WRITABLE_BY_KIND["episode"]
    assert "studio" not in WRITABLE_BY_KIND["episode"]
    assert "content_rating" in WRITABLE_BY_KIND["episode"]


def test_season_plan_ignores_inapplicable_fields():
    item = FakeItem(kind="season")
    edits = plan_edits(item, GatheredFacts(content_rating="17", studio="A24",
                                           critic_rating=4.9))
    assert edits["rating.value"] == pytest.approx(4.9)
    assert "contentRating.value" not in edits
    assert "studio.value" not in edits


def test_date_is_formatted_for_plex():
    item = FakeItem()
    edits = plan_edits(item, GatheredFacts(originally_available=date(2023, 5, 12)))
    assert edits["originallyAvailableAt.value"] == "2023-05-12"


async def test_apply_uses_a_single_batched_call():
    item = FakeItem()
    written = await apply_facts(item, GatheredFacts(critic_rating=4.9, content_rating="17"))
    assert item.batched and item.saved
    assert written["rating.value"] == pytest.approx(4.9)


async def test_apply_does_nothing_when_there_is_nothing_to_write():
    item = FakeItem(rating=4.9)
    written = await apply_facts(item, GatheredFacts(critic_rating=4.9))
    assert written == {}
    assert item.batched is False


async def test_genres_are_applied_through_addgenre():
    item = FakeItem(genres=["Horror"])
    await apply_facts(item, GatheredFacts(genres=["Horror", "Drama"]))
    assert item.edits["genres"] == ["Horror", "Drama"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/test_plex_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.plex.writer'`

- [ ] **Step 3: Implement**

`src/autoposter/plex/writer.py`:

```python
import asyncio
import logging

from autoposter.facts.gather import format_audience, format_critic
from autoposter.facts.models import GatheredFacts

logger = logging.getLogger(__name__)

# plexapi raises AttributeError for fields a type does not carry: a season has
# no contentRating, studio, genres or originallyAvailableAt; an episode has no
# genres or studio.
WRITABLE_BY_KIND: dict[str, set[str]] = {
    "movie": {
        "critic_rating", "audience_rating", "content_rating",
        "genres", "studio", "originally_available",
    },
    "show": {
        "critic_rating", "audience_rating", "content_rating",
        "genres", "studio", "originally_available",
    },
    "season": {"critic_rating", "audience_rating"},
    "episode": {"critic_rating", "audience_rating", "content_rating",
                "originally_available"},
}


def _current_genres(item) -> list[str]:
    return [g.tag for g in getattr(item, "genres", []) or []]


def plan_edits(item, facts: GatheredFacts) -> dict[str, object]:
    """Field/value pairs that differ from what Plex already holds.

    Ratings compare on their *formatted* value, because that is what a viewer
    sees: 8.65 and 8.6 both render "86%", so rewriting one as the other would
    churn Plex for no visible gain.
    """
    writable = WRITABLE_BY_KIND.get(getattr(item, "type", "movie"), set())
    edits: dict[str, object] = {}

    def put(field: str, value: object) -> None:
        edits[f"{field}.value"] = value
        # Locked so Plex's own agent does not revert a value this tool owns.
        edits[f"{field}.locked"] = 1

    if "critic_rating" in writable and facts.critic_rating is not None:
        if format_critic(getattr(item, "rating", None)) != format_critic(facts.critic_rating):
            put("rating", facts.critic_rating)

    if "audience_rating" in writable and facts.audience_rating is not None:
        current = format_audience(getattr(item, "audienceRating", None))
        if current != format_audience(facts.audience_rating):
            put("audienceRating", facts.audience_rating)

    if "content_rating" in writable and facts.content_rating:
        if getattr(item, "contentRating", None) != facts.content_rating:
            put("contentRating", facts.content_rating)

    if "studio" in writable and facts.studio:
        if getattr(item, "studio", None) != facts.studio:
            put("studio", facts.studio)

    if "originally_available" in writable and facts.originally_available:
        formatted = facts.originally_available.strftime("%Y-%m-%d")
        current = getattr(item, "originallyAvailableAt", None)
        current_str = current.strftime("%Y-%m-%d") if hasattr(current, "strftime") else current
        if current_str != formatted:
            put("originallyAvailableAt", formatted)

    return edits


async def apply_facts(item, facts: GatheredFacts) -> dict[str, object]:
    """Write the changed fields in one HTTP call.

    plexapi routes even a single-item edit through the library section, so
    batching the fields together turns six writes into one.
    """
    edits = plan_edits(item, facts)
    writable = WRITABLE_BY_KIND.get(getattr(item, "type", "movie"), set())
    new_genres = None
    if "genres" in writable and facts.genres:
        if sorted(_current_genres(item)) != sorted(facts.genres):
            new_genres = facts.genres

    if not edits and new_genres is None:
        return {}

    def _write() -> None:
        item.batchEdits()
        if edits:
            item.edit(**edits)
        if new_genres is not None:
            item.addGenre(new_genres, locked=True)
        item.saveEdits()

    await asyncio.to_thread(_write)
    logger.info("plex: wrote %d field(s) to %s", len(edits), getattr(item, "type", "?"))
    return edits
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/test_plex_writer.py -v`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/autoposter/plex/writer.py tests/test_plex_writer.py
git commit --no-gpg-sign -m "feat: write gathered facts to Plex"
```

---

## Task 7: Wire into the pipeline

**Files:**
- Modify: `src/autoposter/render/pipeline.py`
- Modify: `src/autoposter/app.py`
- Modify: `src/autoposter/config/schema.py`, `config/autoposter.example.yaml`
- Modify: `README.md`
- Test: `tests/test_pipeline_facts.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: `async apply_metadata(session, config, item, plex_item, deps) -> dict` called from `process_item`, plus config `operations.enabled`, `operations.write_to_plex`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pipeline_facts.py`:

```python
from pathlib import Path

import pytest
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import ItemFacts, MediaItem
from autoposter.facts.models import GatheredFacts
from autoposter.plex.client import ResolvedItem
from autoposter.render import pipeline

EXAMPLE = Path("config/autoposter.example.yaml")


def resolved():
    return ResolvedItem(
        rating_key="w1", library="Movies", kind="movie", title="X", year=2023,
        season_number=None, episode_number=None, root_folder="X", file_path=None,
        art_url=None, tmdb_id=1, tvdb_id=None, imdb_id="tt1", parent_rating_key=None,
    )


class RecordingPlexItem:
    type = "movie"
    rating = None
    audienceRating = None
    contentRating = None
    studio = None
    originallyAvailableAt = None
    genres: list = []

    def __init__(self):
        self.edits = {}

    def batchEdits(self):
        return self

    def saveEdits(self):
        return self

    def edit(self, **kwargs):
        self.edits.update(kwargs)
        return self

    def addGenre(self, genres, locked=True):
        return self


async def _media(session):
    media = MediaItem(rating_key="w1", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    return media


async def test_facts_are_gathered_persisted_and_written(session, monkeypatch):
    media = await _media(session)

    async def fake_gather(_session, _item, _tmdb, _mdblist):
        return GatheredFacts(critic_rating=4.9, sources={"critic_rating": "imdb"})

    monkeypatch.setattr(pipeline, "gather_facts", fake_gather)
    plex_item = RecordingPlexItem()
    config = load_config(EXAMPLE)

    facts = await pipeline.apply_metadata(
        session, config, media.id, resolved(), plex_item, object(), object()
    )

    assert facts.critic_rating == pytest.approx(4.9)
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.critic_rating == pytest.approx(4.9)
    assert plex_item.edits["rating.value"] == pytest.approx(4.9)


async def test_disabling_operations_skips_everything(session, monkeypatch):
    media = await _media(session)

    async def fail_gather(*args, **kwargs):
        raise AssertionError("gather must not run when operations are disabled")

    monkeypatch.setattr(pipeline, "gather_facts", fail_gather)
    config = load_config(EXAMPLE)
    config.operations.enabled = False

    facts = await pipeline.apply_metadata(
        session, config, media.id, resolved(), RecordingPlexItem(), object(), object()
    )
    assert facts.is_empty()
    assert (await session.execute(select(ItemFacts))).scalars().all() == []


async def test_write_to_plex_false_still_stores_facts(session, monkeypatch):
    """The safe setting while the tool being replaced still owns these fields."""
    media = await _media(session)

    async def fake_gather(_session, _item, _tmdb, _mdblist):
        return GatheredFacts(critic_rating=4.9)

    monkeypatch.setattr(pipeline, "gather_facts", fake_gather)
    config = load_config(EXAMPLE)
    config.operations.write_to_plex = False
    plex_item = RecordingPlexItem()

    await pipeline.apply_metadata(
        session, config, media.id, resolved(), plex_item, object(), object()
    )

    assert (await session.execute(select(ItemFacts))).scalar_one().critic_rating
    assert plex_item.edits == {}


def test_example_config_enables_operations():
    config = load_config(EXAMPLE)
    assert config.operations.enabled is True
    assert config.operations.write_to_plex is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/test_pipeline_facts.py -v`
Expected: FAIL — `AttributeError` / missing `operations` config section

- [ ] **Step 3: Add the config section**

In `src/autoposter/config/schema.py`:

```python
class OperationsConfig(BaseModel):
    """Per-item metadata operations, replacing Kometa's mass_*_update."""

    enabled: bool = True
    # Off means gather and store facts but leave Plex untouched — the safe
    # setting while the tool being replaced still owns these fields.
    write_to_plex: bool = True
    imdb_refresh_hours: int = 24
```

Add `operations: OperationsConfig = Field(default_factory=OperationsConfig)` to `Config`, and to `config/autoposter.example.yaml`:

```yaml
operations:
  enabled: true
  write_to_plex: true
  imdb_refresh_hours: 24
```

- [ ] **Step 4: Call it from the pipeline**

In `src/autoposter/render/pipeline.py`, import the new pieces and add:

```python
async def apply_metadata(
    session: AsyncSession,
    config: Config,
    media_item_id: int,
    item: ResolvedItem,
    plex_item,
    tmdb_facts,
    mdblist,
) -> GatheredFacts:
    """Gather this item's facts, store them, and write the changed ones to Plex.

    Runs before any badge rendering, because badges read the values from Plex
    rather than from the providers.
    """
    if not config.operations.enabled:
        return GatheredFacts()

    facts = await gather_facts(session, item, tmdb_facts, mdblist)
    await persist_facts(session, media_item_id, facts)

    if config.operations.write_to_plex and plex_item is not None and not facts.is_empty():
        await apply_facts(plex_item, facts)
    return facts
```

Call it from `process_item` after the item resolves and **before** the artifact loop, passing the resolved Plex object through.

- [ ] **Step 5: Add the IMDb attribution**

In `README.md`, under Attribution, add:

```markdown
### IMDb

Information courtesy of IMDb (https://www.imdb.com). Used with permission.

Ratings come from IMDb's bulk non-commercial datasets, which are licensed for
**personal and non-commercial use only** and must not be republished or
repurposed into another database. If this project is ever distributed
commercially, that licence is a blocker and needs review.
```

- [ ] **Step 6: Run the full suite**

Run: `rtk proxy python -m pytest tests/ -q && rtk proxy ruff check src tests`
Expected: all green, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add src/autoposter README.md config tests
git commit --no-gpg-sign -m "feat: apply metadata operations from the pipeline"
```

---

## Phase 2a completion criteria

Verify each by running it and reading the output.

1. `rtk proxy python -m pytest tests/ -q` passes; `ruff check src tests` clean.
2. A real Radarr webhook results in an `item_facts` row with a critic rating, audience rating and content rating.
3. The values written to Plex match what Kometa currently writes for the same item — compare a handful against `rating`, `audienceRating` and `contentRating` on live items **before** disabling Kometa's operations.
4. Re-delivering the same webhook writes **nothing** to Plex the second time (`plan_edits` returns empty).
5. The IMDb refresh stores rows only for ids the library contains — check `select count(*) from imdb_ratings` is in the thousands, not 1.7 million.

Only once 1-5 hold should `operations` be removed from the Kometa config.

## Notes for Phase 2b

- Badges read `rating`, `audienceRating` and `contentRating` **from Plex**, so they depend on this plan having run.
- The season badge inherits the show's content rating — seasons deliberately gather no facts here.
- Kometa uploads **WebP at quality 90, 1000×1500**, carrying EXIF tag `0x04BC = "overlay"`. Match all three or the cutover is visible.
- The oracle pair for parity testing is already harvested: `.superpowers/oracle/All_Souls_plex_overlaid.jpg` (Kometa's output) against `tests/fixtures/golden/expected_poster.jpg` (our base).
