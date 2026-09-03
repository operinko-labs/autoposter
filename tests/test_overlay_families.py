"""The shipped overlay families (roadmap row 100, sub-phase C1).

Two families ship here: `direct_play` (one definition) and the six
content-rating regionals. `versions` is fenced by adjudication A14 -- its
selection needs a `versions` row in `collections/filters.py`, which this
phase may not edit.

Every number in `families.py` is transcribed from the pinned Kometa tree
(v2.4.8, the image digest `assets/badges/PROVENANCE.md` records). These tests
are the transcription's checksum: what the art is, what each definition
selects on, that every named image actually exists, and that adding the art
moved no fingerprint.
"""
import hashlib

import pytest
from pydantic import ValidationError

from autoposter.config.schema import BadgesConfig
from autoposter.overlays.assets import ASSETS, IMAGES
from autoposter.overlays.families import FAMILIES
from autoposter.overlays.schema import OverlayDefinition

OVERLAY_MANIFEST = ASSETS / "OVERLAY-MANIFEST.sha256"
BUILTIN_MANIFEST = ASSETS / "MANIFEST.sha256"

# The ONE authoritative constant every count pin below derives from, set once
# from Task 2 Step 1's measured `ls .superpowers/p-overlay-c1-vendor/cr | wc -l`
# -- never restated as an independent literal. `98` is this document's
# current best measurement; if Step 1 measured differently, this is the only
# line that needs to change; every pin that depends on it moves with it.
CR_COUNT = 98


def test_the_builtin_manifest_did_not_grow_when_the_family_art_landed():
    """Global Constraint 12 and the storm guard's third arm. `manifest_sha()`
    is in every item's fingerprint; adding `CR_COUNT + 1` entries here would
    re-badge every already-uploaded item in a library that enables no
    family."""
    lines = BUILTIN_MANIFEST.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 513
    assert not [line for line in lines if "images/cr/" in line]
    assert not [line for line in lines if "Direct-Play" in line]


def test_the_family_art_has_its_own_manifest_and_it_is_accurate():
    """Not just present -- correct. Every listed checksum is recomputed."""
    lines = OVERLAY_MANIFEST.read_text(encoding="utf-8").splitlines()
    assert len(lines) == CR_COUNT + 1  # + Direct-Play.png
    for line in lines:
        digest, _, relative = line.partition("  ")
        path = ASSETS / relative
        assert path.exists(), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative


def test_the_cr_directory_holds_the_pinned_regional_count():
    assert len(list((IMAGES / "cr").glob("*.png"))) == CR_COUNT


def test_every_family_definition_names_an_image_that_exists():
    """`overlays/sources.py::resolve_image_path` REFUSES a `builtin:` naming a
    missing file, so a mistyped filename is a hard per-definition failure at
    render time. This is the transcription's real checksum: it catches every
    wrong region prefix, wrong bucket spelling and wrong colour suffix at
    once."""
    missing = []
    for family, definitions in FAMILIES.items():
        for definition in definitions:
            assert definition.builtin, f"{family}/{definition.name} names no image"
            path = IMAGES / (definition.builtin + ".png")
            if not path.exists():
                missing.append(f"{family}/{definition.name} -> {definition.builtin}")
    assert missing == []


def test_direct_play_is_one_definition_with_the_measured_box():
    """Probe section 4.5 / grammar probe section 5.1: box 305x170 -- the one
    family in row 100 with a non-105 height -- centred, bottom, offset 30."""
    definitions = FAMILIES["direct_play"]
    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.name == "Direct-Play"
    assert definition.builtin == "Direct-Play"
    assert definition.condition == {"resolution.regex": "(?i)2160|4k"}
    assert (definition.back_width, definition.back_height) == (305, 170)
    assert definition.back_color == "#00000099"
    assert definition.back_radius == 30
    assert (definition.horizontal_align, definition.horizontal_offset) == ("center", 0)
    assert (definition.vertical_align, definition.vertical_offset) == ("bottom", 30)


def test_every_regional_definition_shares_the_measured_regional_box():
    """Probe section 4.3: all six regions hard-code 305x105 r30 at
    left/15, bottom/270."""
    for family, definitions in FAMILIES.items():
        if not family.startswith("content_rating_"):
            continue
        assert definitions, family
        for definition in definitions:
            assert (definition.back_width, definition.back_height) == (305, 105), definition.name
            assert definition.back_radius == 30, definition.name
            assert definition.back_color == "#00000099", definition.name
            assert (definition.horizontal_align, definition.horizontal_offset) == ("left", 15)
            assert (definition.vertical_align, definition.vertical_offset) == ("bottom", 270)


def test_the_us_movie_g_bucket_carries_kometas_own_alias_list_verbatim():
    """The one bucket the probe banked verbatim
    (`content_rating_us_movie.yml:51`). Pinned as data, because the alias
    tables are the whole of the regional logic and a dropped alias is a
    silently-wrong badge, not a crash. Note the `gb/` and `no/` prefixes:
    those are PLEX agent spellings, and no Common Sense value in
    `item_facts` ever looks like one -- adjudication A5's evidence."""
    definitions = {d.name: d for d in FAMILIES["content_rating_us_movie"]}
    assert definitions["us_movie_g"].condition == {
        "content_rating": [
            "1", "01", "2", "02", "3", "03", "4", "04", "5", "05", "6", "06",
            "G", "G - All Ages", "U", "gb/U", "gb/0+", "E", "gb/E", "A",
            "no/A", "TV-Y", "TV-G",
        ]
    }


