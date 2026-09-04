"""Wiring the poster machinery (Task 4) into the two reconcilers.

The safety property that matters most: a protected or unlabelled collection
must never have a poster applied. That falls out of ``apply_poster`` being
called after ``resolve_collision`` has approved the collection, not before
-- so the tests here assert it directly, on both the smart-collection path
(``reconcile.py``) and the list-collection path (``lists.py``).
"""
import hashlib
import io
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image
from plexapi.exceptions import NotFound
from sqlalchemy import select

from autoposter.collections import groups
from autoposter.collections.buckets import Bucket
from autoposter.collections.lists import _members_hash, reconcile_list_collection
from autoposter.collections.posters import DEFAULT_IMAGES_BASE, hosted_poster_url
from autoposter.collections.reconcile import definition_hash, reconcile_content_ratings
from autoposter.collections.smart import smart_definition_hash
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection
from autoposter.overlays.assets import INTER_BOLD

LABEL = "autoposter"

# The content-ratings group's divider title, as the engine derives it -- no
# longer a name ``reconcile`` exports, since row 49 left that module with no
# reader of its own for it (roadmap row 49, task 3 review Important 1).
SEPARATOR_TITLE = groups.separator_title("content_ratings")


async def _separator_pass(session, section, http, config, dry_run=False):
    """``engine._separators``' one call for the content-ratings group, called
    the same way the engine calls it rather than restating its body.

    Since row 49 the divider is not reconciled by ``reconcile_content_ratings``
    -- the engine drives one per active group -- so the poster wiring is
    asserted where the call now lives.
    """
    from autoposter.collections.engine import _separators

    results = await _separators(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket")],
        config, label=LABEL, dry_run=dry_run,
        listing=lambda: {c.title: c for c in section.collections()}, http=http,
    )
    return [action for result in results for action in result.actions]


def _jpeg_bytes(color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _serving_handler(data, seen):
    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=data)

    return handler


def _refusing_handler():
    async def handler(request):
        raise AssertionError("no poster request should have been made: %s" % request.url)

    return handler


# --- Common Sense smart collections + separator (reconcile.py) ------------


class FakeChoice:
    def __init__(self, title):
        self.title = title
        # Plex answers contentRating's key and title with the same string (the
        # 10a-1 dynamic probe measured it), so the resolver is the identity
        # here. Added when the Common Sense family started resolving its values
        # through ``LibraryTagResolver`` rather than handing plexapi the
        # written words.
        self.key = title


