"""The gated-feature entry-point law for operator-defined overlays.

The proof is made through the REAL entry point -- badges/compose.py::compose,
which render/pipeline.py::apply_badges calls -- not through
overlays/render.py alone. With no definitions configured, the output must be
byte-identical to the recorded pre-swap baseline.
"""
import asyncio
import hashlib
import io
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from autoposter.badges.compose import badge_fingerprint, compose
from autoposter.config.schema import BadgesConfig
from autoposter.db.models import Render
from autoposter.overlays.assets import FONTS
from autoposter.overlays.families import FAMILIES
from autoposter.overlays.schema import OverlayDefinition
from autoposter.plex.artwork import upload_artwork as _plex_upload_artwork
from autoposter.render.pipeline import compose_badged_bytes, deliver
from autoposter.servers.base import CAP_LOCK_ARTWORK, ServerItemRef

from conftest import seed_media_item
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


# --- H1: `font:` is confined beneath `fonts_root`, the same discipline
# overlays/sources.py's `file:` image source uses; a resolution failure
# warns-and-skips just that definition rather than crashing compose() for the
# whole item. Every test above (and every other test in this file) pre-builds
# no font either -- but none of them sets `definition.font` at all, which is
# exactly why H1 had zero coverage. None of the tests below pre-build an
# ImageFont object either: that is what let the bug hide.


def _text_stamp(**over):
    base = dict(
        name="text(HELLO)",
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100, back_color="#FF0000FF", back_radius=10,
    )
    base.update(over)
    return OverlayDefinition(**base)


def test_a_font_beneath_fonts_root_resolves_and_draws():
    """The happy path: the documented spelling -- a bare filename -- resolves
    against `fonts_root`, not the process cwd."""
    stamp = _text_stamp(font="Inter-Bold.ttf")
    data = compose(BASE, "poster", ALL_SOULS, definitions=[stamp], fonts_root=FONTS)
    assert _sha(data) != POSTER_PIXELS_SHA


def test_the_documented_font_spelling_no_longer_resolves_against_cwd():
    """H1's core bug, reproduced through the real compose(): before the fix,
    `ImageFont.truetype(definition.font, ...)` took the raw string, which
    resolves against the process cwd -- not `fonts_root` -- so a bare
    filename (the field's own documented spelling) always raised OSError
    from deep inside compose(), past `apply_badges`'s per-definition net,
    into `pipeline.py`'s blanket per-item handler. After the fix, a font
    that does not exist beneath `fonts_root` warns-and-skips just this
    definition; the rest of the item is unaffected and the output is
    byte-identical to the no-definitions baseline."""
    stamp = _text_stamp(font="does-not-exist.ttf")
    data = compose(BASE, "poster", ALL_SOULS, definitions=[stamp], fonts_root=FONTS)
    assert _sha(data) == POSTER_PIXELS_SHA


def test_a_font_path_that_leaves_fonts_root_is_refused_not_crashed():
    """Same confinement discipline as the `file:` image source: `root` itself
    is refused and a traversal out of it is blocked, not just an unconfined
    read allowed through."""
    stamp = _text_stamp(font="../../../etc/passwd")
    data = compose(BASE, "poster", ALL_SOULS, definitions=[stamp], fonts_root=FONTS)
    assert _sha(data) == POSTER_PIXELS_SHA


def test_a_bundled_face_resolves_even_when_fonts_root_does_not_hold_it(tmp_path):
    """**Adjudication A-4**, and the concrete blocker the C2b recon found:
    the `aspect` family is the first shipped family that draws TEXT, and a
    family definition's `font:` goes through `resolve_font_path`, which
    confines the value strictly beneath the OPERATOR's `fonts_root`. The
    bundled `Inter-Medium.ttf` lives under `assets/badges/fonts/` and is
    reachable only by the builtin draw path, which never touches
    `fonts_root` -- so before this rung existed, every aspect badge was
    refused and skipped for every operator whose `fonts_root` did not happen
    to contain Inter-Medium.

    `tmp_path` here is an EMPTY fonts_root: a real operator mount with their
    own faces in it and no Inter-Medium. The badge must still draw, in
    Inter-Medium."""
    stamp = _text_stamp(font="Inter-Medium.ttf", font_size=63)
    data = compose(BASE, "poster", ALL_SOULS, definitions=[stamp], fonts_root=tmp_path)
    assert _sha(data) != POSTER_PIXELS_SHA, "the bundled face must draw, not be skipped"


def test_the_operators_own_copy_of_a_bundled_name_wins_over_the_bundled_one(tmp_path):
    """Precedence, pinned: the fallback is a FALLBACK. An operator who puts
    their own `Inter-Medium.ttf` in `fonts_root` gets theirs -- the bundled
    rung is consulted only when the confined path does not exist."""
    (tmp_path / "Inter-Medium.ttf").write_bytes((FONTS / "Inter-Bold.ttf").read_bytes())
    theirs = compose(BASE, "poster", ALL_SOULS, fonts_root=tmp_path,
                     definitions=[_text_stamp(font="Inter-Medium.ttf", font_size=63)])
    bundled = compose(BASE, "poster", ALL_SOULS, fonts_root=tmp_path / "empty",
                      definitions=[_text_stamp(font="Inter-Medium.ttf", font_size=63)])
    assert _sha(theirs) != _sha(bundled), (
        "the operator's own file must be the one that draws"
    )


def test_a_bundled_face_resolves_with_no_fonts_root_configured_at_all():
    """`compose`'s `fonts_root` defaults to None and several callers leave it
    there. Before A-4 that short-circuited to a skip before
    `resolve_font_path` was even called; now the bundled rung answers, which
    is what makes the aspect family draw for a caller that passes no mount."""
    stamp = _text_stamp(font="Inter-Medium.ttf", font_size=63)
    data = compose(BASE, "poster", ALL_SOULS, definitions=[stamp])
    assert _sha(data) != POSTER_PIXELS_SHA


def test_a_font_that_resolves_nowhere_is_reported_skipped_by_name(caplog):
    """**A-4's other half: never silently.** A definition naming a face that
    is neither under `fonts_root` nor bundled is skipped -- and the warning
    names the DEFINITION, so an operator with twenty of them can find the one
    that is wrong. The message deliberately carries the exception's class
    name rather than its text, because the text may quote an operator-typed
    path (the rule `overlays/sources.py` already holds itself to)."""
    stamp = _text_stamp(name="text(BADFONT)", font="Helvetica-Neue.ttf")
    with caplog.at_level("WARNING"):
        data = compose(BASE, "poster", ALL_SOULS, definitions=[stamp], fonts_root=FONTS)
    assert _sha(data) == POSTER_PIXELS_SHA, "the rest of the item is unaffected"
    assert any(
        "text(BADFONT)" in record.getMessage() and "font" in record.getMessage()
        for record in caplog.records
    ), "the skip must name the definition"


