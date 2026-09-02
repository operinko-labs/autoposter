"""Badge definitions.

The nine badges are now `OverlayDefinition` rows in
`autoposter.overlays.builtin` (roadmap row 97) rather than hardcoded
`BadgeSpec` rows here. This module keeps `BadgeSpec` and `BADGES` and DERIVES
the latter from those definitions, so every existing badge pin -- the measured
boxes, the coordinate formula, the Pillow primitives, the composition and the
production-residual comparison -- reads exactly what it read before the swap
and is the oracle proving the swap lossless.

Every parity-critical constant now lives in `autoposter.overlays.assets` and
is re-exported below so existing imports of `INTER_MEDIUM`, `BACK_COLOR`,
`POSTER_CANVAS` and friends are unchanged.
"""
from dataclasses import dataclass
from pathlib import Path

from autoposter.overlays.assets import (
    ASSETS,
    BACK_COLOR,
    EPISODE_CANVAS,
    FONT_COLOR,
    FONTS,
    IMAGES,
    INTER_BOLD,
    INTER_MEDIUM,
    POSTER_CANVAS,
)
from autoposter.overlays.builtin import BUILTIN_OVERLAYS

__all__ = [
    "ASSETS", "FONTS", "IMAGES", "POSTER_CANVAS", "EPISODE_CANVAS",
    "BACK_COLOR", "FONT_COLOR", "INTER_BOLD", "INTER_MEDIUM",
    "BadgeSpec", "BADGES", "canvas_for",
]


@dataclass(frozen=True)
class BadgeSpec:
    name: str
    h_align: str
    h_offset: int
    v_align: str
    v_offset: int
    box: tuple[int, int]
    padding: int = 0
    radius: int = 30
    font: Path | None = None
    font_size: int = 55
    has_back: bool = True


def _as_spec(name: str) -> BadgeSpec:
    """One overlay definition, in the shape the existing pins read.

    A pure projection: every value is read off the definition, nothing is
    defaulted here. That is what makes a mutation of any definition attribute
    show up as a failing pin.
    """
    definition = BUILTIN_OVERLAYS[name]
    return BadgeSpec(
        name=name,
        h_align=definition.horizontal_align,
        h_offset=definition.horizontal_offset,
        v_align=definition.vertical_align,
        v_offset=definition.vertical_offset,
        box=(definition.back_width, definition.back_height),
        padding=definition.back_padding,
        radius=definition.back_radius,
        font=Path(definition.font) if definition.font else None,
        font_size=definition.font_size,
        has_back=definition.has_back,
    )


BADGES: dict[str, BadgeSpec] = {name: _as_spec(name) for name in BUILTIN_OVERLAYS}


def canvas_for(art_kind: str) -> tuple[int, int]:
    """Kometa sizes by item type: episodes are landscape, everything else portrait."""
    return EPISODE_CANVAS if art_kind in ("title_card", "background") else POSTER_CANVAS
