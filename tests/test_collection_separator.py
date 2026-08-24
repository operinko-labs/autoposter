"""Reconciling the blank "Ratings Collections" separator.

Part of the Common Sense family (``reconcile_content_ratings``), not a
free-floating extra: same ownership/adoption rules as every other collection
this service manages, via the shared ``resolve_collision`` -- but it must
never be populated. The fakes mirror ``tests/test_collection_reconcile.py``;
the raw-POST plexapi surface (``_uriRoot``, ``joinArgs``, ``query``) is
pinned separately in ``tests/test_plexapi_collection_contract.py``.
"""
from urllib.parse import parse_qs, urlsplit

from plexapi.exceptions import NotFound
from sqlalchemy import select

from autoposter.collections.reconcile import (
    SEPARATOR_SORT_TITLE,
    SEPARATOR_SUMMARY,
    SEPARATOR_TITLE,
    reconcile_content_ratings,
)
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"


class FakeChoice:
    def __init__(self, title):
        self.title = title


class FakeCollection:
    """Mirrors plexapi's lazy ``labels`` (empty until ``reload()``), plus
    the summary/sort-title fields and edit methods the separator uses."""

    def __init__(
        self, title, labels=(), rating_key="1", summary="", sort_title="",
        summary_locked=False,
    ):
        self.title = title
        self.ratingKey = rating_key
        self.summary = summary
        self.titleSort = sort_title
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": summary_locked})()]
        self._fields = []
        self.reloaded = False
        self.summary_set = None
        self.summary_queries = []
        self.sort_title_set = None
        self.labels_added = []
        self.items_added = []
        # Stands in for ``collection._server``: the summary is written with a
        # raw item-level PUT, not ``editSummary``.
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return self._fields

    def reload(self, **kw):
        self.reloaded = True
        self._labels = self._real_labels
        self._fields = self._real_fields

    def editSummary(self, summary, locked=True):
        """Raises the way the live server does -- the section route plexapi
        takes 404s for collection summaries. See
        ``reconcile._edit_collection_summary``."""
        raise NotFound("(404) not_found; /library/sections/42/all?type=18")

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Stands in for ``server.query`` -- the item-level summary PUT.
        Mirrors the real ``summary.locked=1`` argument by locking the fake
        ``summary`` field, so a second pass reads it back as locked."""
        self.summary_queries.append({"key": key, "method": method})
        self.summary_set = parse_qs(urlsplit(key).query)["summary.value"][0]
        self.summary = self.summary_set
        self._real_fields[0].locked = True

    def editSortTitle(self, sortTitle, locked=True):
        self.sort_title_set = sortTitle
        self.titleSort = sortTitle

    def addLabel(self, labels, locked=True):
        self.labels_added.append(labels)
        self._real_labels.append(type("L", (), {"tag": labels})())
        self._labels = self._real_labels

    def addItems(self, items):
        self.items_added.extend(items)


class FakeSection:
    """Also stands in for ``section._server``: the raw POST used to create
    an empty collection targets ``section._server.query(...)``, so this
    fake plays both roles rather than needing a second fake object."""

    def __init__(self, ratings=(), existing=(), section_type="movie"):
        self._ratings = list(ratings)
        self._existing = {c.title: c for c in existing}
        self.created = []
        self.key = "42"
        self.type = section_type
        self._server = self
        self._session = type("Sess", (), {"post": "POST-SENTINEL"})()
        self.raw_posts = []

    def _uriRoot(self):
        return "server://FAKE-MACHINE-ID/com.plexapp.plugins.library"

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        self.raw_posts.append({"key": key, "method": method})
        title = parse_qs(urlsplit(key).query)["title"][0]
        collection = FakeCollection(title, rating_key=str(len(self._existing) + 1))
        self._existing[title] = collection
        return None

    def collection(self, title):
        return self._existing[title]

    def listFilterChoices(self, field, libtype=None):
        return [FakeChoice(r) for r in self._ratings]

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, limit=None,
                          libtype=None, sort=None, filters=None, **kw):
        self.created.append((title, smart, libtype, sort, filters))
        collection = FakeCollection(title, labels=[LABEL], rating_key=str(len(self.created)))
        self._existing[title] = collection
        return collection


def _query(raw_posts):
    """The single POST's args as a ``{key: [values]}`` dict, decoded."""
    assert len(raw_posts) == 1
    call = raw_posts[0]
    assert call["method"] == "POST-SENTINEL"
    assert call["key"].startswith("/library/collections?")
    return parse_qs(urlsplit(call["key"]).query)