def test_the_bundled_rung_is_an_exact_name_lookup_not_a_path_join():
    """The containment property A-4 promises: the rung adds NO traversal
    surface, because it is a constant-keyed dict lookup on the whole written
    value, not a join of operator input onto a bundled directory. A value
    with any path structure in it MISSES the table, and one that leaves the
    root is still refused by `_confined` exactly as it was before.

    On the three values below, and on why the obvious fourth and fifth are
    NOT here: `FONTS` is `assets/badges/fonts` and it really contains
    `Inter-Medium.ttf`, so `../fonts/Inter-Medium.ttf` and
    `./Inter-Medium.ttf` NORMALISE BACK INSIDE the root --
    `(root / value).resolve()` collapses both to
    `assets/badges/fonts/Inter-Medium.ttf`, which passes `_confined` and
    exists, so `resolve_font_path` legitimately returns rung 1's path for
    them. That is correct behaviour, not a hole: a value that resolves back
    inside the mount is not a traversal. Asserting a refusal for them would
    have pinned a bug. The property they DO have is pinned below instead."""
    from autoposter.overlays.sources import BUNDLED_FONTS, OverlaySourceError, resolve_font_path

    assert set(BUNDLED_FONTS) == {"Inter-Bold.ttf", "Inter-Medium.ttf"}
    assert all(path.is_absolute() and path.exists() for path in BUNDLED_FONTS.values())
    for hostile in (
        "fonts/Inter-Medium.ttf",   # confined, but no such file, and it is
                                    # NOT the bundled key -- the table is
                                    # keyed on the bare name
        "../../../etc/passwd",      # leaves the root: _confined refuses
        "/etc/passwd",              # absolute: `root / value` IS `value`,
                                    # so it leaves the root too
    ):
        with pytest.raises(OverlaySourceError):
            resolve_font_path(FONTS, hostile, "hostile")

    # The normalisation property, asserted rather than assumed: a value that
    # resolves back INSIDE the root resolves, and it resolves to rung 1's
    # confined path -- never to the bundled table, which these strings do
    # not key.
    for inside in ("../fonts/Inter-Medium.ttf", "./Inter-Medium.ttf"):
        assert inside not in BUNDLED_FONTS
        assert resolve_font_path(FONTS, inside, "normalised") == (
            FONTS / "Inter-Medium.ttf"
        ).resolve(), inside


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


def test_editing_a_definitions_field_moves_the_fingerprint():
    """The middle case the add/remove pair above doesn't cover: a definition
    that stays configured but has one of its own fields edited (here,
    `back_color`) must still move the digest -- add-to-empty and
    remove-to-empty can't tell an edit from a no-op, since both start or end
    at the same empty list."""
    def _stamp(back_color):
        return OverlayDefinition(
            name="text(HELLO)",
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
            back_width=300, back_height=100,
            back_color=back_color, back_radius=10, font_size=55,
        )

    before = _stamp("#FF0000FF")
    after = _stamp("#00FF00FF")
    before_fp = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [before],
    )
    after_fp = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [after],
    )
    assert before_fp != after_fp


def test_removing_a_definition_reverts_the_fingerprint():
    stamp = OverlayDefinition(name="text(HELLO)")
    with_one = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp],
    )
    reverted = badge_fingerprint("base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [])
    assert reverted == badge_fingerprint("base-fp", "poster", {"critic": "8.6"}, "manifest-sha")
    assert reverted != with_one


# --- A4: the fingerprint must cover the per-item MATCH OUTCOME, and the
# storm pin must EXTEND rather than weaken. Three properties, three pins:
# the empty case stays byte-identical (the literal above, untouched); an
# unchanged item with unchanged matches keeps its digest, and specifically
# keeps the digest the PRE-SEAM five-argument call produced; and an item
# whose matched set moved re-renders. ------------------------------------

# Captured on the freshly cut branch BEFORE `outcomes` existed (T1 Step 3),
# by calling badge_fingerprint with exactly five arguments. Pinned as a
# literal rather than re-derived, for the same reason the gate-off literal
# above is: a re-derivation passes even when the formula and the pin move
# together.
PRE_SEAM_ONE_DEFINITION_FINGERPRINT = "973b2cf3010950d3980be8644f92f1ad5243936691ce100f89eb44bdf94f0923"


def test_a_config_of_unconditioned_definitions_does_not_move_the_fingerprint():
    """The storm guard, extended to the case this phase creates. An operator
    who already configures row-97 definitions and writes no `condition:` on
    any of them must see ZERO fingerprint movement -- `select` reports no
    outcomes for an unconditioned definition, so the new digest part is never
    appended."""
    stamp = OverlayDefinition(name="text(HELLO)")
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp],
    ) == PRE_SEAM_ONE_DEFINITION_FINGERPRINT
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], [],
    ) == PRE_SEAM_ONE_DEFINITION_FINGERPRINT


def test_a_condition_carrying_definition_moves_the_fingerprint_from_the_pre_seam_value():
    """The discriminating half of the same guard. `_definitions_digest`
    excludes `condition` from a definition's dump only while it is unset
    (None) -- a definition that DOES carry one must therefore differ from
    the pre-seam literal. If it did not, the exclusion would be swallowing
    more than the unset default, and an operator's own `condition:` edits
    would stop moving the fingerprint too."""
    conditioned = OverlayDefinition(
        name="text(HELLO)", condition={"resolution": "4k"}
    )
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [conditioned],
    ) != PRE_SEAM_ONE_DEFINITION_FINGERPRINT


def test_an_empty_outcomes_list_is_indistinguishable_from_no_outcomes_at_all():
    """Same guard, at the gate-off end: `outcomes=[]` and `outcomes=None`
    must both leave `parts` untouched, so the pinned literal above stays
    reachable from every call shape."""
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6", "audience": "63%"}, "manifest-sha",
        None, [],
    ) == "576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd"


def test_an_unchanged_item_with_unchanged_matches_keeps_its_digest():
    """A4's first half, tied to the real selection mechanism rather than to
    two calls of `badge_fingerprint` with a hand-typed, byte-identical
    `outcomes` literal -- that only proves hashlib is deterministic (it
    cannot fail under any implementation of `_outcomes_digest`, and would
    survive a bug where two evaluations of the SAME item disagree: a set
    walked in insertion-unstable order, an `lru_cache` keyed on something
    other than the condition's own content). `outcomes` is produced by
    RUNNING `select()` against a real matching item, twice -- a genuinely
    unchanged second pass over the first pass's own inputs -- not typed as a
    literal."""
    from autoposter.overlays.selection import OverlayItemView, select

    stamp = OverlayDefinition(name="dp", condition={"resolution.regex": "(?i)2160|4k"})
    plex_item = _FakePlexItem()
    plex_item.media[0].videoResolution = "4k"
    view = OverlayItemView(None, plex_item=plex_item)

    _, first_outcomes = select([stamp], view)
    _, second_outcomes = select([stamp], view)
    first = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], first_outcomes,
    )
    second = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], second_outcomes,
    )
    assert first == second


def test_a_changed_match_outcome_moves_the_digest():
    """A4's second half, and the reason A4 was ruled yes: an item whose
    resolution changed from 1080 to 4k keeps its old badge forever if the
    fingerprint covers only the config, because the CONFIG did not move."""
    stamp = OverlayDefinition(name="dp", condition={"resolution.regex": "(?i)2160|4k"})
    before = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], [("dp", False)],
    )
    after = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], [("dp", True)],
    )
    assert before != after


def test_the_outcome_digest_is_order_significant_and_name_carrying():
    """Two definitions that swap outcomes are a different item state, and a
    name is part of what an outcome means -- otherwise `[True, False]` and
    `[False, True]` would collide."""
    a = OverlayDefinition(name="a", condition={"resolution": "4k"})
    b = OverlayDefinition(name="b", condition={"resolution": "1080"})
    one = badge_fingerprint(
        "base-fp", "poster", {}, "manifest-sha", [a, b], [("a", True), ("b", False)],
    )
    two = badge_fingerprint(
        "base-fp", "poster", {}, "manifest-sha", [a, b], [("a", False), ("b", True)],
    )
    assert one != two


