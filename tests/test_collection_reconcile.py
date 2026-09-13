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
from plex_doubles import FakeSection as PlexSection

LABEL = "autoposter"


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


class FakeSection(PlexSection):
    """The shared Plex double plus this file's own question.

    ``listFilterChoices`` asserts the attribute it is asked for: every
    assertion in this file is about the Common Sense family, and a bucket
    query that drifted onto another attribute would otherwise pass here by
    answering the same ratings.
    """

    collection_factory = FakeCollection
    created_labels = [LABEL]

    def __init__(self, ratings=(), **kw):
        """``test_collection_adoption.py`` imports this class as
        ``SmartSection`` and calls it positionally (``SmartSection({...},
        existing=[...])``); this keeps that call site working."""
        super().__init__(ratings=ratings, **kw)

    def listFilterChoices(self, field, libtype=None):
        assert field == "contentRating"
        return super().listFilterChoices(field, libtype=libtype)


def _posted(section, title):
    """The ``uri`` of the create POST that made ``title``."""
    for call in section.queries:
        args = parse_qs(urlsplit(call["key"]).query)
        if args.get("title", [None])[0] == title:
            assert call["method"] == "POST-SENTINEL", call
            return args["uri"][0]
    raise AssertionError("%r was never created: %s" % (title, section.queries))


def _put(section, collection):
    """The ``uri`` of the single filter-replacing PUT against ``collection``."""
    prefix = "/library/collections/%s/items?" % collection.ratingKey
    calls = [one for one in section.queries if one["key"].startswith(prefix)]
    assert len(calls) == 1, section.queries
    assert calls[0]["method"] == "PUT-SENTINEL", calls[0]
    return parse_qs(urlsplit(calls[0]["key"]).query)["uri"][0]


async def test_dry_run_performs_no_writes(session):
    section = FakeSection(ratings={"R", "17"})
    actions = await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=True)
    assert section.created == []
    assert section.queries == []
    assert any("Age 17+ Movies" in a for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows == []


async def test_creates_a_smart_collection_with_the_derived_filter(session):
    section = FakeSection(ratings={"R", "17"})
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    uri = _posted(section, "Age 17+ Movies")
    assert uri.startswith(
        "server://FAKE-MACHINE-ID/com.plexapp.plugins.library"
        "/library/sections/42/all?"
    ), uri
    args = parse_qs(urlsplit(uri).query)
    assert args["type"] == ["1"], uri
    # ``originallyAvailableAt:desc``, which is what plexapi was handed before
    # the port -- ``release.desc`` is this engine's spelling of the same sort.
    assert args["sort"] == ["originallyAvailableAt:desc"], uri
    assert sorted(args["contentRating"]) == ["17", "R"], uri
    # An ``any:`` base: one term per value, joined by the block's own
    # conjunction and scoped by a push/pop pair.
    assert args["or"] == ["1"] and args["push"] == ["1"] and args["pop"] == ["1"], uri


async def test_an_empty_bucket_creates_nothing(session):
    """An empty filter would match the entire library."""
    section = FakeSection(ratings={"R"})
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    assert "Age 1+ Movies" not in section._existing
    assert "Age 17+ Movies" in section._existing


async def test_a_second_pass_over_an_unchanged_library_writes_nothing(session):
    section = FakeSection(ratings={"R", "17"})
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    first = len(section.queries)
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    assert len(section.queries) == first
    assert all(c.updated_filters is None for c in section.collections())


async def test_a_changed_rating_set_updates_the_existing_filter(session):
    section = FakeSection(ratings={"R"})
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    section._ratings.append("TV-MA")
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    collection = section._existing["Age 17+ Movies"]
    args = parse_qs(urlsplit(_put(section, collection)).query)
    assert sorted(args["contentRating"]) == ["R", "TV-MA"]
    assert collection.updated_filters is None, "plexapi updateFilters is retired"


async def test_an_unlabelled_collection_with_a_colliding_title_is_never_touched(session):
    """The operator has hand-made collections. Overwriting one because its
    name collides would be the worst failure this phase could have."""
    theirs = FakeCollection("Age 17+ Movies", labels=["something-else"])
    section = FakeSection(ratings={"R", "17"}, existing=[theirs])
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
    section = FakeSection(ratings={"R", "17"})
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
    movies = FakeSection(ratings={"R", "17"})
    kids = FakeSection(ratings={"PG"})

    await reconcile_content_ratings(session, movies, "Movies", "Movie", LABEL, dry_run=False)
    await reconcile_content_ratings(session, kids, "Kids Movies", "Movie", LABEL, dry_run=False)

    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert {r.library for r in rows} == {"Movies", "Kids Movies"}
    assert len(rows) == len({(r.library, r.title) for r in rows}), "rows collided"

    for collection in list(movies.collections()) + list(kids.collections()):
        collection.summary_set = None
    written = (len(movies.queries), len(kids.queries))
    await reconcile_content_ratings(session, movies, "Movies", "Movie", LABEL, dry_run=False)
    await reconcile_content_ratings(session, kids, "Kids Movies", "Movie", LABEL, dry_run=False)
    assert (len(movies.queries), len(kids.queries)) == written
    assert all(
        c.summary_set is None
        for c in list(movies.collections()) + list(kids.collections())
    )


async def test_ownership_check_reloads_before_reading_labels(session):
    """``FakeCollection.labels`` starts empty until ``reload()`` is called,
    mirroring plexapi's lazy ``cached_data_property``. A collection carrying
    our label must still be recognised as ours."""
    ours = FakeCollection("Age 17+ Movies", labels=[LABEL])
    assert ours.labels == []  # unloaded, like a real object fresh off search
    section = FakeSection(ratings={"R", "17"}, existing=[ours])

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
    section = FakeSection(ratings={"17"}, existing=[orphan])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False
    )

    assert not any("conflict" in a.lower() for a in actions)
    assert _put(section, orphan)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert [r.title for r in rows] == ["Age 17+ Movies"]

    written = len(section.queries)
    await reconcile_content_ratings(session, section, "Movies", "Movie", LABEL, dry_run=False)
    assert len(section.queries) == written, "second pass must write nothing"
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert len(rows) == 1, "must not duplicate the row"