async def test_creates_the_separator_via_a_raw_post_with_no_items(session):
    section = FakeSection({"R"})
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=True,
    )

    args = _query(section.raw_posts)
    assert args["smart"] == ["0"]
    assert args["title"] == [SEPARATOR_TITLE]
    assert args["sectionId"] == ["42"]
    assert args["type"] == ["1"]  # movie
    assert args["uri"] == ["server://FAKE-MACHINE-ID/com.plexapp.plugins.library/library/metadata"]

    collection = section._existing[SEPARATOR_TITLE]
    assert collection.items_added == []
    assert any("created" in a.lower() and SEPARATOR_TITLE in a for a in actions)


async def test_creates_the_separator_with_type_2_for_a_show_library(session):
    section = FakeSection({"TV-14"}, section_type="show")
    await reconcile_content_ratings(
        session, section, "TV Shows", "Show", LABEL, dry_run=False, separators=True,
    )
    args = _query(section.raw_posts)
    assert args["type"] == ["2"]


async def test_the_sort_title_is_set_on_creation(session):
    section = FakeSection({"R"})
    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=True,
    )
    collection = section._existing[SEPARATOR_TITLE]
    assert collection.sort_title_set == SEPARATOR_SORT_TITLE == "!110_!Ratings Collections"
    assert collection.summary_set == SEPARATOR_SUMMARY


async def test_a_drifted_summary_and_sort_title_are_corrected(session):
    """Already labelled ours (e.g. from a run before this database row
    existed), but with the wrong summary and sort title -- no raw POST
    should happen, only the two corrections."""
    theirs = FakeCollection(
        SEPARATOR_TITLE, labels=[LABEL], summary="wrong", sort_title="wrong",
    )
    section = FakeSection({"R"}, existing=[theirs])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=True,
    )

    assert section.raw_posts == []
    assert theirs.summary_set == SEPARATOR_SUMMARY
    assert theirs.sort_title_set == SEPARATOR_SORT_TITLE
    assert theirs.items_added == []
    assert any("updated" in a.lower() and SEPARATOR_TITLE in a for a in actions)
    # The summary must go out on the item-level route: the section route
    # plexapi's own ``editSummary`` takes 404s on the live server.
    assert len(theirs.summary_queries) == 1
    assert theirs.summary_queries[0]["key"].startswith("/library/metadata/1?")
    assert theirs.summary_queries[0]["method"] == "PUT-SENTINEL"


async def test_a_separator_already_carrying_the_target_summary_is_not_rewritten(session):
    """The Kometa-era separators on the live server already have the exact
    summary this service wants, and are already locked. The sort title still
    needs correcting, so the update branch runs -- but the summary write must
    be skipped, per the family's rule that an unchanged, already-locked value
    issues no request."""
    theirs = FakeCollection(
        SEPARATOR_TITLE, labels=[LABEL], summary=SEPARATOR_SUMMARY, sort_title="wrong",
        summary_locked=True,
    )
    section = FakeSection({"R"}, existing=[theirs])

    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=True,
    )

    assert theirs.summary_queries == []
    assert theirs.sort_title_set == SEPARATOR_SORT_TITLE


