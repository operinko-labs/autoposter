"""Reconciling ONE Plex-native smart collection from a built search URL.

The fakes mirror plexapi's real signatures, which are pinned separately in
``tests/test_plexapi_collection_contract.py``. The pinned request strings are a
COPY of ``tests/test_smart_collection_oracle.py``'s, shared by value and not by
import -- an oracle and the code it judges must not share a symbol, which is the
rule ``test_the_oracles_vocabulary_fixture_matches_this_files_copy`` already
states about ``CHOICES``.
"""
import ast
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from autoposter.collections.smart import (
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    count_matches,
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

# Three more configs of the oracle, closing the review's Important 2: config 12
# is the ONE byte the shipped envelope computes rather than passes through
# (smart.py:149's ``1 if libtype == "movie" else 2``), and 8/15 are the
# ``!`` -> ``%21`` encoding class that config 1 alone never exercises. Copied
# from tests/test_smart_collection_oracle.py by value, same rule as above --
# and test_this_files_oracle_copies_match_the_oracles ties the copies to it.
URL_SHOW = "?type=2&limit=10&sort=episode.addedAt%3Adesc&show.genre=9&and=1&episode.resolution=1080&and=1&episode.audioLanguage=en&and=1&show.network=42&and=1&show.addedAt%3E%3E=2024-01-01"
KOMETA_POST_SHOW = "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=2&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D2%26limit%3D10%26sort%3Depisode.addedAt%253Adesc%26show.genre%3D9%26and%3D1%26episode.resolution%3D1080%26and%3D1%26episode.audioLanguage%3Den%26and%3D1%26show.network%3D42%26and%3D1%26show.addedAt%253E%253E%3D2024-01-01"

URL_8 = "?type=1&sort=titleSort&rating!=-1&and=1&audienceRating=-1"
KOMETA_POST_8 = "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26rating%21%3D-1%26and%3D1%26audienceRating%3D-1"

URL_15 = "?type=1&sort=titleSort&genre!=1138&and=1&studio!%3D=A24&and=1&studio%3E=Pictures%20%26%20Co&and=1&label=3&and=1&collection=77&and=1&viewCount%3E%3E=3&and=1&viewCount%3C=10"
KOMETA_POST_15 = "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26genre%21%3D1138%26and%3D1%26studio%21%253D%3DA24%26and%3D1%26studio%253E%3DPictures%2520%2526%2520Co%26and%3D1%26label%3D3%26and%3D1%26collection%3D77%26and%3D1%26viewCount%253E%253E%3D3%26and%3D1%26viewCount%253C%3D10"


class FakeContainer:
    """What ``PlexServer.query`` hands back -- an ElementTree element. The
    container-size-0 read carries ``totalSize`` in its attribs and no children
    at all, which is what the probe measured (``docs/research/plex-batch-probe/
    README.md``, probe e: ``totalSize='17' size='0' children=0``)."""

    def __init__(self, attrib):
        self.attrib = attrib


class FakeServer:
    """``section._server``: the internals the raw POST/PUT and the counting
    read reach for.

    ``queries`` holds WRITES only. A write always names a ``method``, and the
    counting read never does, so the absence of one is what tells the two
    apart -- which keeps the "nothing may be written" assertions below meaning
    what they say now that a read goes through this same method.
    """

    def __init__(self, matches=0):
        self.queries = []
        self.reads = []
        self._matches = matches
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://%s/com.plexapp.plugins.library" % MACHINE_IDENTIFIER

    def query(self, key, method=None, headers=None, **kwargs):
        if method is None:
            self.reads.append((key, headers))
            if isinstance(self._matches, Exception):
                raise self._matches
            return FakeContainer({"totalSize": str(self._matches), "size": "0"})
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

    def items(self):
        """Poisoned: a plexapi ``Collection`` truthiness check calls
        ``__len__``, which is ``len(self.items())`` -- a live Plex request. F1
        regression: nothing in this reconciler may reach either."""
        raise AssertionError("items() must not be called")

    def __len__(self):
        raise AssertionError("items() must not be called")

    def query(self, key, method=None, **kwargs):
        """The item-level summary PUT -- or, since row 187, the summary
        CLEAR, whose ``summary.value`` is empty and whose ``summary.locked``
        is 0; ``keep_blank_values`` is what keeps the empty value visible."""
        self.queries.append((key, method))
        query = parse_qs(urlsplit(key).query, keep_blank_values=True)
        self.summary = query["summary.value"][0]
        self._real_fields[0].locked = query.get("summary.locked") == ["1"]


class FakeSection:
    def __init__(self, matches=1, existing=(), key=SECTION_KEY):
        self.key = key
        self._server = FakeServer(matches)
        self._existing = {c.title: c for c in existing}
        self._matches = matches
        self.listings = 0

    def collections(self, **kw):
        self.listings += 1
        return list(self._existing.values())

    def collection(self, title):
        """plexapi's re-read after the raw POST. The fake stands in for the
        collection Plex created, so it comes back SMART."""
        if title not in self._existing:
            self._existing[title] = FakeCollection(title, smart=True)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        """Poisoned. Counting a filter's matches is a container-size-0 read,
        not a fetch (roadmap row 198) -- nothing in this reconciler may pull
        four thousand items across the wire to learn "more than none"."""
        raise AssertionError("count_matches must not materialise the items")


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


async def test_the_show_branchs_type_byte_is_byte_identical_to_the_oracles(session):
    """Config 12: the ONE byte the shipped envelope computes rather than passes
    through (``smart.py:149``). No other test in this file drives the module
    with ``library_type="Show"``."""
    section = FakeSection(matches=7)
    await reconcile_smart_collection(
        session, section, "Shows", "Show", TITLE, URL_SHOW, LABEL, dry_run=False,
    )
    assert [key for key, _ in section._server.queries] == [KOMETA_POST_SHOW]


async def test_a_raw_bang_double_encodes_through_the_shipped_path(session):
    """Config 8: a raw ``!`` in the query, encoded by the envelope alone
    (``%21``) -- never exercised through the shipped path by config 1."""
    section = FakeSection(matches=7)
    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL_8, LABEL, dry_run=False,
    )
    assert [key for key, _ in section._server.queries] == [KOMETA_POST_8]


