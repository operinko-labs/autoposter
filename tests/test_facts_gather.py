from dataclasses import replace
from datetime import date
from pathlib import Path
import asyncio
import gzip
import logging

import httpx
import pytest
from sqlalchemy import func, select

from autoposter.config.schema import OperationsConfig
from autoposter.db.models import ItemFacts, MediaItem
from autoposter.facts import imdb as imdb_module
from autoposter.facts import gather as gather_module
from autoposter.facts.gather import (
    format_audience,
    format_critic,
    gather_facts,
    persist_facts,
)
from autoposter.facts.models import GatheredFacts
from autoposter.plex.client import ResolvedItem
from autoposter.providers.tvdb import TVDBClient

FIXTURES = Path(__file__).parent / "fixtures" / "facts"


def item(kind="movie", season=None, episode=None):
    return ResolvedItem(
        server="plex", native_id="1", library="Movies", kind=kind, title="X", year=2023,
        season_number=season, episode_number=episode, root_folder="X",
        file_path=None, art_url=None, tmdb_id=940143, tvdb_id=371980,
        imdb_id="tt14316486", parent_native_id=None,
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


async def test_persist_a_full_row_survives_a_later_empty_gather(session):
    """Finding 4: a transient failure (e.g. a cached TMDB 404, or an item
    resolving without a tmdb_id) must not blank a previously complete row."""
    media = MediaItem(rating_key="p4", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()

    full = GatheredFacts(
        critic_rating=4.9, audience_rating=6.3, content_rating="17",
        genres=["Horror"], studio="A24", originally_available=date(2023, 5, 12),
        sources={"critic_rating": "imdb", "audience_rating": "tmdb"},
    )
    await persist_facts(session, media.id, full)
    await persist_facts(session, media.id, GatheredFacts())

    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.critic_rating == pytest.approx(4.9)
    assert row.audience_rating == pytest.approx(6.3)
    assert row.content_rating == "17"
    assert row.genres == ["Horror"]
    assert row.studio == "A24"
    assert row.originally_available == date(2023, 5, 12)
    assert row.sources == {"critic_rating": "imdb", "audience_rating": "tmdb"}


async def test_persist_a_partial_gather_only_overwrites_what_it_found(session):
    """A partial gather (e.g. no tmdb_id this pass, so only critic_rating is
    present) must not blank the genres/studio a previous gather stored, and
    must merge into -- not replace -- the stored sources map."""
    media = MediaItem(rating_key="p5", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()

    full = GatheredFacts(
        critic_rating=4.9, audience_rating=6.3, genres=["Horror"], studio="A24",
        sources={"audience_rating": "tmdb", "genres": "tmdb", "studio": "tmdb"},
    )
    await persist_facts(session, media.id, full)

    partial = GatheredFacts(critic_rating=5.5, sources={"critic_rating": "imdb"})
    await persist_facts(session, media.id, partial)

    row = (await session.execute(select(ItemFacts))).scalar_one()
    # The genuinely-changed field overwrote.
    assert row.critic_rating == pytest.approx(5.5)
    # Fields the partial gather did not touch survive.
    assert row.audience_rating == pytest.approx(6.3)
    assert row.genres == ["Horror"]
    assert row.studio == "A24"
    # sources is merged, not replaced: the new key is added, the old ones remain.
    assert row.sources == {
        "audience_rating": "tmdb", "genres": "tmdb", "studio": "tmdb",
        "critic_rating": "imdb",
    }


async def test_persist_season_produces_no_pointless_row(session):
    """Finding 4: a season carries no facts of its own (see gather_facts), so
    persisting its always-empty GatheredFacts() must not create a row at all."""
    media = MediaItem(
        rating_key="p6", library="Shows", kind="season", title="S1", season_number=1,
    )
    session.add(media)
    await session.flush()

    result = await persist_facts(session, media.id, GatheredFacts())

    assert result is None
    assert (await session.execute(select(ItemFacts))).scalars().all() == []


async def test_persist_facts_never_hands_back_an_open_transaction(session):
    """The same commit-then-re-SELECT idiom as ``queue/jobs.py``'s ``claim``:
    ``persist_facts`` commits, then reads the row back, which autobegins a
    transaction nothing closes. It is called from inside ``process_item``
    (render/pipeline.py), so what it hands back is held across the rest of
    that job.

    Both returns, because both do it: the upsert path's trailing read and the
    ``_stored()`` helper the empty-gather and item_id-only returns go
    through."""
    media = MediaItem(rating_key="p9", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()

    await persist_facts(session, media.id, GatheredFacts(critic_rating=4.9))
    assert session.in_transaction() is False, "the upsert path's trailing read"

    await persist_facts(session, media.id, GatheredFacts())
    assert session.in_transaction() is False, "the _stored() helper's read"


async def test_no_mdblist_key_only_degrades_content_rating(session):
    """Finding 1: the stand-in used when no API key is configured must not
    affect critic rating, audience rating, genres, or studio."""
    from autoposter.facts.imdb import store_ratings
    from autoposter.facts.mdblist import NullMDBListClient

    await store_ratings(session, {"tt14316486": 4.9})
    facts = await gather_facts(session, item(), FakeTMDB(), NullMDBListClient())
    assert facts.critic_rating == pytest.approx(4.9)
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.genres == ["Horror"]
    assert facts.studio == "A24"
    assert facts.content_rating is None
    assert "content_rating" not in facts.sources
    assert facts.sources["critic_rating"] == "imdb"


async def test_mdblist_limit_does_not_abort_the_other_facts(session):
    from autoposter.facts.mdblist import MDBListLimitReached

    class Exhausted:
        async def content_rating(self, **kwargs):
            raise MDBListLimitReached("API Limit Reached!")

    facts = await gather_facts(session, item(), FakeTMDB(), Exhausted())
    assert facts.content_rating is None
    assert facts.audience_rating == pytest.approx(6.3)


async def test_mdblist_http_error_does_not_abort_the_other_facts(session):
    """Finding 3: MDBListClient's raise_for_status() means a 429/500/502 or a
    connection error surfaces as httpx.HTTPError, not MDBListLimitReached --
    that must not discard the TMDB/IMDb facts already gathered."""
    import httpx as httpx_module

    from autoposter.facts.imdb import store_ratings

    class Broken:
        async def content_rating(self, **kwargs):
            raise httpx_module.HTTPStatusError(
                "server error", request=httpx_module.Request("GET", "https://x"),
                response=httpx_module.Response(502),
            )

    await store_ratings(session, {"tt14316486": 4.9})
    facts = await gather_facts(session, item(), FakeTMDB(), Broken())
    assert facts.content_rating is None
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.genres == ["Horror"]
    assert facts.critic_rating == pytest.approx(4.9)


async def test_a_release_dates_5xx_does_not_abort_the_rest_of_the_gather(session, caplog):
    """Row 227 review finding I1: ``/movie/{id}/release_dates`` is optional
    and secondary, but until this fix ``gather.py`` caught only
    ``TmdbRateLimited`` around it -- so a transient 500/502/503 propagated out
    of ``gather_facts`` and discarded a gather that would otherwise complete
    entirely from cache plus MDBList, over one optional value. The block
    above (:162-167, the tvdb precedent) already states the rule in its own
    words: "one provider's transient failure must not throw away everything
    else this pass gathered."

    Wired through a REAL ``TMDBFactsClient`` and ``httpx.MockTransport``, not
    a hand-raised exception, so this proves the fix against what ``_get``
    actually raises for a non-429 status (an ``httpx.HTTPStatusError``, not
    ``TmdbRateLimited``).
    """
    import json

    from autoposter.facts.tmdb_facts import TMDBFactsClient

    movie_payload = json.loads((FIXTURES / "tmdb_movie.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/release_dates"):
            return httpx.Response(500, text="tmdb is down")
        return httpx.Response(200, json=movie_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tmdb = TMDBFactsClient("tok", http)
        operations = OperationsConfig(added_at_source="tmdb_digital")
        with caplog.at_level(logging.WARNING):
            facts = await gather_facts(
                session, item(), tmdb, FakeMDBList(), operations=operations,
            )

    assert facts.added_at is None
    assert "added_at" not in facts.sources
    # The rest of the gather -- read entirely from the SAME mocked movie
    # endpoint -- survives untouched.
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.genres == ["Horror", "Drama"]
    assert facts.studio == "First Studio"
    assert facts.originally_available == date(2023, 5, 12)
    assert "tmdb release_dates request failed" in caplog.text
    # Row 213's spirit, one module along: never the URL an httpx error's
    # str() carries in full.
    assert "https://" not in caplog.text


async def test_updated_at_advances_on_second_persist_facts(session_factory):
    """Verify updated_at is re-stamped on upsert via on_conflict_do_update.

    This test proves the fix works: without updated_at in the set_ mapping,
    this test would fail because updated_at would remain frozen at insert time.
    """
    # Create a media item in the database
    async with session_factory() as s:
        media = MediaItem(rating_key="p2", library="Movies", kind="movie", title="X")
        s.add(media)
        await s.flush()
        await s.commit()
        media_id = media.id

    # First persist_facts call in a separate transaction
    async with session_factory() as s:
        await persist_facts(s, media_id, GatheredFacts(critic_rating=4.9))
        # Get the row and the database clock at commit time
        row1 = (await s.execute(select(ItemFacts).where(ItemFacts.item_id == media_id))).scalar_one()
        updated_at_1 = row1.updated_at

    # Ensure a small delay so database clock advances
    await asyncio.sleep(0.1)

    # Second persist_facts call in a separate transaction
    async with session_factory() as s:
        await persist_facts(s, media_id, GatheredFacts(critic_rating=5.1))
        row2 = (await s.execute(select(ItemFacts).where(ItemFacts.item_id == media_id))).scalar_one()
        updated_at_2 = row2.updated_at

    # The contract is that the conflict path RE-STAMPS updated_at (onupdate=
    # never fires on INSERT ... ON CONFLICT DO UPDATE); equality is the one
    # shape the regression produces -- drop `set_["updated_at"]` from
    # persist_facts and the two readings are byte-identical. Strict `>`
    # additionally assumed the DB wall clock is monotonic across the sleep, and
    # the hardening-sweep loop reproduced a backwards step (row 195's close has
    # the numbers; docs/research/dev-clock-step/ has the measurement and the
    # rule) -- an environment fact, not an upsert defect.
    assert updated_at_2 != updated_at_1
    assert row2.critic_rating == pytest.approx(5.1)


async def test_fetched_at_advances_on_second_persist_facts(session_factory):
    """Verify fetched_at is also re-stamped on upsert.

    Both fetched_at and updated_at use the same mechanism (func.now() in set_ mapping),
    so both must be re-stamped on every conflict-update.
    """
    # Create a media item in the database
    async with session_factory() as s:
        media = MediaItem(rating_key="p3", library="Movies", kind="movie", title="X")
        s.add(media)
        await s.flush()
        await s.commit()
        media_id = media.id

    # First persist_facts call in a separate transaction
    async with session_factory() as s:
        await persist_facts(s, media_id, GatheredFacts(audience_rating=6.3))
        row1 = (await s.execute(select(ItemFacts).where(ItemFacts.item_id == media_id))).scalar_one()
        fetched_at_1 = row1.fetched_at

    # Ensure a small delay so database clock advances
    await asyncio.sleep(0.1)

    # Second persist_facts call in a separate transaction
    async with session_factory() as s:
        await persist_facts(s, media_id, GatheredFacts(audience_rating=7.5))
        row2 = (await s.execute(select(ItemFacts).where(ItemFacts.item_id == media_id))).scalar_one()
        fetched_at_2 = row2.fetched_at

    # Same shape as updated_at above: the contract is the RE-STAMP, and equality
    # is the one shape the regression produces -- drop `set_["fetched_at"]` from
    # persist_facts and the two readings are byte-identical. Strict `>` also
    # assumed a monotonic DB wall clock across the sleep, which this environment
    # violates (~2.7 s backwards step every ~30 s; row 195's close has the
    # numbers, docs/research/dev-clock-step/ has the measurement and the rule).
    assert fetched_at_2 != fetched_at_1
    assert row2.audience_rating == pytest.approx(7.5)


# --- the three prefetch columns, and the "we looked" stamp -------------------


async def test_persist_writes_the_three_prefetch_columns(session):
    media = MediaItem(rating_key="pf1", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(
        tmdb_origin_country=["US", "GB"],
        tmdb_original_language="en",
        tmdb_collection_id=1241,
        sources={"tmdb_origin_country": "tmdb"},
    ))
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.tmdb_origin_country == ["US", "GB"]
    assert row.tmdb_original_language == "en"
    assert row.tmdb_collection_id == 1241


async def test_a_later_gather_without_them_does_not_blank_them(session):
    """Finding 4, one field along: a pass with no ``tmdb_id`` must not erase
    what a complete pass stored. ``tmdb_origin_country`` is the interesting one
    -- it is a NOT NULL JSONB defaulting to ``[]``, so a plain SQL COALESCE
    could not tell 'found nothing' from 'honestly empty', which is exactly why
    ``persist_facts`` builds its SET clause from populated fields only."""
    media = MediaItem(rating_key="pf2", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(
        tmdb_origin_country=["FI"], tmdb_original_language="fi",
        tmdb_collection_id=7,
    ))
    await persist_facts(session, media.id, GatheredFacts(critic_rating=4.9))
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.tmdb_origin_country == ["FI"]
    assert row.tmdb_original_language == "fi"
    assert row.tmdb_collection_id == 7
    assert row.critic_rating == pytest.approx(4.9)


# --- roadmap row 100 sub-phase C2c: the two status columns ------------------


async def test_persist_writes_the_two_status_columns(session):
    """The badge path reads the PERSISTED row, not a live gather
    (`render/pipeline.py` selects `ItemFacts` and hands it to
    `OverlayItemView`), so a value that never reaches this table never
    reaches a status badge."""
    media = MediaItem(rating_key="st1", library="Shows", kind="show", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(
        tmdb_status="returning",
        last_episode_aired=date(2026, 8, 20),
        sources={"tmdb_status": "tmdb"},
    ))
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.tmdb_status == "returning"
    assert row.last_episode_aired == date(2026, 8, 20)


async def test_a_later_gather_without_the_status_fields_does_not_blank_them(session):
    """`persist_facts`'s never-blank law, one field pair along: a pass with no
    `tmdb_id` -- or a TMDb 429 that skipped the show fetch -- must not erase
    what a complete pass stored. This is the property that makes the drift
    sweep safe to run against a partially-degraded provider."""
    media = MediaItem(rating_key="st2", library="Shows", kind="show", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(
        tmdb_status="ended", last_episode_aired=date(2024, 5, 1),
    ))
    await persist_facts(session, media.id, GatheredFacts(critic_rating=4.9))
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.tmdb_status == "ended"
    assert row.last_episode_aired == date(2024, 5, 1)
    assert row.critic_rating == pytest.approx(4.9)


async def test_a_gather_whose_only_fact_is_a_status_still_writes_a_row(session):
    """Established facts item **z**, proven through the real `persist_facts`
    rather than only through `is_empty()`. Two gates would swallow this if
    the decision had gone the other way: the `facts.is_empty()`
    short-circuit, and the `len(values) == 1` guard for a gather whose only
    results have no column. A show TMDb knows nothing about except that it
    ended must still get its row, or `status` is a family that silently does
    nothing for the sparsest shows in the library."""
    media = MediaItem(rating_key="st3", library="Shows", kind="show", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(tmdb_status="canceled"))
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.tmdb_status == "canceled"
    assert row.last_episode_aired is None


async def test_an_empty_gather_still_records_that_we_looked(session):
    """C4, and the whole reason rows 189/192 can tell 'TMDb has nothing for
    this item' from 'nobody has asked yet'.

    ``persist_facts`` still writes NO ``item_facts`` row for an empty gather --
    an all-NULL row is pure noise and that rule is unchanged -- but the ATTEMPT
    is now stamped on ``media_items`` regardless, which is the shape
    ``facts_attempted_at`` already had for the drift sweep
    (``scheduler/jobs.py:142-151``). Before this, an item TMDb has never heard
    of and an item nothing ever fetched were the same two NULLs.
    """
    media = MediaItem(rating_key="pf3", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    assert media.facts_attempted_at is None

    result = await persist_facts(session, media.id, GatheredFacts())

    assert result is None, "an empty gather must still write no facts row"
    assert (await session.execute(
        select(func.count()).select_from(ItemFacts)
    )).scalar_one() == 0
    await session.refresh(media)
    assert media.facts_attempted_at is not None


async def test_a_non_empty_gather_stamps_the_attempt_too(session):
    """The stamp is unconditional -- one statement on both paths, not two
    statements that can drift apart."""
    media = MediaItem(rating_key="pf4", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(critic_rating=4.9))
    await session.refresh(media)
    assert media.facts_attempted_at is not None


# --- miss-triggered IMDb refresh --------------------------------------------
#
# _critic_rating (gather.py) calls imdb.note_rating_miss whenever get_rating
# / get_episode_rating finds nothing, then retries the lookup once. These
# tests wire in a real ImdbMissRefresh via the module-level handle gather.py
# consults (see facts/imdb.py's configure_miss_refresh/note_rating_miss --
# gather_facts()'s signature is frozen and carries no http client, so that's
# the seam a fact-gathering pass uses to reach the network).


def _gzip_fixture(name: str) -> bytes:
    return gzip.compress((FIXTURES / name).read_bytes())


async def test_a_miss_is_filled_in_on_this_same_gather_pass(session, monkeypatch):
    def handler(request):
        return httpx.Response(200, content=_gzip_fixture("title.ratings.sample.tsv"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr(
            imdb_module, "_miss_refresh", imdb_module.ImdbMissRefresh(http, cooldown_minutes=60)
        )
        facts = await gather_facts(
            session, replace(item(), imdb_id="tt0111161"), FakeTMDB(), FakeMDBList()
        )

    assert facts.critic_rating == pytest.approx(9.3)
    assert facts.sources["critic_rating"] == "imdb"


async def test_a_failed_miss_refresh_still_returns_a_null_rating(session, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr(
            imdb_module, "_miss_refresh", imdb_module.ImdbMissRefresh(http, cooldown_minutes=60)
        )
        facts = await gather_facts(
            session, replace(item(), imdb_id="tt0111161"), FakeTMDB(), FakeMDBList()
        )

    assert facts.critic_rating is None
    assert "critic_rating" not in facts.sources


# --- row 84: TVDb as a named source (genres/studio/originally_available) ---
#
# gather_facts's own entry point for the overlay, driven through a REAL
# TVDBClient over httpx.MockTransport -- not a hand-rolled fake client -- so
# these prove the wiring gather_facts actually does (the gate, the call, the
# httpx.HTTPError swallow) rather than a double's promise to behave like it.


def _tvdb_login_response(request: httpx.Request) -> httpx.Response | None:
    if request.url.path.endswith("/login"):
        return httpx.Response(200, json={"data": {"token": "faketoken"}})
    return None


async def test_no_tvdb_source_named_makes_no_tvdb_request(session):
    """C2's gate-off half: naming no ``*_source`` as ``tvdb`` must not touch
    the network at all, and the gathered facts must be byte-identical to a
    pass given no tvdb client whatsoever.
    """
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _tvdb_login_response(request) or httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tvdb = TVDBClient("key", http)
        with_tvdb = await gather_facts(
            session, item(), FakeTMDB(), FakeMDBList(),
            operations=OperationsConfig(), tvdb=tvdb,
        )
    without_tvdb = await gather_facts(session, item(), FakeTMDB(), FakeMDBList())

    assert calls == []
    assert with_tvdb == without_tvdb


async def test_tvdb_source_named_overlays_the_gathered_facts(session):
    """C2's gate-on half: naming ``tvdb`` for all three fields makes the
    overlay fire, replacing TMDb's genres/studio/date with TVDb's and
    recording ``tvdb`` as each field's source.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        login = _tvdb_login_response(request)
        if login is not None:
            return login
        assert request.url.path == "/v4/movies/371980/extended"
        return httpx.Response(200, json={"data": {
            "genres": [{"name": "Fantasy"}],
            "companies": {"studio": [{"name": "Studio Ghibli"}]},
            "first_release": {"date": "2001-07-20"},
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tvdb = TVDBClient("key", http)
        operations = OperationsConfig(
            genres_source="tvdb", studio_source="tvdb",
            originally_available_source="tvdb",
        )
        facts = await gather_facts(
            session, item(), FakeTMDB(), FakeMDBList(),
            operations=operations, tvdb=tvdb,
        )

    assert facts.genres == ["Fantasy"]
    assert facts.studio == "Studio Ghibli"
    assert facts.originally_available == date(2001, 7, 20)
    assert facts.sources["genres"] == "tvdb"
    assert facts.sources["studio"] == "tvdb"
    assert facts.sources["originally_available"] == "tvdb"


async def test_tvdb_http_failure_leaves_the_field_ungathered(session, caplog):
    """The MDBList precedent, one provider along: a TVDb 500 must not throw
    away everything else this pass gathered, and the pod log must carry the
    detail rather than swallowing it silently.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return _tvdb_login_response(request) or httpx.Response(500, text="tvdb is down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tvdb = TVDBClient("key", http)
        operations = OperationsConfig(genres_source="tvdb")
        with caplog.at_level(logging.WARNING):
            facts = await gather_facts(
                session, item(), FakeTMDB(), FakeMDBList(),
                operations=operations, tvdb=tvdb,
            )

    # TVDb never answers, so TMDb's own genres (FakeTMDB's "Horror") survive
    # untouched -- the overlay simply never fires.
    assert facts.genres == ["Horror"]
    assert facts.sources.get("genres") != "tvdb"
    assert "tvdb request failed" in caplog.text


async def test_tvdb_source_named_but_no_tvdb_client_configured_warns(session, monkeypatch, caplog):
    """I2: naming ``genres_source: tvdb`` while ``providers.order`` builds no
    TVDb client (or a rename breaks pipeline.py's ``p.name == "TVDB"`` lookup)
    must not fail silently -- gather_facts's own ``tvdb is None`` gate is the
    one place both failure modes funnel through, so the warning belongs here.
    """
    monkeypatch.setattr(gather_module, "_tvdb_source_unconfigured_warned", False)
    operations = OperationsConfig(genres_source="tvdb")

    with caplog.at_level(logging.WARNING):
        facts = await gather_facts(
            session, item(), FakeTMDB(), FakeMDBList(), operations=operations, tvdb=None,
        )

    assert facts.genres == ["Horror"]  # TMDb's own value, untouched
    assert "genres" in caplog.text
    assert "no TVDb provider is configured" in caplog.text