async def test_a_matching_but_unlocked_separator_summary_is_still_written(session):
    """A separator whose text already matches but whose field is unlocked --
    the Kometa-era separators' actual starting state -- must still get the
    write, so the write also locks it. Skipping here would leave it unlocked
    forever, and a later Plex metadata refresh could clear it."""
    theirs = FakeCollection(
        SEPARATOR_TITLE, labels=[LABEL], summary=SEPARATOR_SUMMARY, sort_title="wrong",
        summary_locked=False,
    )
    section = FakeSection({"R"}, existing=[theirs])

    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=True,
    )

    assert len(theirs.summary_queries) == 1
    args = parse_qs(urlsplit(theirs.summary_queries[0]["key"]).query)
    assert args["summary.locked"] == ["1"]
    assert theirs.sort_title_set == SEPARATOR_SORT_TITLE


async def test_no_members_are_ever_added(session):
    """The one rule that must never break: this code path cannot populate
    the collection, no matter what state it starts or ends in.

    What this does *not* prove: that the raw POST in ``create_blank_collection``
    produces an empty collection on a real Plex server. The fake never has
    ``addItems`` called on it by any code path, so this assertion cannot
    fail -- it is a regression pin against a future edit adding one, not
    evidence about the server. The open question is whether a POST carrying
    ``uri=<root>/library/metadata`` with no item keys appended yields zero
    members or the entire library; that has not been verified against live
    Plex, and it cannot be verified from this suite (no test may make a real
    outbound request). It also will not be exercised on the target server,
    where both separators already exist and only the update path runs. An
    operator creating a separator in a fresh library must check its member
    count -- see ``deploy/README.md``.
    """
    theirs = FakeCollection(SEPARATOR_TITLE, labels=[LABEL], summary="wrong")
    section = FakeSection({"R"}, existing=[theirs])

    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=True,
    )

    assert theirs.items_added == []


async def test_a_second_pass_writes_nothing(session):
    section = FakeSection({"R"})
    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=True,
    )
    collection = section._existing[SEPARATOR_TITLE]
    collection.summary_set = None
    collection.sort_title_set = None
    section.raw_posts = []

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=True,
    )

    assert section.raw_posts == []
    assert collection.summary_set is None
    assert collection.sort_title_set is None
    assert not any(SEPARATOR_TITLE in a for a in actions)


async def test_dry_run_writes_nothing(session):
    section = FakeSection({"R"})
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=True, separators=True,
    )
    assert section.raw_posts == []
    assert section._existing == {}
    assert any("would create" in a.lower() and SEPARATOR_TITLE in a for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows == []


async def test_the_toggle_disables_it(session):
    section = FakeSection({"R"})
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=False,
    )
    assert section.raw_posts == []
    assert SEPARATOR_TITLE not in section._existing
    assert not any(SEPARATOR_TITLE in a for a in actions)


async def test_it_is_recorded_as_a_managed_collection(session):
    section = FakeSection({"R"})
    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, separators=True,
    )
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    row = next(r for r in rows if r.title == SEPARATOR_TITLE)
    assert row.library == "Movies"
    assert row.kind == "separator"


async def test_an_existing_kometa_separator_is_a_conflict_when_adopt_is_off(session):
    theirs = FakeCollection(SEPARATOR_TITLE, labels=["Kometa"])
    section = FakeSection({"R"}, existing=[theirs])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        separators=True, adopt=False,
    )

    assert theirs.labels_added == []
    assert theirs.items_added == []
    assert section.raw_posts == []
    assert any("conflict" in a.lower() for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert all(r.title != SEPARATOR_TITLE for r in rows)


async def test_an_existing_kometa_separator_is_adopted_when_adopt_is_on(session):
    theirs = FakeCollection(
        SEPARATOR_TITLE, labels=["Kometa"], summary="wrong", sort_title="wrong",
    )
    section = FakeSection({"R"}, existing=[theirs])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        separators=True, adopt=True, adopt_from=["Kometa"],
    )

    assert theirs.labels_added == [LABEL]
    assert theirs.items_added == []
    assert theirs.summary_set == SEPARATOR_SUMMARY
    assert theirs.sort_title_set == SEPARATOR_SORT_TITLE
    assert any("claimed" in a.lower() for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert any(r.title == SEPARATOR_TITLE for r in rows)
