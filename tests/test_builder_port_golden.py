"""The golden gate for the builder-engine port of the three shipped sources.

Every action string the Common Sense buckets, the IMDb charts and the Oscars
collections produce was recorded -- with the resulting section state -- against
the code that shipped them, *before* any of it moved onto the builder engine
(``tests/fixtures/collections/golden_port.json``, captured at commit 4899ef1).
The port has to reproduce that file byte for byte. Anything else is a behaviour
change to 305 live collections, whether or not it looks like an improvement.

**One deliberate amendment.** The Common Sense family's write path
moved from plexapi's ``createCollection(smart=True, filters=...)`` onto the
raw-POST 9b grammar (roadmap row 185), so the ``filters`` cell -- which recorded
a plexapi CALL SHAPE and not an outcome -- changed for every Common Sense
collection in every scenario, from ``{"contentRating": ["G"]}`` to the POSTed
``uri``. That amendment was a deliberate, reviewed decision, it is the ONLY
cell that moved, it landed as its own reviewed commit, and it is graded by
something this fixture cannot reach: ``tests/test_collection_cs_equivalence.py``
proves the two grammars select the same items. Every other cell -- every action
string, summary, sort, label, member list and poster count -- is the original
capture, unchanged, and so is the separator's own ``filters`` cell: its write
path did not move.

**Second deliberate amendment, row 49 (separators).** The one hand-built
"Ratings Collections" divider became one divider per collection GROUP
(`collections/groups.py`), driven by the engine after the definitions rather
than from inside the Common Sense reconciler. So the charts and awards families
gained headings of their own, the Ratings heading moved to the end of the
pass's actions with them, and its own sort title took the content-ratings
group's section number (`!110_!` -> `!030_!`) -- ours, not Kometa's. Adjudicated
in advance, the ONLY cells that moved are separator ones, it landed as its own
reviewed commit,
and `tests/test_collection_group_separators.py` grades the behaviour this
fixture can only witness.

**Third deliberate amendment, row 49 (sort titles).** Every collection this
service manages now derives its group's `!<NNN>_` sort-title prefix, so the
`sort_title` cell moved from null to a derived string for every collection in
every APPLIED scenario, and the pass that wrote it reports one
`set the sort title of ...` action apiece. Adjudicated in advance,
and nothing else moved: every removed
line is a `"sort_title": null` cell, every added line is either that cell's
derived replacement or the action that wrote it, and no other cell -- summary,
sort, label, member list, filters, poster count -- differs in any scenario. It
landed as its own reviewed commit. The migration settling is visible here too:
`movies_apply_again`, the second unchanged pass, carries all twenty derived
cells and produces NOT ONE of those actions, which is the "exactly once" rule
captured as recorded behaviour rather than an assertion. What this fixture cannot reach --
that the value gets in out of band, that an explicit `sort_title` still wins,
and that expansion never inherits a derived one -- is graded by
`tests/test_collection_groups.py`.

**Fourth deliberate amendment (fence).** Every
scenario with separators on gained exactly one new collection: the closing
"Other Collections" fence divider (`!999_!Other Collections`), created after
the group dividers, adjudicated in advance. Its generated poster art is deliberately unreachable
here (the handler 404s the `@base` layer so generation fails before its
ImageMagick step, keeping this fixture environment-independent), so the
posters-on scenarios record `no poster source for 'Other Collections'`. The
ONLY changed lines are the fence's own actions and cells; the franchises
group's renumbering moved nothing (no scenario reaches section 050).
`tests/test_separator_art.py` and `tests/test_collection_groups.py`'s fence
tests grade the behaviour this fixture can only witness. It landed as its own
reviewed commit.

**Fifth deliberate amendment, roadmap row 252.** ``chart`` became a
``default_images`` family, so a proven-missing chart poster no longer reaches
``hosted_poster_url`` at all -- it fails inside ``ensure_default_image`` and
reports the same ``no poster source for`` sentence every other family kind
already used, with no URL attached. Adjudicated in advance -- "the sticky-miss
semantics the family entry implies... is accepted." Three
things moved, all consequences of the one cache. ``movies_posters_missing``'s
three chart lines (``IMDb Popular``, ``IMDb Top 250``, ``IMDb Lowest Rated``)
lost their URL, because every other kind in that scenario still carries its
URL and only ``chart`` left the hosted table.
``movies_posters_served``'s three chart cells (``golden_port.json:1322,1325,1328``)
changed from ``"...from the hosted default"`` to ``"...from the hosted default
image"``, because that is the family rung's own pre-existing label
(``posters.py:455``), unchanged by this amendment but now the one chart takes.
And that scenario's own ``assets_root`` (``:454``) stopped being shared with
scenario 6, because ``default_images._cache_paths`` keys the on-disk cache on
``(family, key)`` alone with no scenario dimension, so scenario 6's proven-404
``.miss`` markers for the three chart titles would otherwise leak into
scenario 7 -- which exists to exercise the serve-and-upload path for those
same three titles -- and report "no poster source" instead. Every other cell
in both scenarios is the original capture, unchanged. Unlike the first four
amendments, this one did not land as its own reviewed commit: the three cells
above are a consequence of the production change rather than a separable
decision, so they ride with it in the same commit.
`tests/test_collection_poster_apply.py`'s new cache-hit and miss-marker tests
grade the behaviour this fixture can only witness.

``_library_pass`` below is the one seam: it is the per-library sequence
``service.reconcile_libraries`` runs, and the port rewrites it from "the smart
reconciler, then ``build_all``" into the single engine call. Everything else in
this file -- the fakes, the fixtures, the scenarios, the recorded strings --
stays exactly as captured, so a diff can only come from the code under it.

Re-capture (only ever on the pre-port commit, or under a written adjudication
like the one above). It writes the fixture and then *fails*, so a capture run
can never be mistaken for a passing gate::

    docker compose -p 8at3 -f docker-compose.yml \
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
from plex_doubles import FakeSection as PlexSection

GOLDEN = Path("tests/fixtures/collections/golden_port.json")

AWARD_FIXTURE = Path("tests/fixtures/collections/ev0000003.yml").read_text(encoding="utf-8")
VALIDATION_FIXTURE = Path(
    "tests/fixtures/collections/event_validation.yml"
).read_text(encoding="utf-8")
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


class FakeSection(PlexSection):
    """The shared Plex double plus this file's own capture.

    A SMART create's ``uri`` is recorded into ``updated_filters``
    DELIBERATELY, and that is the one deliberate amendment to this harness:
    the cell used to hold a plexapi call shape and now holds the
    raw-POST evidence that replaced it. Same cell, same question ("what
    filter was this collection given"), different grammar. ``smart=0`` --
    the separator's blank POST -- is deliberately NOT recorded: its write
    path did not move, and its cell is the capture's.
    """

    collection_factory = FakeCollection

    def __init__(self, *, items=(), **kw):
        super().__init__(items=[FakeItem(key, guids) for key, guids in items], **kw)

    def _record_post(self, collection, args, key, method):
        if args.get("smart", ["0"])[0] == "1":
            collection.updated_filters = args["uri"][0]

    def _created_collection(self, title, items=None, filters=None, **kw):
        collection = self.collection_factory(
            title,
            rating_key=str(len(self._existing) + 1),
        )
        collection._live = list(items or [])
        collection._cache = list(items or [])
        collection.updated_filters = filters
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
            # Added when the award builders started checking the event id
            # against the dataset's own validation list -- a second file from
            # the same host, fetched before the event file. THE ONLY CHANGE to
            # this harness since capture, and it adds a route rather than
            # touching a recorded value: the Oscars collections' action
            # strings and section state below are the capture's, unchanged.
            if "event_validation" in url:
                return httpx.Response(200, text=VALIDATION_FIXTURE)
            if not awards_ok:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, text=AWARD_FIXTURE)
        if "Default-Images" in url:
            if "separators/@base/" in url:
                # The fence divider's generated art asks for the textless
                # layer; 404ing it here -- even in "served" mode -- keeps
                # generation refused BEFORE its magick step, so this fixture
                # never depends on an ImageMagick install.
                # tests/test_separator_art.py grades generation itself.
                return httpx.Response(404, text="not found")
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
    section = FakeSection(items=MOVIE_GUIDS, ratings=MOVIE_RATINGS)
    await record(
        "movies_dry_run", section, "Movies", "Movie",
        _config(config_factory, tmp_path, apply_to_plex=False), _handler(),
    )

    # 2. The same library for real, then 3. the unchanged second pass.
    section = FakeSection(items=MOVIE_GUIDS, ratings=MOVIE_RATINGS)
    config = _config(config_factory, tmp_path)
    await record("movies_apply", section, "Movies", "Movie", config, _handler())
    await record("movies_apply_again", section, "Movies", "Movie", config, _handler())

    # 4/5. A Show library: fewer charts, no awards, show-worded summaries.
    section = FakeSection(items=SHOW_GUIDS, ratings=SHOW_RATINGS, section_type="show")
    await record(
        "shows_dry_run", section, "TV Shows", "Show",
        _config(config_factory, tmp_path, apply_to_plex=False), _handler(),
    )
    section = FakeSection(items=SHOW_GUIDS, ratings=SHOW_RATINGS, section_type="show")
    await record(
        "shows_apply", section, "TV Shows", "Show",
        _config(config_factory, tmp_path), _handler(),
    )

    # 6. Posters on, every hosted default missing: the failure string carries
    # the URL, which is what pins each collection's poster kind and key --
    # except the chart three, whose miss is a family miss now (row 252) and
    # carries no URL at all.
    section = FakeSection(items=MOVIE_GUIDS, ratings=MOVIE_RATINGS)
    await record(
        "movies_posters_missing", section, "Poster Movies", "Movie",
        _config(config_factory, tmp_path, posters=True), _handler(posters="missing"),
    )

    # 7. Posters on and served: the upload path. A separate assets_root from
    # scenario 6, not a shared one -- since row 252, chart posters are cached
    # on disk by (family, key) alone, so reusing scenario 6's tmp_path would
    # have this scenario inherit ITS proven-404 ``.miss`` markers for the
    # three chart collections and report "no poster source" instead of
    # exercising the serve-and-upload path this scenario exists to cover.
    section = FakeSection(items=MOVIE_GUIDS, ratings=MOVIE_RATINGS)
    await record(
        "movies_posters_served", section, "Art Movies", "Movie",
        _config(config_factory, tmp_path / "posters_served", posters=True),
        _handler(posters="served"),
    )

    # 8. A dead chart fetcher must not touch its collections, nor stop the
    # award family from being built.
    section = FakeSection(items=MOVIE_GUIDS, ratings=MOVIE_RATINGS)
    await record(
        "movies_chart_source_down", section, "Broken Charts", "Movie",
        _config(config_factory, tmp_path), _handler(charts_ok=False),
    )

    # 9. ...and the same the other way around.
    section = FakeSection(items=MOVIE_GUIDS, ratings=MOVIE_RATINGS)
    await record(
        "movies_award_source_down", section, "Broken Awards", "Movie",
        _config(config_factory, tmp_path), _handler(awards_ok=False),
    )

    # 10. A library that owns none of the ids every source names.
    section = FakeSection(items=[("x1", ["imdb://tt9999999"])], ratings=MOVIE_RATINGS)
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


async def test_the_common_sense_family_writes_no_plexapi_filters_at_all(
    session, config_factory, tmp_path
):
    """The one cell the fixture's amendment is about, asserted directly rather
    than only through the recorded state: after the port no collection in any
    scenario is created or updated through plexapi's ``filters=``, so every
    ``filters`` cell in the fixture is a POSTed uri or None and never a dict."""
    recorded = await _scenarios(session, config_factory, tmp_path)
    for name, scenario in recorded.items():
        for title, state in scenario["state"].items():
            assert not isinstance(state["filters"], dict), (name, title)