def test_blur_takes_the_maximum_across_every_matched_definition():
    """The per-item pre-pass semantics (p-overlay-b-recon.md, a fresh fetch
    of modules/overlays.py::run_overlays): max NN across every blur(NN)
    match, not sum, not last-wins, not first-wins."""
    low_only = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[
        OverlayDefinition(name="blur(10)"),
    ]))
    both = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[
        OverlayDefinition(name="blur(10)"), OverlayDefinition(name="blur(80)"),
    ]))
    high_only = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[
        OverlayDefinition(name="blur(80)"),
    ]))
    assert both != low_only, "the higher blur must win, not the first-listed one"
    assert both == high_only, "two matches at (10, 80) must equal a single 80 alone"


def test_blur_suppression_is_resolved_before_the_max_scan():
    """Probe/recon: compile_overlays (suppress+group) runs before the
    per-item blur scan -- a suppressed blur(NN) must not count toward the
    max. Inherited from _resolve_definitions rather than new code; proven
    falsifiable by deliberate mutation in this task's own report, not by a
    RED-before-GREEN cycle."""
    suppressor = OverlayDefinition(name="blur(10)", suppress_overlays=["blur(80)"])
    suppressed = OverlayDefinition(name="blur(80)")
    together = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[suppressor, suppressed]))
    low_only = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[
        OverlayDefinition(name="blur(10)"),
    ]))
    assert together == low_only, "the suppressed blur(80) must not raise the max to 80"


async def test_apply_badges_draws_a_blur_definition_through_the_real_entry_point(
    session, config_with_badges
):
    """The entry-point law (Global Constraint 7): the blur pre-pass is a NEW
    top-level mechanism in compose(), not an extension of an existing
    per-definition draw call -- it needs its own proof through the real
    apply_badges, the same lesson Phase A's own review drew about
    definitions never reaching compose() through anything but a direct
    call."""
    config_with_badges.badges.definitions = []
    item, render = await _render(session, rating_key="blur-entrypoint-item")
    plex_item = _FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    baseline_bytes = plex_item.last_bytes

    config_with_badges.badges.definitions = [OverlayDefinition(name="blur(30)")]
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 2
    assert _sha(plex_item.last_bytes) != _sha(baseline_bytes), "the blur must actually be applied"


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


class FakeServer:
    """The MediaServer surface ``apply_badges`` now goes through, wrapping a
    ``_FakePlexItem``-shaped fake so the real ``plex.artwork.upload_artwork``
    still runs against it -- the object under test is what got uploaded, not
    this shim."""

    name = "plex"
    capabilities = frozenset({CAP_LOCK_ARTWORK})

    def __init__(self, item):
        self._item = item

    async def fetch_item(self, native_id):
        return self._item

    async def upload_artwork(self, ref, data, art_kind, lock):
        await asyncio.to_thread(_plex_upload_artwork, self._item, data, art_kind, lock)


REF = ServerItemRef("plex", "1", "Movies", "movie")


async def apply_badges(
    session, config, render, item, server, ref, facts, probe=None,
    *, http=None, mdblist=None,
):
    """Back-compat shim, local to this suite: ``compose_badged_bytes`` then
    ``deliver`` (Task 19), under the exact old ``apply_badges`` positional
    shape so none of this file's ~30 entry-point calls need to change.
    ``probe`` is accepted and ignored -- every ``FakeServer`` here lacks
    ``CAP_ARTWORK_PROVENANCE``, so ``deliver``'s ``_already_delivered`` always
    fell through to a normal upload before this split too."""
    data = await compose_badged_bytes(
        session, config, render, item, server=server, ref=ref, facts=facts,
        http=http, mdblist=mdblist,
    )
    await deliver(session, config, render, item, {"plex": server}, {"plex": ref}, data)


async def _render(session, rating_key="overlay-entrypoint-item"):
    item = await seed_media_item(session, rating_key, kind="movie", library="Movies", title="X")
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
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
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
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 2, "configuring a definition must re-badge an already-uploaded item"
    assert plex_item.last_bytes != baseline_bytes, "the definition must actually be drawn"
    assert render.badge_fingerprint != baseline_fingerprint

    # Remove it again: the fingerprint must revert exactly, and Plex must
    # receive the original (un-stamped) bytes back.
    config_with_badges.badges.definitions = []
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
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
        session, config_with_badges, baseline_render, baseline_item, FakeServer(baseline_plex), REF, _Facts()
    )

    config_with_badges.badges.definitions = [broken]
    item, render = await _render(session, rating_key="broken-definition")
    plex_item = _FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())

    assert _sha(plex_item.last_bytes) == _sha(baseline_plex.last_bytes)


async def test_apply_badges_draws_a_backdrop_definition_through_the_real_entry_point(
    session, config_with_badges
):
    """The entry-point law (Global Constraint 7): the backdrop sentinel's
    full-canvas arm is exercised through the real apply_badges, not just
    draw_overlay in isolation."""
    config_with_badges.badges.definitions = []
    item, render = await _render(session, rating_key="backdrop-entrypoint-item")
    plex_item = _FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    baseline_bytes = plex_item.last_bytes

    config_with_badges.badges.definitions = [
        OverlayDefinition(name="backdrop", back_color="#00000099"),
    ]
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 2
    assert _sha(plex_item.last_bytes) != _sha(baseline_bytes), "the full-canvas backdrop must actually be drawn"


async def test_apply_badges_threads_http_through_to_a_url_sourced_definition(
    session, config_with_badges, tmp_path, monkeypatch
):
    """Completeness gap the T3 review named: every proof of `http=` threading
    elsewhere stops one level down, at `resolve_image_path` -- this is the
    one at `apply_badges` level, through the real entry point, for a `url:`
    source. Transport mocked and `guard.resolve_host` patched per
    `tests/test_overlay_sources.py`'s own convention for its ladder tests.
    """
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: ["93.184.216.34"]
    )
    calls = []

    def handler(request):
        calls.append(request)
        buffer = io.BytesIO()
        Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(buffer, format="PNG")
        return httpx.Response(
            200, content=buffer.getvalue(), headers={"content-type": "image/png"},
        )

    config_with_badges.overlays_root = tmp_path
    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", url="https://example.com/a.png",
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]
    item, render = await _render(session, rating_key="url-sourced-item")
    plex_item = _FakePlexItem()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await apply_badges(
            session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts(), http=http
        )
    assert len(calls) == 1, "the definition's url must actually be requested"
    assert plex_item.uploads == 1

    config_with_badges.badges.definitions = []
    baseline_item, baseline_render = await _render(session, rating_key="url-baseline")
    baseline_plex = _FakePlexItem()
    await apply_badges(
        session, config_with_badges, baseline_render, baseline_item, FakeServer(baseline_plex), REF, _Facts()
    )
    assert _sha(plex_item.last_bytes) != _sha(baseline_plex.last_bytes), (
        "the downloaded image must actually be drawn, not skipped"
    )


# --- the seam through the REAL entry point: gate-off / gate-on-but-unmatched
# / gate-on-and-matched. Global Constraint 6. `_sha` is a PIXEL hash, not a
# file hash: a differently-configured item legitimately stamps different EXIF
# provenance, and what must be identical is what got DRAWN. --------------


class _FakePlexItem4k(_FakePlexItem):
    """The same fake, one attribute moved. `videoResolution` is what
    `direct_play` selects on."""

    def __init__(self):
        super().__init__()
        self.media[0].videoResolution = "4k"
        self.contentRating = "PG-13"


