"""The shipped overlay families (roadmap row 100, sub-phases C1 and C2a).

Flat `OverlayDefinition` lists, not templates. Kometa expresses each family
as a templated YAML file whose `<<key>>` resolver this service does not
implement (and does not need: the recon's cut emits flat definitions), so
every number here is the RESOLVED value, transcribed from the pinned
v2.4.8 tree -- the same image digest `assets/badges/PROVENANCE.md` records.

Three families ship: `direct_play`, the six content-rating regionals (C1),
and `versions` (C2a, adjudication A14 ruled -- the `collections/filters.py`
row it needed now exists).

Each family is opt-in through `config.badges.families`, and a family that is
not named costs nothing: no definition, no fingerprint movement, no asset
read.
"""
from autoposter.overlays.schema import OverlayDefinition

# Probe section 4.5 and grammar probe section 5.1 (`direct_play.yml`,
# verbatim in full there). One overlay. The box is 305x170 -- the one family
# in row 100 with a non-105 height -- and the image key defaults to the
# overlay's OWN NAME, `Direct-Play`, which is why the definition's `builtin:`
# and `name` are the same string. `resolution.regex` is Kometa's own
# selection, and `tag` carries `.regex` in this service's operator set too.
# `back_radius=30` is NOT in `direct_play.yml` itself -- it inherits from the
# un-vendored `templates.yml` (`external_templates: default: templates`),
# grammar probe section 5.5's `standard` template default. All six regional
# files set it explicitly to the same 30, so this is that same value,
# transcribed from the probe rather than from a vendored file.
DIRECT_PLAY: list[OverlayDefinition] = [
    OverlayDefinition(
        name="Direct-Play",
        builtin="Direct-Play",
        condition={"resolution.regex": "(?i)2160|4k"},
        horizontal_align="center", horizontal_offset=0,
        vertical_align="bottom", vertical_offset=30,
        back_width=305, back_height=170,
        back_color="#00000099", back_radius=30,
    ),
]

