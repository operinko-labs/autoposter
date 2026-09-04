"""The identity re-key, through the real ``process_item``.

``media_items`` is keyed on the Plex rating key, and the Plex rating key is a
*hint* the resolver is free to refuse: ``PlexClient.resolve`` returns the key
it FOUND, so a re-matched or renumbered item resolves to a key the stored row
does not hold, ``_upsert_media_item``'s ``ON CONFLICT (rating_key)`` conflicts
with nothing, and a SECOND row appears carrying every future render while the
original keeps its renders, its facts, its dismissals, its ``logo_upload_key``
and its children and is never written again.

Every test here drives ``process_item`` -- the pipeline's only entry point and
the only caller of ``render_artifact``. A helper-level test would prove the
predicate can be computed and say nothing about whether the shipped path uses
it, which is exactly how a gated feature has twice passed its own tests in
this tree while the wired path did something else.

Two properties are load-bearing and are pinned as hard as the re-key itself:
an item whose key has NOT moved must be byte-identical (same row, same key,
same render row, no audit row), and a re-key must move nothing on disk --
``naming.asset_path`` is keyed by library and root folder, never by the rating
key.
"""
import logging
from pathlib import Path

import asyncpg.exceptions
import httpx
import pytest
from conftest import decodable_png
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from autoposter.config.loader import load_config
from autoposter.db.models import EventLog, MediaItem, Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _config(tmp_path):
    """The example config, rooted in ``tmp_path``, with both Plex-touching
    stages off.

    ``operations`` and ``badges`` each want a live ``plexapi`` object from
    ``plex.fetch_item``, and neither says anything about which ``media_items``
    row the pipeline writes to -- which is the whole subject of this file.
    """
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    config.operations.enabled = False
    config.badges.enabled = False
    return config


class _FakePlex:
    """Answers ``resolve`` with a fixed item, whatever it is asked.

    The resolver is not under test here: what is under test is what the
    pipeline does when the key ``resolve`` returns differs from the one the
    intent carried, so the answer is supplied rather than searched for. The
    same stand-in shape as ``tests/test_pipeline_facts.py``'s.
    """

    def __init__(self, item):
        self._item = item
        self.asked = []

    async def resolve(self, intent):
        self.asked.append(intent)
        return self._item

    async def fetch_item(self, rating_key):
        raise AssertionError(
            "operations and badges are off in this file; nothing may fetch a "
            "live Plex object"
        )


def _resolved(rating_key, **overrides):
    """What ``resolve()`` hands ``process_item`` for a movie."""
    fields = dict(
        rating_key=rating_key, library="Movies", kind="movie",
        title="Dune: Part Two", year=2024, season_number=None,
        episode_number=None, root_folder="Dune Part Two (2024)",
        file_path="/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
        parent_rating_key=None,
    )
    fields.update(overrides)
    return ResolvedItem(**fields)


async def _row(session, rating_key, **columns):
    """One committed ``media_items`` row.

    Committed so its ``updated_at`` is a settled value written by its own
    transaction -- the same reason ``tests/test_scheduler_prune_job.py``'s
    ``_add_item`` commits.
    """
    fields = dict(
        library="Movies", kind="movie", title="Dune: Part Two", year=2024,
        tmdb_id=693134, imdb_id="tt15239678",
    )
    fields.update(columns)
    item = MediaItem(rating_key=rating_key, **fields)
    session.add(item)
    await session.commit()
    return item


def _row_level_render_artifact(monkeypatch):
    """Replace ``render_artifact`` with its own row-level head, and record it.

    Not a bare stub. The claim under test is *which ``media_items`` row the
    render row lands on*, and a stub that merely recorded its ``item``
    argument would prove the argument rather than the row. So this calls the
    REAL ``_upsert_media_item`` and the REAL ``_get_or_create_render`` -- the
    two functions that actually decide -- and skips only the provider fetch
    and the ImageMagick work. ``test_the_real_render_scores_the_re_keyed_row``
    below drives the genuine ``render_artifact`` for the end-to-end claim.

    Returns the list it appends ``(media_item_id, art_kind)`` to.
    """
    seen: list[tuple[int, str]] = []

    async def fake(session, config, http, item, art_kind, providers, **_kwargs):
        media_item = await pipeline._upsert_media_item(session, item)
        target = pipeline.naming.asset_path(
            config, item.library, item.root_folder, art_kind,
            item.season_number, item.episode_number,
        )
        render = await pipeline._get_or_create_render(
            session, media_item, art_kind, target
        )
        render.status = "rendered"
        await session.commit()
        seen.append((media_item.id, art_kind))
        return render

    monkeypatch.setattr(pipeline, "render_artifact", fake)
    return seen


