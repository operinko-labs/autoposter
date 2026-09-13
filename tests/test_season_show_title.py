"""Roadmap row 78 -- the show's title on a season poster -- and row 43's gap.

Nothing in this file invokes ``magick``: the compositor's ``run`` and
``fit_point_size`` are stubbed the way ``tests/test_pipeline.py`` already
stubs them, and every assertion is over the argv ``build_text_argv`` returns
or over a config hash. So NOTHING here carries ``@pytest.mark.imagemagick`` --
CI's main pytest run deselects that marker on a runner with no binary, and a
marked non-compositing test would fail there and nowhere else (#149).

The three version literals below are the storm proof for this row: a new key
under one art kind's subsection must move THAT kind's render version and no
other's. If one of them moves for a key that lives under
``artwork.season_poster``, the partition in
``config/loader.py::render_version_for`` has stopped confining an edit and the
whole point of roadmap row 111 has been lost -- do not re-measure and paste a
new value for THAT.

They were re-measured exactly once, on 2026-09-08, for roadmap row 219, which
removed ``min_width``/``min_height`` from ``ArtKindConfig`` -- the model every
art kind is or subclasses. A field removed from the SHARED BASE moves all four
kinds by construction and says nothing about the partition, which is why that
re-measurement was legitimate and why the constant is no longer named for
row 78. ``PRE_ROW_78_WHOLESALE`` below is untouched: it is still a genuine
pre-row-78 value and the assertion that reads it is still an inequality.
"""
from pathlib import Path

import pytest
import yaml

from autoposter.config.loader import (
    build_config, load_config, moved_kinds, render_version, render_version_for,
)
from autoposter.config.schema import SeasonPosterConfig, TextStyle
from autoposter.plex.client import ResolvedItem
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import (
    SHOW_TITLE_GUTTER, compose_styled, show_title_for, stacked_above, title_text_for,
)
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

# The three kinds `artwork.season_poster.show_title` must not reach. Values
# re-measured 2026-09-08 for roadmap row 219 (see the module docstring); the
# constant is no longer named for row 78 because these are no longer that
# row's pre-change values.
THE_OTHER_THREE_VERSIONS = {
    "poster": "23fb7b54d7766a00",
    "background": "8221f72c1d0467e9",
    "title_card": "84750d93008ca70b",
}

