"""Reconciling Common Sense smart collections.

The fakes here mirror plexapi's real signatures, which are pinned separately
in tests/test_plexapi_collection_contract.py.
"""
from sqlalchemy import select

from autoposter.collections.reconcile import reconcile_content_ratings
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"


class FakeChoice:
    def __init__(self, title):
        self.title = title


class FakeCollection:
    def __init__(self, title, labels=(), rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self._labels = [type("L", (), {"tag": t})() for t in labels]
        self.updated_filters = None
        self.summary_set = None
        self.labels_added = []

    @property
    def labels(self):
        return self._labels

    def updateFilters(self, libtype=None, limit=None, sort=None, filters=None, **kw):
        self.updated_filters = filters

    def editSummary(self, summary, locked=True):
        self.summary_set = summary

    def addLabel(self, labels, locked=True):
        self.labels_added.append(labels)


class FakeSection:
    def __init__(self, ratings, existing=()):
        self._ratings = list(ratings)
        self._existing = {c.title: c for c in existing}
        self.created = []

    def listFilterChoices(self, field, libtype=None):
        assert field == "contentRating"
        return [FakeChoice(r) for r in self._ratings]

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, limit=None,
                         libtype=None, sort=None, filters=None, **kw):
        self.created.append((title, smart, libtype, sort, filters))
        collection = FakeCollection(title, labels=[LABEL], rating_key=str(len(self.created)))
        self._existing[title] = collection
        return collection


async def test_dry_run_performs_no_writes(session):
    section = FakeSection({"R", "17"})
    actions = await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=True)
    assert section.created == []
    assert any("Age 17+ Movies" in a for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows == []


async def test_creates_a_smart_collection_with_the_derived_filter(session):
    section = FakeSection({"R", "17"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    created = {c[0]: c for c in section.created}
    title, smart, libtype, sort, filters = created["Age 17+ Movies"]
    assert smart is True
    assert libtype == "movie"
    assert sort == "originallyAvailableAt:desc"
    assert sorted(filters["contentRating"]) == ["17", "R"]


async def test_an_empty_bucket_creates_nothing(session):
    """An empty filter would match the entire library."""
    section = FakeSection({"R"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    titles = [c[0] for c in section.created]
    assert "Age 1+ Movies" not in titles
    assert "Age 17+ Movies" in titles


async def test_a_second_pass_over_an_unchanged_library_writes_nothing(session):
    section = FakeSection({"R", "17"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    first = len(section.created)
    for collection in section.collections():
        collection.updated_filters = None
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    assert len(section.created) == first
    assert all(c.updated_filters is None for c in section.collections())


async def test_a_changed_rating_set_updates_the_existing_filter(session):
    section = FakeSection({"R"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    section._ratings.append("TV-MA")
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    collection = section._existing["Age 17+ Movies"]
    assert sorted(collection.updated_filters["contentRating"]) == ["R", "TV-MA"]


async def test_an_unlabelled_collection_with_a_colliding_title_is_never_touched(session):
    """The operator has hand-made collections. Overwriting one because its
    name collides would be the worst failure this phase could have."""
    theirs = FakeCollection("Age 17+ Movies", labels=["something-else"])
    section = FakeSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    assert theirs.updated_filters is None
    assert theirs.summary_set is None
    assert any("conflict" in a.lower() for a in actions)


async def test_nothing_is_ever_deleted(session):
    """There is no deletion path in this phase at all."""
    import inspect

    from autoposter.collections import reconcile

    assert ".delete(" not in inspect.getsource(reconcile)


async def test_managed_collections_are_recorded(session):
    section = FakeSection({"R", "17"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    titles = {r.title for r in rows}
    assert "Age 17+ Movies" in titles
    assert all(r.library == "Movie" and r.kind == "smart" for r in rows)
