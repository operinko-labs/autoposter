"""THE KOMETA-STRING ORACLE -- phase 9b's acceptance.

``src/autoposter/collections/search_url.py`` is a *transcription* of Kometa's
``build_filter``, and the module's own docstring names why that is the
dangerous kind of guess: a search with the wrong meaning still loads, still
runs, and produces a full, plausible, wrong collection. Every other test in
this phase asserts the transcription against itself -- against a string
hand-derived from the same source, by the same reading, in the same sitting.

This file asserts it against Kometa. Twenty-four configs, and twenty-four URI
strings produced by **Kometa's own build_filter** -- fetched, transcribed
standalone, run, and pinned below as data. Ours must reproduce them byte for
byte.

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

## The fifteenth config

Added after the whole-branch review, which enumerated the v1 surface against
these goldens and the live probe's builder-built queries and found four render
classes and three rows that neither gate ever reached -- so they were held only
by strings hand-derived from the same reading of the same source, which is the
self-agreement this file exists to escape.

| Never exercised before config 15 | Why it hid |
| --- | --- |
| ``(str, isnot)`` | ``!%3D`` is the only modifier wire string absent from every golden |
| ``(str, ends)`` | shares ``%3E`` with ``.gte``, but had never been pinned on a STRING value |
| ``(tag, not)`` | ``!`` against a RESOLVED key, not against a written word |
| plain-number ``gt``/``lte`` | config 7 pins both on ``duration``, whose renderer multiplies by 60000 and emits a float |
| the ``label``, ``collection`` and ``plays`` rows | no config and no probe query named them |

Config 15 closes all seven at once, and its golden came from the same driver in
the same way. The review had independently rendered each fragment against the
fetched upstream tables and found them correct -- so this config changed no
behaviour; it moved seven facts from "we agree with ourselves" to "Kometa said
so".

## The sixteenth and seventeenth configs

Phase 10a added two rows to the table -- ``decade`` (roadmap row 171) and
``country`` (row 174) -- and
``test_the_configs_cover_every_shipped_value_type`` only asserts that every
value TYPE is exercised, which both rows' types already were. So neither row
would have been reached by any golden: config 16 pins ``decade``'s plain-number
render beside a tag on the same movie query, and config 17 pins ``country``'s
show-library rescoping, which is the one thing about either row that a movie
config cannot reach. Both goldens came from the same driver in the same way.

## The eighteenth and nineteenth configs

Search-tails-1 (roadmap rows 170 + 172) added seven rows -- ``title``,
``edition`` and the five media booleans -- and, as with 16/17, no existing
golden would have reached any of them: ``test_the_configs_cover_every_shipped_
value_type`` covers TYPES, and ``str``/``bool`` were already exercised. The
two configs split by LIBTYPE so that all thirteen field-by-libtype cells are
pinned: config 18 is the movie column (the bare ``title`` field,
``editionTitle`` through search_translation, the five booleans' bare fields
with ``duplicate`` legal only here), config 19 the show column (``show.title``,
the doubly-translated ``show.editionTitle``, the episode-libtype
``hdr``/``dovi``/``trash``, and ``show.unmatched``). Both goldens came from
the same driver in the same way -- predicted from the transcription first,
then confirmed by running it.

## The twentieth and twenty-first configs

The phase-B person rows shipped with unit tests and a live-probe verdict but
never reached a golden: the coverage test covers TYPES and ``tag`` was
already exercised, so the four rows were held only by strings derived from
the same reading of the same source -- the self-agreement this file exists to
escape, flagged by the tails recon. Config 20 pins ``actor``'s show
rescoping (``show.actor``, the divergence phase B specifically corrected
mid-task); config 21 pins its bare movie field beside a movie-only crew row
(``director``). Two configs rather than one because ``director`` on a show
library is refused by Kometa's own movie_only_searches -- there is no legal
single config holding both halves. Both goldens came from the same driver in
the same way. Unlike 18/19 these rows already existed, so the gate's red was
manufactured: ``show_search_field=None`` on the ``actor`` row turns config 20
red (``actor=6`` where Kometa says ``show.actor=6``), which is exactly the
silent-wrong-set failure the config exists to catch.

## The twenty-second config

Search-tail E-1 (roadmap row 173, family E) added twenty rows, and as with
every batch since 16/17 no existing golden would have reached any of them:
``test_the_configs_cover_every_shipped_value_type`` covers TYPES, and every
type the twenty use was already exercised. All twenty are show-only, so ONE
show config holds them all -- there is no movie column to pin, and the movie
refusal is a unit test in ``tests/test_collection_search_url.py``. Config 22
pins every field-by-libtype cell of the family at the SHOW search level
(``type=2``): the five tag rows resolved to keys (one ``.not``), the string
row through ``.begins``, the three dates through the window / ``.after`` /
``.not`` forms, ``episode_plays`` as a plain-int range, the three floats
through ``.gte`` / ``.lt`` / ``.rated``, ``episode_year.gte``, and all six
booleans -- plus a limit and a show sort so the head is pinned with the body.
The golden came from the same driver in the same way -- predicted from the
transcription first, then confirmed by running it. Configs 23 and 24 are
search-tail E-2's, and they are the first two whose
SEARCH type is not the library's kind: a ``season``/``episode`` level against
a ``show`` library, which is what ``builder_level`` selects. They pin the two
halves of the split at once -- the ``type=`` byte and the default sort come
from the level, and ``season.collection``/``episode.title``/``show.unmatched``
come from the library -- and neither writes a ``sort_by``, because a show sort
under an episode search is a refusal (``search_sorts.require_sort_for_libtype``)
rather than a golden.

## The twenty-fifth and twenty-sixth configs

Roadmap row 176 -- `folder_location`, the one attribute in Kometa's search
grammar whose Plex FIELD is a run-time answer rather than a table entry
(`get_search_key`, `modules/plex.py:1286-1297`), restored in the driver's
call site for `get_search_key` at `kometa_build_filter.py:904-907` after being removed by name in 9b's D3. The
driver therefore grows a SECOND shared-by-value fixture, `FILTERS`, standing in
for `LibrarySection.listFilters` the way `CHOICES` stands in for
`get_search_choices`: `CHOICES` says what a filter's values are, `FILTERS` says
which filters exist at all. Config 25 pins the movie column (`location=<key>`
on this server -- the probe found the folder filter's key is `location`, not
`source`); config 26 pins the show column, which is the half a movie config
cannot reach -- Kometa forces the episode libtype on a show library and
returns the field PREFIXED `episode.`, and a transcription that kept the show
libtype would send a field Plex answers with nothing rather than with an
error.

## `current_year`, row 171's other half

Row 171 had two halves: the ``decade`` table row (configs 16/17, above) and
the ``current_year``/``current_year-N`` value grammar. Search-tails-2 Task 2
shipped the grammar in the ``filters:`` engine (``evaluate`` /
``_matches_one`` in ``filters.py``), citing this driver's own transcription
of Kometa's algorithm (``validate_attribute``'s year-attribute branch,
:768-788) as its source rather than an external line reference. At the time
this section was first written it did NOT wire ``current_year`` into a
``plex_search``'s rendering: ``year`` is searchable, but no config above
wrote ``current_year``, and ``search_url._arguments`` had no branch that
would resolve the sentinel (``_CurrentYear``) if one did -- it would render
the object's ``repr()`` into the query string.

That gap closed two commits later in the same branch (``e869a1a``):
``PlexSearchBuilder.build`` and ``SmartFilterBuilder.search_url`` now call
``filters.resolve_search_values`` against one captured moment BEFORE
handing the tree to ``build_search_url``, so a ``plex_search:`` or
``smart_filter:`` block writing ``year: current_year`` resolves to a real
year rather than a sentinel's ``repr()``. ``search_url.py`` itself still
never reads a clock -- the resolution happens one call earlier, in
``filters.py``, which is exactly what keeps this file's own byte-comparison
clock-free.

So the proof below is still not a CONFIGS/KOMETA pair, but the honest reason
is now different and weaker than "production code cannot answer it": a
``plex_search`` config naming ``year: current_year`` COULD now be pinned
against the driver at a captured moment, and a real CONFIGS/KOMETA pair is
buildable if desired -- this file simply has not grown one yet. Instead the
proof below runs the driver's OWN ``validate_attribute`` current-year
branch, at the real run moment, and checks it against our ``filters:``-
engine's resolution of the same value at the same moment -- Kometa's
transcribed algorithm as the oracle, rather than a second reading of the
same source.
"""
from pathlib import Path

