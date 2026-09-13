"""THE SMART-ENVELOPE ORACLE'S ASSERTIONS -- phase 9c's acceptance gate.

Sixteen configs. For each one, the ``POST /library/collections?...`` key Kometa
would issue to create the smart collection, produced by
``tests/oracle/9c/kometa_smart_collection.py`` and pinned below as data. Ours
must reproduce them byte for byte.

Fifteen of the sixteen are 9b's own configs, reused deliberately: their
``uri_args`` are already pinned in ``tests/test_collection_search_oracle.py``,
so anything that differs HERE is the envelope and nothing else. The sixteenth is
config 1's block with no ``sort_by`` at all, driven with
``default_sort="random"`` -- Kometa's ``smart_filter`` default
(modules/builder.py:1478), and the one behaviour 9c's builder has that
``plex_search`` does not.

## What the envelope adds, and why sixteen pins rather than one

``joinArgs`` encodes the whole ``uri`` VALUE with ``safe=''``. Every byte the 9b
query already encoded is encoded a second time: ``%3A`` -> ``%253A``, ``%2C`` ->
``%252C``, ``%20`` -> ``%2520``, ``%26`` -> ``%2526``; and ``!``, which 9b's
queries carry raw, becomes ``%21``. Which of those a config exercises depends
entirely on the config, so one pin would prove one encoding path.

| Config | The envelope byte it is the only pin for |
| --- | --- |
| 2, 12 | ``%253A`` -- a directional sort through both layers |
| 11 | ``%252C`` -- two sorts, joined |
| 8, 9 | ``%21`` -- a raw ``!`` in the query, encoded by the envelope alone |
| 10 | ``%2520`` and ``%2526`` -- a quoted string, quoted again |
| 15 | ``%21%253D`` -- ``!%3D`` through both layers |
| 16 | ``sort=random``, which only ``default_sort`` can produce |

## Do not edit a golden to make a test pass

If ours differs from a pinned string, ours is wrong. The strings came from the
driver, and ``test_the_smart_driver_still_produces_the_pinned_strings`` re-runs
it, so a driver that drifted from them is caught here too.
"""
import importlib.util
from pathlib import Path

import pytest

from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import build_search_url

SMART_DRIVER = Path(__file__).parent / "oracle" / "9c" / "kometa_smart_collection.py"
FILTER_DRIVER = Path(__file__).parent / "oracle" / "9b" / "kometa_build_filter.py"

# Shared BY VALUE with tests/test_collection_search_oracle.py and with the 9b
# driver, for the reason that file's docstring gives: the driver imports nothing
# from this repository and must not import anything into it either.
CHOICES = {
    ("content_rating", "PG-13"): ("5",),
    ("content_rating", "R"): ("7",),
    ("genre", "Horror"): ("1138",),
    ("genre", "Drama"): ("9",),
    ("resolution", "1080"): ("1080",),
    ("network", "HBO"): ("42",),
    ("audio_language", "en"): ("en",),
    ("audio_language", "es"): ("es-419", "es-MX", "spa"),
    ("label", "Overlay"): ("3",),
    ("collection", "The Fast and the Furious Collection"): ("77",),
    # search-tail E-2's level configs (17): the vendored driver's own copy,
    # tests/oracle/9b/kometa_build_filter.py:63.
    ("season_collection", "Specials"): ("301",),
}

MACHINE_IDENTIFIER = "abc123"
SECTION_KEY = "2"
TITLE = "Oracle Collection"
RATING_KEY = "12345"


def resolve(attribute, value, /):
    return CHOICES.get((attribute, value), ())


