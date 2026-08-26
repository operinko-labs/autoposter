"""Most-watched, computed from Tracearr's raw watch history.

Tracearr exposes no ranking endpoint of any kind (``docs/research/
tracearr-api-harvest.md``), so this module is the ranking: a pure function over
the records ``providers/tracearr.py`` pages back. No HTTP, no config, no
clients, no database -- which is what lets the 50 banked records of
``tests/fixtures/collections/tracearr_history_window.json`` serve as a real
oracle, and what keeps a ranking recomputable per pass rather than stored.

**A record is a play.** Tracearr groups sessions into resume chains and drops
chains under two minutes before it answers, so ``len(bucket)`` *is* the play
count Tracearr itself reports and nothing here re-filters. Watch time is the
sum of ``duration_ms``, which is a real integer (unlike ``progress_ms`` and
``total_duration_ms``, which arrive as strings and which nothing here reads).

**What a bucket is keyed by.** ``show_media_id`` for shows, ``media_id`` for
movies -- both canonical Tracearr ids and both merge-aware, so the same title
seen on two servers folds into one bucket with no title normalization, and
every episode of a show folds into the show.

**The identity-less plays, and why they are merged rather than dropped.** Some
plays carry no media identity at all: ``media_id``, ``show_media_id``,
``library_id`` and all three external ids null, while ``rating_key`` and
``grandparent_rating_key`` are still present. In the banked window that is 2
records of 50 -- both episodes of one show, whose honest count is therefore 20
and not the 18 a naive grouping reports. So every bucket also records the Plex
rating keys its own records were seen under, and an identity-less play joins
the bucket that has already been seen under its key. Nothing is double counted
(the play lands in exactly one bucket), nothing is split (the show stays one
row), no extra API call is spent, and there is no operator knob: an undercount
nobody can see is not a choice worth offering.

That merge runs as a second pass over the whole page, after every identified
bucket exists, so the answer does not depend on the order records arrive in.

**Multi-server caveat.** Plex rating keys are server-scoped, so on a
multi-server Tracearr two servers could in principle mint the same key for
different titles and an identity-less play could join the wrong bucket. The
deployment this was built for runs one server; the exposure is bounded to the
identity-less records only (2 of 50 here), and the alternative -- one
``/media/{ref}`` call per unidentified play -- buys nothing, because a record
with no media id has nothing to look up.

**Never an episode id.** A show bucket deliberately carries no external ids at
all. On an episode record ``imdb_id``/``tmdb_id``/``tvdb_id`` are the EPISODE's,
and ``GET /media/show:tvdb:<episode id>`` is a live-verified 404 -- so an
episode id emitted as a collection member resolves to nothing on a Show
library, and "matched nothing" looks exactly like a correct empty collection.
The ids are dropped here rather than guarded downstream, so the builder has
nothing to emit by accident. A show's real ids come from its media document,
which is the builder's business.
"""
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

__all__ = [
    "HISTORY_MEDIA_TYPE",
    "MEDIA_KINDS",
    "METRICS",
    "Bucket",
    "rank",
    "since_instant",
]

# Library type -> the kind of thing a most-watched collection of that library
# ranks. Also the ``allowed`` table the builder hands ``require_library_type``,
# the ``CHART_ENDPOINTS`` idiom: one table, so the two cannot disagree.
MEDIA_KINDS: dict[str, str] = {"Movie": "movie", "Show": "show"}

# ...and the Tracearr ``media_type`` whose records carry it. A show is ranked
# from its EPISODES' plays: Tracearr records no play against a show itself.
HISTORY_MEDIA_TYPE: dict[str, str] = {"movie": "movie", "show": "episode"}

# What "most watched" can mean. Both are free from one page-through -- plays is
# the record count, watch time is the summed duration. Deliberately not
# completed-only (``watched``) or unique users: the vocabulary can grow when
# something asks for it, and each new value is a new thing to explain.
METRICS: tuple[str, ...] = ("plays", "watch_time")

# Per media kind: what buckets a record, which Plex key that bucket is seen
# under, and which field holds the name a bucket is reported by.
_GROUP_FIELD = {"movie": "media_id", "show": "show_media_id"}
_RATING_KEY_FIELD = {"movie": "rating_key", "show": "grandparent_rating_key"}
_TITLE_FIELD = {"movie": "media_title", "show": "show_title"}

_EXTERNAL_ID_FIELDS = ("imdb_id", "tmdb_id", "tvdb_id")


@dataclass(frozen=True)
class Bucket:
    """One title's plays in the window.

    ``media_id`` is Tracearr's canonical uuid for the title -- the show's for a
    show, the movie's for a movie -- and is None for the last-resort bucket
    formed from identity-less plays alone. ``rating_key`` is the Plex key such
    a bucket was formed under, and is None otherwise: exactly one of the two is
    set, and which one decides how the builder names the members.

    The three external ids are populated for MOVIE buckets only. See the
    module docstring's never-an-episode-id rule for why a show bucket carries
    none at all.
    """

    media_id: str | None
    rating_key: str | None
    title: str
    plays: int
    watch_time_ms: int
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None