async def _items(session):
    return (
        await session.execute(select(MediaItem).order_by(MediaItem.id))
    ).scalars().all()


async def _audits(session):
    """The RE-KEY audit rows only.

    Filtered on the event type as well as the source: the fork stop files its
    own row under the same source (one identity story, one source to grep),
    and "no re-key happened" must stay a claim about re-keys.
    """
    return (
        await session.execute(
            select(EventLog).where(
                EventLog.source == pipeline.REKEY_SOURCE,
                EventLog.event_type == pipeline.REKEY_EVENT,
            )
        )
    ).scalars().all()


# --- the re-key itself ----------------------------------------------------


async def test_a_re_matched_item_re_keys_its_own_row_and_creates_no_twin(
    session, tmp_path, monkeypatch
):
    """The whole defect, in one test. The stored row holds key ``1``; Plex now
    serves the same identity under ``2``. Before this phase that produced a
    second row and left the first unscored forever."""
    stale = await _row(session, "1")
    stale_id = stale.id
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(
        kind="movie", title="Dune: Part Two", tmdb_id=693134,
        imdb_id="tt15239678", year=2024, rating_key="1",
    )

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    rows = await _items(session)
    assert [row.rating_key for row in rows] == ["2"], "a twin was created"
    assert rows[0].id == stale_id, "a new row was inserted instead of re-keyed"
    assert {item_id for item_id, _ in seen} == {stale_id}, (
        "the renders landed on a different row than the one that was re-keyed"
    )


async def test_the_re_key_writes_one_audit_row_carrying_both_keys(
    session, tmp_path, monkeypatch
):
    """A re-key that is never written down is a mystery the next
    investigation has to re-derive from the shape of the damage."""
    await _row(session, "1")
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    (audit,) = await _audits(session)
    assert audit.event_type == pipeline.REKEY_EVENT
    assert audit.payload["old_rating_key"] == "1"
    assert audit.payload["new_rating_key"] == "2"
    assert audit.payload["tmdb_id"] == 693134
    assert audit.payload["library"] == "Movies"


async def test_the_re_key_leaves_every_asset_path_untouched(
    session, tmp_path, monkeypatch
):
    """Nothing moves on disk. ``naming.asset_path`` takes
    ``(config, library, root_folder, art_kind, season, episode)`` and no
    rating key, so a re-key is a database-only change -- which is also why
    ``asset_cleanup`` needs no change and correctly moved nothing while the
    twins accumulated."""
    config = _config(tmp_path)
    stale = await _row(session, "1", root_folder="Dune Part Two (2024)")
    # Computed from the same config the pass will use, not a hardcoded
    # literal: naming.asset_path is keyed by (library, root_folder, art_kind),
    # never by the rating key, so this value is the same before and after the
    # re-key -- which is exactly the invariant under test. A hardcoded
    # "/assets/..." literal would silently assume the example config's
    # un-rerooted default and fail under _config's tmp_path-rerooted assets
    # root regardless of the re-key logic.
    before = str(
        pipeline.naming.asset_path(config, "Movies", "Dune Part Two (2024)", "poster")
    )
    session.add(Render(
        item_id=stale.id, art_kind="poster", asset_path=before, status="rendered",
    ))
    await session.commit()
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, config, None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    poster = (
        await session.execute(select(Render).where(Render.art_kind == "poster"))
    ).scalar_one()
    assert poster.asset_path == before, (
        "the re-key rewrote an asset path; it must be a database-only change"
    )


