"""The golden gate for the builder-engine port of the three shipped sources.

Every action string the Common Sense buckets, the IMDb charts and the Oscars
collections produce was recorded -- with the resulting section state -- against
the code that shipped them, *before* any of it moved onto the builder engine
(``tests/fixtures/collections/golden_port.json``, captured at commit 4899ef1).
The port has to reproduce that file byte for byte. Anything else is a behaviour
change to 305 live collections, whether or not it looks like an improvement.

``_library_pass`` below is the one seam: it is the per-library sequence
``service.reconcile_libraries`` runs, and the port rewrites it from "the smart
reconciler, then ``build_all``" into the single engine call. Everything else in
this file -- the fakes, the fixtures, the scenarios, the recorded strings --
stays exactly as captured, so a diff can only come from the code under it.

Re-capture (only ever on the pre-port commit). It writes the fixture and then
*fails*, so a capture run can never be mistaken for a passing gate::

    docker compose -p 8at3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
        run --rm -e AUTOPOSTER_GOLDEN_CAPTURE=1 test \
        pytest -q tests/test_builder_port_golden.py

The scenarios deliberately cover both library types, dry-run and apply, a
second (unchanged) pass, both poster outcomes, a library that owns nothing, and
a dead source on each of the two fetchers -- the failure containment being the
property most easily lost in a rewrite.
"""
import io
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image
from plexapi.exceptions import NotFound

from autoposter.collections.engine import run_definitions
from autoposter.collections.sources import default_definitions

GOLDEN = Path("tests/fixtures/collections/golden_port.json")

AWARD_FIXTURE = Path("tests/fixtures/collections/ev0000003.yml").read_text(encoding="utf-8")
CHART_FIXTURE = Path("tests/fixtures/collections/imdb_chart.json").read_text(encoding="utf-8")

LABEL = "autoposter"

# The chart fixture's three ids, the 2026/2025/2024/2023 Oscars winners, and a
# tmdb-only item: enough for every collection to resolve something, while
# 2022's and 2021's winners are deliberately not owned so the "source returned
# no items" path is recorded too.
MOVIE_GUIDS = [
    ("m1", ["imdb://tt0111161", "tmdb://278"]),
    ("m2", ["imdb://tt0068646"]),
    ("m3", ["imdb://tt0468569"]),
    ("m4", ["imdb://tt31193180"]),
    ("m5", ["imdb://tt30144839"]),
    ("m6", ["imdb://tt1000002"]),
    ("m7", ["imdb://tt1000003"]),
    ("m8", ["imdb://tt2000001"]),
    ("m9", ["imdb://tt3000001"]),
    ("m10", ["tmdb://999"]),
]
SHOW_GUIDS = [
    ("s1", ["imdb://tt0111161"]),
    ("s2", ["imdb://tt0468569", "tvdb://81189"]),
]

MOVIE_RATINGS = ("G", "PG-13", "R", "TV-MA", "NR")
SHOW_RATINGS = ("TV-G", "TV-14", "TV-MA")


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, key, guids):
        self.ratingKey = key
        self.title = key
        self.guids = [FakeGuid(g) for g in guids]


class FakeChoice:
    def __init__(self, title):
        self.title = title