class RatingCollection:
    """Mirrors plexapi's lazy ``labels`` plus the surface the smart and
    separator reconcilers use, including ``uploadPoster``."""

    def __init__(self, title, labels=(), rating_key="1", summary="", sort_title=""):
        self.title = title
        self.ratingKey = rating_key
        self.summary = summary
        self.titleSort = sort_title
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self.updated_filters = None
        self.summary_set = None
        self.summary_queries = []
        self.sort_title_set = None
        self.labels_added = []
        self.items_added = []
        self.uploaded_bytes = []
        self.locks = 0
        # Stands in for ``collection._server``: the summary is written with a
        # raw item-level PUT, not ``editSummary``.
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

    @property
    def labels(self):
        return self._labels

    def reload(self, **kw):
        self._labels = self._real_labels

    def updateFilters(self, libtype=None, limit=None, sort=None, filters=None, **kw):
        self.updated_filters = filters

    def editSummary(self, summary, locked=True):
        """Raises the way the live server does -- the section route plexapi
        takes 404s for collection summaries. See
        ``reconcile._edit_collection_summary``."""
        raise NotFound("(404) not_found; /library/sections/42/all?type=18")

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Stands in for ``server.query`` -- the item-level summary PUT."""
        self.summary_queries.append({"key": key, "method": method})
        self.summary_set = parse_qs(urlsplit(key).query)["summary.value"][0]
        self.summary = self.summary_set

    def editSortTitle(self, sortTitle, locked=True):
        self.sort_title_set = sortTitle
        self.titleSort = sortTitle

    def addLabel(self, labels, locked=True):
        self.labels_added.append(labels)
        self._real_labels.append(type("L", (), {"tag": labels})())
        self._labels = self._real_labels

    def addItems(self, items):
        self.items_added.extend(items)

    def uploadPoster(self, filepath):
        with open(filepath, "rb") as handle:
            self.uploaded_bytes.append(handle.read())

    def lockPoster(self):
        self.locks += 1


class RatingSection:
    """Also stands in for ``section._server``, for the separator's raw POST."""

    def __init__(self, ratings=(), existing=(), section_type="movie", matches=1):
        self._ratings = list(ratings)
        self._existing = {c.title: c for c in existing}
        self.created = []
        self.queries = []
        self.key = "42"
        self.type = section_type
        # ``smart.count_matches``' own container-size-0 read (no ``title`` in
        # the query, ``method=None``): how many items a smart filter matches
        # before anything is written. See ``FakeServer`` in
        # ``test_collection_smart.py``, whose shape this mirrors.
        self._matches = matches
        self._server = self
        self._session = type("Sess", (), {
            "post": "POST-SENTINEL", "put": "PUT-SENTINEL",
        })()

    def _uriRoot(self):
        return "server://FAKE-MACHINE-ID/com.plexapp.plugins.library"

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Three routes: the create POST (which carries a ``title``), a
        bucket's filter-replacing PUT (which carries only a ``uri``), and --
        since ``method`` is None on a read -- ``count_matches``' container
        probe, which returns an attrib rather than being recorded as a write."""
        if method is None:
            return type("Container", (), {"attrib": {"totalSize": str(self._matches)}})()
        self.queries.append({"key": key, "method": method})
        args = parse_qs(urlsplit(key).query)
        if "title" not in args:
            return None
        title = args["title"][0]
        collection = RatingCollection(title, rating_key=str(len(self._existing) + 1))
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
        collection = RatingCollection(title, labels=[LABEL], rating_key=str(len(self.created)))
        self._existing[title] = collection
        return collection


@pytest.fixture
def section_factory():
    """A fresh ``RatingSection`` per call -- the fake this file's own smart
    tests already use, not a second one. It carries every surface
    ``reconcile_smart_collection`` needs: the count-matches probe, the create
    POST, ``collections()``/``collection()`` for the re-read, and a
    ``RatingCollection`` with ``uploadPoster``/``lockPoster`` for the poster
    step that follows."""
    return RatingSection


async def test_a_smart_bucket_gets_its_poster(session, config_factory, tmp_path):
    section = RatingSection({"17"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    data = _jpeg_bytes()
    seen = []

    async with _client(_serving_handler(data, seen)) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    collection = section._existing["Age 17+ Movies"]
    assert collection.uploaded_bytes == [data]
    assert collection.locks == 1
    assert seen == [hosted_poster_url("content_rating", "17")]

    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is not None


async def test_the_catch_all_gets_the_nr_poster(session, config_factory, tmp_path):
    section = RatingSection({"NR"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    data = _jpeg_bytes()
    seen = []

    async with _client(_serving_handler(data, seen)) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    collection = section._existing["Not Rated Movies"]
    assert collection.uploaded_bytes == [data]
    assert seen == [hosted_poster_url("content_rating_other", "other")]
    assert seen[0].endswith("NR.jpg")


async def test_the_separator_gets_the_separator_poster(session, config_factory, tmp_path):
    section = RatingSection(())
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    data = _jpeg_bytes()
    seen = []

    async with _client(_serving_handler(data, seen)) as http:
        await _separator_pass(session, section, http, config)

    collection = section._existing[SEPARATOR_TITLE]
    assert collection.uploaded_bytes == [data]
    # The style-bearing key since C4: "<style>:<stem>", so the URL names the
    # style folder the config selected rather than a hardcoded ``orig``. Both
    # requests are pinned, in order and exhaustively: the "Other Collections"
    # fence reconciles after this divider and, having no upstream stem, asks
    # for the generated ``@base`` layer. Pinning the LIST rather than element
    # zero pins the request count too, so a third fetch this pass has no
    # business making goes red instead of passing unseen.
    assert seen == [
        hosted_poster_url("separator", "orig:content_rating"),
        f"{DEFAULT_IMAGES_BASE}/separators/@base/orig.png",
    ]


async def test_a_protected_collision_never_gets_a_poster_applied(
    session, config_factory, tmp_path
):
    theirs = RatingCollection("Age 17+ Movies", labels=["Collection managed by Maintainerr"])
    section = RatingSection({"17"}, existing=[theirs])
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True

    async with _client(_refusing_handler()) as http:
        actions = await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            protect_labels=["Collection managed by Maintainerr"],
            http=http, config=config,
        )

    assert theirs.uploaded_bytes == []
    assert any("protected" in a.lower() for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows == []


async def test_an_unlabelled_collision_never_gets_a_poster_applied(
    session, config_factory, tmp_path
):
    theirs = RatingCollection("Age 17+ Movies")  # the operator's own, no label at all
    section = RatingSection({"17"}, existing=[theirs])
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True

    async with _client(_refusing_handler()) as http:
        actions = await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert theirs.uploaded_bytes == []
    assert any("conflict" in a.lower() for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows == []


async def test_posters_false_disables_smart_collection_posters(session, config_factory, tmp_path):
    section = RatingSection({"17"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    config.collections.posters = False

    async with _client(_refusing_handler()) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    collection = section._existing["Age 17+ Movies"]
    assert collection.uploaded_bytes == []
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None


async def test_a_smart_collection_with_an_unchanged_definition_still_gets_a_missing_poster(
    session, config_factory, tmp_path
):
    """The 49 collections already on the deployed instance have a correct
    definition, so the definition-hash return fires before the poster block.
    A NULL ``poster_sha256`` -- never set, or a fetch that failed on the pass
    that created the collection -- must still bring us back here."""
    section = RatingSection({"17"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    config.collections.posters = False

    async with _client(_refusing_handler()) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    collection = section._existing["Age 17+ Movies"]
    assert collection.uploaded_bytes == []

    config.collections.posters = True
    written = len(section.queries)
    data = _jpeg_bytes()
    seen = []
    async with _client(_serving_handler(data, seen)) as http:
        actions = await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert collection.uploaded_bytes == [data]
    # The definition was left alone: no create POST and no filter-replacing PUT.
    assert len(section.queries) == written
    assert collection.updated_filters is None
    assert any("poster" in a for a in actions)
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is not None


async def test_a_third_pass_over_an_unchanged_collection_uploads_nothing(
    session, config_factory, tmp_path
):
    """Once the hash is stored the definition-hash return resumes and the
    poster block is not reached at all: no fetch, no upload, no action."""
    section = RatingSection({"17"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    data = _jpeg_bytes()
    seen = []

    async with _client(_serving_handler(data, seen)) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
        collection = section._existing["Age 17+ Movies"]
        assert collection.uploaded_bytes == [data]

        actions = await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert collection.uploaded_bytes == [data]  # no second upload
    assert actions == []


async def test_the_separator_with_an_unchanged_definition_still_gets_a_missing_poster(
    session, config_factory, tmp_path
):
    section = RatingSection(())
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    config.collections.posters = False

    async with _client(_refusing_handler()) as http:
        await _separator_pass(session, section, http, config)

    collection = section._existing[SEPARATOR_TITLE]
    assert collection.uploaded_bytes == []

    config.collections.posters = True
    data = _jpeg_bytes()
    async with _client(_serving_handler(data, [])) as http:
        await _separator_pass(session, section, http, config)

    assert collection.uploaded_bytes == [data]


async def test_a_dry_run_reports_the_poster_it_would_set_without_uploading(
    session, config_factory, tmp_path
):
    """``apply_to_plex: false`` used to produce a report that never mentioned
    posters at all, because every call site hardcoded ``dry_run=False``."""
    section = RatingSection({"17"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    config.collections.posters = False

    async with _client(_refusing_handler()) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    config.collections.posters = True
    collection = section._existing["Age 17+ Movies"]
    async with _client(_serving_handler(_jpeg_bytes(), [])) as http:
        actions = await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=True,
            http=http, config=config,
        )

    assert any("would set the poster" in a for a in actions)
    assert collection.uploaded_bytes == []
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None


# --- Chart and Oscars list collections (lists.py / sources.py) ------------


class FakeItem:
    def __init__(self, key):
        self.ratingKey = key
        self.title = key


class ListCollection:
    """Same caching model as tests/test_collection_lists.py, plus ``uploadPoster``."""

    def __init__(self, title, items=(), labels=(LABEL,), summary=None):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._labels = [type("L", (), {"tag": t})() for t in labels]
        self.summary = summary
        self.uploaded_bytes = []
        self.locks = 0
        # Stands in for ``collection._server``: the summary is written with a
        # raw item-level PUT, not ``editSummary``.
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

    def reload(self):
        self._cache = list(self._live)

    @property
    def labels(self):
        return self._labels

    def items(self):
        return list(self._cache)

    def addItems(self, items):
        self._live.extend(items)

    def removeItems(self, items):
        keys = {i.ratingKey for i in items}
        self._live = [i for i in self._live if i.ratingKey not in keys]

    def moveItem(self, item, after=None):
        pass

    def sortUpdate(self, sort=None):
        pass

    def editSummary(self, summary, locked=True):
        """Raises the way the live server does -- the section route plexapi
        takes 404s for collection summaries. See
        ``reconcile._edit_collection_summary``."""
        raise NotFound("(404) not_found; /library/sections/42/all?type=18")

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Stands in for ``server.query`` -- the item-level summary PUT."""
        self.summary = parse_qs(urlsplit(key).query)["summary.value"][0]

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())

    def uploadPoster(self, filepath):
        with open(filepath, "rb") as handle:
            self.uploaded_bytes.append(handle.read())

    def lockPoster(self):
        self.locks += 1