import pytest

from autoposter.collections.filters import evaluate, parse_filters
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
    ("label", "Overlay"): ("3",),
    ("collection", "The Fast and the Furious Collection"): ("77",),
    ("country", "France"): ("36",),
    ("actor", "Uma Thurman"): ("6",),
    ("director", "Sofia Coppola"): ("58",),
    ("season_collection", "Specials"): ("301",),
    ("season_label", "Overlay"): ("3",),
    ("episode_collection", "Pilots"): ("302",),
    ("episode_label", "Overlay"): ("3",),
    ("episode_actor", "Uma Thurman"): ("6",),
    ("folder_location", "/mnt/media/Movies"): ("1",),
    ("folder_location", "/mnt/media/TV"): ("2",),
}

# The library's FILTER SCHEMA. A COPY of the oracle driver's ``FILTERS``, shared
# by value for the reason ``CHOICES`` is: the driver imports nothing from this
# repository and must not import anything into it. The next-but-one test holds
# the two copies equal, the same way and in the same place.
#
# ``CHOICES`` is what a filter's VALUES are; this is which filters EXIST. Row
# 176 is the only row that needs the second question asked, because it is the
# only row whose Plex field is not in ``FILTER_ATTRIBUTES``. The observed key
# on this server is ``location``, not ``source`` -- the probe's substitution
# rule (docs/research/plex-search-probe/listfilters-folder-location.md).
FILTERS = {
    "movie": (("genre", "Genre"), ("location", "Folder Location")),
    "show": (("genre", "Genre"),),
    "season": (),
    "episode": (("genre", "Genre"), ("location", "Folder Location")),
}


