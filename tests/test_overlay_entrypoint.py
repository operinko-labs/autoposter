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

from autoposter.badges.compose import compose
from autoposter.config.schema import BadgesConfig
from autoposter.overlays.schema import OverlayDefinition
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