async def test_a_webhook_intent_with_no_rating_key_still_re_keys(
    session, tmp_path, monkeypatch
):
    """The case only an identity-shaped predicate can reach.

    ``intake/arr.py`` leaves ``rating_key`` None by design -- Sonarr and
    Radarr know nothing about Plex -- so the fork warning never fires and a
    key-comparison guard would leave this door wide open. It was one of the
    two silent twin producers.
    """
    stale = await _row(session, "1")
    stale_id = stale.id
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(
        kind="movie", title="Dune: Part Two", tmdb_id=693134, rating_key=None,
    )

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    rows = await _items(session)
    assert len(rows) == 1 and rows[0].id == stale_id
    assert rows[0].rating_key == "2"


# --- the byte-identical law ------------------------------------------------


async def test_an_unchanged_item_is_byte_identical(session, tmp_path, monkeypatch):
    """The hot path. An item whose key has not moved gets the same row, the
    same key, the same render row and NO audit row -- the re-key's whole cost
    on the ordinary pass is one existence check that its own row satisfies."""
    stale = await _row(session, "1")
    stale_id = stale.id
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("1")), [], intent,
    )

    session.expire_all()
    rows = await _items(session)
    assert len(rows) == 1 and rows[0].id == stale_id and rows[0].rating_key == "1"
    assert {item_id for item_id, _ in seen} == {stale_id}
    assert await _audits(session) == [], (
        "an item that did not move must not leave a re-key audit row"
    )


# --- the three refusals ----------------------------------------------------


async def test_a_cross_library_match_is_not_a_re_key(session, tmp_path, monkeypatch):
    """Same ids, same kind, different library. An item that exists in two
    libraries at once (a 4K and an HD copy) must keep two rows; merging them
    would be data loss, and this refusal is what makes ``library`` part of the
    predicate rather than a nicety."""
    await _row(session, "1", library="Movies 4K")
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None,
        _FakePlex(_resolved("2", library="Movies")), [], intent,
    )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}
    assert await _audits(session) == []


async def test_an_id_disjoint_match_is_not_a_re_key(session, tmp_path, monkeypatch):
    """No shared external id, so nothing proves these are the same item.

    This is also the shape of the adopted season/episode population:
    ``resolve()`` reports the SHOW's ids for a season or an episode while
    adoption stored each episode's OWN ids, so those rows are id-disjoint by
    construction and are deliberately left to the twin-merge job.
    """
    await _row(session, "1", tmdb_id=111, imdb_id="tt0000111")
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=111,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None,
        _FakePlex(_resolved("2", tmdb_id=693134, imdb_id="tt15239678")), [], intent,
    )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}
    assert await _audits(session) == []


async def test_a_kind_mismatch_is_not_a_re_key(session, tmp_path, monkeypatch):
    """A show row and a movie resolution sharing a TMDB number are not one
    item: a GUID number is unique only WITHIN one agent's namespace, and TMDB
    numbers movies and TV separately -- the production crash
    ``tests/test_plex.py`` already guards at the resolver."""
    await _row(session, "1", kind="show", library="Movies", tmdb_id=693134,
               imdb_id=None)
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None,
        _FakePlex(_resolved("2", imdb_id=None)), [], intent,
    )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}
    assert await _audits(session) == []


async def test_a_row_with_no_external_ids_is_never_re_keyed_on_a_title_match(
    session, tmp_path, monkeypatch
):
    """Titles are not identities. A row carrying no external id at all can
    only be matched by title, and a title match is exactly the class of error
    the resolver's own six refusals exist to avoid."""
    await _row(session, "1", tmdb_id=None, tvdb_id=None, imdb_id=None)
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}
    assert await _audits(session) == []


async def test_two_identity_matches_refuse_the_re_key_and_warn(
    session, tmp_path, monkeypatch, caplog
):
    """Ambiguity is the merge job's problem. Picking either row here would
    pick a side at random and leave the other one to fork again next pass."""
    await _row(session, "1")
    await _row(session, "3")
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    with caplog.at_level(logging.WARNING):
        await pipeline.process_item(
            session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
        )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "3", "2"}
    assert await _audits(session) == []
    assert any(
        "rows carry that identity" in record.getMessage()
        for record in caplog.records
    )


