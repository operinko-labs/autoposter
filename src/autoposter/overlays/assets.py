"""Where the overlay engine's bundled assets and canvas constants live.

These were `badges/spec.py`'s module-level constants. They moved down here
so `overlays/builtin.py` can read them while `badges/spec.py` reads
`overlays/builtin.py`'s definitions -- the two directions would otherwise be
a cycle. `badges/spec.py` re-exports every name, so existing imports of
`INTER_MEDIUM`, `BACK_COLOR`, `POSTER_CANVAS` and friends are unchanged.

Two importers today: `overlays/builtin.py` and `badges/spec.py`.
"""
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
