"""The shipped overlay families (roadmap row 100).

Flat `OverlayDefinition` lists, not templates. Kometa expresses each family
as a templated YAML file whose `<<key>>` resolver this service does not
implement (and does not need: the recon's cut emits flat definitions), so
every number here is the RESOLVED value, transcribed from the pinned
v2.4.8 tree -- the same image digest `assets/badges/PROVENANCE.md` records.

Six families ship: `direct_play` and the six content-rating regionals,
`versions` (which needed the `collections/filters.py` row that now exists),
`aspect` + `language_count`, and `status` (which needed two new `item_facts`
columns, a migration, and a new `facts` source tier so the two rows it needs
can exist). The last three are the families whose
CORRECTNESS depends on group/weight resolution rather than merely benefiting
from it: `aspect`'s 1.65 and 1.66 bands overlap, `language_count`'s Dual and
Multi both match a 2-language item, and `status`'s AIRING both matches and
outranks ENDED on a show whose finale aired inside the window -- so each list
carries an explicit `group` and descending `weight` and the tie-breaks are
pinned.

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
# EIGHT buckets. This module transcribes the real file, not the recon's
# count.
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
# simplification already made for every other family), so only the
# non-episode position is reproduced.
#
# `horizontal_align="right"`, not the `"left"` every other row-100 family
# uses: the vendored `versions.yml`'s own `external_templates.
# template_variables.default` sets `horizontal_align: right` outright (and
# its `conditionals.horizontal_align` block resolves to the same `right` in
# both its listed conditions, so there is no path to `left` in this file at
# all) -- confirmed by direct read of the vendored file, a deviation from an
# earlier draft that this module transcribes rather than repeats. `horizontal_offset=15` still holds either way: the vendored
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

# Probe section 4.1 (`aspect.yml:48-72` at the pinned tag). EIGHT bands, and
# note what is NOT here: no 2.0 and no 2.4. Kometa's own list is
# 1.33/1.65/1.66/1.78/1.85/2.2/2.35/2.77, and each band is a +/-0.01 window
# around the nominal ratio, OPEN AT BOTH ENDS (`.gt`/`.lt`, never the
# inclusive forms) -- so a 1.90 aspect matches nothing at all. That is
# upstream's own arithmetic and it is transcribed, not corrected.
#
# THE FIRST FAMILY THAT DRAWS TEXT. `final_name: text(<<text_<<key>>>>)` with
# `text_<<key>>: <<overlay_name>>` (`aspect.yml:12,38`) means the drawn
# string is the overlay's OWN NAME -- resolved flat, `name="text(1.33)"`,
# with no `<<variable>>` token in it, so `render_text` has nothing to
# substitute and `UnresolvedVariable` can never fire for this family. It also
# means these definitions name no image at all: they take
# `resolve_image_path`'s name-keyed fallback rung, find nothing, and draw
# text alone, which is the rung's documented "a text overlay carrying no
# addon has no image" case reached by a shipped family for the first time.
#
# `font="Inter-Medium.ttf"` is the BARE bundled name, answered by
# `overlays/sources.py::resolve_font_path`'s bundled rung (adjudication A-4)
# for every operator whatever their `fonts_root` holds -- an absolute bundled
# path here would be refused by `_confined` and the definition skipped for
# everyone whose mount is not an ancestor of the bundled fonts directory.
#
# A DECLARED TRANSCRIPTION DIVERGENCE, and the only one in this family:
# `aspect.yml:33` writes `font: fonts/Inter-Medium.ttf` -- a PATH, relative
# to Kometa's own config tree, which has a `fonts/` directory beside the
# overlay YAML. This service writes the bare filename instead, because the
# bundled rung above is an exact-NAME lookup on the whole written value:
# `fonts/Inter-Medium.ttf` misses `BUNDLED_FONTS` entirely AND resolves
# under `_confined` to `<fonts_root>/fonts/Inter-Medium.ttf`, which no
# operator has, so transcribing the path literally would skip every aspect
# badge for everyone. The pixels are identical -- it is the same face -- so
# this is a spelling divergence in the source string, recorded here rather
# than left as an unexplained difference between two files a reader may
# compare.
#
# GROUP AND WEIGHT ARE LOAD-BEARING HERE, unlike the earlier families:
# the 1.65 band (1.64-1.66) and the 1.66 band
# (1.65-1.67) genuinely OVERLAP, so a 1.655 item matches both and only the
# shared group plus the descending weights make the answer deterministic --
# 70 beats 60, the 1.65 badge draws, and `select` still records BOTH
# outcomes, which over-covers the fingerprint and can never under-cover it.
#
# `back_radius=30` is NOT in `aspect.yml` itself -- it inherits from the
# un-vendored `templates.yml` (`external_templates: default: templates`),
# grammar probe section 5.5's `standard` template default, the same
# reasoning `DIRECT_PLAY`'s own comment gives for the identical situation.
ASPECT: list[OverlayDefinition] = [
    OverlayDefinition(
        name=f"text({label})",
        condition={"aspect.gt": low, "aspect.lt": high},
        group="aspect", weight=weight,
        horizontal_align="center", horizontal_offset=0,
        vertical_align="bottom", vertical_offset=150,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font="Inter-Medium.ttf", font_size=63,
    )
    for label, low, high, weight in (
        ("1.33", 1.32, 1.34, 80),
        ("1.65", 1.64, 1.66, 70),
        ("1.66", 1.65, 1.67, 60),
        ("1.78", 1.77, 1.79, 50),
        ("1.85", 1.84, 1.86, 40),
        ("2.2", 2.19, 2.21, 30),
        ("2.35", 2.34, 2.36, 20),
        ("2.77", 2.76, 2.78, 10),
    )
]

# Probe section 4.2 (`language_count.yml:53-79`). Two overlays over the count
# of audio-language STREAMS: Dual is `count_gte: 2` AND `count_lt: 3` --
# exactly two -- and Multi is `count_gte: 2` with no upper bound.
#
# WHAT IS COUNTED IS STREAMS, NOT DISTINCT LANGUAGES, and that is Kometa's
# own arithmetic transcribed rather than tidied: `modules/plex.py:2915-2922`
# builds the value with `test_number.extend([a.language for a in
# part.audioStreams()])` over every part of every `<Media>`, with no dedupe
# anywhere, and `.count_*` is `len()` of that (`plex.py:2931-2932`). So a
# film with an English track plus an English commentary track IS Dual
# upstream, and it is Dual here. An item with no audio streams at all counts
# as ZERO, is compared like any other number, and fails `count_gte: 2` --
# see `collections/filters.py::_matches`, which mirrors the same two lines.
#
# THE SLOT IS UPSTREAM'S: center/0, bottom/30 (`language_count.yml:13-14`
# sets the alignments, and its `conditionals:` resolve the two offsets at
# `:28-29` and `:33-34`). That is the SAME slot `DIRECT_PLAY` occupies,
# which is upstream's own arrangement between two of its own default
# overlays -- an operator enabling both gets what Kometa would give them.
# Neither is moved. It does NOT share the content-rating regionals' left/15,
# bottom/270; an earlier draft of this family had it there and that was
# simply wrong.
#
# ADJUDICATION A-1: the count is spelled as Kometa's own `.count_*` MODIFIER
# on the existing `audio_language` attribute (`builder.py:4350`), not as an
# invented `audio_language_count` attribute -- so a Kometa config ports
# verbatim. This reverses the phase-C recon's A8 recommendation; see the
# comment on `collections/filters.py::OPERATORS_BY_TYPE` for the full
# argument. A two-key condition mapping is already an AND, because
# `parse_filters`' `base` defaults to `all`, so Dual needs no grammar of its
# own.
#
# GROUP AND WEIGHT ARE LOAD-BEARING HERE TOO, and more obviously than for
# `aspect` (adjudication A-5): a 2-language item satisfies Dual AND Multi by
# construction, because Multi is deliberately unbounded above. Group
# `language` with 20 > 10 is the whole of what makes Dual win it.
#
# ONLY THE AUDIO PAIR SHIPS. `use_subtitles: true` is a template variable
# upstream, selecting `subtitle_language` and the `*_subs` art; this module
# ships FLAT resolved definitions rather than a template resolver, so only
# the `false` resolution is a family. The subtitle stream languages are
# collected anyway (`badges/values.py::MediaInfo.subtitle_stream_languages`,
# adjudication A-3) and `subtitle_language.count_*` parses, so a
# `language_count_subs` family is a one-commit follow-up whenever it is
# wanted -- it needs two more vendored PNGs and nothing else.
LANGUAGE_COUNT: list[OverlayDefinition] = [
    OverlayDefinition(
        name="dual_audio",
        builtin="dual_audio",
        condition={"audio_language.count_gte": 2, "audio_language.count_lt": 3},
        group="language", weight=20,
        horizontal_align="center", horizontal_offset=0,
        vertical_align="bottom", vertical_offset=30,
        back_width=188, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    OverlayDefinition(
        name="multi_audio",
        builtin="multi_audio",
        condition={"audio_language.count_gte": 2},
        group="language", weight=10,
        horizontal_align="center", horizontal_offset=0,
        vertical_align="bottom", vertical_offset=30,
        back_width=188, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
]

# Probe section 4.7 and `/defaults/overlays/status.yml`, read in full out of
# the pinned v2.4.8 image (84 lines). FOUR TEXT definitions, ZERO ASSETS: the
# file names no image key anywhere, and `final_name:
# text(<<text_<<key>>>>)` (`:40`) with `text_<<key>>: <<text>>` (`:11`) makes
# the drawn string the overlay's OWN NAME. So these definitions take
# `resolve_image_path`'s name-keyed fallback rung, find nothing, and draw
# text alone -- the case `ASPECT` reached first. `OVERLAY-MANIFEST.sha256`
# does not move, `manifest_sha()` does not move, and no already-badged item
# re-renders for art: sub-phase C2c is the first family slice in row 100 that
# vendors nothing at all.
#
# THE SLOT IS left/15, top/330, AND THE 330 IS NOT A TYPO. `status.yml:13-15`
# sets `default: {horizontal_align: left, vertical_align: top}` -- which
# supplies the ALIGNMENT without making the variable "exist" -- so the
# `vertical_offset` conditional's FIRST condition is the one that fires:
# `vertical_align.exists: false -> 330` (`:20-21`). The `vertical_align: top
# -> 15` branch below it (`:24-25`) is for an operator who sets the alignment
# explicitly, and this module emits flat RESOLVED definitions rather than
# implementing the template resolver, so the no-operator-input resolution is
# the one that ships. `horizontal_offset` resolves to 15 the same way
# (`:33-34`).
#
# `font_size: 50` (`:35`), `back_color: "#00000099"` (`:36`), `back_width:
# 305` / `back_height: 105` (`:37-38`), `group: status` (`:12`). `font`,
# `font_color` and `back_radius` are NOT in `status.yml`: they inherit from
# the un-vendored `templates.yml`'s `standard` template (`external_templates:
# default: templates`, `:8-9`, pulled by each overlay's `template: [name:
# standard, name: status]`) -- `font: fonts/Inter-Medium.ttf` (`:5`),
# `font_color: "#FFFFFF"` (`:7`), `back_radius: 30` (`:8`), with that file's
# `font_size: 55` overridden to 50 by `status.yml:35`. `font_color` is left
# unset below because `OverlayDefinition`'s own default is already `#FFFFFF`:
# the same value, written once.
#
# THE FONT SPELLING DIVERGENCE IS C2b'S, RE-DECLARED because a reader
# comparing the two files deserves it in both places: `templates.yml:5`
# writes `fonts/Inter-Medium.ttf`, a PATH relative to Kometa's own config
# tree; this service writes the bare filename, because
# `overlays/sources.py::resolve_font_path`'s bundled rung is an exact-NAME
# lookup on `BUNDLED_FONTS` and the path would both miss it AND resolve under
# `_confined` to `<fonts_root>/fonts/Inter-Medium.ttf`, which no operator has
# -- so transcribing the path literally would skip every status badge for
# everyone. Same face, same pixels, different spelling.
#
# `allowed_libraries: show` (`:39`) IS ACHIEVED BY CONSTRUCTION, not by a new
# `OverlayDefinition` field, and this is the same "answered by construction"
# argument already made for the `kinds` column. A movie's `item_facts` row
# can never carry `tmdb_status` -- `facts/tmdb_facts.py::parse_movie_facts`
# has no such field to write; a season has no facts row at all
# (`facts/gather.py` returns empty for seasons); an episode's row carries
# only `audience_rating`. All three answer None, which the tag and date
# missing-value rules exclude under every operator. A movie with the family
# enabled draws nothing, and it draws nothing because there is no value, not
# because a library check said so.
#
# ONE BAND IS NOT A `tmdb_status` AT ALL, AND THAT IS THIS SUB-PHASE'S ONE
# REAL SEMANTIC CALL (adjudication A-1, ruled). Upstream selects AIRING with
# `plex_search: {any: {episode_air_date: 14}}` (`status.yml:61-63` with
# `last: 14` at `:71`) -- a server-side Plex search over the LIBRARY's
# episode rows -- and `episode_air_date` is absent from
# `builder.filters_by_type` entirely (`/modules/builder.py:278-350`), so
# NEITHER system can name it in a `filters:`/`condition:` block. That is
# `duplicate`/`versions` (A14) verbatim, and the remedy is the one Kometa
# itself supplies: `last_episode_aired`, a real Kometa show filter over
# TMDb's `last_air_date` (`/modules/tmdb.py:694-705`) whose bare integer
# already means "in the last N days" in both systems (`/modules/util.py:
# 601-604`; `/modules/builder.py:4222-4233`).
#
#   THE DIVERGENCE, DECLARED: upstream asks "does the LIBRARY hold an episode
#   that aired in the last 14 days"; this asks "did the SHOW air an episode in
#   the last 14 days, per TMDb". They differ when the library lags broadcast
#   (upstream draws nothing, this draws AIRING) and when Plex's own episode
#   dates are wrong. The alternative was to defer the AIRING band and ship
#   three; adjudication A-5 ruled all four.
#
# GROUP AND WEIGHT ARE LOAD-BEARING, the third family in a row for which that
# is true (C2b's A-5) and the most realistic case yet: a show whose finale
# aired eight days ago and which TMDb has already marked `Ended` matches
# AIRING (40) AND ENDED (10); a mid-season show matches AIRING and RETURNING
# (30). Unlike `aspect`'s overlapping bands there is no arithmetic that could
# separate them -- the two conditions are over DIFFERENT attributes -- so
# upstream's own group and descending weights are the entire tie-break.
# `select` still records BOTH outcomes, which over-covers the fingerprint and
# can never under-cover it.
#
# THREE OF THE SIX `discover_status` TOKENS DRAW NOTHING, upstream and here:
# `planned`, `production` and `pilot` have no overlay in `status.yml`. A show
# TMDb calls "In Production" draws no status badge in Kometa and must draw
# none here. And a status TMDb spells outside all six is stored verbatim by
# `facts/tmdb_facts.py::_show_status` and matches nothing, where upstream
# raises `KeyError` (`/modules/tmdb.py:685`) -- the same drawn outcome
# without the crash.
STATUS: list[OverlayDefinition] = [
    OverlayDefinition(
        name=f"text({text})",
        condition=condition,
        group="status", weight=weight,
        horizontal_align="left", horizontal_offset=15,
        vertical_align="top", vertical_offset=330,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font="Inter-Medium.ttf", font_size=50,
    )
    for text, condition, weight in (
        ("AIRING", {"last_episode_aired": 14}, 40),
        ("RETURNING", {"tmdb_status": "returning"}, 30),
        ("CANCELED", {"tmdb_status": "canceled"}, 20),
        ("ENDED", {"tmdb_status": "ended"}, 10),
    )
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
    "aspect": ASPECT,
    "language_count": LANGUAGE_COUNT,
    "status": STATUS,
}

__all__ = ["FAMILIES"]
