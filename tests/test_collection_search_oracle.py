"""THE KOMETA-STRING ORACLE -- phase 9b's acceptance.

``src/autoposter/collections/search_url.py`` is a *transcription* of Kometa's
``build_filter``, and the module's own docstring names why that is the
dangerous kind of guess: a search with the wrong meaning still loads, still
runs, and produces a full, plausible, wrong collection. Every other test in
this phase asserts the transcription against itself -- against a string
hand-derived from the same source, by the same reading, in the same sitting.

This file asserts it against Kometa. Fourteen configs, and fourteen URI strings
produced by **Kometa's own build_filter** -- fetched, transcribed standalone,
run, and pinned below as data. Ours must reproduce them byte for byte.

## Where Kometa's strings came from

| What | Source (fetched 2026-08-26) |
| --- | --- |
| The assembly | ``modules/builder.py`` -- ``build_filter`` (:4092-4295), ``validate_attribute`` (:4297-4456), ``date_attributes`` (:469) |
| The translation tables | ``modules/plex.py`` -- ``search_translation`` (:60-138), ``show_translation`` (:168-193), ``modifier_translation`` (:195), ``method_alias``/``modifier_alias``/``date_sub_mods`` (:234-307), ``movie_only_searches`` (:430-445), ``show_only_searches`` (:446-506), the category lists and ``searches`` (:507-601), ``movie_sorts``/``show_sorts`` (:604-667), ``sort_types`` (:779-787), ``split`` (:2735-2751) |
| The value coercions | ``modules/util.py`` -- ``get_list`` (:259-286), ``validate_date`` (:299-307), ``check_int`` (:859-879), ``parse`` (:911-1034); ``modules/request.py`` -- ``quote`` (:37-38) |
| The version | ``VERSION`` -- 2.4.8 |

All from ``https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8``. The
driver is ``tests/oracle/9b/kometa_build_filter.py`` -- under ``tests/``, not
beside 9a's under ``.superpowers/``, because this test READS it and
``.superpowers/`` is gitignored; a file an assertion depends on has to be in
the checkout that runs the assertion. Its raw run is in
``.superpowers/sdd/task-3-report.md``. It imports **nothing** from this
repository -- and, unlike 9a's oracle, nothing from plexapi either: this half of
Kometa reads a config dict and a tag vocabulary and writes a string, and there
is no Plex object anywhere in it. Every removal it makes is marked
``# REMOVED:`` in the driver with what and why.

## What the transcription models rather than copies

**The library's tag vocabulary.** Kometa turns a written ``Horror`` into the
opaque key Plex knows it by via ``get_search_choices`` (plex.py:1300-1316), and
a language code into every locale variant the library carries via
``get_language_search_values`` (:1321-1344). Both are network reads. The driver
replaces them with the fixed ``CHOICES`` below, in the same
``[(written, key), ...]`` shape ``validate_attribute`` produces under
``plex_search=True``. Pinning the mapping is what makes this comparison about
TRANSLATION rather than about one server's contents -- and it is also why
``13-language-expansion`` can assert the multi-term shape without a Plex.

## What the first run found

Green, in all thirteen, on the first run -- and the six configs the plan had
also hand-derived from the source (1, 2, 3, 5, 7 and 12) agree with the driver
exactly, so there is no adjudication to record. That is a weaker result than
9a's four-way disagreement only in the sense that nothing had to be fixed; the
falsifiability proof in the Task 3 report is what shows the gate can fail.

## The fourteenth config

Added after the Task 3 review, which found the one coverage hole in the
original thirteen: configs 1 and 13 pin a multi-term join under ``all`` and no
config pinned one under ``any``, so a renderer hard-coding ``and=1&`` between
the terms of a single written key passed every oracle case. Config 14 is that
case and nothing else, and its golden came from the same driver in the same
way.
"""
from pathlib import Path

import pytest

from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import build_search_url

ORACLE_DRIVER = Path(__file__).parent / "oracle" / "9b" / "kometa_build_filter.py"

# The library's tag vocabulary. A COPY of the oracle driver's ``CHOICES``,
# shared by value and not by import, because the driver imports nothing from
# this repository and must not import anything into it either. The next test
# holds the two copies equal.
CHOICES = {
    ("content_rating", "PG-13"): ("5",),
    ("content_rating", "R"): ("7",),
    ("genre", "Horror"): ("1138",),
    ("genre", "Drama"): ("9",),
    ("resolution", "1080"): ("1080",),
    ("network", "HBO"): ("42",),
    ("audio_language", "en"): ("en",),
    ("audio_language", "es"): ("es-419", "es-MX", "spa"),
}