async def test_a_season_is_not_re_keyed_onto_another_season_of_the_same_show(
    session, tmp_path, monkeypatch
):
    """Every season of one show shares the show's external ids, so without the
    season/episode coordinates in the predicate season 1's row would match
    season 2's resolution -- and with two seasons stored, the exactly-one
    guard would then refuse every season re-key in the library instead of
    making the one right one."""
    await _row(session, "10", kind="season", library="TV Shows", title="A Show",
               tmdb_id=None, tvdb_id=77, imdb_id=None, season_number=1)
    await _row(session, "11", kind="season", library="TV Shows", title="A Show",
               tmdb_id=None, tvdb_id=77, imdb_id=None, season_number=2)
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="season", title="A Show", tvdb_id=77,
                          season_number=2, rating_key="11")
    resolved = _resolved(
        "12", kind="season", library="TV Shows", title="A Show",
        season_number=2, tmdb_id=None, tvdb_id=77, imdb_id=None,
        root_folder="A Show (2020)",
    )

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(resolved), [], intent,
    )

    session.expire_all()
    rows = {row.rating_key: row.season_number for row in await _items(session)}
    assert rows == {"10": 1, "12": 2}, (
        "season 2's row should have followed its key and season 1's should not "
        "have moved"
    )


# --- the key is already taken, and the race -------------------------------


async def test_a_taken_key_leaves_the_existing_twin_alone(
    session, tmp_path, monkeypatch
):
    """When the resolved key already has a row, the twin exists already: this
    is the merge job's pair, not a re-key. The runtime now does nothing at all
    -- it used to render onto the twin's row, which is the RESOLVED item's own
    row and gets its own visits from its own intents.

    This is the fork stop's own case, and the reason the stop asks the
    DATABASE rather than reading the re-key's None: the four sibling refusals
    above answer None too, and on three of them nothing holds the resolved
    key, so those still fall through and mint.
    """
    stale = await _row(session, "1")
    twin = await _row(session, "2")
    stale_id, twin_id = stale.id, twin.id
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    results = await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    assert results == []
    session.expire_all()
    assert {row.id for row in await _items(session)} == {stale_id, twin_id}
    assert seen == [], "the twin's row was rendered by the stale row's job"
    assert await _audits(session) == []


async def test_a_lost_race_does_not_fail_the_job(
    session, session_factory, tmp_path, monkeypatch, caplog
):
    """Two workers can resolve one identity at once and race the key.

    ``rating_key`` carries a real unique constraint, so the loser's flush
    raises ``IntegrityError``. It must roll back and continue -- and, since
    the winner's insert COMMITTED a row under the resolved key, the fork stop
    then ends the pass: that row is the winner's and the winner is rendering
    it. This is one of only two refusals where the resolved key is provably
    occupied, which is why the stop can fire here and does not on the
    row-less ones. A lost race must never FAIL a job, which is what this pins.

    The competing insert is landed from inside ``_identity_candidates``, which
    is where the lock is taken: that puts the winner's commit exactly between
    the free-key check and the update, which no fixture can reach from
    outside. Same trick as ``tests/test_scheduler_prune_job.py``'s
    ``_ReupsertingPlex``.
    """
    await _row(session, "1")
    real_candidates = pipeline._identity_candidates

    async def racing_candidates(inner_session, item):
        rows = await real_candidates(inner_session, item)
        async with session_factory() as other:
            other.add(MediaItem(
                rating_key=item.rating_key, library="Movies", kind="movie",
                title="Winner", tmdb_id=693134, imdb_id="tt15239678",
            ))
            await other.commit()
        return rows

    monkeypatch.setattr(pipeline, "_identity_candidates", racing_candidates)
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    with caplog.at_level(logging.WARNING):
        results = await pipeline.process_item(
            session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
        )

    assert results == [], "the job must complete as a no-op, not fail, on a lost race"
    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}, (
        "the loser must not have duplicated the key"
    )
    assert seen == [], "the loser rendered the winner's row"
    assert await _audits(session) == [], (
        "a rolled-back re-key must leave no audit row"
    )
    # L3: this is the IntegrityError arm -- the winner's row really does
    # hold the key, unlike the deadlock arm below, so the log text must say
    # so and must not claim a fresh twin was minted.
    assert "the winner's row holds the key" in caplog.text
    assert "fresh twin" not in caplog.text