# The WHOLESALE hash -- config.version -- as it stood before row 78's
# show_title key existed. Untouched by the 2026-09-08 row-219 re-measurement
# above: it is a genuine pre-row-78 value from a different, earlier
# measurement. Pinned so the disclosure is proven rather than asserted: the
# claim the PR body and deploy/README.md make is that the SCHEMA ADDITION
# moved this value, and only a pre-change literal can show that.
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
    """Posterizarr's ShowTitleOnSeasonPosterPart, transcribed -- with one
    deliberate exception.

    Twelve of its thirteen keys map 1:1 onto TextStyle and the thirteenth
    (AddShowTitletoSeason) is add_text. All thirteen are restated explicitly
    below, even where a value equals a TextStyle default -- exactly as the
    neighbouring ``text:`` block restates them. The three TextStyle fields
    upstream has no key for at all (``line_spacing``, ``stroke_color``,
    ``stroke_width``) are the ones actually omitted, and they ride on
    TextStyle's own identical defaults. ``font`` is OURS -- the upstream
    part carries no font key at all -- and matches the season block it sits
    above.

    ``text_offset`` is NOT transcribed verbatim: upstream ships ``"+300"``,
    the same value as the season block it sits above (which is why the
    stacking rule ignores this block's own offset entirely -- see
    ``stacked_above``). The shipped example instead ships ``"+120"`` here,
    a deliberate departure so that
    ``test_the_gate_on_draws_the_show_title_as_a_second_block`` can tell a
    correctly-derived stacked offset from a wiring bug that reads this
    block's own value: with a shared ``"+300"`` the two would coincide.
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
        "text_offset": "+120",
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
    """The storm proof, against the other three kinds' render
    versions.

    Roadmap row 111 confines a render version to the art kind whose settings
    the edit touched. ``artwork.season_poster.show_title`` is a member of
    exactly one kind's payload, so exactly one kind's version may move. The
    three digests below were re-measured on 2026-09-08, after roadmap row 219
    removed ``min_width``/``min_height`` from ``ArtKindConfig`` (see the
    module docstring); this test still proves that only the season-posters
    version moves when a season-only key changes.
    """
    for art_kind, digest in THE_OTHER_THREE_VERSIONS.items():
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
    ``api/routes._render_affecting``'s cheap superset short-circuit. Roadmap
    row 247 removed row 111's dual read, which was the third reader. Pinned
    here so a future reader does not read the movement as a defect, and so a
    change that made it stop moving -- which would break the short-circuit's
    superset property -- is loud.
    """
    assert render_version(config) != PRE_ROW_78_WHOLESALE, (
        "the schema ADDITION itself moved the wholesale hash -- that is the "
        "claim the PR body and deploy/README.md make, and it is the half a "
        "value-change assertion cannot prove"
    )
    # The wholesale hash, pinned absolutely rather than merely proven to
    # differ. It has now moved three times: by row 78's show_title block, by
    # a later correction (e987fc3d42d6bac9 ->
    # 386ea7cf4844f52e, the example's show_title.text_offset changing from
    # "+300" to "+120" so the seam test can tell which block's offset the
    # stacking rule actually reads), and on 2026-09-08 by roadmap row 219's
    # removal of ArtKindConfig.min_width/min_height, which took a field out of
    # the wholesale `artwork` dump. The value below is the post-row-219
    # measurement.
    assert render_version(config) == "01b443ab8dfcfbd2"

    off = render_version(config)
    on = load_config(EXAMPLE)
    on.artwork.season_poster.show_title.add_text = True
    assert render_version(on) != off


# --- the compositor block ---------------------------------------------------


def _season(show_title="Severance", season_number=2, title="Season 2"):
    return ResolvedItem(
        server="plex", native_id="556", library="TV Shows", kind="season", title=title,
        year=None, season_number=season_number, episode_number=None,
        root_folder="Severance (2022)", file_path=None, art_url=None,
        tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_native_id="555", show_title=show_title,
    )


def _stub_magick(monkeypatch, point_size=120):
    """Record every argv ``compositor.run`` is handed, invoking no ImageMagick.

    ``fit_point_size`` is the only other function on this path that shells
    out, so it is stubbed too -- exactly what
    ``tests/test_pipeline.py::_stub_out_imagemagick`` does, and the reason
    nothing in this file carries the imagemagick marker.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: calls.append(argv))
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=point_size, truncated=False),
    )
    return calls


def _captions(calls):
    """The (caption text, gravity, geometry) of every text block drawn."""
    drawn = []
    for argv in calls:
        tokens = [str(t) for t in argv]
        caption = next((t[len("caption:"):] for t in tokens if t.startswith("caption:")), None)
        if caption is None:
            continue
        geometry = tokens[tokens.index("-geometry") + 1]
        # The LAST "-gravity" and the token after it. `build_text_argv` emits
        # three: "-gravity center" up front (compositor.py:130), one inside
        # `_caption_group` (:90), and the block's own in the tail (:143) --
        # only the third is `style.gravity`. `len - index_of_last_in_reversed`
        # is that token's own index PLUS ONE, which is exactly the value to
        # read; subtracting one more would hand back the literal "-gravity".
        # "-geometry" appears once (:144), so its half needs no such care.
        gravity = tokens[len(tokens) - tokens[::-1].index("-gravity")]
        drawn.append((caption, gravity, geometry))
    return drawn


async def test_the_gate_off_draws_exactly_one_block_on_a_season_poster(
    config, tmp_path, monkeypatch,
):
    """Byte-identical to before this row while the gate is off, at the seam
    itself: one caption, the season text's, at its own configured offset."""
    calls = _stub_magick(monkeypatch)
    working = tmp_path / "season.jpg"
    working.write_bytes(b"base")
    primary, secondary = title_text_for("season_poster", _season(), config)
    assert secondary is None, "no show title is produced while the gate is off"

    await compose_styled(
        config, "season_poster", working,
        primary_text=primary, secondary_text=secondary,
        draw_text=True, logo_path=None,
    )

    assert _captions(calls) == [("SEASON 2", "south", "+0+300")]