async def test_a_definition_whose_condition_fails_draws_exactly_the_gate_off_pixels(
    session, config_with_badges, tmp_path
):
    """The middle arm of the three-way law, and the one that did not exist
    before this phase: gate ON, condition NOT satisfied, and the drawn pixels
    must equal the no-definitions baseline exactly."""
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(stamp, format="PNG")
    config_with_badges.overlays_root = tmp_path

    config_with_badges.badges.definitions = []
    base_item, base_render = await _render(session, rating_key="cond-baseline")
    base_plex = _FakePlexItem()
    await apply_badges(session, config_with_badges, base_render, base_item, FakeServer(base_plex), REF, _Facts())

    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", file="stamp.png",
            condition={"resolution.regex": "(?i)2160|4k"},
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]
    item, render = await _render(session, rating_key="cond-unmatched")
    plex_item = _FakePlexItem()  # 1080 -- does not satisfy the condition
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())

    assert _sha(plex_item.last_bytes) == _sha(base_plex.last_bytes)


async def test_a_definition_whose_condition_holds_is_actually_drawn(
    session, config_with_badges, tmp_path
):
    """The third arm. Same config, an item that DOES satisfy it."""
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(stamp, format="PNG")
    config_with_badges.overlays_root = tmp_path

    config_with_badges.badges.definitions = []
    base_item, base_render = await _render(session, rating_key="cond-baseline-4k")
    base_plex = _FakePlexItem4k()
    await apply_badges(session, config_with_badges, base_render, base_item, FakeServer(base_plex), REF, _Facts())

    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", file="stamp.png",
            condition={"resolution.regex": "(?i)2160|4k"},
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]
    item, render = await _render(session, rating_key="cond-matched")
    plex_item = _FakePlexItem4k()
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())

    assert _sha(plex_item.last_bytes) != _sha(base_plex.last_bytes)


async def test_an_item_whose_match_outcome_changes_re_badges(
    session, config_with_badges, tmp_path
):
    """A4 through the real entry point, which is where it matters: the same
    render row, the same config, one item attribute moved -- and moved on an
    attribute `badge_values` never reads, so nothing but the match OUTCOME
    could be what moves the fingerprint. `contentRating` is read by the
    overlay view (it is what the six regionals select on) but by no badge:
    `BadgeInputs.content_rating` comes from `facts`, not from `plex_item`
    (`pipeline.py:1321`) -- unlike `videoResolution`, which also drives
    `resolution_image` and would move the fingerprint through `values` on
    its own, discriminating nothing. Without the outcome in the fingerprint,
    `apply_badges`'s gate sees an unchanged digest and
    `upload_status == 'uploaded'` and returns early -- the item keeps the
    wrong badge forever."""
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(stamp, format="PNG")
    config_with_badges.overlays_root = tmp_path
    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", file="stamp.png",
            condition={"content_rating": "PG-13"},
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]

    item, render = await _render(session, rating_key="outcome-moves")
    plex_item = _FakePlexItem()  # no contentRating: does not match
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 1
    unmatched_fingerprint = render.badge_fingerprint

    plex_item.contentRating = "PG-13"  # the item changed
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 2, "a changed match outcome must re-badge"
    assert render.badge_fingerprint != unmatched_fingerprint


async def test_an_unmatched_definition_never_resolves_its_image(
    session, config_with_badges, tmp_path, monkeypatch
):
    """Global Constraint 7. Selection runs BEFORE the per-definition
    resolution loop, so an overlay this item does not match costs no stat, no
    download and no decode. Proven with a `url:` source, where the I/O is
    observable: zero requests must be made."""
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: ["93.184.216.34"]
    )
    calls = []

    def handler(request):
        calls.append(request)
        buffer = io.BytesIO()
        Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(buffer, format="PNG")
        return httpx.Response(
            200, content=buffer.getvalue(), headers={"content-type": "image/png"},
        )

    config_with_badges.overlays_root = tmp_path
    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", url="https://example.com/a.png",
            condition={"resolution.regex": "(?i)2160|4k"},
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]
    item, render = await _render(session, rating_key="unmatched-url")
    plex_item = _FakePlexItem()  # 1080
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await apply_badges(
            session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts(), http=http
        )
    assert calls == [], "an unmatched definition must not resolve its image"


# --- per-family fires/silent, through the REAL apply_badges. One shaped item
# and one healthy item per family, plus the A5 divergence test. ------------


class _FakePlexItemRated(_FakePlexItem):
    """1080p, with a Plex certification. `contentRating` is what the six
    regionals select on."""

    def __init__(self, content_rating="PG-13"):
        super().__init__()
        self.contentRating = content_rating


class _CommonSenseFacts:
    """`item_facts` as `facts/mdblist.py::parse_content_rating` writes it:
    `content_rating` is a Common Sense AGE, not a certification.
    `"G - All Ages"` is IN Kometa's us_movie `g` bucket verbatim (the same
    bucket `"3"` is in), which is precisely why this makes a sharp
    divergence test -- and, unlike `"3"`, it is not itself a digit, so
    `badges/values.py::commonsense_text` answers `None` for it (any
    non-numeric string that is not `"NR"` does) and it draws no commonsense
    badge of its own. That matters here ONLY because the commonsense badge
    is a SEPARATE badge this same `facts.content_rating` field also feeds
    (`pipeline.py:1321`) -- if it drew, its own text would move between
    `divergent` and `plex_only` below for a reason that has nothing to do
    with which regional badge sourced correctly, and the `_sha` equality
    this test needs would fail for the wrong reason."""

    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = "G - All Ages"


class _FactsNoContentRating:
    """`item_facts` with no Common Sense rating at all -- what "Plex alone"
    genuinely looks like, as opposed to "Plex plus some OTHER Common Sense
    value" (a numeric one would draw its own commonsense badge and break the
    same equality `_CommonSenseFacts` above is built to avoid breaking)."""

    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = None


async def _badged(session, config, plex_item, rating_key, facts=None):
    item, render = await _render(session, rating_key=rating_key)
    await apply_badges(
        session, config, render, item, FakeServer(plex_item), REF, facts or _Facts()
    )
    return plex_item.last_bytes


class _FakePlexItemWithRatings(_FakePlexItem):
    """Carries `.userRating` and a `.ratings` collection -- the plex_* four,
    read by `badges.values.plex_native_ratings`."""

    def __init__(self, user_rating=8.0):
        super().__init__()
        self.userRating = user_rating
        self.ratings = [
            type("R", (), {"image": "imdb://image.rating", "value": 7.7})(),
            type("R", (), {"image": "themoviedb://image.rating", "value": 8.4})(),
        ]


class _RaisingMDBList:
    """A stand-in `mdblist` client that always raises, to prove `apply_badges`
    degrades rather than propagating."""

    def __init__(self, exc):
        self._exc = exc

    async def ratings(self, tmdb_id=None, tvdb_id=None, is_movie=True):
        raise self._exc


class _RecordingMDBList:
    def __init__(self, values):
        self._values = values
        self.calls = []

    async def ratings(self, tmdb_id=None, tvdb_id=None, is_movie=True):
        self.calls.append((tmdb_id, tvdb_id, is_movie))
        return self._values


