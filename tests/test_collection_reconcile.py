"""Reconciling Common Sense smart collections.

The fakes here mirror plexapi's real signatures, which are pinned separately
in tests/test_plexapi_collection_contract.py.
"""
from urllib.parse import parse_qs, urlsplit

import pytest
from plexapi.exceptions import NotFound
from sqlalchemy import select

from autoposter.collections.buckets import Bucket
from autoposter.collections.reconcile import (
    _edit_collection_summary,
    definition_hash,
    reconcile_content_ratings,
)
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"


class FakeChoice:
    def __init__(self, title):
        self.title = title


class FakeCollection:
    """Mirrors plexapi's lazy ``labels``: empty until ``reload()`` is called,
    just like a real ``Collection`` fetched from ``section.collections()``."""

    def __init__(self, title, labels=(), rating_key="1", summary=None, summary_locked=False):
        self.title = title
        self.ratingKey = rating_key
        self.summary = summary
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": summary_locked})()]
        self._fields = []
        self.reloaded = False
        self.updated_filters = None
        self.summary_set = None
        self.summary_queries = []
        self.labels_added = []
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

    def updateFilters(self, libtype=None, limit=None, sort=None, filters=None, **kw):
        self.updated_filters = filters

    def editSummary(self, summary, locked=True):
        """Raises the way the live server does. plexapi routes this through
        ``PUT /library/sections/{id}/all?type=18&...``, which 404s for every
        collection summary -- see ``reconcile._edit_collection_summary``. Any
        call site that stops going through the helper reds its test here."""
        raise NotFound("(404) not_found; /library/sections/42/all?type=18")

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Stands in for ``server.query`` -- the item-level summary PUT.
        Mirrors the real ``summary.locked=1`` argument by locking the fake
        ``summary`` field, so a second pass reads it back as locked."""
        self.summary_queries.append({"key": key, "method": method})
        self.summary_set = parse_qs(urlsplit(key).query)["summary.value"][0]
        self.summary = self.summary_set
        self._real_fields[0].locked = True

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


async def test_smart_collection_rows_never_get_reconcile_stats(session):
    """A smart collection has no membership we could count: Plex evaluates the
    filter live, so nothing here ever reads back a member list. The reconcile
    stat columns exist for list collections only and must stay NULL on these
    rows -- a zero would read as "this collection is empty", which is a
    different and wrong claim."""
    section = FakeSection({"17", "PG"})

    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False
    )

    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows, "precondition: the pass created smart rows"
    for row in rows:
        assert row.kind == "smart"
        assert row.member_count is None
        assert row.last_added is None
        assert row.last_removed is None
        assert row.last_reconciled_at is None


# --- fix round: the smart hash carries the ride-along settings too ---------
#
# ``lists.py``'s members hash already folds in the ride-along settings
# (labels, sort title, mode, hub visibility) so a settings-only edit is not
# recognised as "already current" and silently skipped. The smart family's
# ``definition_hash`` did not -- these are its twin of ``lists.py``'s
# ``test_a_definition_with_no_settings_hashes_exactly_as_before`` and
# ``test_a_settings_only_edit_is_applied_to_an_unchanged_membership``.


def test_a_definition_with_no_settings_hashes_exactly_as_before():
    """A definition at its defaults must hash exactly as the shipped code
    did, or the fold would re-reconcile every managed bucket already in a
    library for no actual change -- a re-render storm on deploy."""
    bucket = Bucket(key="17", title="Age 17+ Movies", summary="a summary", values=("R", "17"))
    plain = CollectionDefinition(title="X", builder="cs_bucket")

    assert definition_hash(bucket) == definition_hash(bucket, plain)


async def test_a_settings_only_edit_is_applied_to_an_unchanged_smart_membership(session):
    """The property the hash test above exists for, end to end: two passes
    over the same bucket filter, the second with a label the first did not
    have. Before the fold, the second pass short-circuited on the unchanged
    filter hash and the label was silently never written."""
    section = FakeSection({"R", "17"})
    plain = CollectionDefinition(title="X", builder="cs_bucket")

    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, settings=plain
    )
    collection = section._existing["Age 17+ Movies"]
    collection.labels_added = []

    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        settings=plain.model_copy(update={"labels": ["Added Later"]}),
    )

    assert "Added Later" in collection.labels_added


# --- The summary helper itself (_edit_collection_summary) ----------------
#
# plexapi's ``Collection.editSummary`` routes through the section
# (``PUT /library/sections/{id}/all?type=18&...``), which returns 404 for
# every collection summary on Plex 1.43.3.10896-cb3ebc72d. ``FakeCollection``
# above raises ``NotFound`` from ``editSummary`` for that reason, so any call
# site that reverts to it fails rather than silently passing.


def test_the_summary_helper_writes_the_item_level_route():
    collection = FakeCollection("Age 17+ Movies", rating_key="7318", summary="stale")

    _edit_collection_summary(collection, "Movies rated for ages 17 and up.")

    assert len(collection.summary_queries) == 1
    call = collection.summary_queries[0]
    assert call["method"] == "PUT-SENTINEL", "must be a PUT, not query's default GET"
    key = call["key"]
    assert key.startswith("/library/metadata/7318?"), key
    args = parse_qs(urlsplit(key).query)
    assert args["summary.value"] == ["Movies rated for ages 17 and up."]
    assert args["summary.locked"] == ["1"]
    assert "/library/sections/" not in key, "the section route is the broken one"


def test_the_summary_helper_writes_nothing_when_the_summary_already_matches():
    """The skip requires both the text to match and the field to already be
    locked -- a matching-but-unlocked summary must still be written, see the
    next test."""
    collection = FakeCollection(
        "Age 17+ Movies", summary="Already correct.", summary_locked=True,
    )
    collection.reload()

    _edit_collection_summary(collection, "Already correct.")

    assert collection.summary_queries == []


def test_the_summary_helper_writes_when_the_matching_summary_is_unlocked():
    """The Kometa-era separators' starting state: text already matches, but
    the field was never locked. The write must still happen -- skipping here
    would leave it unlocked forever, so a later Plex metadata refresh could
    clear it."""
    collection = FakeCollection(
        "Age 17+ Movies", summary="Already correct.", summary_locked=False,
    )
    collection.reload()

    _edit_collection_summary(collection, "Already correct.")

    assert len(collection.summary_queries) == 1
    args = parse_qs(urlsplit(collection.summary_queries[0]["key"]).query)
    assert args["summary.locked"] == ["1"]


def test_the_summary_helper_never_calls_plexapis_own_method():
    """Belt and braces for the two tests above: ``editSummary`` is what the
    live server rejects, so calling it must be impossible, not merely
    unobserved."""
    collection = FakeCollection("Age 17+ Movies", summary=None)

    with pytest.raises(NotFound):
        collection.editSummary("anything")

    _edit_collection_summary(collection, "A new summary.")
    assert collection.summary == "A new summary."


# --- C11: a definition that changes shape under an existing collection -------


def test_shape_conflict_is_silent_when_the_shapes_agree():
    from autoposter.collections.reconcile import shape_conflict

    smart = FakeCollection("Recent Horror")
    smart.smart = True
    assert shape_conflict(smart, "Recent Horror", want_smart=True) is None

    dumb = FakeCollection("Hand Picked")
    dumb.smart = False
    assert shape_conflict(dumb, "Hand Picked", want_smart=False) is None


def test_shape_conflict_names_both_shapes_and_the_manual_path():
    """Kometa deletes and recreates here (modules/builder.py:1768-1772). This
    service refuses, because a silent delete crosses every guard the delete
    sweep is built out of -- and the message has to leave the operator able to
    act, which means naming what the collection IS, what the definition BUILDS,
    and the two ways out."""
    from autoposter.collections.reconcile import shape_conflict

    smart = FakeCollection("Recent Horror")
    smart.smart = True
    message = shape_conflict(smart, "Recent Horror", want_smart=False)
    assert message is not None
    assert "Recent Horror" in message
    assert "smart" in message and "list" in message
    assert "delete" in message


def test_shape_conflict_treats_a_missing_attribute_as_a_list_collection():
    """``Collection.smart`` is cast from an XML attribute that defaults to
    ``'0'`` (pinned in tests/test_plexapi_collection_contract.py), and a fake or
    a partially-loaded object may not carry it at all. Absent means NOT smart --
    the same defensiveness ``has_label`` uses for ``labels``."""
    from autoposter.collections.reconcile import shape_conflict

    bare = FakeCollection("Hand Picked")
    assert shape_conflict(bare, "Hand Picked", want_smart=False) is None
    assert shape_conflict(bare, "Hand Picked", want_smart=True) is not None