class ListSection:
    def __init__(self, existing=()):
        self._existing = {c.title: c for c in existing}

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        collection = ListCollection(title, items=items or [], labels=[])
        self._existing[title] = collection
        return collection


async def test_a_chart_collection_gets_its_chart_poster(session, config_factory, tmp_path):
    section = ListSection()
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    data = _jpeg_bytes()
    seen = []

    async with _client(_serving_handler(data, seen)) as http:
        await reconcile_list_collection(
            session, section, "Movies", "IMDb Top 250", [FakeItem("a")], LABEL,
            dry_run=False, kind="chart", key="IMDb Top 250", http=http, config=config,
        )

    collection = section._existing["IMDb Top 250"]
    assert collection.uploaded_bytes == [data]
    assert collection.locks == 1
    assert seen == [hosted_poster_url("chart", "IMDb Top 250")]


async def test_an_oscars_year_collection_gets_that_years_poster(session, config_factory, tmp_path):
    section = ListSection()
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    data = _jpeg_bytes()
    seen = []

    async with _client(_serving_handler(data, seen)) as http:
        await reconcile_list_collection(
            session, section, "Movies", "Oscars Winners 2026", [FakeItem("a")], LABEL,
            dry_run=False, kind="award_year", key="oscars:2026", http=http, config=config,
        )

    collection = section._existing["Oscars Winners 2026"]
    assert collection.uploaded_bytes == [data]
    assert seen == [hosted_poster_url("award_year", "oscars:2026")]


async def test_posters_false_disables_list_collection_posters(session, config_factory, tmp_path):
    section = ListSection()
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    config.collections.posters = False

    async with _client(_refusing_handler()) as http:
        await reconcile_list_collection(
            session, section, "Movies", "IMDb Top 250", [FakeItem("a")], LABEL,
            dry_run=False, kind="chart", key="IMDb Top 250", http=http, config=config,
        )

    collection = section._existing["IMDb Top 250"]
    assert collection.uploaded_bytes == []
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None