async def test_imdb_and_tmdb_rating_tokens_resolve_from_the_existing_facts(
    session, config_with_badges
):
    """The alias half of C2a: no new fetch -- item_facts.critic_rating IS
    imdb_rating and item_facts.audience_rating IS tmdb_rating (probe bucket
    (a)), and now the <<imdb_rating>>/<<tmdb_rating>> SPELLINGS resolve too,
    not only <<critic_rating>>/<<audience_rating>>.

    HIGH finding, preflight review: this item's own gate-off baseline (no
    definitions at all) vs the same item with the alias definition
    configured -- the pixel-hash-vs-baseline pattern
    `test_plex_native_ratings_resolve_through_the_real_entry_point` below
    already uses, not `Path(render.asset_path).exists()` (that path is a
    committed fixture `apply_badges` never writes to; it exists before this
    test runs and would keep existing whether or not the tokens ever
    resolved). If the aliases did NOT resolve, `UnresolvedVariable` would be
    caught and skipped per definition (probe section 2.4), and `fires` would
    come back pixel-identical to `baseline` -- that is what must actually
    fail at RED."""
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = []
    baseline = await _badged(
        session, config_with_badges, _FakePlexItem(), "ratings-aliases-base"
    )

    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="text(<<imdb_rating>> / <<tmdb_rating>>)",
            horizontal_align="center", horizontal_offset=0,
            vertical_align="top", vertical_offset=0,
        ),
    ]
    fires = await _badged(session, config_with_badges, _FakePlexItem(), "ratings-aliases")

    assert _sha(fires) != _sha(baseline), "the imdb_rating/tmdb_rating aliases must actually draw"


async def test_plex_native_ratings_resolve_through_the_real_entry_point(
    session, config_with_badges
):
    """The plex_* four and user_rating, end to end: fires differently on an
    item that carries them vs one that does not, proving the values actually
    reach the fingerprint (and therefore the render), not just that
    apply_badges runs without error."""
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = [
        OverlayDefinition(name="text(<<plex_imdb_rating>>)"),
    ]
    unrated = await _badged(session, config_with_badges, _FakePlexItem(), "pnr-unrated")
    rated = await _badged(
        session, config_with_badges, _FakePlexItemWithRatings(), "pnr-rated"
    )
    assert _sha(unrated) != _sha(rated)


async def test_mdblist_ratings_reach_the_variable_map(session, config_with_badges):
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = [
        OverlayDefinition(name="text(<<mdb_average_rating>>)"),
    ]
    item, render = await _render(session, rating_key="mdb-fires")
    plex_item = _FakePlexItem()
    mdblist = _RecordingMDBList({"mdb_average_rating": 6.5})
    await apply_badges(
        session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts(), mdblist=mdblist
    )
    assert mdblist.calls == [(item.tmdb_id, item.tvdb_id, item.kind == "movie")]


async def test_mdblist_quota_exhaustion_degrades_ratings_only_not_the_whole_badge_stage(
    session, config_with_badges
):
    from autoposter.facts.mdblist import MDBListLimitReached

    item, render = await _render(session, rating_key="mdb-limit")
    plex_item = _FakePlexItem()
    await apply_badges(
        session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts(),
        mdblist=_RaisingMDBList(MDBListLimitReached("API Limit Reached!")),
    )
    assert render.badge_fingerprint is not None  # the badge stage completed


async def test_mdblist_transport_failure_degrades_ratings_only(session, config_with_badges):
    import httpx as httpx_module

    item, render = await _render(session, rating_key="mdb-httperror")
    plex_item = _FakePlexItem()
    await apply_badges(
        session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts(),
        mdblist=_RaisingMDBList(httpx_module.ConnectError("boom")),
    )
    assert render.badge_fingerprint is not None


async def test_no_mdblist_client_is_a_no_op_not_an_error(session, config_with_badges):
    """`mdblist=None` is what every test predating this phase passes -- the
    same shape `http=None` already has."""
    item, render = await _render(session, rating_key="mdb-none")
    await apply_badges(session, config_with_badges, render, item, FakeServer(_FakePlexItem()), REF, _Facts())
    assert render.badge_fingerprint is not None


async def test_a_used_rating_value_changing_moves_the_fingerprint_through_the_real_entry_point(
    session, config_with_badges
):
    """M-1, fix round 1: the three digest pins in test_badge_compose.py call
    `badge_fingerprint` directly -- nothing pinned the WIRING at
    `pipeline.py:1398` (`ratings=ratings`), which is the single argument that
    actually closes the staleness gap. Deleting it leaves the whole suite
    green. Two `apply_badges` calls on the SAME render, only the resolved
    rating VALUE a definition actually names changing between them (same
    config, same item, same everything else), must move
    `render.badge_fingerprint`; a third pass with the value unchanged must
    keep it, the storm guard extended to this path."""
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = [
        OverlayDefinition(name="text(<<mdb_average_rating>>)"),
    ]
    item, render = await _render(session, rating_key="mdb-value-moves")
    plex_item = _FakePlexItem()

    await apply_badges(
        session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts(),
        mdblist=_RecordingMDBList({"mdb_average_rating": 6.5}),
    )
    first_fingerprint = render.badge_fingerprint
    assert first_fingerprint is not None

    await apply_badges(
        session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts(),
        mdblist=_RecordingMDBList({"mdb_average_rating": 7.0}),
    )
    second_fingerprint = render.badge_fingerprint
    assert second_fingerprint != first_fingerprint, "a changed rating value must re-badge"

    await apply_badges(
        session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts(),
        mdblist=_RecordingMDBList({"mdb_average_rating": 7.0}),
    )
    assert render.badge_fingerprint == second_fingerprint, (
        "an unchanged rating value must not re-badge"
    )


async def test_direct_play_fires_on_a_4k_item_and_is_silent_on_a_1080_one(
    session, config_with_badges
):
    config_with_badges.badges.families = []
    base_1080 = await _badged(session, config_with_badges, _FakePlexItem(), "dp-base-1080")
    base_4k = await _badged(session, config_with_badges, _FakePlexItem4k(), "dp-base-4k")

    config_with_badges.badges.families = ["direct_play"]
    silent = await _badged(session, config_with_badges, _FakePlexItem(), "dp-1080")
    fires = await _badged(session, config_with_badges, _FakePlexItem4k(), "dp-4k")

    assert _sha(silent) == _sha(base_1080), "1080p must draw the gate-off pixels"
    assert _sha(fires) != _sha(base_4k), "4k must actually draw Direct-Play"


async def test_a_regional_fires_on_its_bucket_and_is_silent_off_it(
    session, config_with_badges
):
    config_with_badges.badges.families = []
    baseline = await _badged(
        session, config_with_badges, _FakePlexItemRated("PG-13"), "cr-base"
    )

    config_with_badges.badges.families = ["content_rating_us_movie"]
    fires = await _badged(
        session, config_with_badges, _FakePlexItemRated("PG-13"), "cr-pg13"
    )
    silent = await _badged(
        session, config_with_badges, _FakePlexItemRated("Unrated Nonsense"), "cr-none"
    )

    assert _sha(fires) != _sha(baseline), "PG-13 must draw its regional badge"
    assert _sha(silent) == _sha(baseline), "a certification in no bucket draws nothing"