class FakeCollection:
    """One fake for both reconcilers.

    Mirrors the caching model the real code is written against: ``items()``
    returns a snapshot only ``reload()`` refreshes, and ``labels``/``fields``
    stay empty until then (``test_collection_lists.py`` and
    ``test_collection_separator.py`` have the same idiom, split across two
    fakes because no single test needed both).
    """

    def __init__(self, title, labels=(), rating_key="1", summary="", sort_title=""):
        self.title = title
        self.ratingKey = rating_key
        self.summary = summary
        self.titleSort = sort_title
        self._live = []
        self._cache = []
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": False})()]
        self._fields = []
        self.sort_set = None
        self.summary_set = None
        self.sort_title_set = None
        self.updated_filters = None
        self.uploaded = 0
        self.locks = 0
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
        self._cache = list(self._live)
        self._labels = self._real_labels
        self._fields = self._real_fields

    def items(self):
        return list(self._cache)

    def addItems(self, items):
        self._live.extend(items)

    def removeItems(self, items):
        removed = {i.ratingKey for i in items}
        self._live = [i for i in self._live if i.ratingKey not in removed]

    def moveItem(self, item, after=None):
        self._live = [i for i in self._live if i.ratingKey != item.ratingKey]
        if after is None:
            self._live.insert(0, item)
        else:
            position = [i.ratingKey for i in self._live].index(after.ratingKey)
            self._live.insert(position + 1, item)

    def sortUpdate(self, sort=None):
        self.sort_set = sort

    def updateFilters(self, libtype=None, limit=None, sort=None, filters=None, **kw):
        self.updated_filters = filters

    def editSummary(self, summary, locked=True):
        """Raises the way the live server does -- the section route plexapi
        takes 404s for collection summaries."""
        raise NotFound("(404) not_found; /library/sections/42/all?type=18")

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Stands in for ``server.query`` -- the item-level summary PUT."""
        self.summary_set = parse_qs(urlsplit(key).query)["summary.value"][0]
        self.summary = self.summary_set
        self._real_fields[0].locked = True

    def editSortTitle(self, sortTitle, locked=True):
        self.sort_title_set = sortTitle
        self.titleSort = sortTitle

    def addLabel(self, labels, locked=True):
        self._real_labels.append(type("L", (), {"tag": labels})())
        self._labels = self._real_labels

    def uploadPoster(self, filepath):
        with open(filepath, "rb") as handle:
            handle.read()
        self.uploaded += 1

    def lockPoster(self):
        self.locks += 1


class FakeSection:
    """Also stands in for ``section._server``: the separator is created with a
    raw POST against ``section._server.query``."""

    def __init__(self, items=(), ratings=(), section_type="movie"):
        self._items = [FakeItem(key, guids) for key, guids in items]
        self._ratings = list(ratings)
        self._existing: dict[str, FakeCollection] = {}
        self.key = "42"
        self.type = section_type
        self._server = self
        self._session = type("Sess", (), {"post": "POST-SENTINEL"})()

    def _uriRoot(self):
        return "server://FAKE-MACHINE-ID/com.plexapp.plugins.library"

    def all(self):
        return list(self._items)

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        title = parse_qs(urlsplit(key).query)["title"][0]
        self._existing[title] = FakeCollection(title, rating_key=str(len(self._existing) + 1))
        return None

    def collection(self, title):
        return self._existing[title]

    def listFilterChoices(self, field, libtype=None):
        return [FakeChoice(rating) for rating in self._ratings]

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, limit=None,
                         libtype=None, sort=None, filters=None, **kw):
        collection = FakeCollection(title, rating_key=str(len(self._existing) + 1))
        collection._live = list(items or [])
        collection._cache = list(items or [])
        collection.updated_filters = filters
        self._existing[title] = collection
        return collection


def _handler(charts_ok=True, awards_ok=True, posters="none"):
    """One MockTransport handler for all three outbound sources.

    ``posters``: ``"none"`` refuses (nothing may ask), ``"missing"`` 404s every
    hosted default, ``"served"`` answers with a real image.
    """
    image = _png_bytes() if posters == "served" else b""

    async def handle(request):
        url = str(request.url)
        if "graphql.imdb.com" in url:
            if not charts_ok:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, text=CHART_FIXTURE)
        if "IMDb-Awards" in url:
            if not awards_ok:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, text=AWARD_FIXTURE)
        if "Default-Images" in url:
            if posters == "missing":
                return httpx.Response(404, text="not found")
            if posters == "served":
                return httpx.Response(200, content=image)
        raise AssertionError("unexpected request: %s" % url)

    return httpx.MockTransport(handle)


def _config(config_factory, tmp_path, *, charts=True, awards=True, apply_to_plex=True,
            posters=False):
    config = config_factory(assets_root=str(tmp_path))
    config.collections.ownership_label = LABEL
    config.collections.charts = charts
    config.collections.awards = awards
    config.collections.separators = True
    config.collections.posters = posters
    config.collections.apply_to_plex = apply_to_plex
    config.collections.adopt = False
    config.collections.adopt_from = ["Kometa"]
    config.collections.adopt_removes_prior_label = False
    config.collections.protect_labels = []
    return config


async def _library_pass(session, section, library, library_type, config, http):
    """The per-library sequence ``service.reconcile_libraries`` runs.

    THE SEAM. Pre-port that sequence was the smart Common Sense reconciler
    followed by ``build_all``; the port replaced both with this one
    ``engine.run_definitions`` call over the same definitions -- which is
    exactly what ``reconcile_libraries`` now does per library. The recorded
    strings did not move.
    """
    return await run_definitions(
        session, section, library, library_type,
        [*default_definitions(config, library_type), *config.collections.definitions],
        config, http=http,
    )


def _state(section) -> dict:
    """What the pass left behind, beyond what it said it did.

    Action strings alone would not notice a collection created with the wrong
    sort, the wrong summary or the wrong members -- all of which the port could
    get wrong while still reporting "created %r with 3 item(s)".
    """
    return {
        title: {
            "summary": collection.summary_set,
            "sort": collection.sort_set,
            "sort_title": collection.sort_title_set,
            "filters": collection.updated_filters,
            "labels": [tag.tag for tag in collection._real_labels],
            "members": [str(item.ratingKey) for item in collection._live],
            "posters_uploaded": collection.uploaded,
        }
        for title, collection in section._existing.items()
    }


async def _scenarios(session, config_factory, tmp_path) -> dict:
    """Every recorded scenario, in order, sharing one session.

    Library *names* are what ``managed_collections`` is keyed on, so each
    scenario uses its own -- except the "again" pass, which deliberately reuses
    the previous library and section to exercise the unchanged-hash
    short-circuit.
    """
    recorded: dict[str, dict] = {}

    async def record(name, section, library, library_type, config, transport):
        async with httpx.AsyncClient(transport=transport) as http:
            actions = await _library_pass(
                session, section, library, library_type, config, http
            )
        recorded[name] = {"actions": actions, "state": _state(section)}

    # 1. A dry run over a Movie library: everything reported, nothing written.
    section = FakeSection(MOVIE_GUIDS, MOVIE_RATINGS)
    await record(
        "movies_dry_run", section, "Movies", "Movie",
        _config(config_factory, tmp_path, apply_to_plex=False), _handler(),
    )

    # 2. The same library for real, then 3. the unchanged second pass.
    section = FakeSection(MOVIE_GUIDS, MOVIE_RATINGS)
    config = _config(config_factory, tmp_path)
    await record("movies_apply", section, "Movies", "Movie", config, _handler())
    await record("movies_apply_again", section, "Movies", "Movie", config, _handler())

    # 4/5. A Show library: fewer charts, no awards, show-worded summaries.
    section = FakeSection(SHOW_GUIDS, SHOW_RATINGS, section_type="show")
    await record(
        "shows_dry_run", section, "TV Shows", "Show",
        _config(config_factory, tmp_path, apply_to_plex=False), _handler(),
    )
    section = FakeSection(SHOW_GUIDS, SHOW_RATINGS, section_type="show")
    await record(
        "shows_apply", section, "TV Shows", "Show",
        _config(config_factory, tmp_path), _handler(),
    )

    # 6. Posters on, every hosted default missing: the failure string carries
    # the URL, which is what pins each collection's poster kind and key.
    section = FakeSection(MOVIE_GUIDS, MOVIE_RATINGS)
    await record(
        "movies_posters_missing", section, "Poster Movies", "Movie",
        _config(config_factory, tmp_path, posters=True), _handler(posters="missing"),
    )

    # 7. Posters on and served: the upload path.
    section = FakeSection(MOVIE_GUIDS, MOVIE_RATINGS)
    await record(
        "movies_posters_served", section, "Art Movies", "Movie",
        _config(config_factory, tmp_path, posters=True), _handler(posters="served"),
    )

    # 8. A dead chart fetcher must not touch its collections, nor stop the
    # award family from being built.
    section = FakeSection(MOVIE_GUIDS, MOVIE_RATINGS)
    await record(
        "movies_chart_source_down", section, "Broken Charts", "Movie",
        _config(config_factory, tmp_path), _handler(charts_ok=False),
    )

    # 9. ...and the same the other way around.
    section = FakeSection(MOVIE_GUIDS, MOVIE_RATINGS)
    await record(
        "movies_award_source_down", section, "Broken Awards", "Movie",
        _config(config_factory, tmp_path), _handler(awards_ok=False),
    )

    # 10. A library that owns none of the ids every source names.
    section = FakeSection([("x1", ["imdb://tt9999999"])], MOVIE_RATINGS)
    await record(
        "movies_owning_nothing", section, "Sparse Movies", "Movie",
        _config(config_factory, tmp_path), _handler(),
    )

    return recorded


async def test_the_ported_sources_reproduce_the_recorded_run(
    session, config_factory, tmp_path
):
    """The gate. Byte-identical action strings and section state, before and
    after the three shipped sources became builders."""
    recorded = await _scenarios(session, config_factory, tmp_path)

    if os.environ.get("AUTOPOSTER_GOLDEN_CAPTURE"):
        # Only ever run on the pre-port commit -- see the module docstring.
        GOLDEN.write_bytes(
            (json.dumps(recorded, indent=2, sort_keys=False) + "\n").encode("utf-8")
        )
        # Fail, deliberately, rather than falling through to compare the file
        # against the run that just wrote it -- which would pass whatever the
        # code does. A capture run is not a test run, and the only way this
        # branch can be reached after the port is by accident: the failure is
        # what stops that accident being committed as a green gate.
        pytest.fail(
            "fixture recaptured -- this is a capture run, not a gate. Never "
            "commit a golden_port.json written from a post-port tree."
        )

    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert list(recorded) == list(expected)
    for name in expected:
        assert recorded[name]["actions"] == expected[name]["actions"], name
        assert recorded[name]["state"] == expected[name]["state"], name
