"""Reconciling ONE Plex-native smart collection from a built search URL.

The fakes mirror plexapi's real signatures, which are pinned separately in
``tests/test_plexapi_collection_contract.py``. The pinned request strings are a
COPY of ``tests/test_smart_collection_oracle.py``'s, shared by value and not by
import -- an oracle and the code it judges must not share a symbol, which is the
rule ``test_the_oracles_vocabulary_fixture_matches_this_files_copy`` already
states about ``CHOICES``.
"""
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from autoposter.collections.smart import (
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    reconcile_smart_collection,
    smart_definition_hash,
    smart_filter_uri,
)
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"
MACHINE_IDENTIFIER = "abc123"
SECTION_KEY = "2"
TITLE = "Oracle Collection"

# Config 1 of the oracle: the query, and the two request keys its envelope
# produces. Copied from tests/test_smart_collection_oracle.py by value.
URL = "?type=1&sort=titleSort&contentRating=5&and=1&contentRating=7"
KOMETA_POST = "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26contentRating%3D7"
KOMETA_PUT = "/library/collections/12345/items?uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26contentRating%3D7"


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeServer:
    """``section._server``: the three internals the raw POST/PUT reach for."""

    def __init__(self):
        self.queries = []
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://%s/com.plexapp.plugins.library" % MACHINE_IDENTIFIER

    def query(self, key, method=None, **kwargs):
        self.queries.append((key, method))


class FakeCollection:
    """Mirrors plexapi's lazy ``labels``/``fields``: empty until ``reload()``."""

    def __init__(self, title, labels=(), rating_key="12345", smart=True, summary=None):
        self.title = title
        self.ratingKey = rating_key
        self.smart = smart
        self.summary = summary
        self.titleSort = None
        self.collectionMode = None
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": False})()]
        self._fields = []
        self.labels_added = []
        self.sort_titles = []
        # ``_edit_collection_summary`` writes through ``collection._server``,
        # which is the COLLECTION's server and not the section's -- so the
        # summary PUT stays out of ``section._server.queries``, where the
        # envelope assertions read the create POST and the update PUT. Standing
        # in for it with the collection itself is what
        # tests/test_collection_reconcile.py's own fake does.
        self.queries = []
        self._server = self
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return self._fields

    def reload(self, **kw):
        self._labels = self._real_labels
        self._fields = self._real_fields

    def addLabel(self, label, locked=True):
        self.labels_added.append(label)
        self._real_labels.append(type("L", (), {"tag": label})())
        self._labels = self._real_labels

    def editSortTitle(self, value, locked=True):
        self.sort_titles.append(value)
        self.titleSort = value

    def query(self, key, method=None, **kwargs):
        """The item-level summary PUT, through this collection's own server."""
        self.queries.append((key, method))
        self.summary = parse_qs(urlsplit(key).query)["summary.value"][0]
        self._real_fields[0].locked = True


class FakeSection:
    def __init__(self, matches=1, existing=(), key=SECTION_KEY):
        self.key = key
        self._server = FakeServer()
        self._existing = {c.title: c for c in existing}
        self._matches = matches
        self.fetched = []

    def collections(self, **kw):
        return list(self._existing.values())

    def collection(self, title):
        """plexapi's re-read after the raw POST. The fake stands in for the
        collection Plex created, so it comes back SMART."""
        if title not in self._existing:
            self._existing[title] = FakeCollection(title, smart=True)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        self.fetched.append(path)
        if isinstance(self._matches, Exception):
            raise self._matches
        return [FakeItem(str(i)) for i in range(self._matches)]


async def _row(session, library, title):
    return (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.library == library, ManagedCollection.title == title
            )
        )
    ).scalar_one_or_none()


def test_the_uri_is_the_one_kometa_stores():
    """``build_smart_filter``, modules/plex.py:1615-1616. A ``server://`` uri,
    not a URL -- Plex stores this and evaluates it itself."""
    assert smart_filter_uri(FakeServer(), SECTION_KEY, URL) == (
        "server://abc123/com.plexapp.plugins.library/library/sections/2/all" + URL
    )


async def test_the_create_post_is_byte_identical_to_the_oracles(session):
    """THE gate this task exists for. The shipped envelope, against the string
    Kometa's own ``create_smart_collection`` produces for the same query
    (tests/test_smart_collection_oracle.py, config 1)."""
    section = FakeSection(matches=7)
    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert [key for key, _ in section._server.queries] == [KOMETA_POST]
    assert section._server.queries[0][1] == "POST"
    assert any("created" in action for action in actions)


async def test_the_update_put_is_byte_identical_to_the_oracles(session):
    """The second shape: same uri value, different path, and none of the
    create-only arguments."""
    existing = FakeCollection(TITLE, labels=[LABEL], rating_key="12345", smart=True)
    section = FakeSection(matches=7, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash="stale",
    ))
    await session.flush()

    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert [key for key, _ in section._server.queries] == [KOMETA_PUT]
    assert section._server.queries[0][1] == "PUT"
    assert any("updated" in action for action in actions)


async def test_a_filter_matching_nothing_refuses_at_create(session):
    """C8, without ``ignore_blank_results``. Kometa offers the switch; an
    error-downgrade switch is the ``validate:`` class 9b already refused."""
    section = FakeSection(matches=0)
    with pytest.raises(SmartFilterMatchedNothing) as caught:
        await reconcile_smart_collection(
            session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
        )
    assert "widen" in str(caught.value).lower()
    assert section._server.queries == [], "nothing may be written after the refusal"
    assert await _row(session, "Movies", TITLE) is None