async def test_the_regionals_read_plexs_certification_not_item_facts_common_sense(
    session, config_with_badges
):
    """Adjudication A5's divergence test, and it is sharp on purpose: the
    item carries Plex `PG-13` AND an item_facts Common Sense age of
    `"G - All Ages"`, which is IN Kometa's us_movie `g` bucket. If this view
    read `item_facts.content_rating`, the `g` badge would draw. It must draw
    the `pg-13` one.

    `divergent` and `plex_only` are built to draw the SAME commonsense badge
    as each other (neither's `facts.content_rating` is a digit, so neither
    draws one at all -- see `_CommonSenseFacts` and `_FactsNoContentRating`
    above) precisely so that `_sha` equality below isolates the REGIONAL
    badge's sourcing and nothing else."""
    config_with_badges.badges.families = ["content_rating_us_movie"]
    divergent = await _badged(
        session, config_with_badges, _FakePlexItemRated("PG-13"),
        "cr-divergent", facts=_CommonSenseFacts(),
    )

    # The same item as Plex sees it, with no Common Sense value at all.
    plex_only = await _badged(
        session, config_with_badges, _FakePlexItemRated("PG-13"),
        "cr-plex-only", facts=_FactsNoContentRating(),
    )
    # And the item as item_facts alone would have described it -- "3" here,
    # not `_CommonSenseFacts`'s own value, is fine: this call only needs SOME
    # `g`-bucket certification to demonstrate the bucket draws differently,
    # and it does not participate in the `_sha` equality above.
    as_common_sense = await _badged(
        session, config_with_badges, _FakePlexItemRated("3"), "cr-as-cs"
    )

    assert _sha(divergent) == _sha(plex_only), (
        "the Common Sense value must not change what is drawn"
    )
    assert _sha(divergent) != _sha(as_common_sense), (
        "reading item_facts.content_rating would have drawn the g badge"
    )


class _FakePlexItemMultiVersion(_FakePlexItem):
    """Two `<Media>` entries -- what `versions.gt: 1` selects on."""

    def __init__(self):
        super().__init__()
        self.media = self.media + [type("M", (), {
            "parts": [type("P", (), {"file": None})()],
            "videoResolution": "4k", "audioCodec": "eac3", "audioChannels": 6,
        })()]


async def test_versions_fires_on_a_multi_version_item_and_is_silent_on_a_single_one(
    session, config_with_badges
):
    config_with_badges.badges.families = []
    base_single = await _badged(session, config_with_badges, _FakePlexItem(), "v-base-1")
    base_multi = await _badged(
        session, config_with_badges, _FakePlexItemMultiVersion(), "v-base-2"
    )

    config_with_badges.badges.families = ["versions"]
    silent = await _badged(session, config_with_badges, _FakePlexItem(), "v-1")
    fires = await _badged(session, config_with_badges, _FakePlexItemMultiVersion(), "v-2")

    assert _sha(silent) == _sha(base_single), "a single version must draw the gate-off pixels"
    assert _sha(fires) != _sha(base_multi), "a multi-version item must actually draw the badge"


async def test_enabling_a_family_re_badges_an_already_uploaded_item(
    session, config_with_badges
):
    """The same law Finding 1 established for a hand-written definition: a
    family named on an already-uploaded render must reach Plex."""
    config_with_badges.badges.families = []
    item, render = await _render(session, rating_key="family-rebadge")
    plex_item = _FakePlexItem4k()
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 1
    before = render.badge_fingerprint

    config_with_badges.badges.families = ["direct_play"]
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 2
    assert render.badge_fingerprint != before


async def test_enabling_no_family_moves_no_fingerprint(
    session, config_with_badges
):
    """The storm guard at the family surface: a second pass with the same
    empty `families` must not re-upload."""
    config_with_badges.badges.families = []
    item, render = await _render(session, rating_key="family-storm")
    plex_item = _FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 1
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 1, "an unchanged config must not re-badge"


class _FakePlexItemShot(_FakePlexItem):
    """One `<Media>` carrying an `aspectRatio` -- what the eight bands
    compare. `1.655` is the deliberate overlap case (A-5): it satisfies the
    1.65 band AND the 1.66 band."""

    def __init__(self, aspect=2.35):
        super().__init__()
        self.media[0].aspectRatio = aspect


class _FakePlexItemLanguages(_FakePlexItem):
    """Audio streams on the one `<Media>`'s one `<Part>` -- what
    `media_info_from_plex` walks and `language_count` counts."""

    def __init__(self, *codes):
        super().__init__()
        self.media[0].parts = [type("P", (), {
            "file": None,
            "streams": [
                type("S", (), {"streamType": 2, "languageCode": code})()
                for code in codes
            ],
        })()]


async def test_aspect_fires_on_a_shot_item_and_is_silent_on_an_unanalysed_one(
    session, config_with_badges
):
    """The entry-point law for the first TEXT family: through the real
    `apply_badges`, not `compose()` alone. The silent half is the one that
    matters most here -- an item Plex has not analysed carries no
    `aspectRatio`, and the float missing-value rule must drop it rather than
    drawing a wrong band."""
    config_with_badges.badges.families = []
    base_shot = await _badged(session, config_with_badges, _FakePlexItemShot(), "asp-base-1")
    base_none = await _badged(session, config_with_badges, _FakePlexItem(), "asp-base-2")

    config_with_badges.badges.families = ["aspect"]
    fires = await _badged(session, config_with_badges, _FakePlexItemShot(), "asp-1")
    silent = await _badged(session, config_with_badges, _FakePlexItem(), "asp-2")

    assert _sha(fires) != _sha(base_shot), "a 2.35 item must actually draw its band"
    assert _sha(silent) == _sha(base_none), "an unanalysed item must draw the gate-off pixels"


async def test_the_aspect_text_draws_in_the_bundled_face_through_the_real_entry_point(
    session, config_with_badges, tmp_path
):
    """A-4 end to end, and the reason the rung exists: `config.fonts_root` is
    an operator mount that does NOT contain Inter-Medium, which is the normal
    case. Before the bundled rung the definition was skipped here and this
    test's `fires` came back identical to `baseline`."""
    config_with_badges.fonts_root = tmp_path
    config_with_badges.badges.families = []
    baseline = await _badged(session, config_with_badges, _FakePlexItemShot(), "asp-font-0")

    config_with_badges.badges.families = ["aspect"]
    fires = await _badged(session, config_with_badges, _FakePlexItemShot(), "asp-font-1")
    assert _sha(fires) != _sha(baseline)


async def test_only_the_highest_weighted_overlapping_band_is_drawn(
    session, config_with_badges
):
    """A-5 through the real entry point. A 1.655 item matches the 1.65 band
    AND the 1.66 band; group resolution draws only 1.65. Proven by comparing
    against the SAME item badged with a config that carries the 1.65
    definition alone -- if both had drawn, the two would differ."""
    from autoposter.overlays.families import FAMILIES

    bands = {d.name: d for d in FAMILIES["aspect"]}
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = [bands["text(1.65)"]]
    winner_only = await _badged(
        session, config_with_badges, _FakePlexItemShot(1.655), "asp-w-1"
    )

    config_with_badges.badges.definitions = []
    config_with_badges.badges.families = ["aspect"]
    both_match = await _badged(
        session, config_with_badges, _FakePlexItemShot(1.655), "asp-w-2"
    )
    assert _sha(both_match) == _sha(winner_only), (
        "1.66 must lose the group to 1.65 rather than drawing over it"
    )


async def test_language_count_fires_dual_on_two_and_multi_on_three(
    session, config_with_badges
):
    """Both halves of A-5's second case, through `apply_badges`: a
    2-language item matches Dual and Multi and must draw DUAL (weight 20 >
    10); a 3-language item matches Multi alone. The two must therefore differ
    from each other AND both differ from a 1-language item, which matches
    neither."""
    config_with_badges.badges.families = []
    base_one = await _badged(
        session, config_with_badges, _FakePlexItemLanguages("eng"), "lc-base-1"
    )
    base_two = await _badged(
        session, config_with_badges, _FakePlexItemLanguages("eng", "fin"), "lc-base-2"
    )
    base_three = await _badged(
        session, config_with_badges,
        _FakePlexItemLanguages("eng", "fin", "swe"), "lc-base-3",
    )

    config_with_badges.badges.families = ["language_count"]
    one = await _badged(session, config_with_badges, _FakePlexItemLanguages("eng"), "lc-1")
    two = await _badged(
        session, config_with_badges, _FakePlexItemLanguages("eng", "fin"), "lc-2"
    )
    three = await _badged(
        session, config_with_badges,
        _FakePlexItemLanguages("eng", "fin", "swe"), "lc-3",
    )

    assert _sha(one) == _sha(base_one), "one language matches neither band"
    assert _sha(two) != _sha(base_two), "two languages must draw dual_audio"
    assert _sha(three) != _sha(base_three), "three languages must draw multi_audio"
    assert _sha(two) != _sha(three), "dual and multi must be different art"