def resolve(attribute, value, /):
    return CHOICES.get((attribute, value), ())


# (id, libtype, params) -- OUR spelling. Config 7 is the one place the two
# spellings differ: ``2:30`` is 9a's written duration form and Kometa has no
# equivalent, so the oracle is driven with the same value written ``150``.
CONFIGS = [
    ("1-multi-value-tag", "movie", {"all": {"content_rating": ["PG-13", "R"]}}),
    ("2-any-base", "movie", {
        "any": {"studio": "A24", "year.gte": 2020},
        "sort_by": "critic_rating.desc", "limit": 25,
    }),
    ("3-list-nesting", "movie", {"all": {
        "content_rating": "PG-13",
        "any": [{"studio": "A24", "year.gte": 2020}, {"genre": "Horror"}],
    }}),
    ("4-mapping-nesting", "movie", {"all": {
        "year.gte": 2000, "all": {"studio": "A24", "critic_rating.gte": 8},
    }}),
    ("5-relative-dates", "movie", {"all": {
        "added": 30, "release.not": "6o", "last_played.not": "2y",
    }}),
    ("6-absolute-dates", "movie", {"all": {
        "release.after": "2000-01-01", "added.before": "12/25/2020",
    }}),
    ("7-duration", "movie", {"all": {"duration.gt": 90, "duration.lte": "2:30"}}),
    ("8-rated", "movie", {"all": {
        "critic_rating.rated": True, "audience_rating.rated": False,
    }}),
    ("9-booleans", "movie", {"all": {"unplayed": True, "progress": False}}),
    ("10-string-quoting", "movie", {"all": {
        "studio.begins": "Warner Bros",
        "studio.not": "Hallmark & Co",
        "studio.is": "A24",
    }}),
    ("11-several-sorts", "movie", {
        "all": {"year.gte": 2010},
        "sort_by": ["critic_rating.desc", "title.asc"], "limit": 100,
    }),
    ("12-show-rescoping", "show", {
        "all": {
            "genre": "Drama", "resolution": "1080", "audio_language": "en",
            "network": "HBO", "added.after": "2024-01-01",
        },
        "sort_by": "episode_added.desc", "limit": 10,
    }),
    ("13-language-expansion", "movie", {"all": {"audio_language": "es"}}),
    ("14-multi-value-under-any", "movie", {"any": {"content_rating": ["PG-13", "R"]}}),
]

# KOMETA'S OWN ANSWERS, pinned as data. Produced by
# ``tests/oracle/9b/kometa_build_filter.py`` -- Kometa v2.4.8's
# ``build_filter``, transcribed standalone, importing nothing from this
# repository. The raw run is in the Task 3 report (thirteen) and the Task 4
# report (the fourteenth). Do not edit a string here to make a test pass: if
# ours differs, ours is wrong.
KOMETA = {
    "1-multi-value-tag": "?type=1&sort=titleSort&contentRating=5&and=1&contentRating=7",
    "2-any-base": "?type=1&limit=25&sort=rating%3Adesc&push=1&studio=A24&or=1&year%3E=2020&pop=1",
    "3-list-nesting": "?type=1&sort=titleSort&contentRating=5&and=1&push=1&studio=A24&or=1&year%3E=2020&pop=1&and=1&push=1&genre=1138&pop=1",
    "4-mapping-nesting": "?type=1&sort=titleSort&year%3E=2000&and=1&push=1&studio=A24&and=1&rating%3E=8.0&pop=1",
    "5-relative-dates": "?type=1&sort=titleSort&addedAt%3E%3E=-30d&and=1&originallyAvailableAt%3C%3C=-6mon&and=1&lastViewedAt%3C%3C=-2y",
    "6-absolute-dates": "?type=1&sort=titleSort&originallyAvailableAt%3E%3E=2000-01-01&and=1&addedAt%3C%3C=2020-12-25",
    "7-duration": "?type=1&sort=titleSort&duration%3E%3E=5400000.0&and=1&duration%3C=9000000.0",
    "8-rated": "?type=1&sort=titleSort&rating!=-1&and=1&audienceRating=-1",
    "9-booleans": "?type=1&sort=titleSort&unwatched=1&and=1&inProgress!=1",
    "10-string-quoting": "?type=1&sort=titleSort&studio%3C=Warner%20Bros&and=1&studio!=Hallmark%20%26%20Co&and=1&studio%3D=A24",
    "11-several-sorts": "?type=1&limit=100&sort=rating%3Adesc%2CtitleSort&year%3E=2010",
    "12-show-rescoping": "?type=2&limit=10&sort=episode.addedAt%3Adesc&show.genre=9&and=1&episode.resolution=1080&and=1&episode.audioLanguage=en&and=1&show.network=42&and=1&show.addedAt%3E%3E=2024-01-01",
    "13-language-expansion": "?type=1&sort=titleSort&audioLanguage=es-419&and=1&audioLanguage=es-MX&and=1&audioLanguage=spa",
    "14-multi-value-under-any": "?type=1&sort=titleSort&push=1&contentRating=5&or=1&contentRating=7&pop=1",
}


