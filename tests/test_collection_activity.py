"""Ranking Tracearr's watch history: the oracle, and the merge rule.

``tracearr_history_window.json`` is the verbatim ``body`` of
``docs/research/tracearr/payloads/v2-history-since-window.json`` --
``GET /api/v2/public/history?since=2026-07-26&pageSize=50``, one page, 50
records, ``meta.nextCursor`` non-null (so it is the newest slice of the window,
roughly 2026-08-14 to 2026-08-24, not the whole 30 days). The uuids in it are
real, unscrubbed canonical media ids: media identity is deliberately kept in
these fixtures, and the PII-bearing ``user`` block was scrubbed at capture.

The harvest's own worked example (``docs/research/tracearr-api-harvest.md``
:768-819) reports **50 records, 2 skipped, 48 folded into 21 buckets, Warehouse
13 at 18 plays** -- and then says, in its own words, that the honest count for
that title is **20, not 18**. This module is that correction implemented: an
identity-less play joins the bucket whose rating keys it has already been seen
under, so the answer is **50 records, 21 buckets, Warehouse 13 at 20 plays**,
with nothing double-counted and nothing dropped.

Every expected number below was computed from the fixture, not remembered.

One test is the exception to all of that and says so in its own name and
docstring: ``test_the_merge_works_for_movies_too__synthetic_records`` uses
records written by hand, inline, because every movie record in the banked
window carries a ``media_id`` and the movie branch of the merge therefore has
no captured data behind it. Nothing synthetic is written to a fixture file, so
nothing invented can later be mistaken for a capture.
"""
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autoposter.collections.activity import (
    HISTORY_MEDIA_TYPE,
    MEDIA_KINDS,
    METRICS,
    Bucket,
    rank,
    since_instant,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"

WAREHOUSE_13 = "1626e7f7-6d58-4172-817c-bd56f95a295d"
REACHER = "40082858-dfa4-4508-b833-61b9b2654583"
COLIN = "e9141b5c-646d-422a-97dc-38e6dd00bd53"
SILO = "faf036e2-8459-4ace-a8ba-19486b6289c6"
HOUSE_OF_THE_DRAGON = "1a87c4a5-44ea-4fc9-899f-932f1b4adfdb"
TED_LASSO = "1193b102-4538-44a7-b7bb-00dbe26dd69c"
LANTERNS = "dc76248f-854d-4a6c-a8b7-80db867efaf0"
STRANGE_NEW_WORLDS = "b336544e-bd38-405e-b8c1-3628d5b3c650"

PROJECT_HAIL_MARY = "95cf5952-ce05-4c24-ab3b-db529e4d6e16"
BLADE = "e95f63eb-12e3-402b-936e-ceaebd78849a"
TWENTY_EIGHT_YEARS_LATER = "8d08947c-caa3-4625-b1e8-5d69fa6fc91d"
JURASSIC_WORLD = "b614cf9b-e403-4e34-b97a-25c436dd4ec3"
BLADE_TRINITY = "79e224e2-5dd6-447f-9ebc-c9e798300128"


def records(name="tracearr_history_window.json"):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["data"]


# --- the vocabulary -----------------------------------------------------------


def test_the_two_library_types_and_the_media_types_they_rank():
    assert MEDIA_KINDS == {"Movie": "movie", "Show": "show"}
    assert HISTORY_MEDIA_TYPE == {"movie": "movie", "show": "episode"}
    assert METRICS == ("plays", "watch_time")


def test_an_unknown_media_kind_or_metric_is_refused():
    with pytest.raises(ValueError, match="media kind"):
        rank(records(), media_kind="album", metric="plays", limit=5)
    with pytest.raises(ValueError, match="metric"):
        rank(records(), media_kind="show", metric="unique_users", limit=5)


# --- the oracle: shows --------------------------------------------------------


def test_the_show_ranking_reproduces_the_harvest_example_with_the_merge_rule():
    """The whole oracle, in one assertion.

    The harvest's table has Warehouse 13 at 18 and two records skipped. Both
    skipped records are Warehouse 13 episodes whose ``grandparent_rating_key``
    is "75916" -- the same key the identified Warehouse 13 records carry -- so
    they merge, and the count becomes the 20 the harvest itself calls honest.
    """
    ranked = rank(records(), media_kind="show", metric="plays", limit=100)

    assert [(bucket.media_id, bucket.plays, bucket.watch_time_ms) for bucket in ranked] == [
        (WAREHOUSE_13, 20, 42099000),
        (REACHER, 4, 7549301),
        (COLIN, 3, 5508000),
        (SILO, 3, 4982307),
        (HOUSE_OF_THE_DRAGON, 2, 7638000),
        (TED_LASSO, 2, 5478000),
        (LANTERNS, 2, 3308000),
        (STRANGE_NEW_WORLDS, 1, 3378000),
    ]
    # 37 of the 50 records are episodes, and every one of them is accounted for.
    assert sum(bucket.plays for bucket in ranked) == 37
    assert [bucket.title for bucket in ranked[:2]] == ["Warehouse 13", "Reacher"]


def test_the_two_identity_less_plays_are_merged_and_not_double_counted():
    """The merge rule, isolated: dropping the identity-less records would give
    18 (the harvest's own 10% undercount for this title), counting them as
    their own bucket would give 18 + 2 in two rows, and merging gives 20 in
    one. The banked window is 20 Warehouse 13 records in total."""
    ranked = rank(records(), media_kind="show", metric="plays", limit=100)
    warehouse = next(b for b in ranked if b.media_id == WAREHOUSE_13)

    assert warehouse.plays == 20
    assert warehouse.watch_time_ms == 42099000  # 38004000 identified + 4095000 merged
    assert [b for b in ranked if b.media_id is None] == []
    assert len(ranked) == 8


def test_the_merge_does_not_depend_on_the_order_records_arrive_in():
    """The identity-less pass runs after every identified bucket exists, so a
    page that happened to list the unidentified plays first still merges them.
    Tracearr orders newest-first and a window boundary can put them anywhere."""
    forwards = rank(records(), media_kind="show", metric="plays", limit=100)
    backwards = rank(list(reversed(records())), media_kind="show", metric="plays", limit=100)

    assert [(b.media_id, b.plays) for b in forwards] == [
        (b.media_id, b.plays) for b in backwards
    ]


def test_the_merge_works_for_movies_too__synthetic_records():
    """The movie half of the merge rule, on **synthetic records written for
    this test** -- not banked, not scrubbed capture, not evidence of anything
    Tracearr has ever sent.

    It needs saying because every other fixture in this module is real: all 13
    movie records in the banked window carry a ``media_id``, so the movie
    branch of the identity-less merge has no observed data behind it at all.
    That is a gap in the capture, not proof the case cannot happen -- the
    unidentified-play condition is a property of what the media server
    reported, and nothing about it is episode-specific. The branch is
    implemented symmetrically for both kinds, so it is tested symmetrically,
    with the source of the data stated plainly.

    Movies key off ``rating_key`` rather than ``grandparent_rating_key`` (a
    movie has no grandparent), which is the one thing that differs from the
    show case and the one thing this exercises.
    """
    identified = {
        "media_type": "movie", "media_id": "11111111-1111-4111-8111-000000000001",
        "media_title": "A Synthetic Film", "rating_key": "X", "duration_ms": 1000,
        "imdb_id": "tt0000001", "tmdb_id": 1, "tvdb_id": None,
    }
    same_key = {
        "media_type": "movie", "media_id": None, "media_title": "A Synthetic Film",
        "rating_key": "X", "duration_ms": 500,
        "imdb_id": None, "tmdb_id": None, "tvdb_id": None,
    }
    other_key = {
        "media_type": "movie", "media_id": None, "media_title": "Another One",
        "rating_key": "Y", "duration_ms": 250,
        "imdb_id": None, "tmdb_id": None, "tvdb_id": None,
    }

    ranked = rank(
        [identified, same_key, other_key], media_kind="movie", metric="plays", limit=10
    )

    assert ranked == [
        Bucket(
            media_id="11111111-1111-4111-8111-000000000001",
            rating_key=None,
            title="A Synthetic Film",
            plays=2,
            watch_time_ms=1500,
            imdb_id="tt0000001",
            tmdb_id="1",
            tvdb_id=None,
        ),
        Bucket(
            media_id=None,
            rating_key="Y",
            title="Another One",
            plays=1,
            watch_time_ms=250,
        ),
    ]


def test_an_identity_less_play_with_no_bucket_to_join_becomes_a_plex_bucket():
    """The documented last resort. The two records here are the same two that
    merge in the full window; alone, there is no Warehouse 13 bucket for them
    to join, so they form one bucket keyed by the Plex rating key they share --
    which resolves free against the engine's own index and is dropped, not
    guessed, when it is stale."""
    ranked = rank(
        records("tracearr_history_unidentified.json"),
        media_kind="show", metric="plays", limit=10,
    )

    assert ranked == [
        Bucket(
            media_id=None,
            rating_key="75916",
            title="Warehouse 13",
            plays=2,
            watch_time_ms=4095000,
        )
    ]


# --- the never-an-episode-id invariant ----------------------------------------


def test_a_show_bucket_never_carries_an_external_id():
    """The invariant, made structural rather than remembered.

    On an episode record ``imdb_id``/``tmdb_id``/``tvdb_id`` are the EPISODE's,
    and ``GET /media/show:tvdb:<episode id>`` is a live-verified 404 (harvest
    finding 3). An episode id emitted as a collection member would resolve to
    nothing on a Show library, and "matched nothing" is indistinguishable from
    a correct empty collection -- so the ids are not carried out of the ranking
    at all, and the builder has nothing to accidentally emit.
    """
    for bucket in rank(records(), media_kind="show", metric="plays", limit=100):
        assert bucket.imdb_id is None, bucket.title
        assert bucket.tmdb_id is None, bucket.title
        assert bucket.tvdb_id is None, bucket.title

    # And the ids really are present on the records, so the assertion above is
    # about what the ranking DOES rather than about an empty input.
    episodes = [r for r in records() if r["media_type"] == "episode"]
    assert any(r.get("tvdb_id") for r in episodes)


# --- the oracle: movies -------------------------------------------------------


def test_a_movie_bucket_carries_the_movies_own_ids():
    """The short-circuit that is real for movies and only for movies: a movie
    record's external ids are the MOVIE's, so no ``/media/{ref}`` call is
    needed to reach them."""
    ranked = rank(records(), media_kind="movie", metric="plays", limit=100)
    blade_trinity = next(b for b in ranked if b.media_id == BLADE_TRINITY)

    assert (blade_trinity.tmdb_id, blade_trinity.imdb_id, blade_trinity.tvdb_id) == (
        "36648", "tt0359013", "1700",
    )
    assert blade_trinity.title == "Blade: Trinity"


def test_the_movie_ranking_is_thirteen_single_play_buckets_ordered_by_watch_time():
    """Every movie in this window was played once, so the ``plays`` metric is
    one thirteen-way tie -- which is exactly why the ordering contract needs a
    tiebreak, and why the tiebreak is the other measure rather than the uuid."""
    ranked = rank(records(), media_kind="movie", metric="plays", limit=100)

    assert len(ranked) == 13
    assert {bucket.plays for bucket in ranked} == {1}
    assert [bucket.media_id for bucket in ranked[:5]] == [
        PROJECT_HAIL_MARY,          # 9318000 ms
        BLADE,                      # 6920569 ms
        TWENTY_EIGHT_YEARS_LATER,   # 6863000 ms
        JURASSIC_WORLD,             # 6859000 ms
        BLADE_TRINITY,              # 6535412 ms
    ]


def test_records_of_the_other_media_type_are_not_ranked():
    """The builder asks ``/history`` for one ``media_type``, and this is the
    second half of the same guard: 37 episode records in this window and 13
    movie records, and neither ranking ever sees the other's."""
    shows = rank(records(), media_kind="show", metric="plays", limit=100)
    movies = rank(records(), media_kind="movie", metric="plays", limit=100)

    assert sum(bucket.plays for bucket in shows) == 37
    assert sum(bucket.plays for bucket in movies) == 13


# --- the metric and the cap ---------------------------------------------------


def test_watch_time_ranks_by_summed_duration_and_reorders_the_table():
    """Both metrics come free from one page-through, and they genuinely
    disagree: House of the Dragon has two plays and outranks Reacher's four on
    watch time."""
    ranked = rank(records(), media_kind="show", metric="watch_time", limit=100)

    assert [(b.media_id, b.watch_time_ms) for b in ranked] == [
        (WAREHOUSE_13, 42099000),
        (HOUSE_OF_THE_DRAGON, 7638000),
        (REACHER, 7549301),
        (COLIN, 5508000),
        (TED_LASSO, 5478000),
        (SILO, 4982307),
        (STRANGE_NEW_WORLDS, 3378000),
        (LANTERNS, 3308000),
    ]


def test_the_limit_truncates_after_ranking_and_not_before():
    ranked = rank(records(), media_kind="show", metric="plays", limit=3)

    assert [bucket.media_id for bucket in ranked] == [WAREHOUSE_13, REACHER, COLIN]


def test_a_malformed_duration_costs_the_bucket_its_watch_time_and_not_its_place():
    """``duration_ms`` is a real integer on all 50 banked records, unlike
    ``progress_ms``/``total_duration_ms`` which arrive as strings and which
    nothing here reads. It is coerced anyway: a title dropped from a ranking
    over one malformed field is a silently wrong collection."""
    mangled = [dict(record) for record in records("tracearr_history_silo.json")]
    mangled[0]["duration_ms"] = None
    mangled[1]["duration_ms"] = "547000"

    ranked = rank(mangled, media_kind="show", metric="plays", limit=10)

    assert len(ranked) == 1
    assert ranked[0].plays == 3
    assert ranked[0].watch_time_ms == 547000 + 2811000


def test_an_empty_window_ranks_to_nothing():
    """Not an error: a deployment nobody watched anything on in the window is
    data. The builder's caller decides what an empty membership means."""
    assert rank([], media_kind="show", metric="plays", limit=10) == []


# --- the window ---------------------------------------------------------------


def test_since_instant_is_an_instant_and_not_a_calendar_day():
    """``/history``'s ``since`` is an instant; ``/media/{ref}/stats``'
    ``last_7``/``last_30`` are UTC calendar-day buckets, and the harvest shows
    the two disagreeing on live data (Silo: 5 plays against 3). Nothing in this
    phase consults the stats windows, and this is the shape that keeps them
    apart. ``now`` is an argument so this is a fixed assertion rather than a
    wall-clock one (roadmap row 119)."""
    now = datetime(2026, 8, 25, 6, 38, 38, tzinfo=UTC)

    assert since_instant(30, now) == "2026-07-26T06:38:38Z"
    assert since_instant(1, now) == "2026-08-24T06:38:38Z"
    assert since_instant(365, now) == "2025-08-25T06:38:38Z"


def test_since_instant_normalises_a_non_utc_now_to_utc():
    """The instant is Z-suffixed, so a process running in a non-UTC timezone
    must not send a local wall-clock time as though it were UTC."""
    from datetime import timedelta, timezone

    helsinki = timezone(timedelta(hours=3))
    now = datetime(2026, 8, 25, 9, 38, 38, tzinfo=helsinki)

    assert since_instant(30, now) == "2026-07-26T06:38:38Z"


# --- purity -------------------------------------------------------------------


def test_the_ranking_module_imports_nothing_that_can_do_io():
    """Structural, the ``catalog.py`` precedent: this module is a pure function
    over records, so it can be exercised against the banked oracle without a
    transport, a config or a client -- and so a future edit cannot quietly give
    it a network call."""
    import ast

    source = Path("src/autoposter/collections/activity.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"collections", "dataclasses", "datetime", "logging"}, imported
