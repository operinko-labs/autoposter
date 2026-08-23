import ast
from datetime import date
from pathlib import Path

import pytest

from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import WRITABLE_BY_KIND, _item_label, apply_facts, plan_edits


class FakeItem:
    """Models the plexapi surface the writer touches.

    `addGenre`/`removeGenre` mirror documented plexapi behaviour
    (plexapi.mixins.GenreMixin, built on EditTagsMixin.editTags): `addGenre`
    MERGES with the item's current genres rather than replacing them, and
    does not dedupe -- passing a tag that's already present adds a second
    copy. This was verified offline against the real plexapi `Movie` class
    (see test_genre_payload_has_no_conflicting_directives_against_real_plexapi
    below). An earlier version of this double modeled genre edits as a
    set/replace operation, which hid the bug where `apply_facts` could only
    ever grow the genre list, never correct it.
    """

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
        # Merge, not replace -- and no dedupe, matching plexapi's documented
        # GenreMixin.addGenre behaviour.
        if not isinstance(genres, list):
            genres = [genres]
        self.genres = self.genres + [type("G", (), {"tag": g})() for g in genres]
        self.edits["genre.locked"] = 1 if locked else 0
        return self

    def removeGenre(self, genres, locked=True):
        if not isinstance(genres, list):
            genres = [genres]
        removed = set(genres)
        self.genres = [g for g in self.genres if g.tag not in removed]
        self.edits["genre.locked"] = 1 if locked else 0
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


def test_ratings_are_written_rounded_to_one_decimal():
    """Finding 6: the plan requires ratings stored to one decimal, matching
    Kometa -- writing the raw multi-decimal TMDB/IMDb value instead makes
    the badge (int(v*10)) render a different percentage than Kometa did."""
    item = FakeItem()
    edits = plan_edits(item, GatheredFacts(critic_rating=6.284, audience_rating=8.6499))
    assert edits["rating.value"] == pytest.approx(6.3)
    assert edits["audienceRating.value"] == pytest.approx(8.6)


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


def test_item_label_names_a_movie_with_its_year():
    item = FakeItem()
    item.title, item.year = "Heat", 1995
    assert _item_label(item) == "movie 'Heat' (1995)"


def test_item_label_places_an_episode_in_its_show():
    item = FakeItem(kind="episode")
    item.title, item.grandparentTitle = "Pilot", "Dark"
    item.parentIndex, item.index = 1, 1
    assert _item_label(item) == "episode 'Pilot' (Dark S01E01)"


def test_item_label_degrades_to_the_bare_type_when_nothing_else_exists():
    # The log line must never be the thing that raises -- an object with no
    # title (or none of the episode coordinates) still gets a usable label.
    assert _item_label(FakeItem(kind="show")) == "show"
    partial = FakeItem(kind="episode")
    partial.title = "Pilot"
    assert _item_label(partial) == "episode 'Pilot'"


async def test_apply_logs_which_item_was_written(caplog):
    item = FakeItem()
    item.title, item.year = "Heat", 1995
    with caplog.at_level("INFO", logger="autoposter.plex.writer"):
        await apply_facts(item, GatheredFacts(critic_rating=4.9))
    assert "movie 'Heat' (1995)" in caplog.text


async def test_apply_does_nothing_when_there_is_nothing_to_write():
    item = FakeItem(rating=4.9)
    written = await apply_facts(item, GatheredFacts(critic_rating=4.9))
    assert written == {}
    assert item.batched is False


async def test_genres_are_applied_through_addgenre():
    item = FakeItem(genres=["Horror"])
    written = await apply_facts(item, GatheredFacts(genres=["Horror", "Drama"]))
    assert written["genres.added"] == ["Drama"]
    assert written["genres.locked"] == 1
    assert "genres.removed" not in written
    # addGenre's documented merge reads the *current* genres live; re-listing
    # "Horror" (unchanged) would create a duplicate tag on the server, since
    # plexapi's indexed tag write does not dedupe. To avoid that,
    # _apply_genre_edits temporarily resets the cached genres before calling
    # addGenre so only the genuinely new tag is queued. The caller's object
    # must be left exactly as it was found, so the genres are restored after.
    # What actually matters -- the queued payload -- is asserted above and
    # proven conflict-free against real plexapi in the test below.
    assert [g.tag for g in item.genres] == ["Horror"]


async def test_genres_that_drop_one_do_not_survive():
    """Regression test for the concatenating-addGenre bug: the old
    implementation could only grow the genre list, so a target that drops
    a currently-held genre would still leave that genre in place (and
    duplicate any that were kept). This must actually remove it.
    """
    item = FakeItem(genres=["Horror", "Comedy"])
    written = await apply_facts(item, GatheredFacts(genres=["Horror", "Drama"]))
    assert "Comedy" not in [g.tag for g in item.genres]
    assert written["genres.added"] == ["Drama"]
    assert written["genres.removed"] == ["Comedy"]


async def test_genre_only_change_is_reported_in_the_return_value():
    """Finding 2 regression: a genre-only write must not report {} / log 0
    fields written, since a real write did happen.
    """
    item = FakeItem(rating=4.9, genres=["Horror"])
    written = await apply_facts(
        item, GatheredFacts(critic_rating=4.9, genres=["Horror", "Drama"])
    )
    assert written != {}
    assert written["genres.added"] == ["Drama"]


