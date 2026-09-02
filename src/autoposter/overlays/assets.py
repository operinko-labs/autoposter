"""Where the overlay engine's bundled assets and canvas constants will live.

These are copies of `badges/spec.py`'s module-level constants, scaffolded
here ahead of the module that needs them: `overlays/builtin.py` (T3) will
read these while `badges/spec.py` reads `overlays/builtin.py`'s definitions
-- the two directions would otherwise be a cycle. Until T3 lands,
`badges/spec.py` still defines `INTER_MEDIUM`, `BACK_COLOR`, `POSTER_CANVAS`
and friends itself rather than re-exporting them from here, so this module is
currently dead code with no importer -- deliberately, not an oversight.
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