async def test_a_list_collection_with_unchanged_membership_still_gets_a_missing_poster(
    session, config_factory, tmp_path
):
    """Same hole as the smart path: the membership-hash return fires before
    the poster block, so a row whose ``poster_sha256`` is NULL would never be
    revisited once its members were correct."""
    section = ListSection()
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    config.collections.posters = False
    items = [FakeItem("a")]

    async with _client(_refusing_handler()) as http:
        await reconcile_list_collection(
            session, section, "Movies", "IMDb Top 250", items, LABEL,
            dry_run=False, kind="chart", key="IMDb Top 250", http=http, config=config,
        )

    collection = section._existing["IMDb Top 250"]
    assert collection.uploaded_bytes == []

    config.collections.posters = True
    data = _jpeg_bytes()
    seen = []
    async with _client(_serving_handler(data, seen)) as http:
        actions = await reconcile_list_collection(
            session, section, "Movies", "IMDb Top 250", items, LABEL,
            dry_run=False, kind="chart", key="IMDb Top 250", http=http, config=config,
        )

    assert collection.uploaded_bytes == [data]
    assert any("poster" in a for a in actions)
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is not None


async def test_a_list_collection_dry_run_reports_the_poster_it_would_set(
    session, config_factory, tmp_path
):
    section = ListSection()
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    config.collections.posters = False
    items = [FakeItem("a")]

    async with _client(_refusing_handler()) as http:
        await reconcile_list_collection(
            session, section, "Movies", "IMDb Top 250", items, LABEL,
            dry_run=False, kind="chart", key="IMDb Top 250", http=http, config=config,
        )

    config.collections.posters = True
    collection = section._existing["IMDb Top 250"]
    async with _client(_serving_handler(_jpeg_bytes(), [])) as http:
        actions = await reconcile_list_collection(
            session, section, "Movies", "IMDb Top 250", items, LABEL,
            dry_run=True, kind="chart", key="IMDb Top 250", http=http, config=config,
        )

    assert any("would set the poster" in a for a in actions)
    assert collection.uploaded_bytes == []
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None


async def test_a_kind_without_a_key_makes_no_request(session, config_factory, tmp_path):
    """``key`` is typed ``str | None``. Guarding only ``kind`` would build a
    URL with the literal string "None" in it."""
    section = ListSection()
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True

    async with _client(_refusing_handler()) as http:
        await reconcile_list_collection(
            session, section, "Movies", "IMDb Top 250", [FakeItem("a")], LABEL,
            dry_run=False, kind="chart", key=None, http=http, config=config,
        )

    collection = section._existing["IMDb Top 250"]
    assert collection.uploaded_bytes == []
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None


async def test_the_franchise_builder_names_its_own_collection_as_the_poster_key():
    """Upstream keys franchise art by NAME, never by TMDb collection id
    (p-defimg-probe.md §1 and its 'Confirmed non-findings' section), and the
    name we have is the unit's own title -- TMDb's collection name with the
    pack's ' Collection' suffix already stripped by `family_titles`."""
    from autoposter.collections.builders.base import BuilderContext, BuilderResult
    from autoposter.collections.builders.tmdb import TmdbCollectionBuilder

    class _Client:
        async def collection_parts(self, collection_id):
            return ["603"]

    class _Sources:
        tmdb = _Client()

    ctx = BuilderContext(
        library="Movies", library_type="Movie",
        config={"id": 1241},
        sources=_Sources(),
        definition=CollectionDefinition(
            title="Harry Potter", builder="tmdb_collection", params={"id": 1241},
        ),
    )
    result = await TmdbCollectionBuilder().build(ctx)

    assert isinstance(result, BuilderResult)
    assert result.poster_kind == "franchise"
    assert result.poster_key == "Harry Potter"