async def test_a_deadlock_does_not_fail_the_job(
    session, session_factory, tmp_path, monkeypatch, caplog
):
    """A lock cycle across two identity re-keys raises, under asyncpg, a
    plain ``sqlalchemy.exc.DBAPIError`` whose ``orig`` carries sqlstate
    ``40P01`` (Postgres ``DeadlockDetected``) -- never ``OperationalError``,
    which the asyncpg dialect defines but raises nowhere. The same
    rollback-then-continue that saves a lost race on the unique constraint
    must also save this, or the job fails instead of degrading to the twin
    path.

    ``flush`` -- not ``commit`` -- is monkeypatched to fail exactly once, on
    the re-key's own flush, and the fake never calls the real flush: the real
    ``stale.rating_key = item.rating_key`` mutation must never reach the
    database, because an actually-flushed, still-uncommitted UPDATE to the
    same unique column would make the winner row landed below BLOCK on that
    open transaction instead of failing -- a real lock wait, not the
    exception this pins. The winner lands under the resolved key from a
    second session so the fallthrough's ordinary upsert has a genuine row to
    land on, the same shape ``test_a_lost_race_degrades_to_the_ordinary_upsert``
    pins for the unique-constraint race.
    """
    await _row(session, "1")
    real_flush = session.flush
    calls = {"n": 0}

    async def flush_or_deadlock():
        calls["n"] += 1
        if calls["n"] == 1:
            async with session_factory() as other:
                other.add(MediaItem(
                    rating_key="2", library="Movies", kind="movie",
                    title="Winner", tmdb_id=693134, imdb_id="tt15239678",
                ))
                await other.commit()
            raise DBAPIError(
                "UPDATE media_items SET rating_key = %s", ("2",),
                asyncpg.exceptions.DeadlockDetectedError("deadlock detected"),
            )
        await real_flush()

    monkeypatch.setattr(session, "flush", flush_or_deadlock)
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    with caplog.at_level(logging.WARNING):
        results = await pipeline.process_item(
            session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
        )

    assert results == [], "the job must complete as a no-op, not fail, on a deadlock"
    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}, (
        "the loser must not have duplicated the key"
    )
    assert seen == [], "the deadlock victim rendered the winner's row"
    assert await _audits(session) == [], (
        "a rolled-back re-key must leave no audit row"
    )
    # L3: this is the deadlock arm -- a deadlock victim's rollback implies
    # nothing about the key having been taken, unlike the IntegrityError arm
    # above, so the log text must not claim the winner's row holds it.
    assert "nothing took the key" in caplog.text
    assert "fresh twin" in caplog.text


async def test_a_non_deadlock_dbapi_error_still_fails_the_job(
    session, tmp_path, monkeypatch
):
    """A ``DBAPIError`` whose ``orig`` carries a sqlstate other than
    ``40P01`` -- a dropped connection, not a lock cycle -- must propagate.
    Silently swallowing it would fall through to the ordinary upsert and
    INSERT a twin instead of failing the job loudly.

    ``flush`` is monkeypatched the same way as the deadlock pin, but with
    ``ConnectionFailureError`` (sqlstate ``08006``) as ``orig``, and the real
    flush is never reached at all -- the job must fail before it would ever
    matter.
    """
    stale = await _row(session, "1")

    async def flush_connection_failure():
        raise DBAPIError(
            "UPDATE media_items SET rating_key = %s", ("2",),
            asyncpg.exceptions.ConnectionFailureError("connection failure"),
        )

    monkeypatch.setattr(session, "flush", flush_connection_failure)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    with pytest.raises(DBAPIError):
        await pipeline.process_item(
            session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
        )

    await session.rollback()
    session.expire_all()
    items = await _items(session)
    assert {row.rating_key for row in items} == {"1"}, (
        "no twin must be created when the job fails"
    )
    assert items[0].id == stale.id