async def test_a_double_encoded_bang_equals_matches_the_oracles(session):
    """Config 15: ``!%3D`` through both encoding layers (``%21%253D``)."""
    section = FakeSection(matches=7)
    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL_15, LABEL, dry_run=False,
    )
    assert [key for key, _ in section._server.queries] == [KOMETA_POST_15]


def test_this_files_oracle_copies_match_the_oracles():
    """Review ⚠️5: nothing previously guarded ``KOMETA_POST``/``KOMETA_PUT``
    above, or the three added for configs 8/12/15, against drifting from
    ``tests/test_smart_collection_oracle.py``'s copies. Read as text and
    compared by literal, the same idiom
    ``test_the_oracles_vocabulary_fixture_matches_this_files_copy`` uses for
    ``CHOICES`` -- an oracle and the code it judges must not share a symbol,
    so this reaches the other file's source rather than importing it.
    """
    source = (Path(__file__).parent / "test_smart_collection_oracle.py").read_text()
    tree = ast.parse(source)
    kometa_post = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and node.targets[0].id == "KOMETA_POST"
    )
    kometa_put = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and node.targets[0].id == "KOMETA_PUT"
    )
    assert kometa_post["1-multi-value-tag"] == KOMETA_POST
    assert kometa_post["12-show-rescoping"] == KOMETA_POST_SHOW
    assert kometa_post["8-rated"] == KOMETA_POST_8
    assert kometa_post["15-unreached-renders-and-rows"] == KOMETA_POST_15
    # The PUT copy is the last literal in this file that nothing tied back to
    # the oracle. It shares ``smart_filter_uri`` and ``joinArgs`` with the POST,
    # so a drift reaching only this string takes a hand-edit to this one line --
    # narrow, and the phase's acceptance rides on the chain being closed.
    assert kometa_put["1-multi-value-tag"] == KOMETA_PUT