async def test_a_franchise_built_without_a_definition_offers_no_poster_key():
    """A direct caller has no definition (`BuilderContext.definition` is None
    for one), and a poster key invented from an id would be a URL that 404s."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.tmdb import TmdbCollectionBuilder

    class _Client:
        async def collection_parts(self, collection_id):
            return ["603"]

    class _Sources:
        tmdb = _Client()

    ctx = BuilderContext(
        library="Movies", library_type="Movie",
        config={"id": 1241}, sources=_Sources(),
    )
    result = await TmdbCollectionBuilder().build(ctx)

    assert result.poster_kind is None


# --- The dynamic-family hook (smart.py's poster_kind/poster_key pair) -----


async def test_a_dynamic_family_unit_gets_its_upstream_poster(
    session, section_factory, config_factory, tmp_path
):
    """The hook the whole dynamic half of this phase hangs on. Before it,
    `smart.reconcile_smart_collection` passed literal `None, None` to
    `apply_poster` for EVERY dynamic family -- so genre, studio, country,
    network, decade and both language families had no poster source at all
    beyond an operator's own file. The key is the unit's own, which for
    `audio_language` is the ISO code p-defimg-probe.md §2 shows the files are
    named by."""
    from autoposter.collections.smart import reconcile_smart_collection

    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    seen = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=data)

    section = section_factory()
    async with _client(handler) as http:
        await reconcile_smart_collection(
            session, section, "Movies", "Movie", "Top Finnish Movies",
            "?type=1&audioLanguage=fi", LABEL,
            dry_run=False, http=http, config=config,
            poster_kind="audio_language", poster_key="fi",
        )

    assert seen == [
        DEFAULT_IMAGES_BASE + "/audio_language/fi.jpg"
    ]
    row = (await session.execute(
        select(ManagedCollection).where(ManagedCollection.title == "Top Finnish Movies")
    )).scalar_one()
    assert row.poster_sha256 is not None


async def test_a_smart_definition_with_no_family_still_has_no_poster_source(
    session, section_factory, config_factory, tmp_path
):
    """The default is unchanged for every caller that does not pass the pair --
    `smart_filter`, `cs_bucket`'s own path, `credits_family`, and the engine's
    plain smart dispatch. A definition with no builder to derive artwork from
    still says so, honestly, on every pass."""
    from autoposter.collections.smart import reconcile_smart_collection

    config = config_factory(assets_root=str(tmp_path), library_folders=True)

    async def handler(request):
        raise AssertionError("a family-less smart collection must fetch nothing")

    section = section_factory()
    async with _client(handler) as http:
        actions = await reconcile_smart_collection(
            session, section, "Movies", "Movie", "Hand-written", "?type=1",
            LABEL, dry_run=False, http=http, config=config,
        )

    assert any("no poster source" in action for action in actions)


async def test_the_leftovers_bucket_is_never_given_a_family_poster():
    """`dynamic_titles.OTHER_KEY` is the string 'other', and `aspect/other.jpg`
    exists upstream -- so an unguarded lookup would ask for
    `country/color/other.jpg` for 'Other Countries' and, if the families were
    ever merged, hand it the aspect-ratio catch-all's art. The bucket is a name
    this service invented; upstream never drew it."""
    from autoposter.collections.builders.dynamic import poster_for_unit
    from autoposter.collections.dynamic_titles import OTHER_KEY, TitledKey
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES

    row = DYNAMIC_TYPES["country"]
    ordinary = TitledKey(
        key="France", key_name="France", title="France", values=("France",)
    )
    leftovers = TitledKey(
        key=OTHER_KEY, key_name=OTHER_KEY, title="Other Countries",
        values=("Sealand",),
    )

    assert poster_for_unit(row, ordinary) == ("country", "France")
    assert poster_for_unit(row, leftovers) == (None, None)
    assert poster_for_unit(DYNAMIC_TYPES["content_rating"], ordinary) == (None, None)


async def test_a_streaming_definition_takes_its_service_poster():
    """`production_streaming` is built by the GENERIC `tmdb_discover` builder,
    so there is no family-specific builder to hang a kind on -- the provider id
    is the only thing on the definition that names the service."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.tmdb_discover import TmdbDiscoverBuilder

    class _Client:
        async def discover(self, media_type, filters):
            return ["603"]

    class _Sources:
        tmdb = _Client()

    ctx = BuilderContext(
        library="Movies", library_type="Movie",
        config={
            "with_watch_providers": "8",
            "watch_region": "US",
            "sort_by": "popularity.desc",
        },
        sources=_Sources(),
    )
    result = await TmdbDiscoverBuilder().build(ctx)

    assert (result.poster_kind, result.poster_key) == ("streaming", "Netflix")


async def test_a_discover_definition_with_no_watch_provider_offers_no_poster():
    """Every other `tmdb_discover` definition -- and there are many -- behaves
    exactly as it does today."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.tmdb_discover import TmdbDiscoverBuilder

    class _Client:
        async def discover(self, media_type, filters):
            return ["603"]

    class _Sources:
        tmdb = _Client()

    ctx = BuilderContext(
        library="Movies", library_type="Movie",
        # A filtering attribute is required (`_needs_at_least_one_attribute`);
        # `with_genres` rather than any watch-provider field is what makes this
        # "no watch provider set", not "no filter at all".
        config={"with_genres": "18", "sort_by": "popularity.desc"}, sources=_Sources(),
    )
    result = await TmdbDiscoverBuilder().build(ctx)

    assert (result.poster_kind, result.poster_key) == (None, None)


async def test_a_resolution_bucket_takes_its_poster_from_its_own_filter():
    """`media_resolution` is four `plex_all` definitions distinguished only by
    their `resolution` filter -- the pack's own bucket key, and the same four
    strings p-defimg-probe.md §5 `resolution/` names its files by. A `plex_all`
    definition with any other filter, or none, is untouched."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.plex_trivial import PlexAllBuilder

    class _Access:
        def owned_index(self):
            return {"plex": ["1", "2"]}

    class _Sources:
        plex = _Access()

    def _ctx(filters):
        return BuilderContext(
            library="Movies", library_type="Movie", config={},
            sources=_Sources(),
            definition=CollectionDefinition(
                title="4k Movies", builder="plex_all", filters=filters,
            ),
        )

    bucket = await PlexAllBuilder().build(_ctx({"resolution": ["4k", "8k"]}))
    assert (bucket.poster_kind, bucket.poster_key) == ("resolution", "4k")

    other = await PlexAllBuilder().build(_ctx({"genre": ["Action"]}))
    assert (other.poster_kind, other.poster_key) == (None, None)

    bare = await PlexAllBuilder().build(
        BuilderContext(
            library="Movies", library_type="Movie", config={}, sources=_Sources(),
        )
    )
    assert (bare.poster_kind, bare.poster_key) == (None, None)