def since_instant(days: int, now: datetime) -> str:
    """The ``since`` value for a ``days``-long window ending at ``now``.

    An instant, and never a calendar day. ``/history``'s ``since``/``until``
    are instants while ``/media/{ref}/stats``' ``last_7``/``last_30`` are UTC
    calendar-day buckets, and the harvest demonstrates the two disagreeing on
    live data -- 5 plays against 3 for the same title in overlapping windows.
    Nothing in this phase consults the stats windows at all, and this shape is
    what keeps the two from being mixed by accident.

    Takes ``now`` rather than reading the clock, so it can be asserted against
    a fixed instant instead of a wall-clock delta (roadmap row 119).
    """
    moment = (now - timedelta(days=days)).astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def rank(
    records: Iterable[dict], *, media_kind: str, metric: str, limit: int
) -> list[Bucket]:
    """The ``limit`` most-watched titles in ``records``, best first.

    ``media_kind`` is ``"movie"`` or ``"show"``; ``metric`` is ``"plays"`` or
    ``"watch_time"``. Records of the other media type are ignored -- the
    builder already asks ``/history`` for one kind, and this is the second half
    of the same guard.
    """
    if media_kind not in _GROUP_FIELD:
        raise ValueError(
            f"unknown media kind {media_kind!r}: known media kinds are "
            + ", ".join(sorted(_GROUP_FIELD))
        )
    if metric not in METRICS:
        raise ValueError(
            f"unknown metric {metric!r}: known metrics are " + ", ".join(METRICS)
        )

    wanted = HISTORY_MEDIA_TYPE[media_kind]
    group_field = _GROUP_FIELD[media_kind]
    key_field = _RATING_KEY_FIELD[media_kind]
    title_field = _TITLE_FIELD[media_kind]
    mine = [record for record in records if record.get("media_type") == wanted]

    buckets: dict[str, dict] = {}
    order: list[str] = []
    identity_less: list[dict] = []

    for record in mine:
        group = record.get(group_field)
        if not group:
            identity_less.append(record)
            continue
        entry = buckets.get(group)
        if entry is None:
            entry = buckets[group] = {
                "media_id": group,
                "rating_key": None,
                "title": record.get(title_field) or record.get("media_title") or group,
                "plays": 0,
                "watch_time_ms": 0,
                "keys": set(),
                # Populated for movies only -- the never-an-episode-id rule.
                **{field: None for field in _EXTERNAL_ID_FIELDS},
            }
            order.append(group)
            if media_kind == "movie":
                for field in _EXTERNAL_ID_FIELDS:
                    entry[field] = _external_id(record.get(field))
        entry["plays"] += 1
        entry["watch_time_ms"] += _duration_ms(record)
        seen = record.get(key_field)
        if seen:
            entry["keys"].add(str(seen))

    # The second pass, and it has to be a second pass: an identity-less play
    # can appear before the bucket it belongs to (Tracearr answers newest
    # first, and a window boundary can land anywhere).
    for record in identity_less:
        seen = record.get(key_field) or record.get("rating_key")
        if not seen:
            # No identity and no Plex key: there is nothing to bucket it under
            # and nothing to guess. Logged so a window full of them is
            # visible, and not raised -- one unusable record must not take a
            # whole collection down.
            logger.debug(
                "a Tracearr play of %r carries neither a media id nor a rating key",
                record.get("media_title"),
            )
            continue
        seen = str(seen)
        home = next((key for key in order if seen in buckets[key]["keys"]), None)
        if home is None:
            home = "plex:%s" % seen
            if home not in buckets:
                buckets[home] = {
                    "media_id": None,
                    "rating_key": seen,
                    "title": (
                        record.get(title_field) or record.get("media_title") or seen
                    ),
                    "plays": 0,
                    "watch_time_ms": 0,
                    "keys": {seen},
                    **{field: None for field in _EXTERNAL_ID_FIELDS},
                }
                order.append(home)
        buckets[home]["plays"] += 1
        buckets[home]["watch_time_ms"] += _duration_ms(record)

    ranked = [
        Bucket(
            media_id=entry["media_id"],
            rating_key=entry["rating_key"],
            title=entry["title"],
            plays=entry["plays"],
            watch_time_ms=entry["watch_time_ms"],
            imdb_id=entry["imdb_id"],
            tmdb_id=entry["tmdb_id"],
            tvdb_id=entry["tvdb_id"],
        )
        for entry in (buckets[key] for key in order)
    ]
    ranked.sort(key=lambda bucket: _sort_key(bucket, metric))
    return ranked[:limit]


def _sort_key(bucket: Bucket, metric: str):
    """Best first, and deterministic all the way down.

    The tiebreak is the *other* measure, not the id: in a 30-day window every
    movie may well have exactly one play, and breaking that thirteen-way tie by
    uuid would order a collection alphabetically by an identifier no operator
    has ever seen. The id is the last term only so two titles with identical
    measures still order the same way on every pass -- a collection's member
    order is written to Plex, and an order that shuffles is a diff every pass.
    """
    primary, secondary = (
        (bucket.plays, bucket.watch_time_ms)
        if metric == "plays"
        else (bucket.watch_time_ms, bucket.plays)
    )
    return (-primary, -secondary, bucket.media_id or "", bucket.rating_key or "")


def _duration_ms(record: dict) -> int:
    """``duration_ms`` as an int, or 0 if it is unusable.

    Observed as a real integer on all 50 banked records -- unlike
    ``progress_ms``/``total_duration_ms``, which arrive as strings and which
    nothing here reads. Coerced anyway, and defaulted rather than raised: a
    title dropped from a ranking over one malformed field is a silently wrong
    collection, which is the outcome this whole module is arranged to avoid.
    """
    try:
        return int(record.get("duration_ms"))
    except (TypeError, ValueError):
        return 0


def _external_id(value) -> str | None:
    """One external id as a string, or None when the record carries none."""
    if value is None or value == "" or value == 0:
        return None
    return str(value)