async def test_the_gate_on_draws_the_show_title_as_a_second_block(
    config, tmp_path, monkeypatch,
):
    """Two captions, the show title second -- it must be drawn AFTER the season
    block, because its offset is computed from that block's fitted size."""
    config.artwork.season_poster.show_title.add_text = True
    calls = _stub_magick(monkeypatch, point_size=120)
    working = tmp_path / "season.jpg"
    working.write_bytes(b"base")
    primary, secondary = title_text_for("season_poster", _season(), config)
    assert (primary, secondary) == ("Season 2", "Severance")

    await compose_styled(
        config, "season_poster", working,
        primary_text=primary, secondary_text=secondary,
        draw_text=True, logo_path=None,
    )

    drawn = _captions(calls)
    assert [d[0] for d in drawn] == ["SEASON 2", "SEVERANCE"]
    assert drawn[0][1:] == ("south", "+0+300")
    # The DERIVED offset, not the show-title block's OWN configured "+120"
    # (see the example's ships-"+120" note above), and this assertion is the
    # point of the test. `stacked_above` is pinned as a pure function below,
    # but a pure pin cannot see whether `compose_styled` actually APPLIES it:
    # drop the `model_copy` override from the loop and every other test in
    # this plan stays green while the show title composites at its own
    # configured +0+120 instead of the derived +0+430 -- the exact
    # helper-passes-but-the-wiring-differs class of bug this suite has been burned
    # by three times. Before an earlier correction, the example gave
    # both blocks the same "+300", so this drop would have silently landed
    # the two blocks on top of each other (300 == 300) rather than reddening
    # this assertion; shipping "+120" here is what makes the wiring bug
    # visible as a wrong NUMBER rather than a coincidentally-right one. The
    # stub fits the season block at 120pt, so the derived offset is
    # season's own "+300" + 120 (fitted size) + 10 (gutter) = 430.
    assert drawn[1] == ("SEVERANCE", "south", "+0+430")


async def test_the_gate_on_draws_the_show_title_at_the_season_blocks_gravity(
    config, tmp_path, monkeypatch,
):
    """The derived offset's SIGN comes from the season
    block's gravity (``stacked_above``), so the drawn caption's ANCHOR must
    come from the same place or the sign and the anchor disagree -- an
    operator who moves the season text to a non-bottom gravity without
    touching the show-title block would otherwise get a title drawn hundreds
    of pixels from where the derived offset assumed it would land.

    The season block is retargeted to ``north``/``+300``; the show-title
    block keeps the example's own ``south`` UNCHANGED, so a fix that copies
    ``text_offset`` but not ``gravity`` would still draw this caption at
    "south" and this assertion would catch it.
    """
    config.artwork.season_poster.text.gravity = "north"
    config.artwork.season_poster.show_title.add_text = True
    assert config.artwork.season_poster.show_title.gravity == "south", (
        "the show-title block's OWN gravity is untouched -- proving the "
        "drawn gravity below comes from the SEASON block, not a coincidence"
    )
    calls = _stub_magick(monkeypatch, point_size=120)
    working = tmp_path / "season.jpg"
    working.write_bytes(b"base")
    primary, secondary = title_text_for("season_poster", _season(), config)

    await compose_styled(
        config, "season_poster", working,
        primary_text=primary, secondary_text=secondary,
        draw_text=True, logo_path=None,
    )

    drawn = _captions(calls)
    assert drawn[0] == ("SEASON 2", "north", "+0+300")
    # "north" is not bottom-anchored, so `stacked_above` SUBTRACTS:
    # 300 - (120 fitted + 10 gutter) = 170. The show title must draw at
    # "north" too -- the SEASON block's gravity -- not its own configured
    # "south".
    assert drawn[1] == ("SEVERANCE", "north", "+0+170")