async def test_a_universe_list_takes_its_short_code_poster():
    """Three builders, one table. An IMDb list id, a TMDb list id and an
    MDBList ref all resolve through `UNIVERSE_CODES`; a list ref that is not a
    universe resolves to nothing, which is every other list definition."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.imdb_lists import ImdbListBuilder

    async def handler(request):
        return httpx.Response(200, json={"titles": []})

    class _Sources:
        pass

    # The builder's own fetch is stubbed at the module seam it already uses;
    # this test asserts the poster pair, not the membership.
    import autoposter.collections.builders.imdb_lists as module

    async def _entries(http, list_id):
        return []

    original = module.fetch_list
    module.fetch_list = _entries
    try:
        mcu = await ImdbListBuilder().build(BuilderContext(
            library="Movies", library_type="Movie",
            config={"list": "ls539646485"}, sources=_Sources(),
        ))
        other = await ImdbListBuilder().build(BuilderContext(
            library="Movies", library_type="Movie",
            config={"list": "ls000000001"}, sources=_Sources(),
        ))
    finally:
        module.fetch_list = original

    assert (mcu.poster_kind, mcu.poster_key) == ("universe", "mcu")
    assert (other.poster_kind, other.poster_key) == (None, None)


def test_the_universe_poster_keys_survive_the_category_move():
    """The design note's own watch item. Universe art is keyed by the LIST REF
    a definition carries (`UNIVERSE_CODES`, resolved inside the three generic
    list builders), never by the preset's tab -- so moving both packs into the
    `franchises` category cannot change which poster any of them gets. Pinned
    because 'franchise/' art is keyed by DISPLAY NAME and these collections
    must NOT start resolving through it."""
    from autoposter.collections.catalog import BY_KEY
    from autoposter.collections.default_images import UNIVERSE_CODES

    universes = BY_KEY["content_universes"]
    dc = BY_KEY["content_dc"]
    assert universes.category == "franchises"
    assert dc.category == "franchises"

    refs = [d.params["list"] for d in universes.definitions("Movie")]
    refs += [
        str(d.params["list"]) if "list" in d.params else str(d.params["id"])
        for d in dc.definitions("Movie")
    ]
    assert len(refs) == 11
    unmapped = [ref for ref in refs if ref not in UNIVERSE_CODES]
    assert unmapped == ["fa11en82/in-association-with-dc"]


# --- the collection-title composite (roadmap row 105) -------------------------
#
# Every test here drives a REAL reconciler -- ``reconcile_content_ratings`` or
# ``engine._separators`` through ``_separator_pass`` -- and never
# ``apply_poster`` or ``compose_collection_title`` in isolation. A helper test
# would prove the composite can be computed and say nothing about whether the
# shipped path uses it, which is exactly how a gated feature has twice passed
# its own tests in this tree.


def _enable_title(config):
    """Turn row 105's gate on, leaving every other knob at its shipped default."""
    config.collections.poster_title.enabled = True
    return config