async def test_a_database_row_with_no_matching_plex_collection_is_recreated(session):
    """Divergence case (b): a ManagedCollection row exists but its Plex
    collection is gone -- the next pass must create a fresh one and reuse the
    existing row rather than raising an IntegrityError on a duplicate."""
    section = FakeSection(ratings={"17"})
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
    section = FakeSection(ratings={"17", "PG"})

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


# --- the smart hash carries the ride-along settings too --------------------
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
    section = FakeSection(ratings={"R", "17"})
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


# --- phase 10a-2: the family writes the 9b grammar ---------------------------
#
# Roadmap row 185. ``section.createCollection(smart=True, filters=...)`` and
# ``Collection.updateFilters()`` are gone; both write calls go through
# ``smart.create_smart_collection``/``update_smart_collection`` over a
# ``build_search_url`` query, which is the same oracle-proven URI every other
# smart collection this service manages is written with. The two plexapi
# entry points are KEPT on the fakes above so these tests can prove they are
# never called, rather than merely observing that they were not.


class RefusingResolver:
    """The pass's ``LibraryTagResolver``, with one value it will not resolve.

    The resolver answers two questions and this double splits them: ``choices``
    says what the library HOLDS and ``__call__`` says which Plex key a written
    value resolves to. A real ``LibraryTagResolver`` memoises ONE
    ``listFilterChoices`` and serves both halves out of it, so no ``FakeSection``
    can make the two disagree -- which is why an unresolvable value is injected
    through the reconciler's own ``resolver`` seam here instead of being faked a
    layer down.
    """

    def __init__(self, ratings, refuse=()):
        self._ratings = list(ratings)
        self._refuse = set(refuse)

    def choices(self, attribute, /):
        assert attribute == "content_rating"
        return tuple((rating, rating) for rating in self._ratings)

    def __call__(self, attribute, value, /):
        return () if str(value) in self._refuse else (str(value),)


async def test_a_bucket_is_created_with_a_raw_post_and_no_plexapi_filters(session):
    """Roadmap row 185. The second query grammar is retired: this family now
    writes the same oracle-proven URI ``smart_filter`` writes, through the same
    two functions, so there is exactly one smart write path in this service."""
    section = FakeSection(ratings=["G", "TV-G", "PG"])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )

    assert "created 'Age 1+ Movies'" in actions, actions
    assert section.created == [], "plexapi createCollection must not be called"
    posted = [one for one in section.queries if one["method"] == "POST-SENTINEL"]
    assert posted, section.queries
    uri = parse_qs(urlsplit(posted[0]["key"]).query)["uri"][0]
    assert uri.startswith("server://")
    assert "push=1" in uri and "or=1" in uri and "contentRating=G" in uri


async def test_a_bucket_whose_filter_changed_is_updated_with_a_put(session):
    """The migration's own shape: an existing collection whose stored hash
    predates the port is not current, so the pass re-PUTs its filter. One PUT
    per collection, once."""
    existing = FakeCollection("Age 1+ Movies", labels=[LABEL])
    section = FakeSection(ratings=["G"], existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title="Age 1+ Movies", kind="smart",
        plex_rating_key="1", definition_hash="the-pre-port-hash",
    ))
    await session.flush()

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )

    assert "updated 'Age 1+ Movies'" in actions, actions
    assert existing.updated_filters is None, (
        "plexapi updateFilters must not be called"
    )
    put = [one for one in section.queries if one["method"] == "PUT-SENTINEL"]
    assert len(put) == 1, section.queries
    assert "/items?" in put[0]["key"] and "uri=" in put[0]["key"]