async def test_a_created_collection_goes_back_into_the_callers_listing(session):
    """``lists.py:283``'s rule, on this path too. The map belongs to the PASS,
    so a later definition reading it has to see a collection this one created
    rather than a listing taken before it existed."""
    section = FakeSection(matches=7)
    listing: dict = {}

    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
        existing=listing,
    )

    assert listing[TITLE].title == TITLE
    # The caller's map was the one consulted, so nothing listed the section.
    assert section.listings == 0


async def test_a_supplied_listing_is_used_instead_of_listing_the_section(session):
    """The reason the caller passes one at all: ``section.collections()``
    returns every collection in the library, and the engine already paid for it
    once this pass. The listing is read before the hash short-circuit, so a
    definition that costs nothing else still costs this -- which is what made it
    worth sharing rather than amortising."""
    existing = FakeCollection(TITLE, labels=[LABEL], rating_key="12345", smart=True)
    section = FakeSection(matches=7)
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash=smart_definition_hash(URL, None, None),
    ))
    await session.flush()

    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
        existing={TITLE: existing},
    )

    assert actions == []
    assert section.listings == 0


async def test_taking_over_a_list_row_stamps_it_smart_and_clears_its_stamps(session):
    """The C11 remediation path, in the list -> smart direction.

    The definition was a ``plex_search``; the operator switched it to
    ``smart_filter``, met the shape refusal, and did what it told them --
    deleted the collection in Plex so the next pass could create it. That pass
    arrives here with the LIST definition's row: ``kind="manual"``, and the
    membership stamps that definition wrote. Nothing on this path revisited
    them before, so the row reported a count Plex has since taken ownership of
    as if a pass had just confirmed it.
    """
    section = FakeSection(matches=7)
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="manual", plex_rating_key="12345",
        definition_hash="the list definition's", member_count=42,
        last_added=3, last_removed=1,
        last_reconciled_at=datetime(2020, 1, 1, tzinfo=UTC),
    ))
    await session.flush()

    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )

    row = await _row(session, "Movies", TITLE)
    kind, member_count = row.kind, row.member_count
    last_added, last_removed = row.last_added, row.last_removed
    last_reconciled = row.last_reconciled_at
    assert kind == "smart"
    # The invariant ``ManagedCollection``'s own comment states for a smart row:
    # Plex evaluates the filter live, so there is no membership to count.
    assert (member_count, last_added, last_removed, last_reconciled) == (
        None, None, None, None
    )


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


def test_counting_matches_reads_totalsize_at_container_size_zero():
    """Roadmap row 198, closed by the phase-B probe (``docs/research/
    plex-batch-probe/README.md``, probe e). Validating a filter that matches
    four thousand items used to pull four thousand items across the wire to
    learn "more than none"; Plex answers the same search URL with a
    ``totalSize`` attrib and zero children when asked for a container of size
    zero, so the count costs one tiny GET. ``FakeSection.fetchItems`` is
    poisoned, which is what makes "never materialises them" an assertion here
    rather than a claim."""
    section = FakeSection(matches=4123)
    assert count_matches(section, "?type=1&genre=1138") == 4123
    path, headers = section._server.reads[0]
    assert path == "/library/sections/2/all?type=1&genre=1138"
    assert headers == {"X-Plex-Container-Start": "0", "X-Plex-Container-Size": "0"}


def test_a_container_with_no_totalsize_is_a_failure_and_not_a_zero():
    """Zero MATCHES is an answer; an unreadable count is not. A response
    without the attrib would otherwise read as "this filter matches nothing"
    and refuse a perfectly good smart collection at ``require_matches`` -- so
    the missing attrib raises, and the caller learns Plex would not answer."""

    class Silent(FakeSection):
        def __init__(self):
            super().__init__(matches=0)
            self._server.query = lambda *a, **kw: FakeContainer({"size": "0"})

    with pytest.raises(SmartCollectionUnavailable) as caught:
        count_matches(Silent(), "?type=1&genre=1138")
    assert "ValueError" in str(caught.value)


def test_counting_matches_does_not_refuse_at_zero():
    """``require_matches`` refuses at zero because a permanently-empty smart
    collection is not a collection. A per-key MINIMUM asks a different question
    -- "how many, so I can compare" -- and zero is a legitimate answer to it.
    Two functions rather than a flag, so neither caller can be read as the
    other."""
    assert count_matches(FakeSection(matches=0), "?type=1&genre=1138") == 0