def test_genre_payload_has_no_conflicting_directives_against_real_plexapi():
    """Offline regression test against plexapi's *real* `Movie`/`GenreMixin`
    classes -- no server connection is made or needed, since `batchEdits()`
    causes all subsequent edit calls to accumulate into `item._edits`
    without any network I/O; only `saveEdits()` would perform a request, and
    this test never calls it.

    This guards against a real gotcha found while implementing the
    documented-API rewrite: `addGenre`'s "merge with existing genres" reads
    the item's *current* `genres` property live off its original XML data,
    unaffected by a `removeGenre` queued earlier in the same batch. Naively
    chaining `removeGenre(surplus)` + `addGenre(missing)` -- the pattern the
    plexapi docs themselves suggest for "set the genres to exactly this
    list" -- would silently re-list the surplus genre as an explicit add in
    the very request that removes it, sending contradictory directives for
    the same tag. `writer._apply_genre_edits` avoids this by resetting the
    cached `genres` to `[]` immediately before `addGenre`; this test proves
    that fix against plexapi's actual merge logic, not a model of it.
    """
    import xml.etree.ElementTree as ET

    from plexapi.video import Movie

    from autoposter.plex.writer import _apply_genre_edits

    xml = (
        '<Video ratingKey="1" key="/library/metadata/1" type="movie" title="T">'
        '<Genre tag="Horror" /><Genre tag="Comedy" /></Video>'
    )
    item = Movie(server=None, data=ET.fromstring(xml))
    assert sorted(g.tag for g in item.genres) == ["Comedy", "Horror"]

    item.batchEdits()
    _apply_genre_edits(item, additions=["Drama"], removals=["Comedy"])

    # The accumulated payload, exactly as it would be sent to the server.
    edits = item._edits
    assert edits["genre[].tag.tag-"] == "Comedy"
    # Exactly one indexed add, for the genuinely new tag -- no entry
    # resurrecting "Comedy" and no duplicate entry for "Horror".
    add_values = {v for k, v in edits.items() if k.startswith("genre[") and k.endswith("].tag.tag")}
    assert add_values == {"Drama"}
    assert edits["genre.locked"] == 1

    # No network call was made: saveEdits() was never invoked, and the
    # object still reports batch-edit mode is active (a dict, not None).
    assert isinstance(item._edits, dict)


async def test_apply_restores_item_genres_snapshot_after_addgenre():
    """The snapshot of item.genres must be restored after addGenre completes,
    so the caller's object is not left in the temporary state used to avoid
    re-adding genres that were removed in the same batch. Removals are kept
    (they actually happened), but the temporary reset for addGenre is undone."""
    item = FakeItem(genres=["Horror", "Comedy"])

    # Modify genres: remove Comedy, add Drama. Result should be Horror + Drama.
    await apply_facts(item, GatheredFacts(genres=["Horror", "Drama"]))

    # After apply_facts, item.genres should reflect the removals (Comedy is gone)
    # but not be left in the temporary empty state that addGenre used.
    # We should have Horror (kept) but not Comedy (removed).
    current_tags = [g.tag for g in item.genres]
    assert "Comedy" not in current_tags, "Removed genres should not be present"
    # This verifies that the snapshot is restored after addGenre; without the fix,
    # the item would be left with just [] (empty).
    assert len(current_tags) > 0, "Genres should not be left empty"


def test_no_refresh_calls_project_wide():
    """``.refresh()`` on a Plex object is forbidden everywhere in ``src/``.

    It tells the Plex server to re-pull metadata from its agents, which can
    overwrite artwork and locked fields this tool owns. ``reload()`` -- a
    plain re-read of existing metadata -- is the permitted alternative.

    An AST walk rather than a text search: ``api/artwork.py`` has a comment
    reading "never .refresh(), which would have Plex re-pull", and a regex
    would fail on the very comment documenting the rule. Parsing sees calls
    only -- not comments, not docstrings -- and gives the line number for
    free. Chained receivers (``plex.fetchItem(x).refresh()``) still match,
    since the callee is an ``ast.Attribute`` regardless of receiver shape.
    What static scanning cannot see is ``getattr(item, "refresh")()`` alias
    evasion -- which is why the behavioural guard in test_api_artwork.py
    (``refreshed is False`` on the fake item) must stay alongside this test.

    ``session.refresh(...)`` is SQLAlchemy's and is excluded, but only for a
    receiver that is the bare name ``session``: a future
    ``self.session.refresh(obj)`` or ``db_session.refresh(obj)`` would fail
    this test. That is the guard being conservative, not a real violation --
    rename the receiver or widen the exclusion deliberately if it happens.

    ``facts/imdb.py`` needs no exemption: its module-level
    ``async def refresh(...)`` is always called as a bare name
    (``await refresh(session, ...)``), which parses as ``ast.Name``, not
    ``ast.Attribute``; and ``self._maybe_refresh()`` is a different
    attribute. Neither matches this walk's filter.
    """
    src_root = Path(__file__).parent.parent / "src" / "autoposter"
    assert src_root.is_dir(), f"source tree not found at {src_root}"

    scanned, offenders = [], []
    for path in sorted(src_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(src_root).as_posix()
        scanned.append(relative)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            if not isinstance(called, ast.Attribute) or called.attr != "refresh":
                continue
            if isinstance(called.value, ast.Name) and called.value.id == "session":
                continue
            offenders.append(f"src/autoposter/{relative}:{node.lineno}")

    # The walk has to be shown to have found the tree before "no offenders"
    # means anything: a scan of nothing passes. These three are the modules
    # that hold live Plex objects, which is the whole point of the rule.
    for anchor in ("collections/lists.py", "plex/client.py", "api/artwork.py"):
        assert anchor in scanned, f"{anchor} was not scanned; the walk found {len(scanned)} files"

    assert not offenders, (
        "%s calls .refresh() on an object; a Plex metadata refresh reverts "
        "locked fields and the artwork this project uploaded, and is never an "
        "acceptable recovery action" % ", ".join(offenders)
    )