async def test_the_ambiguous_path_releases_its_locks_before_returning(
    session, session_factory
):
    """The ambiguous branch used to return ``None`` holding ``FOR UPDATE`` on
    both candidate rows for the rest of the render window -- the provider
    fetch, the image download, the ImageMagick compose -- blocking any
    concurrent upsert or prune on either row for that whole time. It must
    roll back before returning.

    Calling ``_rekey_by_identity`` directly, rather than through
    ``process_item``, is deliberate: ``process_item`` commits again later in
    its own ordinary upsert, which would release the lock as a side effect
    and hide the defect this pins.
    """
    await _row(session, "1")
    await _row(session, "3")
    resolved = _resolved("2")

    result = await pipeline._rekey_by_identity(session, resolved)

    assert result is None
    async with session_factory() as other:
        rows = (
            await other.execute(
                select(MediaItem)
                .where(MediaItem.rating_key.in_(["1", "3"]))
                .with_for_update(nowait=True)
            )
        ).scalars().all()
        assert len(rows) == 2, (
            "a second session could not lock the candidate rows -- the "
            "ambiguous path is still holding them"
        )


# --- the end-to-end claim, through the genuine render_artifact -------------


class _Provider:
    """One art candidate for every kind this movie asks for. ``name`` is a
    class attribute on the real clients, so the runtime provider rank reads
    exactly this."""

    name = "TMDB"

    async def fetch(self, request):
        if request.art_kind in ("poster", "background"):
            return [ArtCandidate(
                self.name, "https://img/art.jpg", "en", 2000, 3000, 5.0
            )]
        return []


def _fake_http():
    async def handler(request):
        # A decodable PNG, not a placeholder: ``pipeline._download`` decodes
        # every body it keeps (see conftest.decodable_png).
        return httpx.Response(200, content=decodable_png())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_the_real_render_scores_the_re_keyed_row(
    session, tmp_path, monkeypatch
):
    """The claim the whole phase exists to make, with nothing stubbed between
    ``process_item`` and the render row: after a re-key the render is written
    and SCORED on the original row, so the Action Center's unscored floor
    actually falls."""
    stale = await _row(session, "1")
    stale_id = stale.id
    monkeypatch.setattr(pipeline.compositor, "run", lambda argv: None)
    monkeypatch.setattr(
        pipeline, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          imdb_id="tt15239678", year=2024, rating_key="1")

    async with _fake_http() as http:
        await pipeline.process_item(
            session, _config(tmp_path), http, _FakePlex(_resolved("2")),
            [_Provider()], intent,
        )

    session.expire_all()
    assert [row.id for row in await _items(session)] == [stale_id]
    renders = (
        await session.execute(select(Render).order_by(Render.art_kind))
    ).scalars().all()
    assert renders, "the pass produced no render row at all"
    assert {render.item_id for render in renders} == {stale_id}
    assert all(render.quality_scored_at is not None for render in renders), (
        "the re-keyed row's renders were not scored -- the unscored floor "
        "would not move"
    )

# --- the fork stop --------------------------------------------------------
#
# The re-key's refusals (above) are correct and stay exactly as ruled: no
# re-key, no audit row, the twin pair left to the merge job. What changes here
# is what the job does AFTERWARDS -- and ONLY when a media_items row ALREADY
# HOLDS the resolved key. Then the resolved item is somebody else's: it has
# its own row and its own visits, and this job was never asked about it. It
# used to carry on for it anyway -- upsert it, render every art kind onto it,
# upload, and -- because the unchanged-check compares against the intent's row
# -- write Plex fields to it on every single visit ("plex: wrote 2 field(s) to
# movie 'Boss Level'", every run, forever).
#
# When the resolved key is ROW-LESS the job proceeds exactly as it always has,
# minting the resolved item's row through the ordinary upsert. That is not an
# oversight: three of _rekey_by_identity's five refusals (zero identity
# candidates, an ambiguous pair, a 40P01 deadlock) leave it row-less, there is
# no twin for the merge job to reconcile on any of them, and the merge job
# creates no rows. The five refusal pins ABOVE are what hold that line; they
# are deliberately not touched by this task.


class _CountingPlex(_FakePlex):
    """``_FakePlex``, but ``fetch_item`` is recorded rather than forbidden.

    ``process_item``'s metadata block catches every exception and logs a
    WARNING, so a ``fetch_item`` that RAISED would let a fork that did NOT
    stop pass this file's tests. Counting is the only shape that fails loudly.
    """

    def __init__(self, item):
        super().__init__(item)
        self.fetched = []

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        return object()


async def _renders(session):
    return (await session.execute(select(Render).order_by(Render.id))).scalars().all()


async def _fork_stops(session):
    return (
        await session.execute(
            select(EventLog).where(EventLog.event_type == pipeline.FORK_EVENT)
        )
    ).scalars().all()


