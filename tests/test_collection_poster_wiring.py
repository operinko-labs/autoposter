"""Wiring the poster machinery (Task 4) into the two reconcilers.

The safety property that matters most: a protected or unlabelled collection
must never have a poster applied. That falls out of ``apply_poster`` being
called after ``resolve_collision`` has approved the collection, not before
-- so the tests here assert it directly, on both the smart-collection path
(``reconcile.py``) and the list-collection path (``lists.py``).
"""
import io
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image
from plexapi.exceptions import NotFound
from sqlalchemy import select

from autoposter.collections import groups
from autoposter.collections.lists import reconcile_list_collection
from autoposter.collections.posters import DEFAULT_IMAGES_BASE, hosted_poster_url
from autoposter.collections.reconcile import reconcile_content_ratings
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection

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