@pytest.mark.parametrize(("name", "libtype", "params"), CONFIGS, ids=[c[0] for c in CONFIGS])
def test_our_url_is_byte_identical_to_kometas(name, libtype, params):
    base = "all" if "all" in params else "any"
    group = parse_filters(params[base], field="params", searching=True, base=base)
    sort_by = params.get("sort_by") or ()
    if isinstance(sort_by, str):
        sort_by = [sort_by]
    ours = build_search_url(
        group,
        libtype=libtype,
        sort_by=sort_by,
        limit=params.get("limit"),
        resolve_tag=resolve,
    )
    assert ours == KOMETA[name]


def test_the_oracles_vocabulary_fixture_matches_this_files_copy():
    """The driver imports nothing from here and this file imports nothing from
    there, so the shared fixture is shared by VALUE. This reads the driver as
    text and compares the literal, which is the only coupling that does not
    break the isolation.

    Anchored to ``__file__`` rather than to the working directory: what this
    test guarantees is only worth having if it cannot quietly stop applying,
    and a CWD-relative path turns "run pytest from somewhere else" into a
    vanished assertion. It is also why the driver lives under ``tests/`` at all
    -- ``.superpowers/`` is gitignored, so a driver there would be absent from
    a fresh clone and this test would fail (or, worse, be made to skip) for a
    reason that has nothing to do with the transcription.
    """
    import ast

    source = ORACLE_DRIVER.read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "CHOICES":
            assert ast.literal_eval(node.value) == CHOICES
            return
    pytest.fail("the oracle driver has no CHOICES literal")


def test_the_driver_still_produces_the_pinned_strings():
    """The goldens above are data, and the driver that produced them is now
    tracked, reviewable and editable -- so it can drift from them with nothing
    noticing, which is a smaller version of the argument that moved it under
    ``tests/`` in the first place.

    Running the driver HERE does not violate "never compare our builder against
    the driver at test time": the builder's assertion above still runs against
    the pinned text, and this one never touches the builder. Run in-process
    rather than marked slow or deferred to a container step, because the driver
    imports only the standard library, opens no socket, reads no clock on any
    path these configs reach (``datetime.now()`` is behind ``current_year`` and
    ``today``, which no config writes) and finishes in milliseconds -- a marker
    would be cost with no saving, and a skipped guard is not a guard.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("kometa_oracle_driver", ORACLE_DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    for index, ((libtype, plex_filter), (name, pinned)) in enumerate(
        zip(driver.CONFIGS, KOMETA.items(), strict=True), start=1
    ):
        assert name.startswith(f"{index}-"), f"{name} is not config {index}"
        _, url = driver.build_filter("plex_search", plex_filter, libtype)
        assert url == pinned, name


def test_every_oracle_url_is_free_of_includeCollections():
    for url in KOMETA.values():
        assert "includeCollections" not in url


def test_the_configs_cover_every_shipped_value_type():
    """Coverage, asserted rather than claimed. If a later task adds a value
    type to the table, this fails until a config exercises it through the
    oracle."""
    from autoposter.collections.filters import FILTER_ATTRIBUTES

    exercised = set()
    for _, _, params in CONFIGS:
        base = "all" if "all" in params else "any"
        stack = [params[base]]
        while stack:
            block = stack.pop()
            for key, value in block.items():
                if key in ("any", "all"):
                    stack.extend(value if isinstance(value, list) else [value])
                    continue
                name = key.split(".")[0]
                exercised.add(next(r.type for r in FILTER_ATTRIBUTES if r.name == name))
    assert exercised == {"tag", "str", "int", "float", "date", "duration", "bool"}