# --- R1: alias-list completeness beyond the one hand-pinned `us_movie_g`
# bucket above. `test_every_family_definition_names_an_image_that_exists`
# and the box pins catch a wrong `builtin:` path or a wrong position; neither
# catches a DROPPED bucket or a truncated alias list -- the failure mode the
# plan itself names as the one that matters ("a dropped alias is not a
# crash; it is a silently missing badge"). Two nets: bucket NAMES the probe
# already gives for UK and US movie (section 4.3), pinned outright below; and
# a per-family (bucket count, total alias count) pair for every family,
# counted directly off the vendored YAML while Step 7 transcribes it -- never
# estimated, never copied from this document -- so a dropped bucket or a
# short alias list fails a count instead of passing silently.

FAMILY_ALIAS_COUNTS = {
    # <family>: (bucket_count, total_alias_count) -- filled in at Task 2
    # Step 7, counted directly off the vendored
    # `content_rating_<region>.yml` while reading it out. Every entry below
    # is `None` until Step 7 replaces it; a family still `None` when Step 8
    # runs means Step 7 is not actually done for it.
    "content_rating_us_movie": (6, 85),
    "content_rating_us_show": (6, 78),
    # EIGHT buckets, not the seven the recon probe named -- the vendored
    # content_rating_uk.yml carries a separate `12a` overlay (own alias
    # list, own image cr/uk12ac) alongside `12`; see families.py's own note
    # on CONTENT_RATING_UK and Task 2's report.
    "content_rating_uk": (8, 106),
    "content_rating_au": (7, 105),
    "content_rating_de": (7, 92),
    "content_rating_nz": (12, 98),
}


def test_every_family_transcribes_every_bucket_and_every_alias():
    """The completeness net Step 7's worked example alone cannot provide:
    every regional family's bucket count and total alias count, both counted
    directly off its own vendored YAML, not estimated from `us_movie_g`'s
    shape or guessed from this document."""
    for family, expected in FAMILY_ALIAS_COUNTS.items():
        assert expected is not None, f"{family}: record its (buckets, aliases) count"
        bucket_count, alias_count = expected
        definitions = FAMILIES[family]
        assert len(definitions) == bucket_count, family
        assert (
            sum(len(d.condition["content_rating"]) for d in definitions)
            == alias_count
        ), family


def test_the_uk_and_us_movie_bucket_names_are_all_present():
    """A spot-check beyond counts, for the two families whose bucket names
    the probe already gives by name (section 4.3): `u, pg, 12, 15, 18, r18,
    nr` for UK, `g, pg, pg-13, r, nc-17, nr` for US movie.

    The UK set below adds `12a` to the probe's seven: the vendored
    `content_rating_uk.yml` carries `12` and `12a` as two DISTINCT overlays,
    each with its own alias list and its own image (`cr/uk12c` /
    `cr/uk12ac`), and the recon's enumeration missed the split. Task 2's
    report names this divergence from the plan text; this pin follows the
    vendored source, not the probe's count. A missing bucket here is a
    dropped CONDITION, not a typo -- an item with that certification draws
    no regional badge at all, silently."""
    uk_names = {d.name for d in FAMILIES["content_rating_uk"]}
    assert uk_names == {
        f"uk_{b}" for b in ("u", "pg", "12", "12a", "15", "18", "r18", "nr")
    }
    us_movie_names = {d.name for d in FAMILIES["content_rating_us_movie"]}
    assert us_movie_names == {
        f"us_movie_{b}" for b in ("g", "pg", "pg-13", "r", "nc-17", "nr")
    }


def test_the_six_regions_ship_and_no_more():
    assert sorted(k for k in FAMILIES if k.startswith("content_rating_")) == [
        "content_rating_au",
        "content_rating_de",
        "content_rating_nz",
        "content_rating_uk",
        "content_rating_us_movie",
        "content_rating_us_show",
    ]


def test_versions_is_not_shipped_and_the_reason_is_recorded():
    """Adjudication A14: the family needs a `versions` row in
    `collections/filters.py`, which this phase may not edit. Pinned so that
    shipping it later is a deliberate act with a ruling behind it, not a
    quiet fill-in."""
    assert "versions" not in FAMILIES


# --- the operator surface: `badges.families` -------------------------------


def test_no_families_is_the_default_and_changes_nothing():
    """Gate-off. `all_definitions()` must be exactly `definitions` -- the
    same list every existing caller already reads."""
    config = BadgesConfig()
    assert config.families == []
    assert config.all_definitions() == []


def test_naming_a_family_expands_it_in_front_of_the_operators_own_definitions():
    """Family first, operator second, so an operator's own definition can
    suppress a family member with `suppress_overlays`."""
    own = OverlayDefinition(name="mine")
    config = BadgesConfig(families=["direct_play"], definitions=[own])
    expanded = config.all_definitions()
    assert expanded[-1] is own
    assert [d.name for d in expanded[:-1]] == ["Direct-Play"]


def test_expansion_is_not_stored_so_a_config_round_trip_cannot_double_it():
    """`definitions` itself is never mutated: the config editor round-trips
    config -> YAML -> config, and an expansion written back into
    `definitions` would be re-expanded on the next load."""
    config = BadgesConfig(families=["direct_play"])
    config.all_definitions()
    assert config.definitions == []


def test_an_unknown_family_is_refused_at_load_naming_what_exists():
    with pytest.raises(ValidationError) as caught:
        BadgesConfig(families=["ribbon"])
    message = str(caught.value)
    assert "ribbon" in message
    assert "direct_play" in message
    assert "content_rating_uk" in message


def test_naming_the_same_family_twice_is_refused():
    """Two copies of every definition would draw each one twice and double
    every group's members."""
    with pytest.raises(ValidationError):
        BadgesConfig(families=["direct_play", "direct_play"])
