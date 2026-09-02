"""The gated-feature entry-point law for operator-defined overlays.

The proof is made through the REAL entry point -- badges/compose.py::compose,
which render/pipeline.py::apply_badges calls -- not through
overlays/render.py alone. With no definitions configured, the output must be
byte-identical to the recorded pre-swap baseline.
"""
import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image

from autoposter.badges.compose import badge_fingerprint, compose
from autoposter.config.schema import BadgesConfig
from autoposter.db.models import MediaItem, Render
from autoposter.overlays.schema import OverlayDefinition
from autoposter.render.pipeline import apply_badges
# Bare module import, not `tests.test_overlay_engine_golden`: this repo has no
# tests/__init__.py, so `tests` is not an importable package -- the precedent
# is test_config_safety.py's `from test_api_config_editor import (...)`.
from test_overlay_engine_golden import (
    ALL_SOULS,
    POSTER_PIXELS_SHA,
    pixels_sha,
)

ORACLE = Path("tests/fixtures/oracle")
BASE = ORACLE / "All_Souls_base_no_overlay.jpg"


def _sha(data: bytes) -> str:
    array = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.uint8)
    return hashlib.sha256(array.tobytes()).hexdigest()


def test_the_default_config_configures_no_definitions():
    """The gate-off value is the empty list, which is what makes 'no change
    for a config that does not set the key' checkable rather than asserted."""
    assert BadgesConfig().definitions == []


def test_no_definitions_is_byte_identical_to_the_pre_swap_baseline():
    """Global Constraint 8. Compared against the RECORDED hash, not against a
    second call to the same function -- a re-derivation would pass even if
    both sides changed together."""
    assert _sha(compose(BASE, "poster", ALL_SOULS, definitions=[])) == POSTER_PIXELS_SHA


def test_passing_no_definitions_argument_at_all_is_also_byte_identical():
    """Every existing caller omits the argument; none of them may move."""
    assert pixels_sha(BASE, "poster", ALL_SOULS) == POSTER_PIXELS_SHA


def test_one_definition_changes_the_output():
    """The other half of the gate: on must differ from off, or the gate is
    proving nothing."""
    stamp = OverlayDefinition(
        name="text(HELLO)",
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100,
        back_color="#FF0000FF", back_radius=10,
        font_size=55,
    )
    data = compose(BASE, "poster", ALL_SOULS, definitions=[stamp])
    assert _sha(data) != POSTER_PIXELS_SHA


def test_a_definition_naming_an_unresolvable_variable_is_skipped_not_fatal():
    """Probe section 2.4: an unresolved variable is a per-item overlay skip
    with a warning, not a run abort. The other overlays still draw, so the
    output equals the no-definitions baseline exactly."""
    stamp = OverlayDefinition(
        name="text(<<trakt_user_rating>>)",
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100,
        back_color="#FF0000FF", back_radius=10,
        font_size=55,
    )
    assert _sha(compose(BASE, "poster", ALL_SOULS, definitions=[stamp])) == POSTER_PIXELS_SHA


def test_a_group_keeps_only_the_highest_weight_member():
    """Probe section 4.1: group resolution is winner-take-highest, per item."""
    def _stamp(name, weight, colour):
        return OverlayDefinition(
            name=name, group="ribbon", weight=weight,
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
            back_width=300, back_height=100,
            back_color=colour, back_radius=10, font_size=55,
        )

    both = compose(BASE, "poster", ALL_SOULS, definitions=[
        _stamp("text(LOW)", 10, "#00FF00FF"), _stamp("text(HIGH)", 190, "#FF0000FF"),
    ])
    winner_only = compose(BASE, "poster", ALL_SOULS, definitions=[
        _stamp("text(HIGH)", 190, "#FF0000FF"),
    ])
    assert _sha(both) == _sha(winner_only)


def test_suppression_is_resolved_before_group_weight():
    """Probe section 4.1: if A suppresses B and both match, B is dropped
    outright -- group weight never arbitrates that pair."""
    suppressor = OverlayDefinition(
        name="text(A)", group="g", weight=10, suppress_overlays=["text(B)"],
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100, back_color="#FF0000FF",
        back_radius=10, font_size=55,
    )
    suppressed = OverlayDefinition(
        name="text(B)", group="g", weight=190,
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100, back_color="#00FF00FF",
        back_radius=10, font_size=55,
    )
    alone = compose(BASE, "poster", ALL_SOULS, definitions=[suppressor])
    together = compose(BASE, "poster", ALL_SOULS, definitions=[suppressor, suppressed])
    # B has the higher weight; without suppression it would win. It is dropped.
    assert _sha(together) == _sha(alone)


# --- badge_fingerprint must cover `definitions` (Finding 1), without moving
# the gate-off value (the storm guard: this repo already paid for a
# fingerprint that shifted for every already-badged item once, in the
# mass-ops additive-keys re-fingerprint that invalidated ~18k rows) --------


def test_the_gate_off_fingerprint_is_pinned_byte_identical():
    """Pinned against a literal computed on the pre-Finding-1 formula, not
    re-derived from the function under test -- a re-derivation would pass
    even if the formula and the pin moved together. Gate-off (no
    `definitions` argument at all) must never shift, or every one of the
    ~16k already-badged items in a library that configures no overlays
    re-badges for nothing."""
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6", "audience": "63%"}, "manifest-sha",
    ) == "576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd"