def test_the_show_title_stacks_one_line_above_the_season_text(config):
    """The layout adjudication, pinned as computed offsets rather than pixels.

    No oracle exists: Kometa has no season-poster compositing at all, and
    Posterizarr's own toggle ships false so its values -- the SAME +300/south
    the season text uses -- were never tuned against a render. Drawn as
    configured the two blocks overlap. The ruling is that the show title
    stacks above the season text by one line of it plus a gutter.

    The SIGN is the part that is not a detail. ``build_text_argv`` composites
    with ``-gravity south -geometry +0<offset>`` and ImageMagick measures that
    offset INWARD from the named edge, so under a bottom gravity a LARGER
    value is HIGHER. Adding is what "above" means here.
    """
    season = config.artwork.season_poster.text
    assert (season.text_offset, season.gravity) == ("+300", "south")
    assert SHOW_TITLE_GUTTER == 10

    assert stacked_above(season, 120) == "+430"
    assert stacked_above(season, 250) == "+560"
    # The season block's FITTED size, not its configured maximum: a title that
    # auto-fitted small must not push the show title a hundred points clear.
    assert stacked_above(season, 100) != stacked_above(season, season.max_point_size)


def test_a_non_bottom_gravity_stacks_by_subtracting(config):
    """Every gravity that is not bottom-anchored measures a positive Y offset
    DOWNWARD, so "above" is the other direction there. Two lines of code, and
    the alternative is a silently upside-down layout for an operator who moved
    the season text to the top of the poster."""
    north = TextStyle(
        min_point_size=100, max_point_size=250, max_width=1200,
        max_height=485, text_offset="+300", gravity="north",
    )
    assert stacked_above(north, 120) == "+170"

    # And it may legitimately cross zero: TextStyle.text_offset is validated to
    # carry a sign, so a negative result is spelled with its own minus and is
    # still a value build_text_argv can concatenate after "+0".
    shallow = TextStyle(
        min_point_size=100, max_point_size=250, max_width=1200,
        max_height=485, text_offset="+50", gravity="north",
    )
    assert stacked_above(shallow, 120) == "-80"


def test_stacked_above_treats_gravity_case_insensitively(config):
    """``TextStyle.gravity`` carries no validator and no normalisation
    (unlike the collection side's vocabulary check), and ImageMagick's own
    ``-gravity`` argument matches case-insensitively -- "South" and
    "SOUTHEAST" are both legal today and both render identically to "south".
    A case-sensitive ``startswith`` would take the SUBTRACT branch for
    either, landing the show title on top of the season text instead of
    above it. Both spellings below must still ADD."""
    capitalized = TextStyle(
        min_point_size=100, max_point_size=250, max_width=1200,
        max_height=485, text_offset="+300", gravity="South",
    )
    assert stacked_above(capitalized, 120) == "+430"

    shouty = TextStyle(
        min_point_size=100, max_point_size=250, max_width=1200,
        max_height=485, text_offset="+300", gravity="SOUTHEAST",
    )
    assert stacked_above(shouty, 120) == "+430"


