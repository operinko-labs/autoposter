"""``find_mergeable`` / ``merge`` / ``make_merge_job``: the twin-row merge.

Before the pipeline learned to re-key, a re-matched item produced a SECOND
``media_items`` row under the live key. The original kept its render rows, its
facts, its credits, its dismissals, its ``logo_upload_key`` and its children,
and was never written again. This job reconciles those pairs.

The three things worth knowing before changing anything here:

* ``media_items.parent_id`` cascades, so deleting the stale row of a show pair
  would take its seasons and episodes with it -- children are repointed onto
  the survivor BEFORE the delete, in the same transaction, and that ordering
  is the single most dangerous thing in this module;
* the dry run is PROBE-FREE by design (it elects the survivor by newest rating
  key) so the report can be read while Plex is down, and the applied pass
  probes both rows by key before it deletes anything;
* a repointed dismissal usually re-surfaces, because ``action_dismissals``
  keys on a hash of the render facts and the survivor is scored where the
  stale row was not. That is the dismissal contract working, not a bug, and
  the summary says so.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from autoposter.config.holder import ConfigHolder
from autoposter.db.models import (
    ActionDismissal, EventLog, ItemCredit, ItemFacts, Job, MediaItem, Render,
)
from autoposter.plex.client import ResolvedItem
from autoposter.render.pipeline import _upsert_media_item
from autoposter.scheduler import merge as merge_module
from autoposter.scheduler.merge import (
    MERGE_EVENT,
    MERGE_SOURCE,
    MergeRefused,
    find_mergeable,
    implausible_merge_count,
    intent_for_row,
    make_merge_job,
    merge,
    verify_survivors,
)


class FakePlex:
    """A ``PlexClient`` stand-in answering only ``keys_resolve``.

    ``live`` is the set of rating keys whose OWN key Plex still accepts --
    which is the whole question the survivor election asks, and the reason
    this double does not implement ``exists_many`` at all. ``error``, when
    set, is raised instead: "the probe failed" and "the key is refused" must
    never be the same answer.
    """

    def __init__(self, live=(), *, error=None):
        self._live = set(live)
        self._error = error
        self.asked = []

    async def keys_resolve(self, intents):
        if self._error is not None:
            raise self._error
        self.asked.extend(intents)
        return [intent.rating_key in self._live for intent in intents]


async def _item(session, rating_key, **columns):
    """One committed ``media_items`` row. Committed so ``updated_at`` is a
    settled value from its own transaction -- half the delete's key."""
    fields = dict(
        library="Movies", kind="movie", title="Dune: Part Two", year=2024,
        tmdb_id=693134,
    )
    fields.update(columns)
    item = MediaItem(rating_key=rating_key, **fields)
    session.add(item)
    await session.commit()
    return item


async def _render(session, item_id, art_kind, *, scored=False, path=None):
    render = Render(
        item_id=item_id, art_kind=art_kind, status="rendered",
        asset_path=path or f"/assets/Movies/Dune Part Two (2024)/{art_kind}.jpg",
    )
    session.add(render)
    await session.commit()
    if scored:
        await session.execute(
            Render.__table__.update()
            .where(Render.id == render.id)
            .values(quality_scored_at=func.now())
        )
        await session.commit()
    return render


async def _pair(session, *, stale_key="1", survivor_key="2", **columns):
    """The canonical twin pair: one identity, two keys, survivor is the newer."""
    stale = await _item(session, stale_key, **columns)
    survivor = await _item(session, survivor_key, **columns)
    return stale, survivor


def _config(*, apply=False, max_merges=500, max_merge_share=0.25):
    """Only what the job reads -- the ``tests/test_scheduler_prune_job.py``
    ``_config`` pattern, so each test's intent stays on screen."""
    return SimpleNamespace(
        merge=SimpleNamespace(
            apply=apply, max_merges=max_merges, max_merge_share=max_merge_share
        ),
        scheduler=SimpleNamespace(merge_days=7),
    )


def _job(config, plex, *, healthy=True):
    return make_merge_job(ConfigHolder(config), lambda: plex, lambda: healthy)


# --- finding and electing --------------------------------------------------


async def test_two_rows_with_one_identity_are_a_pair_and_the_newer_key_survives(
    session,
):
    """A5: the dry run elects by newest rating key, which is probe-free, so
    the report runs anywhere -- including with Plex down."""
    await _pair(session, stale_key="16201", survivor_key="165269")

    scan = await find_mergeable(session)

    assert len(scan.plans) == 1
    pair = scan.plans[0].pair
    assert pair.stale.rating_key == "16201"
    assert pair.survivor.rating_key == "165269"
    assert scan.total == 2