def load(path):
    """Load an oracle driver by PATH, never by import: they live outside the
    package and must stay unimportable from ``src``. The idiom is
    ``tests/test_collection_search_oracle.py``'s own."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# (id, libtype, params, default_sort) -- OUR spelling. The first fifteen are
# tests/test_collection_search_oracle.py's CONFIGS verbatim; config 7's ``2:30``
# is 9a's written duration form, which the driver is given as ``150``.
CONFIGS = [
    ("1-multi-value-tag", "movie", {"all": {"content_rating": ["PG-13", "R"]}}, None),
    ("2-any-base", "movie", {
        "any": {"studio": "A24", "year.gte": 2020},
        "sort_by": "critic_rating.desc", "limit": 25,
    }, None),
    ("3-list-nesting", "movie", {"all": {
        "content_rating": "PG-13",
        "any": [{"studio": "A24", "year.gte": 2020}, {"genre": "Horror"}],
    }}, None),
    ("4-mapping-nesting", "movie", {"all": {
        "year.gte": 2000, "all": {"studio": "A24", "critic_rating.gte": 8},
    }}, None),
    ("5-relative-dates", "movie", {"all": {
        "added": 30, "release.not": "6o", "last_played.not": "2y",
    }}, None),
    ("6-absolute-dates", "movie", {"all": {
        "release.after": "2000-01-01", "added.before": "12/25/2020",
    }}, None),
    ("7-duration", "movie", {"all": {"duration.gt": 90, "duration.lte": "2:30"}}, None),
    ("8-rated", "movie", {"all": {
        "critic_rating.rated": True, "audience_rating.rated": False,
    }}, None),
    ("9-booleans", "movie", {"all": {"unplayed": True, "progress": False}}, None),
    ("10-string-quoting", "movie", {"all": {
        "studio.begins": "Warner Bros",
        "studio.not": "Hallmark & Co",
        "studio.is": "A24",
    }}, None),
    ("11-several-sorts", "movie", {
        "all": {"year.gte": 2010},
        "sort_by": ["critic_rating.desc", "title.asc"], "limit": 100,
    }, None),
    ("12-show-rescoping", "show", {
        "all": {
            "genre": "Drama", "resolution": "1080", "audio_language": "en",
            "network": "HBO", "added.after": "2024-01-01",
        },
        "sort_by": "episode_added.desc", "limit": 10,
    }, None),
    ("13-language-expansion", "movie", {"all": {"audio_language": "es"}}, None),
    ("14-multi-value-under-any", "movie", {"any": {"content_rating": ["PG-13", "R"]}}, None),
    ("15-unreached-renders-and-rows", "movie", {"all": {
        "genre.not": "Horror",
        "studio.isnot": "A24",
        "studio.ends": "Pictures & Co",
        "label": "Overlay",
        "collection": "The Fast and the Furious Collection",
        "plays.gt": 3,
        "plays.lte": 10,
    }}, None),
    # 16: the ONLY thing a smart_filter does that a plex_search does not. No
    # ``sort_by`` is written, and Kometa's smart_filter call site passes
    # ``default_sort="random"`` (modules/builder.py:1478), so the query sorts
    # ``random`` rather than ``titleSort``. Config 1's block, unchanged, so the
    # diff against config 1's golden is exactly the sort and nothing else.
    ("16-random-default-sort", "movie", {"all": {"content_rating": ["PG-13", "R"]}}, "random"),
]

# The libtype key Kometa sends as ``type`` in BOTH the query and the POST args:
# 1 for movie, 2 for show, and -- since search-tail E-2 -- 3 for a season
# search and 4 for an episode one (modules/plex.py:779-787). Kometa reads it
# off ``build_filter``'s first return value, which is what the driver returns
# too, so this table is a restatement rather than a second decision.
SMART_TYPE = {"movie": 1, "show": 2, "season": 3, "episode": 4}

# KOMETA'S OWN ANSWERS, pinned as data. Produced by
# ``tests/oracle/9c/kometa_smart_collection.py`` wrapping
# ``tests/oracle/9b/kometa_build_filter.py``. Do not edit a string here to make
# a test pass: if ours differs, ours is wrong.
KOMETA_POST = {
    "1-multi-value-tag": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26contentRating%3D7",
    "2-any-base": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26limit%3D25%26sort%3Drating%253Adesc%26push%3D1%26studio%3DA24%26or%3D1%26year%253E%3D2020%26pop%3D1",
    "3-list-nesting": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26push%3D1%26studio%3DA24%26or%3D1%26year%253E%3D2020%26pop%3D1%26and%3D1%26push%3D1%26genre%3D1138%26pop%3D1",
    "4-mapping-nesting": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26year%253E%3D2000%26and%3D1%26push%3D1%26studio%3DA24%26and%3D1%26rating%253E%3D8.0%26pop%3D1",
    "5-relative-dates": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26addedAt%253E%253E%3D-30d%26and%3D1%26originallyAvailableAt%253C%253C%3D-6mon%26and%3D1%26lastViewedAt%253C%253C%3D-2y",
    "6-absolute-dates": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26originallyAvailableAt%253E%253E%3D2000-01-01%26and%3D1%26addedAt%253C%253C%3D2020-12-25",
    "7-duration": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26duration%253E%253E%3D5400000.0%26and%3D1%26duration%253C%3D9000000.0",
    "8-rated": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26rating%21%3D-1%26and%3D1%26audienceRating%3D-1",
    "9-booleans": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26unwatched%3D1%26and%3D1%26inProgress%21%3D1",
    "10-string-quoting": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26studio%253C%3DWarner%2520Bros%26and%3D1%26studio%21%3DHallmark%2520%2526%2520Co%26and%3D1%26studio%253D%3DA24",
    "11-several-sorts": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26limit%3D100%26sort%3Drating%253Adesc%252CtitleSort%26year%253E%3D2010",
    "12-show-rescoping": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=2&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D2%26limit%3D10%26sort%3Depisode.addedAt%253Adesc%26show.genre%3D9%26and%3D1%26episode.resolution%3D1080%26and%3D1%26episode.audioLanguage%3Den%26and%3D1%26show.network%3D42%26and%3D1%26show.addedAt%253E%253E%3D2024-01-01",
    "13-language-expansion": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26audioLanguage%3Des-419%26and%3D1%26audioLanguage%3Des-MX%26and%3D1%26audioLanguage%3Dspa",
    "14-multi-value-under-any": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26push%3D1%26contentRating%3D5%26or%3D1%26contentRating%3D7%26pop%3D1",
    "15-unreached-renders-and-rows": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26genre%21%3D1138%26and%3D1%26studio%21%253D%3DA24%26and%3D1%26studio%253E%3DPictures%2520%2526%2520Co%26and%3D1%26label%3D3%26and%3D1%26collection%3D77%26and%3D1%26viewCount%253E%253E%3D3%26and%3D1%26viewCount%253C%3D10",
    "16-random-default-sort": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3Drandom%26contentRating%3D5%26and%3D1%26contentRating%3D7",
}

# The UPDATE key, pinned for the two configs whose difference is the whole point
# of having a second shape at all: the same ``uri`` value, a different path, and
# no ``type``/``title``/``smart``/``sectionId`` at all.
KOMETA_PUT = {
    "1-multi-value-tag": "/library/collections/12345/items?uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26contentRating%3D7",
    "16-random-default-sort": "/library/collections/12345/items?uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3Drandom%26contentRating%3D5%26and%3D1%26contentRating%3D7",
}


def our_query(libtype, params, default_sort):
    """OUR query string for one config -- 9b's builder, with 9c's default sort.

    ``sort_by or (default_sort,)`` is the whole of the sort delta. The shipped
    builder writes it as that one expression
    (``smart_filter.SmartFilterBuilder.search_url``); this writes an EQUIVALENT
    of it in three statements, because the params here are a raw mapping rather
    than a validated ``PlexSearchParams`` and the spelling has to survive a
    string ``sort_by``. What this file proves is the sort the builder must
    produce, not the expression it produces it with -- the expression itself is
    pinned by ``tests/test_builder_smart_filter.py``'s two sort tests.
    """
    base = "all" if "all" in params else "any"
    group = parse_filters(params[base], field="params", searching=True, base=base)
    sort_by = params.get("sort_by") or ()
    if isinstance(sort_by, str):
        sort_by = [sort_by]
    if not sort_by and default_sort:
        sort_by = (default_sort,)
    return build_search_url(
        group, libtype=libtype, sort_by=sort_by,
        limit=params.get("limit"), resolve_tag=resolve,
    )


@pytest.mark.parametrize(
    ("name", "libtype", "params", "default_sort"), CONFIGS, ids=[c[0] for c in CONFIGS]
)
def test_the_envelope_is_byte_identical_to_kometas(name, libtype, params, default_sort):
    """OUR query, wrapped by the driver's envelope, against Kometa's own key.

    The envelope here is the DRIVER's, because the shipped writer's own test has
    not been written yet: this test's subject is the query reaching the envelope
    unchanged. ``test_the_create_post_is_byte_identical_to_the_oracles`` then asserts the
    SHIPPED envelope against these same pinned strings, which is what makes the
    pair a gate rather than a round trip.
    """
    driver = load(SMART_DRIVER)
    key = driver.create_smart_collection(
        TITLE, SMART_TYPE[libtype], our_query(libtype, params, default_sort),
        item_count=7,
    )
    assert key == KOMETA_POST[name]


@pytest.mark.parametrize("name", sorted(KOMETA_PUT), ids=sorted(KOMETA_PUT))
def test_the_update_key_is_byte_identical_to_kometas(name):
    driver = load(SMART_DRIVER)
    libtype, params, default_sort = next(
        (c[1], c[2], c[3]) for c in CONFIGS if c[0] == name
    )
    key = driver.update_smart_collection(
        RATING_KEY, our_query(libtype, params, default_sort), item_count=7,
    )
    assert key == KOMETA_PUT[name]


def test_the_smart_driver_still_produces_the_pinned_strings():
    """The goldens above are data, and the drivers that produced them are
    tracked, reviewable and editable -- so they can drift from them with nothing
    noticing. This runs BOTH drivers end to end, ours nowhere in sight, and is
    the only place ``default_sort="random"`` is exercised through Kometa's own
    ``build_filter`` rather than through our sort table.

    Both drivers import only the standard library, open no socket and read no
    clock on any path these configs reach, so this runs in-process rather than
    behind a marker: a skipped guard is not a guard.
    """
    smart = load(SMART_DRIVER)
    build = load(FILTER_DRIVER)
    kometa_configs = {
        name: (libtype, params, default_sort)
        for name, libtype, params, default_sort in CONFIGS
    }
    # Kometa's own spelling of config 7's second duration -- ``2:30`` is 9a's
    # written form and has no Kometa equivalent; the MINUTE VALUE is identical,
    # which is the point of the comparison.
    kometa_configs["7-duration"] = (
        "movie", {"all": {"duration.gt": 90, "duration.lte": 150}}, None,
    )
    for name, pinned in KOMETA_POST.items():
        libtype, params, default_sort = kometa_configs[name]
        type_key, uri_args = build.build_filter(
            "smart_filter", params, libtype, default_sort=default_sort
        )
        assert smart.create_smart_collection(
            TITLE, type_key, uri_args, item_count=7
        ) == pinned, name
        if name in KOMETA_PUT:
            assert smart.update_smart_collection(
                RATING_KEY, uri_args, item_count=7
            ) == KOMETA_PUT[name], name


def test_the_transcribed_joinArgs_matches_plexapis():
    """The driver transcribes plexapi's ``joinArgs`` rather than importing it,
    so the two are held equal here -- by value, on the exact argument dict the
    create path builds. Case-insensitive key ordering and value-only encoding
    would each survive a wrong transcription of the other, which is why the
    assertion is on the whole string.
    """
    from plexapi.utils import joinArgs

    driver = load(SMART_DRIVER)
    args = {
        "type": 1, "title": TITLE, "smart": 1, "sectionId": SECTION_KEY,
        "uri": driver.build_smart_filter("?type=1&sort=rating%3Adesc&studio=A24"),
    }
    assert driver.joinArgs(args) == joinArgs(args)


def test_the_transcribed_uri_root_matches_plexapis_shape():
    """``_uriRoot`` is a PRIVATE plexapi method the create path depends on.
    ``tests/test_plexapi_collection_contract.py`` pins that it still exists and
    still interpolates the machine identifier; this pins that the driver spells
    the same string, so an upstream change fails in both places rather than
    silently leaving the oracle describing a server nobody has.
    """
    import inspect

    from plexapi.server import PlexServer

    driver = load(SMART_DRIVER)
    source = inspect.getsource(PlexServer._uriRoot)
    assert "com.plexapp.plugins.library" in source
    # By VALUE against this file's own copy, not only against the driver's
    # constant: every golden below hard-codes ``abc123``, so a driver that
    # renamed its server would otherwise satisfy the line below while reddening
    # all eighteen pins for a reason none of them names.
    assert driver.MACHINE_IDENTIFIER == MACHINE_IDENTIFIER
    # And the section key, for the same reason: every golden hard-codes
    # ``/library/sections/2/``, and ``build_smart_filter`` falls back to the
    # driver's own ``SECTION_KEY`` when called with one argument.
    assert driver.SECTION_KEY == SECTION_KEY
    assert driver.uri_root() == (
        "server://%s/com.plexapp.plugins.library" % driver.MACHINE_IDENTIFIER
    )


def test_a_filter_matching_nothing_refuses_in_kometa_too():
    """This module adopts this verdict, so it is pinned rather than assumed. Both paths:
    create refuses unless ``ignore_blank_results`` is set, update refuses
    unconditionally -- which is why 9c refusing at both is the stricter reading
    of upstream and not a departure from it.
    """
    driver = load(SMART_DRIVER)
    # The wording is matched, not only the class: the driver's docstring calls
    # the message Kometa's verbatim "because the refusal wording is part of what
    # this oracle records" (upstream modules/plex.py:1584), and an unmatched
    # ``raises`` records nothing about it.
    with pytest.raises(driver.Failed, match="No items for smart filter"):
        driver.create_smart_collection(TITLE, 1, "?type=1&sort=titleSort&year=1900", 0)
    with pytest.raises(driver.Failed, match="No items for smart filter"):
        driver.update_smart_collection(RATING_KEY, "?type=1&sort=titleSort&year=1900", 0)
    # The escape hatch upstream offers and 9c refuses to offer, recorded so
    # the refusal reads as a decision rather than as something nobody noticed.
    assert driver.create_smart_collection(
        TITLE, 1, "?type=1&sort=titleSort&year=1900", 0, ignore_blank_results=True,
    ).startswith("/library/collections?")


def test_no_envelope_carries_a_token_or_includeCollections():
    """The two things that must never appear in a stored smart filter: a token
    (the uri is persisted BY PLEX and would outlive any rotation) and
    ``includeCollections``, which does not enrich a listing -- it MIXES
    Collection objects into the result set (roadmap Notes-for-9b item 1)."""
    for key in list(KOMETA_POST.values()) + list(KOMETA_PUT.values()):
        assert "X-Plex-Token" not in key
        assert "includeCollections" not in key


def test_config_16_differs_from_config_1_only_in_the_sort():
    """The default-sort case is worth pinning as a DIFFERENCE, not only as a
    string: config 16 is config 1's block with no ``sort_by``, so any byte that
    moves other than ``titleSort`` -> ``random`` is a defect in the parameter
    rather than in the sort table."""
    assert KOMETA_POST["1-multi-value-tag"].replace(
        "sort%3DtitleSort", "sort%3Drandom"
    ) == KOMETA_POST["16-random-default-sort"]


# (id, search_type, library_kind, params, default_sort) -- search-tail E-2's
# two, whose SEARCH type is not the library's kind. Separate from CONFIGS
# above so the sixteen pinned envelopes keep their shape exactly.
LEVEL_CONFIGS = [
    ("17-season-level-on-a-show", "season", "show",
     {"all": {"season_collection": "Specials"}}, None),
    ("18-episode-level-on-a-show", "episode", "show",
     {"all": {"episode_title.begins": "Pilot"}}, None),
]

KOMETA_POST_LEVELS = {
    "17-season-level-on-a-show": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=3&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D3%26sort%3Dseason.index%252Cseason.titleSort%26season.collection%3D301",
    "18-episode-level-on-a-show": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=4&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D4%26sort%3DtitleSort%26episode.title%253C%3DPilot",
}


@pytest.mark.parametrize(
    ("name", "search_type", "library_kind", "params", "default_sort"),
    LEVEL_CONFIGS, ids=[c[0] for c in LEVEL_CONFIGS],
)
def test_a_level_envelope_is_byte_identical_to_kometas(
    name, search_type, library_kind, params, default_sort
):
    """The smart collection's own ``type=`` byte, against Kometa's. This file
    already byte-pinned that byte for movie and show; search-tail E-2 makes it
    reachable for season and episode, and ``smart.py``'s
    ``COLLECTION_TYPES[...]`` is the shipped side of the same pin."""
    base = "all" if "all" in params else "any"
    group = parse_filters(params[base], field="params", searching=True, base=base)
    sort_by = (default_sort,) if default_sort else ()
    ours = build_search_url(
        group, libtype=library_kind, search_type=search_type,
        sort_by=sort_by, limit=params.get("limit"), resolve_tag=resolve,
    )
    driver = load(SMART_DRIVER)
    key = driver.create_smart_collection(
        TITLE, SMART_TYPE[search_type], ours, item_count=7,
    )
    assert key == KOMETA_POST_LEVELS[name]


def test_the_smart_driver_still_produces_the_pinned_level_strings():
    """Both drivers end to end, ours nowhere in sight -- the same guard
    ``test_the_smart_driver_still_produces_the_pinned_strings`` is, for the two
    configs whose search level is not their library kind."""
    smart = load(SMART_DRIVER)
    build = load(FILTER_DRIVER)
    for name, search_type, library_kind, params, default_sort in LEVEL_CONFIGS:
        type_key, uri_args = build.build_filter(
            "smart_filter", params, search_type,
            default_sort=default_sort, library_kind=library_kind,
        )
        assert smart.create_smart_collection(
            TITLE, type_key, uri_args, item_count=7
        ) == KOMETA_POST_LEVELS[name], name