def resolve(attribute, value, /):
    return CHOICES.get((attribute, value), ())


def discover_field(attribute, libtype, /):
    """``TagResolver``'s third member, as a fixture.

    The production resolver answers this from a live ``listFilters``; here it is
    the fixture above, read the way ``get_search_key`` reads it -- the first
    filter matching either of Kometa's two clauses, ``episode.``-prefixed on a
    show library. Attached to ``resolve`` below rather than written as a class,
    because ``resolve`` is a function everywhere else in this file and a class
    here would change what the goldens are driven with.
    """
    filter_type = "episode" if libtype == "show" else libtype
    key = next(
        f[0] for f in FILTERS[filter_type]
        if f[0] == "source" or str(f[1]).lower().replace(" ", "_") == attribute
    )
    return f"episode.{key}" if libtype == "show" else key


resolve.discover_field = discover_field


# (id, search_type, library_kind, params) -- OUR spelling. Config 7 is the one
# place the two spellings differ: ``2:30`` is 9a's written duration form and
# Kometa has no equivalent, so the oracle is driven with the same value
# written ``150``. ``search_type`` and ``library_kind`` are the same string
# for every config but 23/24 (search-tail E-2), which is what those two pin.
CONFIGS = [
    ("1-multi-value-tag", "movie", "movie", {"all": {"content_rating": ["PG-13", "R"]}}),
    ("2-any-base", "movie", "movie", {
        "any": {"studio": "A24", "year.gte": 2020},
        "sort_by": "critic_rating.desc", "limit": 25,
    }),
    ("3-list-nesting", "movie", "movie", {"all": {
        "content_rating": "PG-13",
        "any": [{"studio": "A24", "year.gte": 2020}, {"genre": "Horror"}],
    }}),
    ("4-mapping-nesting", "movie", "movie", {"all": {
        "year.gte": 2000, "all": {"studio": "A24", "critic_rating.gte": 8},
    }}),
    ("5-relative-dates", "movie", "movie", {"all": {
        "added": 30, "release.not": "6o", "last_played.not": "2y",
    }}),
    ("6-absolute-dates", "movie", "movie", {"all": {
        "release.after": "2000-01-01", "added.before": "12/25/2020",
    }}),
    ("7-duration", "movie", "movie", {"all": {"duration.gt": 90, "duration.lte": "2:30"}}),
    ("8-rated", "movie", "movie", {"all": {
        "critic_rating.rated": True, "audience_rating.rated": False,
    }}),
    ("9-booleans", "movie", "movie", {"all": {"unplayed": True, "progress": False}}),
    ("10-string-quoting", "movie", "movie", {"all": {
        "studio.begins": "Warner Bros",
        "studio.not": "Hallmark & Co",
        "studio.is": "A24",
    }}),
    ("11-several-sorts", "movie", "movie", {
        "all": {"year.gte": 2010},
        "sort_by": ["critic_rating.desc", "title.asc"], "limit": 100,
    }),
    ("12-show-rescoping", "show", "show", {
        "all": {
            "genre": "Drama", "resolution": "1080", "audio_language": "en",
            "network": "HBO", "added.after": "2024-01-01",
        },
        "sort_by": "episode_added.desc", "limit": 10,
    }),
    ("13-language-expansion", "movie", "movie", {"all": {"audio_language": "es"}}),
    ("14-multi-value-under-any", "movie", "movie", {"any": {"content_rating": ["PG-13", "R"]}}),
    ("15-unreached-renders-and-rows", "movie", "movie", {"all": {
        "genre.not": "Horror",
        "studio.isnot": "A24",
        "studio.ends": "Pictures & Co",
        "label": "Overlay",
        "collection": "The Fast and the Furious Collection",
        "plays.gt": 3,
        "plays.lte": 10,
    }}),
    ("16-decade-and-country", "movie", "movie", {"all": {"decade": 1980, "country": "France"}}),
    ("17-country-on-a-show", "show", "show", {"all": {"country": "France"}}),
    ("18-the-tails-on-a-movie", "movie", "movie", {"all": {
        "title": "Dune",
        "edition.begins": "Director",
        "hdr": True,
        "dovi": False,
        "trash": False,
        "duplicate": True,
        "unmatched": False,
    }}),
    ("19-the-tails-on-a-show", "show", "show", {"all": {
        "title.isnot": "Dune",
        "edition.ends": "Cut",
        "hdr": True,
        "dovi": False,
        "trash": True,
        "unmatched": False,
    }}),
    ("20-actor-on-a-show", "show", "show", {"all": {"actor": "Uma Thurman"}}),
    ("21-people-on-a-movie", "movie", "movie", {"all": {
        "actor": "Uma Thurman", "director": "Sofia Coppola",
    }}),
    ("22-family-e-on-a-show", "show", "show", {
        "all": {
            "season_collection": "Specials",
            "season_label": "Overlay",
            "episode_collection": "Pilots",
            "episode_label.not": "Overlay",
            "episode_title.begins": "Pilot",
            "episode_actor": "Uma Thurman",
            "episode_added": 30,
            "episode_air_date.after": "2024-01-01",
            "episode_last_played.not": "2y",
            "episode_plays.gt": 3,
            "episode_user_rating.gte": 7,
            "episode_critic_rating.lt": 5,
            "episode_audience_rating.rated": True,
            "episode_year.gte": 2010,
            "episode_unplayed": True,
            "episode_duplicate": False,
            "episode_progress": True,
            "episode_unmatched": False,
            "show_unmatched": False,
            "unplayed_episodes": True,
        },
        "sort_by": "episode_added.desc", "limit": 5,
    }),
    ("23-season-level-on-a-show", "season", "show", {
        "all": {"season_collection": "Specials", "season_label": "Overlay"},
    }),
    ("24-episode-level-on-a-show", "episode", "show", {
        "all": {
            "episode_title.begins": "Pilot",
            "episode_added": 30,
            "episode_unplayed": True,
            "show_unmatched": False,
        },
        "limit": 5,
    }),
    ("25-folder-on-a-movie", "movie", "movie", {
        "all": {"folder_location": "/mnt/media/Movies"},
    }),
    ("26-folder-on-a-show", "show", "show", {
        "all": {"folder_location": "/mnt/media/TV"},
    }),
]