async def test_three_english_subtitle_tracks_are_three_subtitle_streams(
    session, config_with_badges
):
    """**Kometa's counting rule, end to end through the real entry point**
    rather than only at `media_info_from_plex`. `subtitle_language.count_gte:
    2` MUST fire on a film whose three English subtitle tracks are full, SDH
    and forced, because upstream's value is a flat `extend`-ed list of
    streams with no dedupe (`modules/plex.py:2915-2922`) and `.count_*` is
    `len()` of it (`plex.py:2931-2932`).

    This plan's first draft asserted the opposite -- that the three collapse
    to one language and the badge stays silent -- and it was wrong; the
    pinned image settled it. The test is inverted rather than deleted
    because it is the one place the whole `MediaInfo` -> view -> `_matches`
    -> `compose` chain is exercised on the repeat case, and a regression to
    a deduped read anywhere along it lands here.

    Written as a hand-configured definition because the subtitle FAMILY does
    not ship (adjudication A-3) while the attribute and the operators do.
    The one-language control is the second half: it must stay silent, which
    is what proves the fire above is the COUNT and not just "any subtitle
    stream at all"."""
    item = _FakePlexItemLanguages("eng")
    item.media[0].parts[0].streams += [
        type("S", (), {"streamType": 3, "languageCode": code})()
        for code in ("eng", "eng", "eng")
    ]
    one = _FakePlexItemLanguages("eng")
    one.media[0].parts[0].streams += [
        type("S", (), {"streamType": 3, "languageCode": "eng"})()
    ]
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = []
    baseline_three = await _badged(session, config_with_badges, item, "subs-base-3")
    baseline_one = await _badged(session, config_with_badges, one, "subs-base-1")

    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="subs", builtin="multi_audio",
            condition={"subtitle_language.count_gte": 2},
            horizontal_align="center", horizontal_offset=0,
            vertical_align="top", vertical_offset=0,
        ),
    ]
    fires = await _badged(session, config_with_badges, item, "subs-3")
    silent = await _badged(session, config_with_badges, one, "subs-1")
    assert _sha(fires) != _sha(baseline_three), (
        "three English tracks are THREE subtitle-language entries, the way "
        "Kometa counts them"
    )
    assert _sha(silent) == _sha(baseline_one), "one track is one, and stays silent"


async def test_enabling_a_c2b_family_re_badges_once_and_the_second_pass_is_unchanged(
    session, config_with_badges
):
    """**The digest-evolution law, both directions, through the real entry
    point** (Global Constraint 6). Three properties, and the middle one is
    the honest reading of "re-renders exactly the items whose verdict is
    true":

    1. gate-off, the item badges once;
    2. enabling the family re-badges it ONCE -- and it does so for EVERY
       already-badged item, matched or not, because `apply_badges` hashes the
       WHOLE `all_definitions()` list, not the matched subset. That is the
       one-time re-badge roadmap row 100's cell already discloses; what the
       VERDICT decides is what gets DRAWN (the fires/silent pins above), not
       whether the fingerprint moves;
    3. a second pass over the same config uploads nothing -- the fingerprint
       is stable again, which is the property that makes (2) one-time rather
       than a storm."""
    item, render = await _render(session, rating_key="c2b-storm")
    plex_item = _FakePlexItemShot()
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = []

    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 1
    gate_off_fingerprint = render.badge_fingerprint

    config_with_badges.badges.families = ["aspect"]
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 2, "enabling a family must re-badge an already-uploaded item"
    enabled_fingerprint = render.badge_fingerprint
    assert enabled_fingerprint != gate_off_fingerprint

    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 2, "the second pass must upload nothing"
    assert render.badge_fingerprint == enabled_fingerprint

    config_with_badges.badges.families = []
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert plex_item.uploads == 3
    assert render.badge_fingerprint == gate_off_fingerprint, (
        "disabling the family must revert the fingerprint exactly"
    )


async def test_a_family_this_config_does_not_name_costs_nothing(
    session, config_with_badges
):
    """The other end of the same law: `FAMILIES` gaining two keys must not
    move a fingerprint for a config that names neither.
    `BadgesConfig.all_definitions()` expands only NAMED families, so a
    C1/C2a config is byte-identical across this sub-phase -- and so is the
    empty one, which is what the two pinned literals at the top of this file
    (`576f88e5...` and `PRE_SEAM_ONE_DEFINITION_FINGERPRINT`) guard
    unmodified."""
    from autoposter.config.schema import BadgesConfig

    assert BadgesConfig().all_definitions() == []
    assert BadgesConfig(families=["direct_play"]).all_definitions() == list(
        FAMILIES["direct_play"]
    )

    item, render = await _render(session, rating_key="c2b-untouched")
    plex_item = _FakePlexItemShot()
    config_with_badges.badges.families = ["direct_play"]
    config_with_badges.badges.definitions = []
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    first = render.badge_fingerprint
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, _Facts())
    assert render.badge_fingerprint == first
    assert plex_item.uploads == 1


# --- sub-phase C2c: the `status` family, through the real apply_badges -----


class _StatusFacts(_Facts):
    """`item_facts` as `facts/tmdb_facts.py::parse_show_facts` writes it after
    sub-phase C2c. Subclasses this file's own `_Facts` so the three rating
    fields keep their existing values and no OTHER badge moves between the
    baseline and the fires case -- if the critic/audience ratings differed,
    the pixel comparison would fail for a reason that has nothing to do with
    the status band."""

    def __init__(self, tmdb_status=None, last_episode_aired=None):
        self.tmdb_status = tmdb_status
        self.last_episode_aired = last_episode_aired


def _days_ago(days):
    return date.today() - timedelta(days=days)


async def test_status_fires_on_a_returning_show_and_is_silent_without_facts(
    session, config_with_badges
):
    """The entry-point law for the first FACTS-backed family: through the real
    `apply_badges`, which is where the persisted `ItemFacts` row is loaded and
    handed to the view. The silent half is the one that matters most -- an
    item whose facts carry no status (a movie, or a show not yet re-gathered)
    must draw the gate-off pixels rather than a wrong band."""
    config_with_badges.badges.families = []
    base_returning = await _badged(
        session, config_with_badges, _FakePlexItem(), "st-base-1",
        facts=_StatusFacts(tmdb_status="returning"),
    )
    base_none = await _badged(
        session, config_with_badges, _FakePlexItem(), "st-base-2",
        facts=_StatusFacts(),
    )

    config_with_badges.badges.families = ["status"]
    fires = await _badged(
        session, config_with_badges, _FakePlexItem(), "st-1",
        facts=_StatusFacts(tmdb_status="returning"),
    )
    silent = await _badged(
        session, config_with_badges, _FakePlexItem(), "st-2",
        facts=_StatusFacts(),
    )

    assert _sha(fires) != _sha(base_returning), "a returning show must draw RETURNING"
    assert _sha(silent) == _sha(base_none), (
        "an item with no status facts must draw the gate-off pixels"
    )


