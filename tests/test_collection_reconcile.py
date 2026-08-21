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
    """Mirrors plexapi's lazy ``labels``: empty until ``reload()`` is called,
    just like a real ``Collection`` fetched from ``section.collections()``."""

    def __init__(self, title, labels=(), rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self.reloaded = False
        self.updated_filters = None
        self.summary_set = None
        self.labels_added = []

    @property
    def labels(self):
        return self._labels

    def reload(self, **kw):
        self.reloaded = True
        self._labels = self._real_labels

    def updateFilters(self, libtype=None, limit=None, sort=None, filters=None, **kw):
        self.updated_filters = filters

    def editSummary(self, summary, locked=True):
        self.summary_set = summary

    def addLabel(self, labels, locked=True):
        self.labels_added.append(labels)
        self._real_labels.append(type("L", (), {"tag": labels})())
        self._labels = self._real_labels


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
    actions = await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=True)
    assert section.created == []
    assert any("Age 17+ Movies" in a for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows == []


async def test_creates_a_smart_collection_with_the_derived_filter(session):
    section = FakeSection({"R", "17"})
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    created = {c[0]: c for c in section.created}
    title, smart, libtype, sort, filters = created["Age 17+ Movies"]
    assert smart is True
    assert libtype == "movie"
    assert sort == "originallyAvailableAt:desc"
    assert sorted(filters["contentRating"]) == ["17", "R"]


async def test_an_empty_bucket_creates_nothing(session):
    """An empty filter would match the entire library."""
    section = FakeSection({"R"})
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    titles = [c[0] for c in section.created]
    assert "Age 1+ Movies" not in titles
    assert "Age 17+ Movies" in titles


async def test_a_second_pass_over_an_unchanged_library_writes_nothing(session):
    section = FakeSection({"R", "17"})
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    first = len(section.created)
    for collection in section.collections():
        collection.updated_filters = None
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    assert len(section.created) == first
    assert all(c.updated_filters is None for c in section.collections())


async def test_a_changed_rating_set_updates_the_existing_filter(session):
    section = FakeSection({"R"})
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    section._ratings.append("TV-MA")
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    collection = section._existing["Age 17+ Movies"]
    assert sorted(collection.updated_filters["contentRating"]) == ["R", "TV-MA"]


async def test_an_unlabelled_collection_with_a_colliding_title_is_never_touched(session):
    """The operator has hand-made collections. Overwriting one because its
    name collides would be the worst failure this phase could have."""
    theirs = FakeCollection("Age 17+ Movies", labels=["something-else"])
    section = FakeSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
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
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    titles = {r.title for r in rows}
    assert "Age 17+ Movies" in titles
    assert all(r.library == "Movies" and r.kind == "smart" for r in rows)


async def test_two_libraries_of_the_same_type_do_not_collide(session):
    """An operator can configure two movie libraries (e.g. 'Movies' and
    'Kids Movies'). Both have library_type='Movie', but ManagedCollection is
    keyed on the section name, so their rows must stay distinct and a second
    pass over either must write nothing."""
    movies = FakeSection({"R", "17"})
    kids = FakeSection({"PG"})

    await reconcile_content_ratings(session, movies, "Movies", "Movie", LABEL, dry_run=False)
    await reconcile_content_ratings(session, kids, "Kids Movies", "Movie", LABEL, dry_run=False)

    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert {r.library for r in rows} == {"Movies", "Kids Movies"}
    assert len(rows) == len({(r.library, r.title) for r in rows}), "rows collided"

    for collection in list(movies.collections()) + list(kids.collections()):
        collection.updated_filters = None
        collection.summary_set = None
    await reconcile_content_ratings(session, movies, "Movies", "Movie", LABEL, dry_run=False)
    await reconcile_content_ratings(session, kids, "Kids Movies", "Movie", LABEL, dry_run=False)
    assert all(
        c.updated_filters is None and c.summary_set is None
        for c in list(movies.collections()) + list(kids.collections())
    )


async def test_ownership_check_reloads_before_reading_labels(session):
    """``FakeCollection.labels`` starts empty until ``reload()`` is called,
    mirroring plexapi's lazy ``cached_data_property``. A collection carrying
    our label must still be recognised as ours."""
    ours = FakeCollection("Age 17+ Movies", labels=[LABEL])
    assert ours.labels == []  # unloaded, like a real object fresh off search
    section = FakeSection({"R", "17"}, existing=[ours])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False
    )

    assert ours.reloaded is True
    assert not any("conflict" in a.lower() for a in actions)


async def test_a_labelled_collection_with_no_database_row_is_adopted(session):
    """Divergence case (a): our label is on the collection in Plex, but no
    ManagedCollection row exists for it -- it should be updated once and then
    recorded, not treated as a conflict or duplicated."""
    orphan = FakeCollection("Age 17+ Movies", labels=[LABEL])
    section = FakeSection({"17"}, existing=[orphan])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False
    )

    assert not any("conflict" in a.lower() for a in actions)
    assert orphan.updated_filters is not None
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert [r.title for r in rows] == ["Age 17+ Movies"]

    orphan.updated_filters = None
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    assert orphan.updated_filters is None, "second pass must write nothing"
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert len(rows) == 1, "must not duplicate the row"


async def test_a_database_row_with_no_matching_plex_collection_is_recreated(session):
    """Divergence case (b): a ManagedCollection row exists but its Plex
    collection is gone -- the next pass must create a fresh one and reuse the
    existing row rather than raising an IntegrityError on a duplicate."""
    section = FakeSection({"17"})
    row = ManagedCollection(
        library="Movies", title="Age 17+ Movies", kind="smart",
        plex_rating_key="999", definition_hash="stale",
    )
    session.add(row)
    await session.flush()

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False
    )

    assert any("Age 17+ Movies" in a for a in actions)
    assert "Age 17+ Movies" in section._existing
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == row.id
    assert rows[0].definition_hash != "stale"