async def test_a_filter_matching_nothing_refuses_at_update_too(session):
    """The update path refuses on zero as well -- which is upstream's own shape
    (modules/plex.py:1618-1620 calls test_smart_filter unconditionally) and the
    one that matters more: a library that lost every match would otherwise have
    its filter quietly rewritten to one that finds nothing."""
    existing = FakeCollection(TITLE, labels=[LABEL], smart=True)
    section = FakeSection(matches=0, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash="stale",
    ))
    await session.flush()

    with pytest.raises(SmartFilterMatchedNothing):
        await reconcile_smart_collection(
            session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
        )
    assert section._server.queries == []


async def test_the_match_probe_wraps_a_plex_failure_by_class_name_only(session):
    """A tokenised URL can ride in a plexapi exception's message. The engine's
    log line prints the class name and nothing else, so this must too --
    ``plex_search.py:351-359`` is the shape."""
    boom = RuntimeError("https://plex.example:32400/library?X-Plex-Token=SECRET")
    section = FakeSection(matches=boom)
    with pytest.raises(SmartCollectionUnavailable) as caught:
        await reconcile_smart_collection(
            session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
        )
    assert "RuntimeError" in str(caught.value)
    assert "SECRET" not in str(caught.value)
    assert "X-Plex-Token" not in str(caught.value)


async def test_a_dry_run_probes_but_writes_nothing(session):
    """The probe is a READ, so it runs under dry_run too: an operator seeing the
    preview before enabling apply_to_plex should learn that the filter matches
    nothing THEN, not on the first applied pass."""
    section = FakeSection(matches=3)
    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=True,
    )
    assert section.fetched == ["/library/sections/2/all" + URL]
    assert section._server.queries == []
    assert any("would create" in action and "3" in action for action in actions)
    assert await _row(session, "Movies", TITLE) is None


async def test_an_unchanged_definition_writes_nothing_and_probes_nothing(session):
    """C10: the hash is over the BUILT URI, so a pass whose URI is unchanged
    short-circuits before the probe. That is what keeps a smart definition from
    costing one Plex query per pass forever."""
    existing = FakeCollection(TITLE, labels=[LABEL], smart=True)
    section = FakeSection(matches=7, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash=smart_definition_hash(URL, None),
    ))
    await session.flush()

    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert actions == []
    assert section.fetched == []
    assert section._server.queries == []


async def test_the_hash_is_over_the_uri_and_not_over_plexs_echo(session):
    """C10 in one assertion: two different URIs hash differently and the same
    URI hashes identically, and nothing anywhere reads ``Collection.content``.
    Plex-side manual edits to a smart filter therefore go undetected -- exactly
    as they do for the Common Sense family today, whose hash is over the desired
    state too."""
    assert smart_definition_hash(URL, None) == smart_definition_hash(URL, None)
    assert smart_definition_hash(URL, None) != smart_definition_hash(
        URL.replace("titleSort", "random"), None
    )
    assert smart_definition_hash(URL, "a summary") != smart_definition_hash(URL, None)


async def test_a_changed_summary_alone_re_applies(session):
    """The summary is in the hash for the reason ``lists._settings_parts``
    exists: a pass short-circuits on the hash, so a definition whose only edit
    was the summary would otherwise be recognised as current and the edit would
    never be applied."""
    existing = FakeCollection(TITLE, labels=[LABEL], smart=True, summary="old")
    section = FakeSection(matches=7, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash=smart_definition_hash(URL, "old"),
    ))
    await session.flush()

    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL,
        summary="new", dry_run=False,
    )
    assert existing.summary == "new"


async def test_the_ride_along_settings_are_applied_on_create(session):
    """Row 104's half that lives on the create path: the definition's labels and
    sort title reach the collection object, through the SAME
    ``apply_collection_settings`` both other reconcilers call."""
    section = FakeSection(matches=7)
    definition = CollectionDefinition(
        title=TITLE, builder="plex_id", params={"ids": ["1"]},
        labels=["Curated"], sort_title="!300_Recent",
    )
    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL,
        dry_run=False, settings=definition,
    )
    created = section.collection(TITLE)
    assert LABEL in created.labels_added
    assert "Curated" in created.labels_added
    assert created.sort_titles == ["!300_Recent"]


async def test_a_row_is_written_with_kind_smart(session):
    section = FakeSection(matches=7)
    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    row = await _row(session, "Movies", TITLE)
    # Read into locals before anything can expire them -- the async-SQLAlchemy
    # rule in this plan's Global Constraints.
    kind, rating_key, digest = row.kind, row.plex_rating_key, row.definition_hash
    assert kind == "smart"
    assert rating_key == "12345"
    assert digest == smart_definition_hash(URL, None)


async def test_a_collection_that_is_not_ours_is_left_alone(session):
    """The ownership rule, unchanged and shared: ``resolve_collision`` is the
    one place it lives, so it cannot drift between the three reconcilers."""
    stranger = FakeCollection(TITLE, labels=["SomeoneElse"], smart=True)
    section = FakeSection(matches=7, existing=[stranger])
    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert section._server.queries == []
    assert any("conflict" in action for action in actions)
    assert await _row(session, "Movies", TITLE) is None


async def test_a_list_collection_under_a_smart_definition_refuses(session):
    """C11, the smart half. The collection exists and is ours, and it is a LIST
    collection -- so the definition changed shape and this refuses rather than
    deleting it the way Kometa does (modules/builder.py:1768-1772)."""
    dumb = FakeCollection(TITLE, labels=[LABEL], smart=False)
    section = FakeSection(matches=7, existing=[dumb])
    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert section._server.queries == []
    assert len(actions) == 1
    assert "shape conflict" in actions[0]
    assert await _row(session, "Movies", TITLE) is None