def test_an_empty_definitions_list_does_not_move_the_fingerprint():
    """The other half of the same guard: an explicit `[]` must be
    indistinguishable from the argument being omitted."""
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [],
    ) == badge_fingerprint("base-fp", "poster", {"critic": "8.6"}, "manifest-sha")


def test_configuring_a_definition_changes_the_fingerprint():
    stamp = OverlayDefinition(name="text(HELLO)")
    without = badge_fingerprint("base-fp", "poster", {"critic": "8.6"}, "manifest-sha")
    with_one = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp],
    )
    assert with_one != without


def test_removing_a_definition_reverts_the_fingerprint():
    stamp = OverlayDefinition(name="text(HELLO)")
    with_one = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp],
    )
    reverted = badge_fingerprint("base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [])
    assert reverted == badge_fingerprint("base-fp", "poster", {"critic": "8.6"}, "manifest-sha")
    assert reverted != with_one


# --- the real entry point: render.pipeline.apply_badges, not compose() ------
#
# Finding 2 (GC8): every proof above calls compose() directly. Nothing
# anywhere calls apply_badges with a non-empty config.badges.definitions, so
# the per-definition resolution loop, the OverlaySourceError skip, the
# `extra` kwargs assembly and `http=` threading are all wired but untested --
# and Finding 1 (a configured overlay never reaching an already-uploaded
# item) lives exactly in that untested gap.


class _FakePlexItem:
    def __init__(self):
        self.media = [type("M", (), {"parts": [type("P", (), {"file": None})()],
                                     "videoResolution": "1080", "audioCodec": "eac3",
                                     "audioChannels": 6})()]
        self.duration = 4845912
        self.seasonNumber = None
        self.episodeNumber = None
        self.uploads = 0
        self.last_bytes = None

    def uploadPoster(self, url=None, filepath=None):
        self.uploads += 1
        self.last_bytes = Path(filepath).read_bytes()

    def lockPoster(self):
        pass


class _Facts:
    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = "17"


async def _render(session, rating_key="overlay-entrypoint-item"):
    item = MediaItem(kind="movie", rating_key=rating_key, library="Movies", title="X")
    session.add(item)
    await session.flush()
    render = Render(
        item_id=item.id, art_kind="poster", base_sha256="abc",
        status="rendered", asset_path=str(BASE),
    )
    session.add(render)
    await session.flush()
    return item, render


async def test_apply_badges_draws_a_file_sourced_definition_through_the_real_entry_point(
    session, config_with_badges, tmp_path
):
    """Closes Finding 1 and Finding 2 together: this is the one test the
    review named -- one definition configured, run through the real
    `apply_badges`, a second pass over the SAME already-uploaded render after
    the config changes."""
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(stamp, format="PNG")
    config_with_badges.overlays_root = tmp_path
    config_with_badges.badges.definitions = []

    item, render = await _render(session)
    plex_item = _FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 1
    baseline_bytes = plex_item.last_bytes
    baseline_fingerprint = render.badge_fingerprint

    # Configure one definition on the SAME already-uploaded render. Without
    # Finding 1's fix the gate at pipeline.py:1332 sees an unchanged
    # fingerprint and `upload_status == "uploaded"` and returns early --
    # the new overlay silently never reaches Plex.
    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", file="stamp.png",
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 2, "configuring a definition must re-badge an already-uploaded item"
    assert plex_item.last_bytes != baseline_bytes, "the definition must actually be drawn"
    assert render.badge_fingerprint != baseline_fingerprint

    # Remove it again: the fingerprint must revert exactly, and Plex must
    # receive the original (un-stamped) bytes back.
    config_with_badges.badges.definitions = []
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 3
    assert render.badge_fingerprint == baseline_fingerprint
    assert plex_item.last_bytes == baseline_bytes


async def test_a_definition_whose_image_fails_to_resolve_does_not_stamp_an_empty_backdrop(
    session, config_with_badges, tmp_path
):
    """Finding 3 (MEDIUM 4): `resolve_image_path` raising OverlaySourceError
    logs "skipping it", but the definition itself must not reach compose() --
    otherwise a `back_color` with no image draws a visible empty backdrop
    rectangle from a line that claims to have skipped the overlay.

    Compared by pixels (`_sha`), not raw bytes: Finding 1's fix folds
    `config.badges.definitions` itself into the fingerprint, so a config that
    NAMES a (failing) definition legitimately stamps different EXIF
    provenance than one that names none -- that is the fingerprint gate
    working, not a bug. What must be identical is what got drawn.
    """
    config_with_badges.overlays_root = tmp_path
    broken = OverlayDefinition(
        name="broken", file="does-not-exist.png",
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100, back_color="#FF0000FF",
    )

    config_with_badges.badges.definitions = []
    baseline_item, baseline_render = await _render(session, rating_key="baseline")
    baseline_plex = _FakePlexItem()
    await apply_badges(
        session, config_with_badges, baseline_render, baseline_item, baseline_plex, _Facts()
    )

    config_with_badges.badges.definitions = [broken]
    item, render = await _render(session, rating_key="broken-definition")
    plex_item = _FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())

    assert _sha(plex_item.last_bytes) == _sha(baseline_plex.last_bytes)