# KOMETA'S OWN ANSWERS, pinned as data. Produced by
# ``tests/oracle/9b/kometa_build_filter.py`` -- Kometa v2.4.8's
# ``build_filter``, transcribed standalone, importing nothing from this
# repository. The raw run is in the Task 3 report (thirteen), the Task 4
# report (the fourteenth), the Task 7 report (the fifteenth) and the Task 1
# report of phase 10a-1 (sixteen and seventeen, `decade` and `country` --
# predicted at Step 6 before the driver ran, then confirmed by it, same as the
# fifteen before them), search-tails-1's plan (eighteen and nineteen,
# predicted from the transcription, then confirmed by the driver) and
# search-tails-1's Task 2 report (twenty and twenty-one, the same way),
# search-tail E-1's plan (twenty-two, predicted from the transcription, then
# confirmed by the driver), and search-tail E-2's Task 2 (twenty-three and
# twenty-four, the season and episode levels, predicted then confirmed the
# same way). Do not edit a string here to make a test pass: if ours differs,
# ours is wrong.
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
    "15-unreached-renders-and-rows": "?type=1&sort=titleSort&genre!=1138&and=1&studio!%3D=A24&and=1&studio%3E=Pictures%20%26%20Co&and=1&label=3&and=1&collection=77&and=1&viewCount%3E%3E=3&and=1&viewCount%3C=10",
    "16-decade-and-country": "?type=1&sort=titleSort&decade=1980&and=1&country=36",
    "17-country-on-a-show": "?type=2&sort=titleSort&show.country=36",
    "18-the-tails-on-a-movie": "?type=1&sort=titleSort&title=Dune&and=1&editionTitle%3C=Director&and=1&hdr=1&and=1&dovi!=1&and=1&trash!=1&and=1&duplicate=1&and=1&unmatched!=1",
    "19-the-tails-on-a-show": "?type=2&sort=titleSort&show.title!%3D=Dune&and=1&show.editionTitle%3E=Cut&and=1&episode.hdr=1&and=1&episode.dovi!=1&and=1&episode.trash=1&and=1&show.unmatched!=1",
    "20-actor-on-a-show": "?type=2&sort=titleSort&show.actor=6",
    "21-people-on-a-movie": "?type=1&sort=titleSort&actor=6&and=1&director=58",
    "22-family-e-on-a-show": "?type=2&limit=5&sort=episode.addedAt%3Adesc&season.collection=301&and=1&season.label=3&and=1&episode.collection=302&and=1&episode.label!=3&and=1&episode.title%3C=Pilot&and=1&episode.actor=6&and=1&episode.addedAt%3E%3E=-30d&and=1&episode.originallyAvailableAt%3E%3E=2024-01-01&and=1&episode.lastViewedAt%3C%3C=-2y&and=1&episode.viewCount%3E%3E=3&and=1&episode.userRating%3E=7.0&and=1&episode.rating%3C%3C=5.0&and=1&episode.audienceRating!=-1&and=1&episode.year%3E=2010&and=1&episode.unwatched=1&and=1&episode.duplicate!=1&and=1&episode.inProgress=1&and=1&episode.unmatched!=1&and=1&show.unmatched!=1&and=1&show.unwatchedLeaves=1",
    "23-season-level-on-a-show": "?type=3&sort=season.index%2Cseason.titleSort&season.collection=301&and=1&season.label=3",
    "24-episode-level-on-a-show": "?type=4&limit=5&sort=titleSort&episode.title%3C=Pilot&and=1&episode.addedAt%3E%3E=-30d&and=1&episode.unwatched=1&and=1&show.unmatched!=1",
    "25-folder-on-a-movie": "?type=1&sort=titleSort&location=1",
    "26-folder-on-a-show": "?type=2&sort=titleSort&episode.location=2",
}