async def test_the_show_title_is_not_drawn_when_draw_text_is_off(
    config, tmp_path, monkeypatch,
):
    """The block obeys the EXISTING draw_text flag and gains no
    precedence logic of its own. draw_text is already False for a local source
    with skip_local_text_add on (row 39), for a suppressed-styling candidate
    (row 41), and -- for posters -- when a logo took the text's place. Sitting
    inside that arm inherits all three and invents no fourth rule."""
    config.artwork.season_poster.show_title.add_text = True
    calls = _stub_magick(monkeypatch)
    working = tmp_path / "season.jpg"
    working.write_bytes(b"base")
    primary, secondary = title_text_for("season_poster", _season(), config)

    await compose_styled(
        config, "season_poster", working,
        primary_text=primary, secondary_text=secondary,
        draw_text=False, logo_path=None,
    )

    assert _captions(calls) == []


async def test_the_show_title_font_enters_the_asset_hashes_only_when_the_gate_is_on(
    config, tmp_path,
):
    """The font gate, which is the half that fails SILENTLY if it is missed.

    ``text_inputs`` picks the show title up for free -- the existing line
    already folds in every truthy element of (primary, secondary). The
    ``asset_hashes`` half does not: without the season branch, swapping the
    show title's font would change the rendered image and move no fingerprint.
    """
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "Comfortaa-Medium.ttf").write_bytes(b"the season font")
    (fonts / "Other-Font.ttf").write_bytes(b"a different font")
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / "bottom-up-fade.png").write_bytes(b"the season poster overlay")
    config.fonts_root = fonts
    config.overlays_root = overlays

    off_texts, off_hashes = await pipeline_module.gather_fingerprint_inputs(
        config, _season(), "season_poster"
    )
    assert off_texts == ["Season 2"]

    config.artwork.season_poster.show_title.add_text = True
    on_texts, on_hashes = await pipeline_module.gather_fingerprint_inputs(
        config, _season(), "season_poster"
    )
    assert on_texts == ["Season 2", "Severance"]
    assert len(on_hashes) == len(off_hashes) + 1

    config.artwork.season_poster.show_title.font = "Other-Font.ttf"
    swapped_texts, swapped_hashes = await pipeline_module.gather_fingerprint_inputs(
        config, _season(), "season_poster"
    )
    assert swapped_texts == on_texts, "the same strings"
    assert swapped_hashes != on_hashes, (
        "a font swap on the new block must move the fingerprint, or a "
        "re-styled library silently never re-renders"
    )


def test_show_title_for_refuses_wrong_kind_missing_style_and_gate_off(config):
    """``show_title_for``'s three documented refusals, pinned directly rather
    than only transitively through ``title_text_for``, which never takes the
    ``style is None`` arm (the example config always ships the block)."""
    config.artwork.season_poster.show_title.add_text = True
    season = _season()

    assert show_title_for("title_card", season, config) is None, (
        "every art kind but season_poster refuses, before it even looks at "
        "the item"
    )

    unset = load_config(EXAMPLE)
    unset.artwork.season_poster.show_title = None
    assert show_title_for("season_poster", season, config=unset) is None, (
        "an unset show_title block refuses"
    )

    config.artwork.season_poster.show_title.add_text = False
    assert show_title_for("season_poster", season, config) is None, (
        "the block's own gate off refuses"
    )

    config.artwork.season_poster.show_title.add_text = True
    assert show_title_for("season_poster", season, config) == "Severance"


# --- row 43's gap, co-delivered ----------------------------------