async def test_a_lone_row_is_not_a_pair(session):
    await _item(session, "1")

    scan = await find_mergeable(session)

    assert scan.plans == []


async def test_three_rows_for_one_identity_are_ambiguous_and_left_alone(session):
    """Three rows for one item is not three merges, it is one ambiguity. A
    pairwise walk would happily collapse them in an order nobody chose."""
    for key in ("1", "2", "3"):
        await _item(session, key)

    scan = await find_mergeable(session)

    assert scan.plans == []
    assert scan.ambiguous == 1


async def test_a_non_numeric_rating_key_makes_the_pair_unelectable(session):
    """The election compares keys as integers. A key that is not a number
    cannot be ordered, and guessing an order would guess which row dies."""
    await _pair(session, stale_key="1", survivor_key="not-a-number")

    scan = await find_mergeable(session)

    assert scan.plans == []
    assert scan.unelectable == 1


async def test_rows_in_different_libraries_are_not_a_pair(session):
    """An item that exists in two libraries at once -- a 4K copy and an HD one
    -- is two items. Merging them is data loss."""
    await _item(session, "1", library="Movies")
    await _item(session, "2", library="Movies 4K")

    assert (await find_mergeable(session)).plans == []


async def test_rows_with_no_shared_external_id_are_not_a_pair(session):
    await _item(session, "1", tmdb_id=111)
    await _item(session, "2", tmdb_id=222)

    assert (await find_mergeable(session)).plans == []


async def test_two_seasons_of_one_show_are_not_a_pair(session):
    """Every season carries the SHOW's ids, so without the season number in
    the predicate the whole show collapses into one row."""
    common = dict(kind="season", library="TV Shows", title="A Show",
                  tmdb_id=None, tvdb_id=77)
    await _item(session, "10", season_number=1, **common)
    await _item(session, "11", season_number=2, **common)

    assert (await find_mergeable(session)).plans == []


async def test_a_row_with_no_external_ids_is_never_paired(session):
    await _item(session, "1", tmdb_id=None, tvdb_id=None, imdb_id=None)
    await _item(session, "2", tmdb_id=None, tvdb_id=None, imdb_id=None)

    assert (await find_mergeable(session)).plans == []


async def test_the_scan_counts_unscored_rows_with_no_twin_separately(session):
    """A2's population, made visible before any apply. An unscored render on a
    row that has NO identity twin is a different problem -- an adopted
    season/episode whose stored ids are its own, or a genuine miss -- and this
    job must not claim credit for it."""
    lonely = await _item(session, "9", tmdb_id=555)
    await _render(session, lonely.id, "poster")
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, survivor.id, "poster", scored=True)

    scan = await find_mergeable(session)

    assert len(scan.plans) == 1
    assert scan.no_identity_match == 1


async def test_the_scan_counts_pairs_where_neither_row_is_scored(session):
    """The operator's own cross-check: a pair with no scored render on either
    row is a second defect wearing this one's clothes, and the merge must not
    report it as progress."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, survivor.id, "background")

    scan = await find_mergeable(session)

    assert len(scan.plans) == 1
    assert scan.neither_scored == 1


# --- planning --------------------------------------------------------------


async def test_a_kind_the_survivor_lacks_is_planned_for_a_repoint(session):
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, survivor.id, "background")

    (plan,) = (await find_mergeable(session)).plans

    assert plan.renders_repoint == ["poster"]
    assert plan.renders_drop == []


async def test_a_kind_both_rows_hold_is_planned_for_a_drop(session):
    """The survivor's is the one being scored, so the stale row's is the pure
    duplicate. Removing it is what honestly shrinks the denominator."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, survivor.id, "poster", scored=True)

    (plan,) = (await find_mergeable(session)).plans

    assert plan.renders_repoint == []
    assert plan.renders_drop == ["poster"]


# --- applying --------------------------------------------------------------