def _poster_bytes(colour: str = "navy") -> bytes:
    """A 200x300 poster -- big enough that the composite really draws glyphs.

    This file's own ``_jpeg_bytes`` (:52-55) is **4x4**, and 4/2000 scales every
    box, offset and point size in this section to the module's 1px floor: the
    title box becomes 4x1 at 1pt, every test here would emit the clamp WARNING,
    and ``uploaded[0] != data`` would go green because the module re-encodes at
    ``quality=100, subsampling=0`` while the fixture was saved at Pillow's
    default 75 -- not because anything was drawn. At 200x300 the title block
    gets a 190x50 box at 10-25pt and the fixed line a 120x15 box at 4-9pt, which
    is real text and needs no clamping. Plain colour rather than noise, so "these
    pixels moved" is unambiguously the glyphs.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (200, 300), colour).save(buffer, format="JPEG", quality=100)
    return buffer.getvalue()


def _moved_pixels(before: bytes, after: bytes, top: int, bottom: int) -> int:
    """How many pixels between rows ``top`` and ``bottom`` moved.

    A count rather than a boolean so a test can say "the text band moved a great
    deal more than an untouched band did", which holds whether or not a flat
    region round-trips a JPEG re-encode exactly.
    """
    original = Image.open(io.BytesIO(before)).convert("RGB")
    composited = Image.open(io.BytesIO(after)).convert("RGB")
    assert original.size == composited.size
    return sum(
        original.getpixel((x, y)) != composited.getpixel((x, y))
        for y in range(top, bottom)
        for x in range(original.width)
    )


async def test_the_gate_off_uploads_the_fetched_bytes_untouched(
    session, config_factory, tmp_path
):
    """The default, and the whole no-storm argument: off is byte-identical."""
    section = RatingSection({"17"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    assert config.collections.poster_title.enabled is False
    data = _poster_bytes()

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert section._existing["Age 17+ Movies"].uploaded_bytes == [data]


async def test_the_gate_on_composites_the_title_onto_a_managed_poster(
    session, config_factory, tmp_path
):
    """Through the real reconciler, so the wiring is what is proven.

    And proven by PIXELS inside the text band rather than by
    ``uploaded[0] != data``: the module re-encodes at ``quality=100,
    subsampling=0``, so any call at all changes the bytes of a fixture saved at
    Pillow's default quality. "The bytes differ" would stay green with the
    drawing removed. What is asserted instead is that the bottom third moved a
    great deal and the top half did not.
    """
    section = RatingSection({"17"})
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    data = _poster_bytes()

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    uploaded = section._existing["Age 17+ Movies"].uploaded_bytes
    assert len(uploaded) == 1
    composited = Image.open(io.BytesIO(uploaded[0]))
    assert composited.format == "JPEG"
    assert composited.size == (200, 300)

    # gravity south, a +300 title offset and a +120 line offset, all at the
    # 200/2000 scale: the glyphs land in the bottom third and nowhere else.
    drawn = _moved_pixels(data, uploaded[0], 200, 300)
    untouched = _moved_pixels(data, uploaded[0], 0, 150)
    assert drawn > 100, "the composite drew nothing into the title band"
    assert drawn > 20 * untouched

    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 == hashlib.sha256(uploaded[0]).hexdigest()


async def test_the_gate_on_re_uploads_each_managed_poster_exactly_once(
    session, config_factory, tmp_path
):
    """Adjudication A-9's arithmetic, proven rather than asserted in prose.

    THE RULE, in two digests. The poster_title settings are PART of the bytes
    hashed into ``poster_sha256``, because the compose step sits ABOVE that
    digest (``posters.py:481``) -- so the gate-on pass uploads a poster whose
    bytes differ from the pre-gate upload. And they are PART of
    ``definition_hash`` too, as ``poster_title_parts``' suffix term -- so that
    pass is the very NEXT one, not whichever later pass happens to move the
    definition for some other reason. A-9 promised "gate-on re-uploads each
    managed poster once", instantly; this is the whole of it.

    NOTHING IS NULLED HERE, AND THAT IS THE TEST. An earlier revision of this
    plan reached ``apply_poster`` by setting ``row.poster_sha256 = None``
    between the passes. That would go green while proving only the caller's
    documented NULL-sha fall-through -- every caller short-circuits on
    ``definition_current and not (posters_on and record.poster_sha256 is
    None)`` (``reconcile.py:1124``, ``smart.py:375``, ``lists.py:305``), which
    this file's own
    ``test_a_third_pass_over_an_unchanged_collection_uploads_nothing`` (:389)
    already documents -- and would say nothing at all about the roll-out an
    operator actually gets. Pass 2 below reaches ``apply_poster`` because the
    gate flip moved ``definition_hash``, which IS the production roll-out. Drop
    the suffix term and this test goes red at ``len(uploaded) == 2``.

    Pass 3 settles at the CALLER's short-circuit, on both digests at once. The
    in-process determinism that makes the CONTENT compare settle too is pinned
    separately, by Task 1's ``test_the_same_inputs_give_byte_identical_output``;
    this test does not claim to prove it.
    """
    section = RatingSection({"17"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    data = _poster_bytes()

    # Pass 1, gate off: the fetched bytes go up untouched, and both digests
    # land -- the content one and the definition one.
    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    uploaded = section._existing["Age 17+ Movies"].uploaded_bytes
    assert uploaded == [data]
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    gate_off_definition = row.definition_hash
    assert gate_off_definition

    # The operator turns the gate on, and NOTHING else is touched: no sha is
    # nulled, no definition is edited, no row is deleted.
    _enable_title(config)

    # Pass 2, gate on: the definition hash moved, so this pass -- the very next
    # one -- reaches apply_poster. Exactly one more upload, and its pixels
    # differ.
    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 2, "the gate flip did not move definition_hash"
    assert row.definition_hash != gate_off_definition
    assert uploaded[1] != uploaded[0]
    assert _moved_pixels(uploaded[0], uploaded[1], 200, 300) > 100
    assert row.poster_sha256 == hashlib.sha256(uploaded[1]).hexdigest()

    # Pass 3, gate still on and both digests stored: nothing at all.
    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 2


async def test_a_changed_text_knob_re_uploads_each_managed_poster_exactly_once(
    session, config_factory, tmp_path
):
    """A-9's third property: an edit made while the gate is ALREADY on.

    Same two digests, same arithmetic. The knob is inside the model dump that
    ``poster_title_parts`` folds into ``definition_hash``, so the next pass
    reaches ``apply_poster``; and it changes the glyphs, so ``poster_sha256``
    moves and exactly one upload follows. The pass after that short-circuits.

    ``collection_line_text`` rather than a box size, so the assertion does not
    depend on a 200x300 fixture rendering two point sizes distinguishably: a
    different word is different glyphs at any size.
    """
    section = RatingSection({"17"})
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    data = _poster_bytes()

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    uploaded = section._existing["Age 17+ Movies"].uploaded_bytes
    assert len(uploaded) == 1

    config.collections.poster_title.collection_line_text = "SERIES"

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 2, "the knob did not move definition_hash"
    assert uploaded[1] != uploaded[0]

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 2


def test_the_gate_off_leaves_every_definition_hash_byte_identical(config_factory):
    """A-9's second property, on all three hash functions at once.

    ``config=None`` is not a contrivance: it is literally the signature every
    caller in the base suite still uses, so "equal to the ``config=None``
    payload" IS "equal to the digest already stored on the live server". What
    makes it byte-identical rather than merely equal-at-the-defaults is that
    ``poster_title_parts`` refuses on ``settings.enabled`` before it dumps
    anything -- so the boxes are retuned below with the gate off and the three
    digests do not move.

    ``separator_hash`` is absent on purpose: it never gets the term, because a
    divider is never captioned (A-3).

    Not driven through a reconciler because there is nothing wired to drive --
    these three functions ARE the mechanism, and Global Constraint 9's real
    entry point is covered by the two tests above, which reach them through
    ``reconcile_content_ratings``.
    """
    config = config_factory()
    assert config.collections.poster_title.enabled is False
    config.collections.poster_title.title.max_width = 111
    config.collections.poster_title.collection_line_text = "SET"

    bucket = Bucket(key="17", title="Age 17+ Movies", summary="s", values=("17",))
    assert definition_hash(bucket, None, "u", config) == definition_hash(bucket, None, "u")
    assert smart_definition_hash("u", "s", None, config) == smart_definition_hash("u", "s")
    assert _members_hash([], "s", "sync", None, config) == _members_hash([], "s", "sync")

    # And the gate ON moves all three, which is what makes the roll-out instant
    # rather than lazy.
    config.collections.poster_title.enabled = True
    assert definition_hash(bucket, None, "u", config) != definition_hash(bucket, None, "u")
    assert smart_definition_hash("u", "s", None, config) != smart_definition_hash("u", "s")
    assert _members_hash([], "s", "sync", None, config) != _members_hash([], "s", "sync")


async def test_an_operators_own_poster_file_passes_through_untouched(
    session, config_factory, tmp_path
):
    """Adjudication A-2. The local rung is also where api/manual.py's poster
    endpoint writes, so this covers the manual surface too: a file the operator
    supplied is theirs, and restyling it is a stronger claim than this service
    makes anywhere else."""
    section = RatingSection({"17"})
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    theirs = _poster_bytes("green")
    folder = tmp_path / "Movies" / "Age 17+ Movies"
    folder.mkdir(parents=True)
    (folder / "poster.jpg").write_bytes(theirs)

    async with _client(_refusing_handler()) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert section._existing["Age 17+ Movies"].uploaded_bytes == [theirs]


async def test_a_divider_is_never_captioned_twice(session, config_factory, tmp_path):
    """Adjudication A-3, on the kind rather than on the ordering.

    A divider's art already carries the group's name -- baked in by
    separator_art._render for the generated ones, and printed by upstream on
    the fetched ``separators/<style>/<stem>.jpg``. Compositing on top would
    print the title twice.
    """
    section = RatingSection(())
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    data = _poster_bytes()

    async with _client(_serving_handler(data, [])) as http:
        await _separator_pass(session, section, http, config)

    assert section._existing[SEPARATOR_TITLE].uploaded_bytes == [data]


async def test_a_font_that_resolves_nowhere_reports_a_skip_and_uploads_nothing(
    session, config_factory, tmp_path
):
    """Adjudication A-4's tail. The action names the FONT -- the string the
    operator wrote -- and nothing else; poster_sha256 is left NULL so the next
    pass retries, exactly as an unfetchable hosted default is."""
    section = RatingSection({"17"})
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    config.fonts_root = str(tmp_path / "fonts")
    config.collections.poster_title.title.font = "NoSuchFace.ttf"

    async with _client(_serving_handler(_poster_bytes(), [])) as http:
        actions = await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert section._existing["Age 17+ Movies"].uploaded_bytes == []
    assert any("NoSuchFace.ttf" in action for action in actions)
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None


async def test_a_font_refusal_on_an_existing_poster_is_retried_once_the_font_resolves(
    session, config_factory, tmp_path
):
    """review I-1: a refusal must not freeze ``definition_hash`` at ``wanted``.

    Unlike the fresh-collection case above, this collection already has a
    non-NULL ``poster_sha256`` when the font breaks -- the common shape on any
    live server, and the one ``test_a_font_that_resolves_nowhere_...`` cannot
    catch, because its NULL-sha fall-through rescues the retry regardless of
    whether ``definition_hash`` was correctly left stale.

    Pass 2 refuses with the font unresolved and the config UNCHANGED between
    passes 2 and 3 -- only the file appearing under ``fonts_root`` differs --
    so ``wanted`` is identical both times. If pass 2 had stamped
    ``definition_hash = wanted`` regardless of the refusal, pass 3 would
    short-circuit on ``definition_current`` forever, exactly as adjudicated:
    "the operator mounts the font. Nothing happens. Ever."
    """
    section = RatingSection({"17"})
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    config.fonts_root = str(tmp_path / "fonts")
    data = _poster_bytes()

    # Pass 1: a working (bundled) font composites and uploads once, so the
    # collection already carries a non-NULL poster_sha256 going into the
    # refusal -- the A-9 roll-out shape, not a fresh collection.
    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    uploaded = section._existing["Age 17+ Movies"].uploaded_bytes
    assert len(uploaded) == 1
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is not None

    # The operator points at a font that is not yet mounted -- a typo, or the
    # fonts volume is not there yet. definition_hash moves (the font name is
    # part of the suffix term), so this pass reaches apply_poster and refuses.
    config.collections.poster_title.title.font = "Colus-Regular.ttf"

    async with _client(_serving_handler(data, [])) as http:
        actions = await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 1, "the refusal must not upload anything"
    assert any("Colus-Regular.ttf" in action for action in actions)

    # The operator mounts the font -- nothing about the config changes, so
    # ``wanted`` is byte-identical to what pass 2 already computed. A bundled
    # face other than pass 1's default, so its glyphs -- and therefore the
    # composited digest -- differ from pass 1's upload: otherwise the
    # sha-compare at ``posters.py:551`` would correctly skip a re-upload of
    # identical pixels and this test would not be able to tell "skipped
    # because unchanged" from "skipped because never retried".
    fonts_dir = Path(config.fonts_root)
    fonts_dir.mkdir(parents=True, exist_ok=True)
    (fonts_dir / "Colus-Regular.ttf").write_bytes(INTER_BOLD.read_bytes())

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 2, "the mounted font was never retried"
    assert uploaded[1] != uploaded[0]
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 == hashlib.sha256(uploaded[1]).hexdigest()