def test_a_season_name_override_renames_the_season_posters_own_text(config):
    """Posterizarr puts OverrideSeasonName in SeasonPosterOverlayPart -- it
    renames the text on the SEASON POSTER -- while roadmap row 43 landed the
    table on TitleCardConfig and wired it only to the card's second line. The
    same EXISTING table is read here; no second key is added, so an operator
    who already wrote {"0": "Specials"} gets it on both artifacts at once."""
    config.artwork.title_card.season_name_overrides = {"0": "Specials", "2": "Series 2"}

    assert title_text_for("season_poster", _season(season_number=2), config)[0] == "Series 2"
    assert title_text_for(
        "season_poster", _season(season_number=0, title="Specials"), config
    )[0] == "Specials"
    # A season not listed keeps whatever Plex called it -- the same fail-open
    # the title card's own arm has.
    assert title_text_for(
        "season_poster", _season(season_number=5, title="Season 5"), config
    )[0] == "Season 5"

    # And a DEGENERATE entry means the same thing on both artifacts. The table
    # is shared, so `.get(key, default)` is used here exactly as the title
    # card uses it: a blanked entry draws nothing, on the poster and on the
    # card alike, rather than silently falling back on one of them.
    config.artwork.title_card.season_name_overrides = {"2": ""}
    assert title_text_for("season_poster", _season(season_number=2), config)[0] == ""


async def test_a_blank_season_override_leaves_the_show_title_at_its_own_offset(
    config, tmp_path, monkeypatch,
):
    """The one path on which the show title's OWN ``text_offset`` AND
    ``gravity`` are live.

    A blanked ``season_name_overrides`` entry blanks the season text (pinned
    above); ``compose_styled``'s loop then skips that block entirely
    (``if style is None or not text: continue``), so ``primary_point_size``
    stays ``None`` and the ``style is show_title_style and primary_point_size
    is not None`` guard never fires. The show title draws at its own
    CONFIGURED offset and gravity instead of derived ones -- defensible,
    since there is no season text left to overlap, but unpinned until now.
    """
    config.artwork.title_card.season_name_overrides = {"2": ""}
    config.artwork.season_poster.show_title.add_text = True
    calls = _stub_magick(monkeypatch)
    working = tmp_path / "season.jpg"
    working.write_bytes(b"base")
    primary, secondary = title_text_for("season_poster", _season(), config)
    assert primary == "", "the blanked override reaches the season poster's own text"
    assert secondary == "Severance"

    await compose_styled(
        config, "season_poster", working,
        primary_text=primary, secondary_text=secondary,
        draw_text=True, logo_path=None,
    )

    # Only ONE caption: a blank season text means "nothing to draw", not
    # "draw an empty caption" -- and the show title's gravity and geometry
    # are its own configured "south"/"+120", not derived values.
    assert _captions(calls) == [("SEVERANCE", "south", "+0+120")]


def test_the_title_cards_second_line_is_unchanged(config):
    """The regression guard on the other side of the shared table: row 78
    READS it and must not reshape it. An override still replaces the whole
    season half of the card's second line, label and number both."""
    config.artwork.title_card.season_name_overrides = {"0": "Specials"}
    episode = ResolvedItem(
        server="plex", native_id="557", library="TV Shows", kind="episode",
        title="Who Is Alive?",
        year=None, season_number=0, episode_number=3, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
    )
    primary, secondary = title_text_for("title_card", episode, config)
    assert primary == "Who Is Alive?"
    assert secondary is not None and secondary.startswith("Specials ")
    assert "Specials 0" not in secondary


def test_a_season_name_override_moves_the_season_posters_version_too(config):
    """The hole row 78 would otherwise open in row 111's partition.

    ``season_name_overrides`` lives under ``artwork.title_card``, so row 111
    confines it to the title_card kind's payload. From this row on it also
    decides what a SEASON POSTER draws -- so without a projection, editing it
    would move the title cards and leave every season poster serving text the
    config no longer describes. ``render_version_for`` projects the one key
    into season_poster's payload, mirroring how it already projects
    ``season_episode_templates`` into two kinds.
    """
    edited = load_config(EXAMPLE)
    edited.artwork.title_card.season_name_overrides = {"0": "Specials"}
    assert moved_kinds(config, edited) == {"season_poster", "title_card"}

    # And the projection is one key, not the whole title_card subsection: a
    # title-card-only edit still reaches title_card alone.
    label = load_config(EXAMPLE)
    label.artwork.title_card.season_label = "Kausi"
    assert moved_kinds(config, label) == {"title_card"}