@pytest.mark.parametrize(
    ("name", "search_type", "library_kind", "params"), CONFIGS, ids=[c[0] for c in CONFIGS]
)
def test_our_url_is_byte_identical_to_kometas(name, search_type, library_kind, params):
    base = "all" if "all" in params else "any"
    group = parse_filters(params[base], field="params", searching=True, base=base)
    sort_by = params.get("sort_by") or ()
    if isinstance(sort_by, str):
        sort_by = [sort_by]
    ours = build_search_url(
        group,
        libtype=library_kind,
        search_type=search_type,
        sort_by=sort_by,
        limit=params.get("limit"),
        resolve_tag=resolve,
    )
    assert ours == KOMETA[name]


def test_the_oracles_vocabulary_fixtures_match_this_files_copies():
    """The driver imports nothing from here and this file imports nothing from
    there, so the shared fixtures are shared by VALUE. This reads the driver as
    text and compares the literals, which is the only coupling that does not
    break the isolation.

    Anchored to ``__file__`` rather than to the working directory: what this
    test guarantees is only worth having if it cannot quietly stop applying,
    and a CWD-relative path turns "run pytest from somewhere else" into a
    vanished assertion. It is also why the driver lives under ``tests/`` at all
    -- ``.superpowers/`` is gitignored, so a driver there would be absent from
    a fresh clone and this test would fail (or, worse, be made to skip) for a
    reason that has nothing to do with the transcription.

    TWO fixtures since roadmap row 176, checked the same way and in one pass:
    ``CHOICES`` (what a filter's values are) and ``FILTERS`` (which filters the
    library has at all). A row whose field is discovered at run time needs the
    second question asked, and a second fixture drifting silently is the same
    failure as the first one drifting.
    """
    import ast

    expected = {"CHOICES": CHOICES, "FILTERS": FILTERS}
    found = {}
    tree = ast.parse(ORACLE_DRIVER.read_text())
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in expected
        ):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    assert found == expected, "the oracle driver's shared fixtures have drifted"


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

    for index, ((sort_type, library_kind, plex_filter), (name, pinned)) in enumerate(
        zip(driver.CONFIGS, KOMETA.items(), strict=True), start=1
    ):
        assert name.startswith(f"{index}-"), f"{name} is not config {index}"
        _, url = driver.build_filter(
            "plex_search", plex_filter, sort_type, library_kind=library_kind
        )
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
    for _, _, _, params in CONFIGS:
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


