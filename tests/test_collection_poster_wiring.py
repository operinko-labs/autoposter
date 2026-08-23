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
from PIL import Image
from plexapi.exceptions import NotFound
from sqlalchemy import select

from autoposter.collections.lists import reconcile_list_collection
from autoposter.collections.posters import hosted_poster_url
from autoposter.collections.reconcile import SEPARATOR_TITLE, reconcile_content_ratings
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"


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

    def __init__(self, ratings=(), existing=(), section_type="movie"):
        self._ratings = list(ratings)
        self._existing = {c.title: c for c in existing}
        self.created = []
        self.key = "42"
        self.type = section_type
        self._server = self
        self._session = type("Sess", (), {"post": "POST-SENTINEL"})()

    def _uriRoot(self):
        return "server://FAKE-MACHINE-ID/com.plexapp.plugins.library"

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        title = parse_qs(urlsplit(key).query)["title"][0]
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
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            separators=True, http=http, config=config,
        )

    collection = section._existing[SEPARATOR_TITLE]
    assert collection.uploaded_bytes == [data]
    assert seen == [hosted_poster_url("separator", "content_rating")]


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
    data = _jpeg_bytes()
    seen = []
    async with _client(_serving_handler(data, seen)) as http:
        actions = await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert collection.uploaded_bytes == [data]
    assert collection.updated_filters is None  # the definition was left alone
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
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            separators=True, http=http, config=config,
        )

    collection = section._existing[SEPARATOR_TITLE]
    assert collection.uploaded_bytes == []

    config.collections.posters = True
    data = _jpeg_bytes()
    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            separators=True, http=http, config=config,
        )

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
            dry_run=False, kind="award_year", key="2026", http=http, config=config,
        )

    collection = section._existing["Oscars Winners 2026"]
    assert collection.uploaded_bytes == [data]
    assert seen == [hosted_poster_url("award_year", "2026")]


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