async def test_airing_fires_inside_the_window_and_is_silent_outside_it(
    session, config_with_badges
):
    """Adjudication A-1's band, end to end. The window is relative to the run
    moment, so the fixture dates are computed from `date.today()` rather than
    written as literals -- a hardcoded 2026 date would pass for a fortnight
    and then start failing for a reason that has nothing to do with the
    code."""
    config_with_badges.badges.families = []
    base = await _badged(
        session, config_with_badges, _FakePlexItem(), "air-base",
        facts=_StatusFacts(last_episode_aired=_days_ago(3)),
    )
    base_old = await _badged(
        session, config_with_badges, _FakePlexItem(), "air-base-2",
        facts=_StatusFacts(last_episode_aired=_days_ago(200)),
    )

    config_with_badges.badges.families = ["status"]
    fires = await _badged(
        session, config_with_badges, _FakePlexItem(), "air-1",
        facts=_StatusFacts(last_episode_aired=_days_ago(3)),
    )
    silent = await _badged(
        session, config_with_badges, _FakePlexItem(), "air-2",
        facts=_StatusFacts(last_episode_aired=_days_ago(200)),
    )

    assert _sha(fires) != _sha(base), "an episode 3 days old must draw AIRING"
    assert _sha(silent) == _sha(base_old), (
        "an episode 200 days old is outside the 14-day window and draws nothing"
    )


async def test_a_null_status_column_draws_nothing_rather_than_a_wrong_band(
    session, config_with_badges
):
    """**The upgrade's own initial condition, pinned (adjudication A-4).**
    C2c ships no backfill job, so on the pass that follows the migration
    EVERY row in the library has both columns NULL. That must draw the
    gate-off pixels for every item -- not a band, and not an error -- until
    each row is re-gathered. It is also what a `GatheredFacts()` fallback
    looks like when `operations.enabled` is off and there is no persisted row
    at all, which is the same shape and the same answer."""
    from autoposter.facts.models import GatheredFacts

    config_with_badges.badges.families = []
    base = await _badged(
        session, config_with_badges, _FakePlexItem(), "null-base",
        facts=GatheredFacts(),
    )

    config_with_badges.badges.families = ["status"]
    after = await _badged(
        session, config_with_badges, _FakePlexItem(), "null-1",
        facts=GatheredFacts(),
    )
    assert _sha(after) == _sha(base)


async def test_only_the_highest_weighted_status_band_is_drawn(
    session, config_with_badges
):
    """Adjudication A-5 through the real entry point, on the case a real
    library actually produces: a show whose finale aired 8 days ago and which
    TMDb has already marked `Ended` matches AIRING (40) and ENDED (10).
    Proven by comparing against the SAME item badged with a config carrying
    the AIRING definition ALONE -- if both had drawn, the two would differ."""
    from autoposter.overlays.families import FAMILIES

    bands = {d.name: d for d in FAMILIES["status"]}
    facts = _StatusFacts(tmdb_status="ended", last_episode_aired=_days_ago(8))

    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = [bands["text(AIRING)"]]
    winner_only = await _badged(
        session, config_with_badges, _FakePlexItem(), "st-w-1", facts=facts
    )

    config_with_badges.badges.definitions = []
    config_with_badges.badges.families = ["status"]
    both_match = await _badged(
        session, config_with_badges, _FakePlexItem(), "st-w-2", facts=facts
    )
    assert _sha(both_match) == _sha(winner_only), (
        "ENDED must lose the group to AIRING rather than drawing over it"
    )


async def test_the_status_text_draws_in_the_bundled_face_through_the_real_entry_point(
    session, config_with_badges, tmp_path
):
    """C2b's A-4 rung, inherited and re-proven for this family: the operator's
    `fonts_root` is a mount that does NOT contain Inter-Medium, which is the
    normal case. Without the bundled rung the definition would be skipped by
    name here and `fires` would come back identical to `baseline`."""
    config_with_badges.fonts_root = tmp_path
    config_with_badges.badges.families = []
    facts = _StatusFacts(tmdb_status="ended")
    baseline = await _badged(
        session, config_with_badges, _FakePlexItem(), "st-font-0", facts=facts
    )

    config_with_badges.badges.families = ["status"]
    fires = await _badged(
        session, config_with_badges, _FakePlexItem(), "st-font-1", facts=facts
    )
    assert _sha(fires) != _sha(baseline)


async def test_enabling_the_status_family_re_badges_once_and_the_second_pass_is_unchanged(
    session, config_with_badges
):
    """**The digest-evolution law, both directions, through the real entry
    point** (Global Constraint 7), stated as what the mechanism DOES:

    1. gate-off, the item badges once;
    2. enabling the family re-badges it ONCE -- and for EVERY already-badged
       item, matched or not, because `apply_badges` hashes the WHOLE
       `all_definitions()` list rather than the matched subset. That is the
       one-time re-badge roadmap row 100's cell already discloses; what the
       VERDICT decides is what gets DRAWN (the fires/silent pins above);
    3. a second pass over the same config uploads nothing -- the fingerprint
       is stable again, which is what makes (2) one-time rather than a storm;
    4. disabling reverts the fingerprint EXACTLY, which is the property that
       proves nothing else moved."""
    item, render = await _render(session, rating_key="c2c-storm")
    plex_item = _FakePlexItem()
    facts = _StatusFacts(tmdb_status="returning")
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = []

    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, facts)
    assert plex_item.uploads == 1
    gate_off_fingerprint = render.badge_fingerprint

    config_with_badges.badges.families = ["status"]
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, facts)
    assert plex_item.uploads == 2, "enabling a family must re-badge an already-uploaded item"
    enabled_fingerprint = render.badge_fingerprint
    assert enabled_fingerprint != gate_off_fingerprint

    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, facts)
    assert plex_item.uploads == 2, "the second pass must upload nothing"
    assert render.badge_fingerprint == enabled_fingerprint

    config_with_badges.badges.families = []
    await apply_badges(session, config_with_badges, render, item, FakeServer(plex_item), REF, facts)
    assert plex_item.uploads == 3
    assert render.badge_fingerprint == gate_off_fingerprint, (
        "disabling the family must revert the fingerprint exactly"
    )


async def test_the_family_list_gaining_status_moves_no_fingerprint_for_a_config_without_it(
    session, config_with_badges
):
    """The other end of the same law: `FAMILIES` gaining an eleventh key must
    not move a fingerprint for a config that does not name it.
    `BadgesConfig.all_definitions()` expands only NAMED families, so a
    C1/C2a/C2b config is byte-identical across this sub-phase -- and so is
    the empty one, which is what the two pinned literals at the top of this
    file (`576f88e5...` and `PRE_SEAM_ONE_DEFINITION_FINGERPRINT`) guard
    unmodified. Neither literal is edited by C2c."""
    from autoposter.config.schema import BadgesConfig

    assert BadgesConfig().all_definitions() == []
    assert BadgesConfig(families=["direct_play"]).all_definitions() == list(
        FAMILIES["direct_play"]
    )

    item, render = await _render(session, rating_key="c2c-untouched")
    plex_item = _FakePlexItem()
    config_with_badges.badges.families = ["direct_play"]
    config_with_badges.badges.definitions = []
    await apply_badges(
        session, config_with_badges, render, item, FakeServer(plex_item), REF, _StatusFacts()
    )
    first = render.badge_fingerprint
    await apply_badges(
        session, config_with_badges, render, item, FakeServer(plex_item), REF, _StatusFacts()
    )
    assert render.badge_fingerprint == first
    assert plex_item.uploads == 1
