"""Roadmap row 78 -- the show's title on a season poster -- and row 43's gap.

Nothing in this file invokes ``magick``: the compositor's ``run`` and
``fit_point_size`` are stubbed the way ``tests/test_pipeline.py`` already
stubs them, and every assertion is over the argv ``build_text_argv`` returns
or over a config hash. So NOTHING here carries ``@pytest.mark.imagemagick`` --
CI's main pytest run deselects that marker on a runner with no binary, and a
marked non-compositing test would fail there and nowhere else (#149).

The three version literals below were measured on ``origin/main`` BEFORE the
``artwork.season_poster.show_title`` key existed, from the shipped example
config. They are the storm proof: a new key under one art kind's subsection
must move THAT kind's render version and no other's. If one of them moves, the
partition in ``config/loader.py::render_version_for`` has stopped confining an
edit and the whole point of roadmap row 111 has been lost -- do not re-measure
and paste a new value.
"""
from pathlib import Path

import pytest
import yaml

from autoposter.config.loader import (
    build_config, load_config, moved_kinds, render_version, render_version_for,
)
from autoposter.config.schema import SeasonPosterConfig, TextStyle

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

# Measured on origin/main before this row's key existed. See the module
# docstring.
PRE_ROW_78_VERSIONS = {
    "poster": "4ac64b5874ce0ff3",
    "background": "9ae9ab3b95ae68ec",
    "title_card": "31f00cfe0ef31fba",
}

# The WHOLESALE hash -- config.version -- as it stood before the key existed,
# from the same measurement. Pinned so the disclosure is proven rather than
# asserted: the claim the PR body and deploy/README.md make is that the SCHEMA
# ADDITION moved this value, and only a pre-change literal can show that.
PRE_ROW_78_WHOLESALE = "d66ab041c795004a"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def _document() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_the_show_title_block_is_off_by_default():
    """Off by CONSTRUCTION, not by a default that happens to be falsy.

    ``show_title`` unset is ``None``, which ``show_title_for`` refuses before
    it ever looks at the item -- the same shape ``TitleCardConfig.episode_text``
    has had since it shipped. A config that never mentions the key draws
    exactly what it drew before this row.
    """
    bare = SeasonPosterConfig(overlay_file="bottom-up-fade.png")
    assert bare.show_title is None

    style = TextStyle(
        min_point_size=45, max_point_size=300, max_width=1900,
        max_height=500, text_offset="+300",
    )
    assert style.add_text is True, (
        "TextStyle's own default is on; the OFF-ness of this feature comes "
        "from the field being unset, and the example config states add_text "
        "false explicitly on top of that"
    )
    assert SeasonPosterConfig(
        overlay_file="bottom-up-fade.png", show_title=style,
    ).show_title is style


def test_the_example_config_ships_the_upstream_values_with_the_gate_off():
    """Posterizarr's ShowTitleOnSeasonPosterPart, transcribed.

    Twelve of its thirteen keys map 1:1 onto TextStyle and the thirteenth
    (AddShowTitletoSeason) is add_text. The values that equal a TextStyle
    default are not restated in the example, exactly as the neighbouring
    ``text:`` block does not restate them. ``font`` is OURS -- the upstream
    part carries no font key at all -- and matches the season block it sits
    above.
    """
    block = _document()["artwork"]["season_poster"]["show_title"]
    assert block == {
        "font": "Comfortaa-Medium.ttf",
        "all_caps": True,
        "font_color": "white",
        "min_point_size": 45,
        "max_point_size": 300,
        "max_width": 1900,
        "max_height": 500,
        "text_offset": "+300",
        "gravity": "south",
        "add_text": False,
        "add_stroke": False,
        "newline_on_symbols": [],
        "newline_words": {},
    }
    config = build_config(_document())
    assert config.artwork.season_poster.show_title is not None
    assert config.artwork.season_poster.show_title.add_text is False, (
        "the shipped gate is OFF: upstream's own AddShowTitletoSeason is false"
    )


def test_only_the_season_posters_version_moves(config):
    """The storm proof (facts C2), against literals measured before the key
    existed.

    Roadmap row 111 confines a render version to the art kind whose settings
    the edit touched. ``artwork.season_poster.show_title`` is a member of
    exactly one kind's payload, so exactly one kind's version may move. The
    three digests below are what ``origin/main`` produced for the shipped
    example config before this row; they must still be produced now.
    """
    for art_kind, digest in PRE_ROW_78_VERSIONS.items():
        assert render_version_for(art_kind, config) == digest, (
            f"{art_kind}'s render version moved for a key that is not in its "
            "payload -- row 111's partition has stopped confining an edit"
        )

    # And the gate flip itself reaches one kind and one kind only. `moved_kinds`
    # is row 111's own helper, so this is the partition answering in its own
    # words rather than three hashes compared by hand.
    flipped = load_config(EXAMPLE)
    flipped.artwork.season_poster.show_title.add_text = True
    assert moved_kinds(config, flipped) == {"season_poster"}

    # Retuning a knob inside the block, gate on, is still one kind.
    retuned = load_config(EXAMPLE)
    retuned.artwork.season_poster.show_title.add_text = True
    retuned.artwork.season_poster.show_title.max_point_size = 280
    assert moved_kinds(flipped, retuned) == {"season_poster"}


def test_the_wholesale_render_version_moves_and_that_is_expected(config):
    """The disclosure, asserted rather than left to the PR body.

    ``render_version`` -- which is ``config.version`` -- stays the WHOLESALE
    hash of the artwork section under row 111, so adding any key under
    ``artwork`` moves it. Since row 111 that value is no longer a component of
    any render fingerprint (``render/pipeline.py`` fingerprints against
    ``render_version_for``), so no CURRENT row re-composites because of
    it: it is the config editor's "version A to B" line and
    ``api/routes._render_affecting``'s cheap superset short-circuit. The one
    place it is still load-bearing is row 111's dual read, which computes a
    pre-111 row's LEGACY candidate from it -- so a not-yet-migrated row of any
    art kind takes the grandfather's ``unchanged`` arm on its next pass and is
    re-stamped with its per-kind value, with no composite, no provider call and
    no upload. Task 1 Step 1b makes that re-stamp a precondition of executing
    this plan at all. Pinned here so a future reader does not read the movement
    as a defect, and so a change that made it stop moving -- which would break
    the short-circuit's superset property -- is loud.
    """
    assert render_version(config) != PRE_ROW_78_WHOLESALE, (
        "the schema ADDITION itself moved the wholesale hash -- that is the "
        "claim the PR body and deploy/README.md make, and it is the half a "
        "value-change assertion cannot prove"
    )

    off = render_version(config)
    on = load_config(EXAMPLE)
    on.artwork.season_poster.show_title.add_text = True
    assert render_version(on) != off