def test_the_hash_folds_the_built_uri_so_the_migration_actually_happens():
    """The one thing that could make this port a silent no-op in production: a
    hash over the bucket's VALUES is unchanged by the port, so the first pass
    would skip every collection and leave every stored filter in the old
    grammar. The hash is over the URI, so it cannot."""
    bucket = Bucket(key="1", title="Age 1+ Movies", summary="s", values=("G",))
    assert definition_hash(bucket, None, "?type=1&push=1&contentRating=G&pop=1") != (
        definition_hash(bucket, None, "?type=1&push=1&contentRating=PG&pop=1")
    )


async def test_an_unchanged_second_pass_still_writes_nothing(session):
    """The migration is ONE pass. The second finds the new hash stored and
    short-circuits exactly as it did before."""
    section = FakeSection(ratings=["G", "TV-G", "PG"])
    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )
    before = len(section.queries)

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )

    assert actions == []
    assert len(section.queries) == before


async def test_an_empty_bucket_is_still_never_created_and_never_deleted(session):
    """Addendum 3, pinned at the port. Kometa DROPS a bucket no library value
    matches (meta.py:1224-1225); ``derive_buckets`` returns it with empty values
    and this reconciler declines to create or modify it, so production never
    creates one and never deletes one that already exists. The port does not
    change that, and ``cs_bucket.titles()`` still names every bucket -- which is
    what keeps an existing-but-now-empty Common Sense collection out of the
    delete sweep's candidate set entirely."""
    from types import SimpleNamespace

    from autoposter.collections.builders.cs_bucket import CsBucketBuilder

    stale = FakeCollection("Age 18+ Movies", labels=[LABEL])
    section = FakeSection(ratings=["G"], existing=[stale])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )

    assert not any("Age 18+ Movies" in one for one in actions), actions
    assert stale.updated_filters is None
    assert stale.summary_set is None
    config = SimpleNamespace(collections=SimpleNamespace(separators=False))
    assert "Age 18+ Movies" in CsBucketBuilder().titles("Movie", config)


async def test_a_bucket_whose_query_cannot_be_built_refuses_only_itself(session):
    """``build_search_url`` and ``parse_filters`` can refuse, and this
    reconciler is reached through a SMART builder whose ``apply`` the engine
    does not wrap -- so an uncaught refusal here costs the whole library its
    reconcile. Contained to one bucket, like every other per-key refusal in this
    service."""
    section = FakeSection(ratings=["G", "PG"])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        resolver=RefusingResolver(["G", "PG"], refuse={"PG"}),
    )

    assert any("created 'Age 1+ Movies'" in one for one in actions), actions
    assert any("refused" in one and "PG" in one for one in actions), actions


async def test_a_dead_filter_lookup_refuses_the_whole_family_rather_than_raising(
    session,
):
    """The family's one input. Returned, not raised: this reconciler is reached
    through a smart builder the engine does not wrap, so an escape here would
    cost the library every other definition's work too."""
    from autoposter.collections.builders.plex_search import PlexSearchUnavailable

    class DeadResolver:
        def choices(self, attribute, /):
            raise PlexSearchUnavailable("Plex has no 'content_rating' filter")

        def __call__(self, attribute, value, /):  # pragma: no cover - never reached
            raise AssertionError("nothing may resolve after the listing failed")

    section = FakeSection(ratings=["G"])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        resolver=DeadResolver(),
    )

    assert len(actions) == 1, actions
    assert actions[0].startswith("refused the Common Sense collections:"), actions
    assert section.queries == []


# --- a definition that changes shape under an existing collection -----------


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


# --- roadmap row 88: a blank collection at season or episode granularity -----
#
# The LIST path creates through ``section.createCollection``, where plexapi
# derives the type from the members (pinned in
# tests/test_plexapi_collection_contract.py). This raw POST is the OTHER
# creation route -- the separator and the ``ops/blank`` endpoint -- and it
# hardcoded ``1 if libtype == "movie" else 2``, which answers "season" and
# "episode" with "show".


class _BlankPostSection:
    """Captures the raw POST ``create_blank_collection`` makes."""

    key = "7"

    def __init__(self):
        self.queries: list[str] = []
        self._server = self
        self._session = type("Sess", (), {"post": "POST-SENTINEL"})()

    def _uriRoot(self):
        return "server://abc/com.plexapp.plugins.library"

    def query(self, key, method=None, **kwargs):
        self.queries.append(key)

    def collection(self, title):
        return type("C", (), {"title": title})()


def _created_type(libtype: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    from autoposter.collections.reconcile import create_blank_collection

    section = _BlankPostSection()
    create_blank_collection(section, libtype, "Blank")
    [query] = section.queries
    return parse_qs(urlsplit(query).query)["type"][0]


def test_a_blank_collection_carries_plexs_own_type_for_every_libtype():
    assert _created_type("movie") == "1"
    assert _created_type("show") == "2"
    assert _created_type("season") == "3"
    assert _created_type("episode") == "4"


def test_an_unknown_libtype_raises_instead_of_creating_a_show_collection():
    """The old ``else 2`` made every unrecognised libtype a show collection --
    a real object in the operator's library, of the wrong kind, reported as
    created."""
    import pytest

    with pytest.raises(KeyError):
        _created_type("chapter")