async def test_a_repointed_render_keeps_its_asset_path(session):
    """Nothing moves on disk: both rows record the SAME path, because
    ``naming.asset_path`` is keyed by library and root folder."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster",
                  path="/assets/Movies/Dune Part Two (2024)/poster.jpg")
    survivor_id = survivor.id
    scan = await find_mergeable(session)

    await merge(session, scan.plans)
    await session.commit()

    session.expire_all()
    poster = (await session.execute(select(Render))).scalar_one()
    assert poster.item_id == survivor_id
    assert poster.asset_path == "/assets/Movies/Dune Part Two (2024)/poster.jpg"


async def test_a_duplicate_render_is_deleted_and_the_survivors_is_kept(session):
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    kept = await _render(session, survivor.id, "poster", scored=True)
    kept_id = kept.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.renders_dropped == 1 and outcome.renders_repointed == 0
    session.expire_all()
    assert [r.id for r in (await session.execute(select(Render))).scalars()] == [kept_id]


async def test_children_are_repointed_before_the_delete_and_no_cascade_loss(session):
    """The most dangerous step in the module. ``media_items.parent_id`` is a
    self-FK with ON DELETE CASCADE, so a delete that ran first would take
    every season and episode under the stale show with it -- silently, with no
    audit row and nothing in the counts."""
    common = dict(kind="show", library="TV Shows", title="A Show",
                  tmdb_id=None, tvdb_id=77)
    stale = await _item(session, "100", **common)
    survivor = await _item(session, "200", **common)
    season = await _item(session, "110", kind="season", library="TV Shows",
                         title="A Show", tmdb_id=None, tvdb_id=77,
                         season_number=1, parent_id=stale.id)
    episode = await _item(session, "111", kind="episode", library="TV Shows",
                          title="An Episode", tmdb_id=None, tvdb_id=77,
                          season_number=1, episode_number=3, parent_id=season.id)
    survivor_id, season_id, episode_id = survivor.id, season.id, episode.id
    scan = await find_mergeable(session)
    assert [p.pair.stale.rating_key for p in scan.plans] == ["100"]

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.children_repointed == 1
    session.expire_all()
    rows = {row.id: row for row in (await session.execute(select(MediaItem))).scalars()}
    assert set(rows) == {survivor_id, season_id, episode_id}, (
        "the cascade took a live child with the stale row"
    )
    assert rows[season_id].parent_id == survivor_id
    assert rows[episode_id].parent_id == season_id


async def test_a_child_inserted_between_scan_and_lock_is_still_repointed(session):
    """F1 (MEDIUM): the old code gated the repoint on ``plan.children``, a
    ``COUNT(*)`` taken at scan time, minutes before the lock. Inserting a
    child does not write the stale row, so the ``(id, updated_at)`` guard
    cannot see one that lands in the scan-to-lock window -- and a gate on the
    stale count would leave it for the CASCADE delete to take silently. The
    repoint must run off what actually exists under the lock."""
    common = dict(kind="show", library="TV Shows", title="A Show",
                  tmdb_id=None, tvdb_id=77)
    stale = await _item(session, "100", **common)
    survivor = await _item(session, "200", **common)
    survivor_id = survivor.id
    scan = await find_mergeable(session)
    assert scan.plans[0].children == 0, "nothing under the stale row at scan time"

    # Simulate the scan-to-lock window: a child lands under the stale row
    # AFTER the plan was built but BEFORE merge() takes its lock.
    season = await _item(session, "110", kind="season", library="TV Shows",
                         title="A Show", tmdb_id=None, tvdb_id=77,
                         season_number=1, parent_id=stale.id)
    season_id = season.id

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.children_repointed == 1
    session.expire_all()
    rows = {row.id: row for row in (await session.execute(select(MediaItem))).scalars()}
    assert season_id in rows, (
        "the cascade took a child that arrived in the scan-to-lock window"
    )
    assert rows[season_id].parent_id == survivor_id


async def test_a_dismissal_the_survivor_lacks_is_repointed(session):
    """A4: it is repointed and it usually re-surfaces, because the evidence
    hash covers whether the row is scored. That is the dismissal contract --
    a dismissal holds only while the facts hold."""
    stale, survivor = await _pair(session)
    session.add(ActionDismissal(item_id=stale.id, art_kind="poster",
                                flag="language_miss", evidence="a" * 64))
    await session.commit()
    survivor_id = survivor.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.dismissals_repointed == 1 and outcome.dismissals_dropped == 0
    session.expire_all()
    assert (
        await session.execute(select(ActionDismissal))
    ).scalar_one().item_id == survivor_id


async def test_a_dismissal_both_rows_hold_is_dropped(session):
    stale, survivor = await _pair(session)
    for item in (stale, survivor):
        session.add(ActionDismissal(item_id=item.id, art_kind="poster",
                                    flag="language_miss", evidence="b" * 64))
    await session.commit()
    survivor_id = survivor.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.dismissals_dropped == 1
    session.expire_all()
    assert (
        await session.execute(select(ActionDismissal))
    ).scalar_one().item_id == survivor_id


async def test_a_dismissal_inserted_between_scan_and_lock_is_repointed(session):
    """F2 (LOW): the old code decided repoint-vs-drop off art_kind sets
    captured at scan time. api/action_center.py inserts a dismissal without
    writing media_items, so the ``(id, updated_at)`` guard cannot see one
    that lands in the scan-to-lock window -- a scan-time set would leave it
    for the CASCADE delete to take silently instead of repointing it."""
    stale, survivor = await _pair(session)
    survivor_id = survivor.id
    scan = await find_mergeable(session)
    assert scan.plans[0].dismissals_repoint == [], "nothing on the stale row at scan time"

    # Simulate the scan-to-lock window: a dismissal lands on the stale row
    # AFTER the plan was built but BEFORE merge() takes its lock.
    session.add(ActionDismissal(item_id=stale.id, art_kind="poster",
                                flag="language_miss", evidence="c" * 64))
    await session.commit()

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.dismissals_repointed == 1 and outcome.dismissals_dropped == 0
    session.expire_all()
    assert (
        await session.execute(select(ActionDismissal))
    ).scalar_one().item_id == survivor_id


async def test_facts_credits_and_the_logo_marker_are_carried(session):
    """``logo_upload_key`` is the marker that makes a logo revert safe. Losing
    it means the revert can never run again for that item -- which is why
    ``prune.retire`` writes it into its audit rather than letting it vanish."""
    stale, survivor = await _pair(session)
    stale.logo_upload_key = "upload://abc"
    session.add(ItemFacts(item_id=stale.id, critic_rating=7.5))
    session.add(ItemCredit(item_id=stale.id, kind="actor", person="Someone"))
    session.add(ItemCredit(item_id=survivor.id, kind="actor", person="Someone"))
    session.add(ItemCredit(item_id=stale.id, kind="director", person="Another"))
    await session.commit()
    survivor_id = survivor.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.facts_repointed == 1 and outcome.logos_carried == 1
    session.expire_all()
    survivor_row = (
        await session.execute(select(MediaItem).where(MediaItem.id == survivor_id))
    ).scalar_one()
    assert survivor_row.logo_upload_key == "upload://abc"
    facts = (await session.execute(select(ItemFacts))).scalar_one()
    assert facts.item_id == survivor_id and facts.critic_rating == pytest.approx(7.5)
    credits = {
        (credit.kind, credit.person)
        for credit in (await session.execute(select(ItemCredit))).scalars()
    }
    assert credits == {("actor", "Someone"), ("director", "Another")}


async def test_the_survivors_own_parent_link_is_carried_when_it_has_none(session):
    """A season minted by the fork resolved its parent by the LIVE rating key
    while the show's row still held the STALE one, so the parent lookup
    missed and the survivor was inserted with ``parent_id = NULL``. The stale
    season holds the correct link, and losing it orphans the survivor from
    its show."""
    show = await _item(session, "500", kind="show", library="TV Shows",
                        title="A Show", tmdb_id=None, tvdb_id=99)
    _stale = await _item(session, "501", kind="season", library="TV Shows",
                          title="A Show", tmdb_id=None, tvdb_id=88,
                          season_number=1, parent_id=show.id)
    survivor = await _item(session, "502", kind="season", library="TV Shows",
                            title="A Show", tmdb_id=None, tvdb_id=88,
                            season_number=1, parent_id=None)
    show_id, survivor_id = show.id, survivor.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.parents_carried == 1
    session.expire_all()
    survivor_row = (
        await session.execute(select(MediaItem).where(MediaItem.id == survivor_id))
    ).scalar_one()
    assert survivor_row.parent_id == show_id


async def test_the_merge_writes_one_audit_row_carrying_both_identities(session):
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    scan = await find_mergeable(session)

    await merge(session, scan.plans)
    await session.commit()

    session.expire_all()
    (audit,) = (
        await session.execute(select(EventLog).where(EventLog.source == MERGE_SOURCE))
    ).scalars().all()
    assert audit.event_type == MERGE_EVENT
    assert audit.payload["stale_rating_key"] == "1"
    assert audit.payload["survivor_rating_key"] == "2"
    assert audit.payload["renders_repointed"] == ["poster"]


async def test_a_row_re_upserted_under_the_pass_is_skipped_and_nothing_is_lost(
    session,
):
    """The concurrency guard, exercised through the actual writer. The delete
    is keyed on ``(id, updated_at)`` like ``prune.retire``'s, and the check
    runs under the row lock BEFORE anything is repointed -- so a skipped pair
    leaves the graph exactly as it found it, not half-moved."""
    stale, survivor = await _pair(session)
    # Captured before any ``expire_all`` below: reading ``.id`` off an expired
    # instance is a lazy load, which under asyncio is a MissingGreenlet rather
    # than a query.
    stale_id, survivor_id = stale.id, survivor.id
    await _render(session, stale.id, "poster")
    scan = await find_mergeable(session)

    resolved = ResolvedItem(
        rating_key="1", library="Movies", kind="movie", title="New Title",
        year=2024, season_number=None, episode_number=None,
        root_folder="Dune Part Two (2024)", file_path="/mnt/Media/Movies/x.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id=None,
    )
    await _upsert_media_item(session, resolved)
    await session.commit()

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.merged == [] and outcome.skipped == 1
    session.expire_all()
    assert {row.id for row in (await session.execute(select(MediaItem))).scalars()} == {
        stale_id, survivor_id,
    }
    poster = (await session.execute(select(Render))).scalar_one()
    assert poster.item_id == stale_id, "a skipped pair must not be half-merged"
    assert (
        await session.execute(select(EventLog).where(EventLog.source == MERGE_SOURCE))
    ).scalars().all() == []


async def test_a_survivor_re_upserted_under_the_pass_is_skipped_and_nothing_is_lost(
    session,
):
    """The guard is symmetric. A worker landing on the SURVIVOR's live key
    during ``verify_survivors``'s probe walk re-upserts ``media_items`` before
    it can go on to write a render -- if this pass did not notice, the
    repoint below would collide with that render (``uq_render_item_kind``) as
    an uncaught IntegrityError, not a clean skip."""
    stale, survivor = await _pair(session)
    stale_id, survivor_id = stale.id, survivor.id
    await _render(session, stale.id, "poster")
    scan = await find_mergeable(session)

    resolved = ResolvedItem(
        rating_key="2", library="Movies", kind="movie", title="Dune: Part Two",
        year=2024, season_number=None, episode_number=None,
        root_folder="Dune Part Two (2024)", file_path="/mnt/Media/Movies/x.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id=None,
    )
    await _upsert_media_item(session, resolved)
    await session.commit()

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.merged == [] and outcome.skipped == 1
    session.expire_all()
    assert {row.id for row in (await session.execute(select(MediaItem))).scalars()} == {
        stale_id, survivor_id,
    }
    poster = (await session.execute(select(Render))).scalar_one()
    assert poster.item_id == stale_id, "a skipped pair must not be half-merged"


# --- the caps and the job --------------------------------------------------


def test_an_implausible_absolute_count_refuses_with_both_numbers():
    config = SimpleNamespace(max_merges=3, max_merge_share=0.25)

    refusal = implausible_merge_count(4, 100, config)

    assert refusal is not None
    assert "4" in refusal and "3" in refusal and "100" in refusal
    assert "refus" in refusal.lower()
    assert implausible_merge_count(3, 100, config) is None


def test_an_implausible_share_refuses_on_a_library_too_small_for_the_cap():
    config = SimpleNamespace(max_merges=500, max_merge_share=0.25)

    assert implausible_merge_count(10, 20, config) is not None
    # Below the minimum sample the share means nothing; only the absolute cap
    # applies -- "2 of 3 rows are twins" is a small library, not evidence.
    assert implausible_merge_count(2, 3, config) is None


def test_the_share_cap_counts_rows_not_pairs():
    """L4: a pair occupies TWO rows, so the share this cap compares against
    config/autoposter.example.yaml's "share of the library" is rows, not
    pairs -- pairs / total would silently need double the real duplication
    before the 0.25 default ever fired."""
    config = SimpleNamespace(max_merges=500, max_merge_share=0.25)

    # 2 pairs = 4 rows of 20 total = exactly a 20% share of rows: must not
    # refuse yet under the rows reading.
    assert implausible_merge_count(2, 20, config) is None
    # 3 pairs = 6 rows of 20 total = a 30% share of rows: over the cap.
    refusal = implausible_merge_count(3, 20, config)
    assert refusal is not None
    assert "3" in refusal and "6" in refusal and "20" in refusal


async def test_the_job_is_named_and_paced_off_the_holder():
    holder = ConfigHolder(_config())
    job = make_merge_job(holder, lambda: FakePlex(), lambda: True)

    assert job.name == "plex_merge"
    assert job.current_interval() == 7 * 24 * 3600

    faster = _config()
    faster.scheduler.merge_days = 1
    holder.swap(faster)

    assert job.current_interval() == 24 * 3600


async def test_an_empty_media_items_table_refuses(session):
    def exploding_factory():
        raise AssertionError("no Plex client may be built for an empty table")

    job = make_merge_job(ConfigHolder(_config(apply=True)), exploding_factory,
                         lambda: True)

    summary = await job.run(session)

    assert "refus" in summary.lower() and "empty" in summary.lower()


async def test_an_empty_scan_never_builds_a_plex_client(session):
    """L7: after the first applied pass, an empty scan (a non-empty table
    with no twin pairs) is every week's steady state. Building the client
    anyway means a connect -- and a possible refusal -- on a pass with
    nothing to do."""
    await _item(session, "1")  # no twin: an empty plan list, non-empty table

    def exploding_factory():
        raise AssertionError(
            "no Plex client may be built when the scan found no plans"
        )

    job = make_merge_job(ConfigHolder(_config(apply=True)), exploding_factory,
                         lambda: True)

    summary = await job.run(session)

    assert summary.startswith("merged 0 of 1")


async def test_the_dry_run_needs_no_plex_and_writes_nothing(session):
    """A5's whole point: the report is readable during an outage, so the
    operator can size the population before deciding anything. L2: the dry
    run is the surface the runbook tells the operator to read before flipping
    ``apply``, so it must preview the same categories -- facts, logo marker,
    survivor parent link -- the applied summary later reports."""
    parent = await _item(session, "9", tmdb_id=999999)
    stale, survivor = await _pair(session)
    stale.parent_id = parent.id
    stale.logo_upload_key = "upload://abc"
    session.add(ItemFacts(item_id=stale.id))
    await session.commit()
    await _render(session, stale.id, "poster")
    stale_id = stale.id

    def exploding_factory():
        raise AssertionError("the dry run must not build a Plex client")

    job = make_merge_job(ConfigHolder(_config()), exploding_factory, lambda: False)
    summary = await job.run(session)

    assert summary.startswith("dry run:")
    assert "1 render(s) would repoint" in summary
    assert "1 item_facts row(s) would repoint" in summary
    assert "1 logo marker(s) would carry" in summary
    assert "1 survivor parent link(s) would carry" in summary
    session.expire_all()
    assert (
        await session.execute(select(MediaItem).where(MediaItem.id == stale_id))
    ).scalar_one_or_none() is not None
    assert (
        await session.execute(select(EventLog).where(EventLog.source == MERGE_SOURCE))
    ).scalars().all() == []


async def test_an_over_cap_scan_refuses_and_deletes_nothing(session):
    for index in range(6):
        await _item(session, str(100 + index), tmdb_id=1000 + index // 2)

    job = _job(_config(apply=True, max_merges=2), FakePlex(live={"101"}))
    summary = await job.run(session)

    assert "refus" in summary.lower()
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 6


async def test_an_unhealthy_plex_refuses_the_applied_pass(session):
    """The dry run runs anywhere; the APPLY must not, because the survivor
    cannot be verified and every merge ends in a delete."""
    await _pair(session)

    def exploding_factory():
        raise AssertionError("no Plex client may be built when Plex is unhealthy")

    job = make_merge_job(ConfigHolder(_config(apply=True)), exploding_factory,
                         lambda: False)
    summary = await job.run(session)

    assert "refus" in summary.lower() and "unhealthy" in summary.lower()
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 2


async def test_the_session_is_not_idle_in_transaction_when_the_probe_runs(
    session, monkeypatch
):
    """M1: the scan's six SELECTs (including a full media_items read) open a
    transaction, and the probe below is a live Plex walk of minutes at this
    branch's own scale -- idle-in-transaction for that whole window pins a
    pooled connection and the vacuum horizon. One ``rollback()`` between the
    scan and the probe closes it; ``MergePlan`` is frozen dataclasses and
    ``merge()`` re-locks and re-reads both rows anyway, so nothing is lost."""
    await _pair(session)
    real_verify_survivors = merge_module.verify_survivors
    seen = {}

    async def spy(plex, plans):
        seen["in_transaction"] = session.in_transaction()
        return await real_verify_survivors(plex, plans)

    monkeypatch.setattr(merge_module, "verify_survivors", spy)

    job = _job(_config(apply=True), FakePlex(live={"2"}))
    await job.run(session)

    assert seen == {"in_transaction": False}


async def test_a_probe_failure_raises_and_names_only_the_exception_class(session):
    """``last_detail`` is rendered on the dashboard and a Plex error's message
    carries the server address and sometimes the token."""
    await _pair(session)
    plex = FakePlex(error=ConnectionError("https://plex.example:32400 reset"))

    job = _job(_config(apply=True), plex)

    with pytest.raises(MergeRefused) as caught:
        await job.run(session)

    message = str(caught.value)
    assert "ConnectionError" in message
    assert "plex.example" not in message and "reset" not in message
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 2


async def test_a_pair_whose_rows_both_resolve_by_key_is_refused(session):
    """Two real Plex items carrying one identity is a duplicate in the
    library: an operator's decision, not this job's."""
    await _pair(session)
    scan = await find_mergeable(session)

    probe = await verify_survivors(FakePlex(live={"1", "2"}), scan.plans)

    assert probe.accepted == [] and probe.both_live == 1


async def test_a_pair_whose_rows_resolve_by_neither_key_is_refused(session):
    """That is ``plex_prune``'s population, and taking it here would delete a
    row on the strength of a probe that says nothing."""
    await _pair(session)
    scan = await find_mergeable(session)

    probe = await verify_survivors(FakePlex(live=set()), scan.plans)

    assert probe.accepted == [] and probe.neither_live == 1


async def test_a_pair_where_the_older_key_is_the_live_one_is_refused(session):
    """The newest-key heuristic and the probe disagree, so the dry run's
    preview was wrong about this pair and nothing is deleted on it."""
    await _pair(session, stale_key="1", survivor_key="2")
    scan = await find_mergeable(session)

    probe = await verify_survivors(FakePlex(live={"1"}), scan.plans)

    assert probe.accepted == [] and probe.election_disagreed == 1


async def test_an_applied_pass_merges_and_dismisses_jobs_for_both_keys(session):
    """A6: an in-flight or parked payload can name either key -- the dead one
    because it was queued before the fork, the survivor's because the graph it
    was queued against has just changed."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    survivor_id = survivor.id
    for key in ("1", "2"):
        session.add(Job(
            kind="process_item",
            payload={"kind": "movie", "title": "Dune", "rating_key": key},
            dedupe_key=f"process_item:movie:tmdb693134:{key}",
            state="parked",
        ))
    await session.commit()

    job = _job(_config(apply=True), FakePlex(live={"2"}))
    summary = await job.run(session)

    assert summary.startswith("merged 1 of 2")
    assert "dismissed 2 queued job(s)" in summary
    session.expire_all()
    assert [row.id for row in (await session.execute(select(MediaItem))).scalars()] == [
        survivor_id
    ]
    assert {j.state for j in (await session.execute(select(Job))).scalars()} == {
        "dismissed"
    }


async def test_the_applied_summary_reports_repoints_and_drops_separately(session):
    """Deleted duplicates shrink the Action Center's denominator; repointed
    orphans stay in it and become finishable. Reporting one number would let
    an operator read the first as data loss."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, stale.id, "background")
    await _render(session, survivor.id, "poster", scored=True)

    job = _job(_config(apply=True), FakePlex(live={"2"}))
    summary = await job.run(session)

    assert "repointed 1 render(s)" in summary
    assert "dropped 1" in summary


async def test_intent_for_row_carries_the_rows_stored_key_and_ids(session):
    """The probe must ask exactly what the pipeline asks, or the job would
    decide a row's fate on a question the pipeline never poses."""
    stale, _ = await _pair(session)
    scan = await find_mergeable(session)

    intent = intent_for_row(scan.plans[0].pair.stale)

    assert intent.rating_key == stale.rating_key
    assert intent.kind == "movie"
    assert intent.tmdb_id == 693134
    assert intent.title == "Dune: Part Two"
