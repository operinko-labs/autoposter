"""Badge definitions.

Every parity-critical constant lives here so it can be reviewed in one place.
Values come from Kometa v2.4.8's overlay defaults, cross-checked against
measured pixels from two production oracle images -- see
docs/research/kometa-overlays.md.
"""
from dataclasses import dataclass
from pathlib import Path

from autoposter.assets import asset_path

ASSETS = asset_path("badges")
FONTS = ASSETS / "fonts"
IMAGES = ASSETS / "images"

POSTER_CANVAS = (1000, 1500)
EPISODE_CANVAS = (1920, 1080)

# "#00000099" -- 60% opacity black.
BACK_COLOR = (0, 0, 0, 153)
FONT_COLOR = (255, 255, 255, 255)

INTER_BOLD = FONTS / "Inter-Bold.ttf"
INTER_MEDIUM = FONTS / "Inter-Medium.ttf"


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


BADGES: dict[str, BadgeSpec] = {
    "resolution": BadgeSpec("resolution", "left", 15, "top", 15, (305, 105)),
    "audio_codec": BadgeSpec("audio_codec", "center", 0, "top", 15, (305, 105)),
    "critic": BadgeSpec(
        "critic", "right", 30, "center", -105, (160, 160),
        padding=15, font=INTER_BOLD, font_size=63,
    ),
    "audience": BadgeSpec(
        "audience", "right", 30, "center", 105, (160, 160),
        padding=15, font=INTER_BOLD, font_size=63,
    ),
    # 270, not 30: Kometa's `vertical_align.exists: false` branch fires here
    # even though the file sets `vertical_align: bottom`. Confirmed in pixels.
    "commonsense": BadgeSpec(
        "commonsense", "left", 15, "bottom", 270, (305, 105), font=INTER_MEDIUM,
    ),
    "video_format": BadgeSpec(
        "video_format", "left", 15, "bottom", 30, (305, 105), font=INTER_MEDIUM,
    ),
    "runtimes": BadgeSpec(
        "runtimes", "right", 15, "bottom", 30, (600, 105), font=INTER_MEDIUM,
    ),
    # 150 for the same reason as commonsense's 270; measured at y=825 on a
    # 1080-high canvas, which is exactly 1080 - 105 - 150.
    "episode_info": BadgeSpec(
        "episode_info", "right", 15, "bottom", 150, (305, 105), font=INTER_MEDIUM,
    ),
    # The only badge without a backdrop: the production config applies the
    # languages overlay twice, the second time fully transparent, and neither
    # oracle image shows a backdrop behind the flag.
    "languages": BadgeSpec(
        "languages", "left", 15, "top", 223, (190, 105),
        radius=26, font=INTER_BOLD, font_size=50, has_back=False,
    ),
}


def canvas_for(art_kind: str) -> tuple[int, int]:
    """Kometa sizes by item type: episodes are landscape, everything else portrait."""
    return EPISODE_CANVAS if art_kind in ("title_card", "background") else POSTER_CANVAS