def test_current_year_matches_kometas_own_transcribed_algorithm():
    """Row 171's second half (see the module docstring). Runs the driver's
    OWN ``validate_attribute`` current-year branch -- Kometa's algorithm,
    transcribed, not re-derived -- at the real run moment, and checks it
    against a ``filters:`` block using the same value, evaluated at the same
    moment. This is not byte comparison against a pinned string (there is no
    plex_search rendering to pin, per the docstring above); it is the same
    "ours must reproduce Kometa's" claim this whole file exists to make,
    against the one Kometa source this task has -- the vendored driver.

    Reads the real clock rather than a pinned one, because the driver's own
    ``validate_attribute`` does (``datetime.now().year``, unconditionally,
    with no injectable clock) -- so a pinned ``NOW`` on our side would be
    comparing against a moment the driver never used. Both sides read the
    same captured ``moment`` within this test, which is the same reasoning
    ``test_current_year_resolves_against_the_run_moment_not_the_parse_moment``
    (``tests/test_collection_filters.py``) already relies on for
    determinism within a single evaluation.
    """
    import datetime as dt
    import importlib.util

    spec = importlib.util.spec_from_file_location("kometa_oracle_driver_current_year", ORACLE_DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    moment = dt.datetime.now()
    kometa_bare = driver.validate_attribute("year", "", "year", "current_year")
    kometa_offset = driver.validate_attribute("year", "", "year", "current_year-5")
    assert kometa_bare == [moment.year]
    assert kometa_offset == [moment.year - 5]

    bare = parse_filters({"year": "current_year"})
    offset = parse_filters({"year": "current_year-5"})
    assert evaluate(bare, {"year": moment.year}, now=moment) is True
    assert evaluate(bare, {"year": moment.year - 1}, now=moment) is False
    assert evaluate(offset, {"year": moment.year - 5}, now=moment) is True
    assert evaluate(offset, {"year": moment.year}, now=moment) is False


def test_the_driver_defaults_its_library_kind_to_its_sort_type():
    """Search-tail E-2 (facts C4). The driver conflated two things Kometa keeps
    apart: ``is_show = sort_type == "show"`` (kometa_build_filter.py:858) drove
    ``show_translation`` and the kind gates, which Kometa derives from
    ``self.library.is_show`` (modules/builder.py:4176-4181) and NOT from the
    search level. Un-conflating them is a signature change to a vendored
    transcription, so the first thing pinned is that it changed nothing: with
    ``library_kind`` left at its default every config produces the byte it
    produced before -- Task 2's added 23/24 too, trivially: passing
    ``library_kind=sort_type`` explicitly is definitionally the same as
    omitting it, whatever the config's OWN (possibly different) library_kind
    is. That the two can differ is what
    ``test_the_driver_types_by_the_sort_type_and_scopes_by_the_library_kind``
    and the byte-identical oracle configs 23/24 pin instead.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("kometa_oracle_driver", ORACLE_DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    for sort_type, _library_kind, plex_filter in driver.CONFIGS:
        _, defaulted = driver.build_filter("plex_search", plex_filter, sort_type)
        _, explicit = driver.build_filter(
            "plex_search", plex_filter, sort_type, library_kind=sort_type
        )
        assert defaulted == explicit


def test_the_driver_types_by_the_sort_type_and_scopes_by_the_library_kind():
    """The half the default cannot prove, with a predicate that actually
    discriminates it. ``episode_title``/``show_unmatched`` both resolve
    through ``search_translation``, which is kind-independent -- so a driver
    that kept the conflation (``is_show = sort_type == "show"``) answers the
    original version of this test identically whether ``library_kind`` is
    honoured or ignored (Task 1 review, Important I-1). ``title.begins`` is
    the discriminating case: ``title`` is bare in ``search_translation`` and
    reachable through ``show_translation`` only (kometa_build_filter.py:909),
    so it renders ``show.title`` under ``library_kind="show"`` and bare
    ``title`` when the kind is left to default from ``sort_type`` --
    "episode" here, neither "movie" nor "show" -- and ONLY if ``is_show`` is
    actually gated on ``library_kind``. A conflated driver renders both calls
    as bare ``title`` (confirmed empirically against a driver copy with the
    conflated ``is_show = sort_type == "show"`` restored, without touching
    this repo's tracked driver file: both calls answer
    ``?type=4&sort=titleSort&title%3C=Pilot``, so the first assertion below
    fails).
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("kometa_oracle_driver", ORACLE_DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    _, show = driver.build_filter(
        "plex_search",
        {"all": {"title.begins": "Pilot"}},
        "episode",
        library_kind="show",
    )
    assert show.startswith("?type=4&")
    assert "show.title%3C=Pilot" in show

    _, bare = driver.build_filter(
        "plex_search",
        {"all": {"title.begins": "Pilot"}},
        "episode",
    )
    assert bare.startswith("?type=4&")
    assert "title%3C=Pilot" in bare
    assert "show.title" not in bare