def test_counting_matches_still_wraps_a_dead_plex_class_name_only():
    """The wrap is on the counting half, so both callers inherit it and neither
    can leak a tokenised URL by being the one that forgot."""
    boom = RuntimeError("https://plex.example:32400/library?X-Plex-Token=SECRET")
    with pytest.raises(SmartCollectionUnavailable) as caught:
        count_matches(FakeSection(matches=boom), "?type=1&genre=1138")
    assert "RuntimeError" in str(caught.value)
    assert "https://" not in str(caught.value)
    assert "X-Plex-Token" not in str(caught.value)
    assert "SECRET" not in str(caught.value)


async def test_a_dry_run_probes_but_writes_nothing(session):
    """The probe is a READ, so it runs under dry_run too: an operator seeing the
    preview before enabling apply_to_plex should learn that the filter matches
    nothing THEN, not on the first applied pass."""
    section = FakeSection(matches=3)
    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=True,
    )
    assert [path for path, _ in section._server.reads] == [
        "/library/sections/2/all" + URL
    ]
    assert section._server.queries == []
    assert any("would create" in action and "3" in action for action in actions)
    assert await _row(session, "Movies", TITLE) is None


async def test_a_dry_run_against_an_existing_collection_reports_update_not_create(session):
    """F1: ``if collection`` tests a plexapi ``Collection`` for truthiness,
    which calls ``__len__`` -> ``len(self.items())`` -- an extra Plex request
    inside a dry run, and falsy for an existing collection whose stored filter
    currently matches zero items. ``FakeCollection.items``/``__len__`` are
    poisoned, so this reds on the old ``if collection`` and greens on
    ``if collection is not None``."""
    existing = FakeCollection(TITLE, labels=[LABEL], smart=True)
    section = FakeSection(matches=3, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash="stale",
    ))
    await session.flush()

    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=True,
    )
    assert any("would update" in action for action in actions)
    assert not any("would create" in action for action in actions)
    assert section._server.queries == []


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
    assert section._server.reads == []
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


async def test_a_removed_summary_is_cleared_and_unlocked(session):
    """Roadmap row 187 (9c Minor M-B). The summary is IN the definition hash,
    so deleting ``summary:`` triggers a pass -- which then performed no
    summary edit at all and stored the new hash as current: the operator's
    edit recognised, acted on by nothing, recorded as done. Now it clears:
    empty value, lock released -- the full revert of what this service
    writes. The gate is the LOCK, the marker every managed write leaves."""
    existing = FakeCollection(
        TITLE, labels=[LABEL], rating_key="12345", smart=True, summary="Old."
    )
    existing._real_fields[0].locked = True
    section = FakeSection(matches=7, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash="stale",
    ))
    await session.flush()

    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )

    keys = [key for key, _ in existing.queries]
    assert len(keys) == 1
    query = parse_qs(urlsplit(keys[0]).query, keep_blank_values=True)
    assert query["summary.value"] == [""]
    assert query["summary.locked"] == ["0"]
    assert any("cleared the summary" in action for action in actions)


async def test_a_summary_this_service_never_wrote_is_left_alone(session):
    """The other half of the clear-vs-skip decision: an UNLOCKED summary was
    never written through the managed route (every write here locks), so it
    is not this service's to clear."""
    existing = FakeCollection(
        TITLE, labels=[LABEL], rating_key="12345", smart=True, summary="Theirs."
    )
    section = FakeSection(matches=7, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash="stale",
    ))
    await session.flush()

    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )

    assert existing.queries == []
    assert existing.summary == "Theirs."


async def test_the_update_action_does_not_claim_the_filter_changed(session):
    """9c Minor M-A, folded into row 187: a settings-only or summary-only
    edit re-PUTs a byte-identical uri while the old string said 'updated the
    smart filter'. The pass cannot know which part changed, so the string
    stops claiming one."""
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

    assert "updated %r from its definition (7 item(s) match now)" % TITLE in actions
    assert not any("updated the smart filter" in action for action in actions)


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