async def test_a_refused_re_key_stops_before_any_render_row(
    session, tmp_path, monkeypatch
):
    """The whole defect, in one test. Key 1's row is in another library, so
    the re-key correctly refuses -- and key 2 is the RESOLVED item's OWN row,
    which gets its own visits from its own intents. The job must then do
    NOTHING, rather than render and score somebody else's row.

    The pre-existing key-2 row is what makes this the stop's case at all: the
    five refusal pins above cover the row-LESS forks, where the job still
    falls through and mints. ``title="Boss Level"`` is the production line
    this defect was found in, and it doubles as the no-upsert probe --
    ``_upsert_media_item`` would overwrite it with the resolved title.
    """
    await _row(session, "1", library="Movies 4K")
    await _row(session, "2", library="Movies", title="Boss Level")
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    results = await pipeline.process_item(
        session, _config(tmp_path), None,
        _FakePlex(_resolved("2", library="Movies")), [], intent,
    )

    assert results == []
    session.expire_all()
    rows = {row.rating_key: row for row in await _items(session)}
    assert set(rows) == {"1", "2"}, "a third row was minted"
    assert rows["2"].title == "Boss Level", (
        "the resolved item's own row was upserted by this job"
    )
    assert seen == [], "a render row was written for the resolved item"
    assert await _renders(session) == []


async def test_the_fork_stop_writes_no_field_to_the_resolved_plex_item(
    session, tmp_path, monkeypatch
):
    """The observed symptom. ``apply_facts`` reaches Plex through the object
    ``fetch_item`` returns, so a stop that happens before that call is the
    thing that ends the write-every-run loop."""
    await _row(session, "1", library="Movies 4K")
    await _row(session, "2", library="Movies")
    _row_level_render_artifact(monkeypatch)
    config = _config(tmp_path)
    config.operations.enabled = True
    plex = _CountingPlex(_resolved("2", library="Movies"))
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    results = await pipeline.process_item(
        session, config, None, plex, [], intent, tmdb_facts=object(),
    )

    assert results == []
    assert plex.fetched == [], "the resolved item was fetched and written to"


async def test_the_fork_stop_warns_once_and_leaves_one_audit_row(
    session, tmp_path, monkeypatch, caplog
):
    """A stop nobody can see is a job that silently did nothing. The WARNING
    is the grep and the events_log row is the durable record -- filed under
    the re-key's own source, because an operator asking what happened to a
    row's key should grep one source, not two."""
    await _row(session, "1", library="Movies 4K")
    await _row(session, "2", library="Movies")
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    with caplog.at_level(logging.WARNING):
        await pipeline.process_item(
            session, _config(tmp_path), None,
            _FakePlex(_resolved("2", library="Movies")), [], intent,
        )

    forks = [
        record for record in caplog.records
        if "differs from the intent's" in record.getMessage()
    ]
    assert len(forks) == 1
    session.expire_all()
    (audit,) = await _fork_stops(session)
    assert audit.source == pipeline.REKEY_SOURCE
    assert audit.outcome == pipeline.FORK_OUTCOME
    assert audit.payload["intent_rating_key"] == "1"
    assert audit.payload["resolved_rating_key"] == "2"


async def test_a_successful_re_key_still_renders_the_intents_own_row(
    session, tmp_path, monkeypatch
):
    """The stop's first over-firing guard: a SUCCESSFUL re-key.

    A row does now exist under the resolved key -- the re-key just moved it
    there -- so an existence check on its own would stop the very case the
    re-key phase exists to enable. ``rekeyed_from == intent.rating_key`` is
    what settles it, and it settles it without a query.

    The stop's other over-firing guard is a refusal onto a ROW-LESS resolved
    key, and that is pinned by the five refusal tests above
    (``test_a_cross_library_match_is_not_a_re_key`` and its four siblings),
    which still assert the ordinary upsert's ``{"1", "2"}`` and must keep
    passing untouched through this whole task.
    """
    stale = await _row(session, "1")
    stale_id = stale.id
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    results = await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    assert len(results) == 2
    assert {item_id for item_id, _ in seen} == {stale_id}
    session.expire_all()
    assert await _fork_stops(session) == []
