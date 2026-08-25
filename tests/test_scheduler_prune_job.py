"""``find_prunable`` / ``retire`` / ``make_prune_job``: the media_items prune sweep.

Roadmap row 129. Items that leave Plex -- deleted, moved into a library the
operator excluded, or re-matched under a new rating key -- leave a
``media_items`` row nothing removes. Every full pass re-enqueues that row, the
job cannot resolve it, and it parks; forever. This sweep retires those rows.

The three things worth knowing before changing anything here:

* an unreachable Plex makes EVERY row look gone, so "cannot probe" must read as
  refuse and never as a work order -- the inversion of the cleanup sweep's
  empty-table guard, and the reason several tests here are about NOT deleting;
* ``media_items.parent_id`` cascades, so a show deleted first takes its seasons
  and episodes with it, silently and without audit rows -- hence the
  descendant rules and the deepest-first ordering;
* the delete is keyed on ``(id, updated_at)`` so a row a worker re-upserted
  under the pass survives it.
"""
from autoposter.db.models import MediaItem
from autoposter.scheduler.prune import find_prunable, intent_for


class FakePlex:
    """A ``PlexClient`` stand-in answering only the question the pruner asks.

    ``live`` is the set of rating keys that still resolve; ``error``, when set,
    is raised instead, because "the probe failed" and "everything is gone" must
    never be the same answer.
    """

    def __init__(self, live=(), *, error=None):
        self._live = set(live)
        self._error = error
        self.asked = []

    async def exists_many(self, intents):
        if self._error is not None:
            raise self._error
        self.asked.extend(intents)
        return [intent.rating_key in self._live for intent in intents]


async def _add_item(session, rating_key, *, kind="movie", parent=None, **columns):
    """One ``media_items`` row, committed so its ``updated_at`` is a settled
    value from its own transaction (which the concurrency guard depends on)."""
    item = MediaItem(
        rating_key=rating_key,
        library=columns.pop("library", "Movies" if kind == "movie" else "TV Shows"),
        kind=kind,
        title=columns.pop("title", f"Item {rating_key}"),
        parent_id=parent.id if parent is not None else None,
        **columns,
    )
    session.add(item)
    await session.commit()
    return item


async def test_a_row_plex_still_resolves_is_never_a_candidate(session):
    await _add_item(session, "10")

    scan = await find_prunable(session, FakePlex(live={"10"}))

    assert scan.prunable == []
    assert (scan.gone, scan.held, scan.total) == (0, 0, 1)


async def test_a_row_that_no_longer_resolves_is_prunable(session):
    await _add_item(session, "10")
    await _add_item(session, "11")

    scan = await find_prunable(session, FakePlex(live={"10"}))

    assert [c.rating_key for c in scan.prunable] == ["11"]
    assert (scan.gone, scan.held, scan.total) == (1, 0, 2)


async def test_the_probe_is_asked_exactly_what_the_pipeline_asks(session):
    """The intent carries the stored rating key and the row's external ids,
    field for field what ``_enqueue_reprocess`` builds (api/routes.py). Probing
    a different question than the pipeline poses would judge rows on evidence
    the pipeline never sees."""
    await _add_item(
        session, "42", kind="episode", title="An Episode",
        tvdb_id=77, season_number=1, episode_number=3, year=2020,
    )
    plex = FakePlex(live={"42"})

    await find_prunable(session, plex)

    (intent,) = plex.asked
    assert intent.rating_key == "42"
    assert intent.kind == "episode"
    assert intent.title == "An Episode"
    assert intent.tvdb_id == 77
    assert (intent.season_number, intent.episode_number) == (1, 3)
    assert intent.year == 2020


async def test_a_parent_is_held_when_any_descendant_still_resolves(session):
    """``parent_id`` cascades. Deleting a show whose episode still resolves
    would take a live row with it, silently -- so one surviving descendant
    holds the whole chain above it, and the count is reported, not swallowed."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)

    scan = await find_prunable(session, FakePlex(live={"111"}))

    assert scan.prunable == []
    assert (scan.gone, scan.held, scan.total) == (2, 2, 3)


async def test_a_family_that_is_entirely_gone_is_prunable_deepest_first(session):
    """Order is not cosmetic: the cascade means a show deleted first removes
    its season and episode before either gets an audit row of its own."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)

    scan = await find_prunable(session, FakePlex(live=set()))

    assert [c.rating_key for c in scan.prunable] == ["111", "110", "100"]
    assert (scan.gone, scan.held, scan.total) == (3, 0, 3)


async def test_a_gone_child_under_a_surviving_parent_is_prunable_on_its_own(session):
    """The re-match case: the show is fine, 64 episode rows are not. The
    descendant rule protects live rows from a cascade; it does not protect
    gone rows from themselves."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)

    scan = await find_prunable(session, FakePlex(live={"100", "110"}))

    assert [c.rating_key for c in scan.prunable] == ["111"]
    assert (scan.gone, scan.held, scan.total) == (1, 0, 3)


async def test_an_empty_table_probes_nothing(session):
    plex = FakePlex(live=set())

    scan = await find_prunable(session, plex)

    assert (scan.prunable, scan.gone, scan.held, scan.total) == ([], 0, 0, 0)
    assert plex.asked == []


async def test_the_scan_counts_the_asset_directories_a_prune_would_orphan(session):
    """One per pruned movie or show. Seasons and episodes keep their artwork
    under the show's folder (render/naming.py), so they orphan nothing of their
    own -- and the count is what the summary hands to the operator, because
    those directories become the existing asset_cleanup sweep's work."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)
    await _add_item(session, "200", kind="movie")

    scan = await find_prunable(session, FakePlex(live=set()))

    assert len(scan.prunable) == 4
    assert scan.directories == 2


def test_intent_for_leaves_no_identity_behind():
    """Every id the row has must ride into the probe: the rating-key hint can
    be stale (Plex renumbers on a rebuild) and the GUID fallback is all that
    stands between a renumbered item and a wrongly pruned row."""
    from datetime import datetime, timezone

    from autoposter.scheduler.prune import PruneCandidate

    candidate = PruneCandidate(
        id=1, rating_key="9", kind="movie", library="Movies", title="A Movie",
        parent_id=None, tmdb_id=1, tvdb_id=2, imdb_id="tt3", year=1999,
        season_number=None, episode_number=None, logo_upload_key=None,
        updated_at=datetime.now(timezone.utc),
    )

    intent = intent_for(candidate)

    assert (intent.tmdb_id, intent.tvdb_id, intent.imdb_id) == (1, 2, "tt3")
    assert intent.rating_key == "9"
    assert intent.kind == "movie"