# The six content-rating regionals. Probe section 4.3: all six share one
# shape -- a per-bucket comma-separated ALIAS LIST matched against
# `item.contentRating`, the Plex string, and nothing else. No API, no fact
# lookup.
#
# ADJUDICATION A5, and it is the whole reason these lists look the way they
# do: this matches PLEX's own certification, NOT `item_facts.content_rating`,
# which is MDBList's Common Sense AGE rating. Kometa's buckets carry Plex
# agent spellings -- `gb/U`, `gb/0+`, `no/A` -- that Common Sense never
# emits, and `collections/filters.py`'s own `content_rating` row says so in
# as many words. `overlays/selection.py::OverlayItemView` reads
# `plex_item.contentRating` for exactly this reason.
#
# The alias lists below are transcribed VERBATIM from the pinned files,
# including their numeric aliases. A dropped alias is not a crash; it is a
# silently missing badge on the items that carried it. Where the source YAML
# repeats an alias within one bucket (au/m and nz/m each list "12" twice),
# that duplicate is kept rather than silently deduped -- this module is a
# transcription, not an edit.
CONTENT_RATING_US_MOVIE: list[OverlayDefinition] = [
    OverlayDefinition(
        name="us_movie_g",
        builtin="cr/usgc",
        condition={"content_rating": [
            "1", "01", "2", "02", "3", "03", "4", "04", "5", "05", "6", "06",
            "G", "G - All Ages", "U", "gb/U", "gb/0+", "E", "gb/E", "A",
            "no/A", "TV-Y", "TV-G",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_movie_pg",
        builtin="cr/uspgc",
        condition={"content_rating": [
            "PG", "PG - Children", "gb/PG", "gb/9+", "TV-PG", "TV-Y7",
            "TV-Y7-FV", "7", "8", "9", "07", "08", "09", "10", "11",
            "no/5", "no/05", "no/6", "no/06", "no/7", "no/07",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_movie_pg-13",
        builtin="cr/uspg-13c",
        condition={"content_rating": [
            "PG-13", "gb/12A", "gb/12", "12+", "TV-13", "gb/14+", "gb/15",
            "TV-14", "12", "13", "14", "15", "16",
            "PG-13 - Teens 13 or older", "no/9", "no/09", "no/10", "no/11",
            "no/12",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_movie_r",
        builtin="cr/usrc",
        condition={"content_rating": [
            "R", "17", "18", "gb/18", "MA-17", "TVMA", "TV-MA",
            "R - 17+ (violence & profanity)", "R+ - Mild Nudity",
            "no/15", "no/16", "no/18",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_movie_nc-17",
        builtin="cr/usnc-17c",
        condition={"content_rating": [
            "NC-17", "gb/R18", "gb/X", "R18", "X", "Rx - Hentai",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_movie_nr",
        builtin="cr/usnrc",
        condition={"content_rating": ["None", "NR", "Not Rated", "Unrated"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
]

# A SEPARATE file from us_movie upstream, with its own board
# (TV-Y/TV-G/TV-PG/TV-14/TV-MA and friends). Its `nr` bucket resolves to the
# exact same `builtin:` (`cr/usnrc`) as `us_movie_nr` above -- the source
# file's own template computes the image path from the bucket key alone
# (both buckets are named `nr`), so the two families share that one PNG by
# construction, not by an error here.
CONTENT_RATING_US_SHOW: list[OverlayDefinition] = [
    OverlayDefinition(
        name="us_show_tv-g",
        builtin="cr/ustv-gc",
        condition={"content_rating": [
            "TV-G", "1", "01", "2", "02", "3", "03", "4", "04", "5", "05",
            "6", "06", "U", "G", "gb/U", "gb/0+", "G - All Ages", "A", "no/A",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_show_tv-y",
        builtin="cr/ustv-yc",
        condition={"content_rating": [
            "TV-Y", "TV-Y7", "TV-Y7-FV", "7", "07", "8", "08", "9", "09",
            "no/5", "no/05", "no/6", "no/06", "no/7", "no/07",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_show_tv-pg",
        builtin="cr/ustv-pgc",
        condition={"content_rating": [
            "gb/PG", "gb/9+", "10", "11", "12", "13", "TV-PG",
            "PG - Children", "no/9", "no/09", "no/10", "no/11", "no/12",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_show_tv-14",
        builtin="cr/ustv-14c",
        condition={"content_rating": [
            "gb/12A", "12+", "PG-13", "TV-13", "TV-14", "12",
            "PG-13 - Teens 13 or older", "gb/14+", "gb/15", "14", "15",
            "16", "17", "no/15", "no/16",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_show_tv-ma",
        builtin="cr/ustv-mac",
        condition={"content_rating": [
            "18", "gb/18", "MA-17", "NC-17", "R", "TV-MA", "TVMA",
            "R - 17+ (violence & profanity)", "R+ - Mild Nudity",
            "Rx - Hentai", "no/18",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="us_show_nr",
        builtin="cr/usnrc",
        condition={"content_rating": ["None", "NR", "Not Rated", "Unrated"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
]

# EIGHT buckets, not the seven the recon probe named: the vendored
# `content_rating_uk.yml` carries a `12a` overlay (its own alias list, its
# own image `cr/uk12ac`) distinct from `12` -- confirmed by the vendored
# `images/cr/uk12a*.png` files existing alongside `images/cr/uk12*.png`, and
# by the uk* file count (16) only reconciling at 2 files per bucket across
# EIGHT buckets. Task 2's report names this divergence from the plan's own
# recon; this module transcribes the real file, not the recon's count.
#
# These eight buckets are NOT disjoint: `uk_12` and `uk_12a` share six
# aliases ("PG-13 - Teens 13 or older", no/9, no/09, no/10, no/11, no/12),
# and that alias also appears in `uk_15` -- so a single item can match three
# UK definitions at once, all drawn at the same coordinates. This is
# upstream's overlap (Kometa's `content_rating_uk.yml` ships the same
# overlapping alias lists and sets no `group:` either), not a transcription
# error, and not new in `12a` -- `uk_12`/`uk_15` already shared one alias at
# seven buckets; `12a` widens the overlap to six. It is transcribed
# faithfully rather than resolved, so arbitration between stacked UK
# overlays on a matching item falls to `suppress_overlays`/`group`/`weight`
# on the operator's own definitions -- this module sets none of those, and
# an operator hitting a stack should add a `suppress_overlays` entry.
CONTENT_RATING_UK: list[OverlayDefinition] = [
    OverlayDefinition(
        name="uk_u",
        builtin="cr/ukuc",
        condition={"content_rating": [
            "U", "0", "1", "01", "2", "02", "3", "03", "4", "04", "5", "05",
            "6", "06", "G", "TV-G", "TV-Y", "G - All Ages", "gb/U", "gb/Uc",
            "gb/0+", "gb/6+", "gb/Kids & Family", "E", "gb/E", "A", "no/A",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="uk_pg",
        builtin="cr/ukpgc",
        condition={"content_rating": [
            "gb/PG", "gb/9+", "gb/7", "gb/7+", "TV-PG", "TV-Y7",
            "TV-Y7-FV", "PG", "7", "07", "8", "08", "9", "09", "10", "11",
            "PG - Children", "no/5", "no/05", "no/6", "no/06", "no/7",
            "no/07",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="uk_12",
        builtin="cr/uk12c",
        condition={"content_rating": [
            "gb/12", "gb/A", "gb/Caution", "gb/G",
            "PG-13 - Teens 13 or older", "no/9", "no/09", "no/10", "no/11",
            "no/12",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="uk_12a",
        builtin="cr/uk12ac",
        condition={"content_rating": [
            "12A", "gb/12A", "12+", "PG-13", "TV-13", "12",
            "PG-13 - Teens 13 or older", "no/9", "no/09", "no/10", "no/11",
            "no/12",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="uk_15",
        builtin="cr/uk15c",
        condition={"content_rating": [
            "gb/15", "gb/14+", "gb/16", "gb/16+", "gb/AA", "TV-14", "13",
            "14", "15", "PG-13 - Teens 13 or older", "no/15", "no/16",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="uk_18",
        builtin="cr/uk18c",
        condition={"content_rating": [
            "gb/18", "gb/18+", "MA-17", "TVMA", "TV-MA", "R", "16", "17",
            "NC-17", "18", "R - 17+ (violence & profanity)", "no/18",
            "gb/X",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="uk_r18",
        builtin="cr/ukr18c",
        condition={"content_rating": [
            "R18", "gb/R18", "X", "R+ - Mild Nudity", "Rx - Hentai",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="uk_nr",
        builtin="cr/uknrc",
        condition={"content_rating": ["None", "NR", "Not Rated", "Unrated"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
]

CONTENT_RATING_AU: list[OverlayDefinition] = [
    OverlayDefinition(
        name="au_g",
        builtin="cr/au_gc",
        condition={"content_rating": [
            "au/G", "de/0", "U", "0", "1", "01", "2", "02", "3", "03", "4",
            "04", "5", "05", "6", "06", "G", "TV-G", "TV-Y", "G - All Ages",
            "gb/U", "gb/0+", "E", "gb/E", "A", "no/A", "no/5", "no/05",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="au_pg",
        builtin="cr/au_pgc",
        condition={"content_rating": [
            "au/PG", "de/6", "gb/9+", "TV-PG", "TV-Y7", "TV-Y7-FV", "PG",
            "7", "07", "8", "08", "9", "09", "10", "11", "PG - Children",
            "no/6", "no/06", "no/7", "no/07", "no/9", "no/09", "no/10",
            "no/11",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="au_m",
        builtin="cr/au_mc",
        condition={"content_rating": [
            "au/M", "de/12", "gb/12", "12", "no/12", "gb/15", "gb/14+",
            "TV-14", "12", "13", "14", "15", "PG-13 - Teens 13 or older",
            "PG-13", "no/15",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="au_ma",
        builtin="cr/au_mac",
        condition={"content_rating": [
            "au/MA15+", "au/MA 15+", "de/16", "no/16", "A-17", "TVMA",
            "TV-MA", "R", "16", "17", "M/PG",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="au_r",
        builtin="cr/au_rc",
        condition={"content_rating": [
            "au/R 18+", "au/R18+", "de/18", "gb/18", "M", "18",
            "R - 17+ (violence & profanity)", "no/18", "R18", "gb/X", "X",
            "NC-17", "R+ - Mild Nudity", "Rx - Hentai",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="au_x",
        builtin="cr/au_xc",
        condition={"content_rating": [
            "au/X 18+", "au/X18+", "de/BPjM Restricted", "BPjM Restricted",
            "gb/R18",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="au_nr",
        builtin="cr/au_nrc",
        condition={"content_rating": [
            "None", "NR", "Not Rated", "Unrated", "de/Unrated",
            "de/Not Rated", "au/Unrated", "au/Not Rated",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
]

CONTENT_RATING_DE: list[OverlayDefinition] = [
    OverlayDefinition(
        name="de_0",
        builtin="cr/de0c",
        condition={"content_rating": [
            "de/0", "U", "0", "1", "01", "2", "02", "3", "03", "4", "04",
            "5", "05", "G", "TV-G", "TV-Y", "G - All Ages", "gb/U", "gb/0+",
            "E", "gb/E", "A", "no/A", "no/5", "no/05",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="de_6",
        builtin="cr/de6c",
        condition={"content_rating": [
            "de/6", "6", "gb/9+", "TV-PG", "TV-Y7", "TV-Y7-FV", "PG", "7",
            "07", "8", "08", "9", "09", "10", "11", "PG - Children", "no/6",
            "no/06", "no/7", "no/07", "no/9", "no/09", "no/10", "no/11",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="de_12",
        builtin="cr/de12c",
        condition={"content_rating": [
            "de/12", "gb/12", "12", "no/12", "gb/15", "gb/14+", "TV-14",
            "13", "14", "15", "PG-13 - Teens 13 or older", "PG-13", "no/15",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="de_16",
        builtin="cr/de16c",
        condition={"content_rating": [
            "de/16", "no/16", "A-17", "TVMA", "TV-MA", "R", "16", "17",
            "M/PG",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="de_18",
        builtin="cr/de18c",
        condition={"content_rating": [
            "de/18", "gb/18", "M", "18", "R - 17+ (violence & profanity)",
            "no/18", "R18", "gb/R18", "gb/X", "X", "NC-17",
            "R+ - Mild Nudity", "Rx - Hentai",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="de_bpjm",
        builtin="cr/debpjmc",
        condition={"content_rating": ["de/BPjM Restricted", "BPjM Restricted"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="de_nr",
        builtin="cr/denrc",
        condition={"content_rating": [
            "None", "NR", "Not Rated", "Unrated", "de/Unrated",
            "de/Not Rated",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
]

CONTENT_RATING_NZ: list[OverlayDefinition] = [
    OverlayDefinition(
        name="nz_g",
        builtin="cr/nz_gc",
        condition={"content_rating": [
            "au/G", "de/0", "U", "0", "1", "01", "2", "02", "3", "03", "4",
            "04", "5", "05", "6", "06", "G", "TV-G", "TV-Y", "G - All Ages",
            "gb/U", "gb/0+", "E", "gb/E", "A", "no/A", "no/5", "no/05",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_pg",
        builtin="cr/nz_pgc",
        condition={"content_rating": [
            "au/PG", "de/6", "gb/9+", "TV-PG", "TV-Y7", "TV-Y7-FV", "PG",
            "7", "07", "8", "08", "9", "09", "10", "11", "PG - Children",
            "no/6", "no/06", "no/7", "no/07", "no/9", "no/09", "no/10",
            "no/11",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_m",
        builtin="cr/nz_mc",
        condition={"content_rating": ["au/M", "de/12", "gb/12", "12", "no/12", "12"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_r13",
        builtin="cr/nz_r13c",
        condition={"content_rating": [
            "13", "PG-13 - Teens 13 or older", "PG-13",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_rp13",
        builtin="cr/nz_rp13c",
        condition={"content_rating": ["gb/14+", "TV-14", "14"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_r15",
        builtin="cr/nz_r15c",
        condition={"content_rating": ["au/MA15+", "15", "M", "TVMA", "TV-MA"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_r16",
        builtin="cr/nz_r16c",
        condition={"content_rating": ["de/16", "no/16", "16"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_rp16",
        builtin="cr/nz_rp16c",
        condition={"content_rating": ["A-17", "17"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_r",
        builtin="cr/nz_rc",
        condition={"content_rating": [
            "18", "R", "R - 17+ (violence & profanity)", "no/18", "NC-17",
            "R+ - Mild Nudity", "Rx - Hentai",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_r18",
        builtin="cr/nz_r18c",
        condition={"content_rating": ["de/18", "gb/18", "gb/R18", "R18"]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_rp18",
        builtin="cr/nz_rp18c",
        condition={"content_rating": [
            "au/X 18+", "gb/X", "X", "de/BPjM Restricted", "BPjM Restricted",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="nz_nr",
        builtin="cr/nz_nrc",
        condition={"content_rating": [
            "None", "NR", "Not Rated", "Unrated", "de/Unrated",
            "de/Not Rated", "au/Unrated", "au/Not Rated",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
]

# Probe section 4.8: a single static badge, 105x105. Selection is Plex's
# `duplicate`/`episode_duplicate` smart search upstream; this service selects
# on the NEW `versions` int filter attribute instead (adjudication A14),
# because `duplicate` itself is search-only and unfilterable, so an overlay
# `condition:` block cannot name it at all. `versions.gt: 1` means "more than
# one `<Media>` entry" -- the same threshold. Position is the non-episode
# default (`horizontal_offset: 15`, `vertical_offset: 335`); the
# episode-specific position (235/270) is the one positioning conditional in
# the whole row-100 set that branches on `builder_level`, and this module
# ships flat, resolved definitions rather than a template engine (the same
# simplification C1 already made for every other family), so only the
# non-episode position is reproduced.
#
# `horizontal_align="right"`, not the `"left"` every other row-100 family
# uses: the vendored `versions.yml`'s own `external_templates.
# template_variables.default` sets `horizontal_align: right` outright (and
# its `conditionals.horizontal_align` block resolves to the same `right` in
# both its listed conditions, so there is no path to `left` in this file at
# all) -- confirmed by direct read of the vendored file in Task 2 Step 1, a
# deviation from Task 2's own draft that this module transcribes rather than
# repeats. `horizontal_offset=15` still holds either way: the vendored
# `conditionals.horizontal_offset` maps BOTH `left` and `right` to `15`.
# `back_color="#00000099"` is explicit in the same vendored file (not
# inherited); `back_radius` is NOT set anywhere in `versions.yml`, so it
# inherits from the un-vendored `templates.yml` (`external_templates:
# default: templates`), grammar probe section 5.5's `standard` template
# default -- the same reasoning `DIRECT_PLAY`'s own comment gives for the
# identical situation.
VERSIONS: list[OverlayDefinition] = [
    OverlayDefinition(
        name="versions",
        builtin="versions",
        condition={"versions.gt": 1},
        horizontal_align="right", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=335,
        back_width=105, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
]

FAMILIES: dict[str, list[OverlayDefinition]] = {
    "direct_play": DIRECT_PLAY,
    "content_rating_au": CONTENT_RATING_AU,
    "content_rating_de": CONTENT_RATING_DE,
    "content_rating_nz": CONTENT_RATING_NZ,
    "content_rating_uk": CONTENT_RATING_UK,
    "content_rating_us_movie": CONTENT_RATING_US_MOVIE,
    "content_rating_us_show": CONTENT_RATING_US_SHOW,
    "versions": VERSIONS,
}

__all__ = ["FAMILIES"]
