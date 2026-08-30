"""Generated divider art: our exact titles on upstream's textless layers.

Seven of the ten groups -- and every group added since -- have no upstream
separator whose baked-in word matches our divider's title (the recon's
inventory: upstream names art by defaults FILE, not by category). Mapping
them to near-miss art would put GENRE on a divider titled "Content
Collections". Instead: fetch the style's textless layer
(``separators/@base/<style>.png``), caption the divider's own
``separator_title`` onto it with the in-tree ImageMagick pipeline
(``render/compositor.py`` + ``render/textfit.py`` -- no new dependency), and
cache the result under ``<assets_root>/.generated/separators/``. Exact by
construction, and a future group's divider is free.

**The parameters are upstream's, transcribed -- not ours, and not guessed.**
``Kometa-Team/Defaults-Image-Creation@a9e02e9`` is MIT (``Copyright (c) 2025
Kometa Team``) and its ``create_defaults/create_default_posters.ps1`` generates
the very separators we fetch for the other three groups: face
``Comfortaa-Medium``, ALL CAPS, ``#FFFFFF``, caption box 1900x1000, point size
fitted then clamped to [100, 203], composited ``-gravity center -geometry
+0+0`` onto the 2000x3000 ``@base/<style>.png``.
``.superpowers/sdd/p-div-font.md`` records the transcription and the render
that verified it: upstream's own shipped ``separators/orig/genre.jpg``,
reproduced from its own ``@base/orig.png``, RMSE 0.000158 normalised -- JPEG
quantisation noise. Two consequences of that verification are worth keeping in
view:

- ``caption:`` WORD-WRAPS, so a longer label does not shrink -- it takes
  another line and keeps growing until the box is full. Every real divider
  label fits at 243 and is clamped to 203, so the CLAMP decides, not the fit.
  That also makes the result robust across ImageMagick builds: the fit would
  have to fall 17% before the version mattered.
- ``compositor._caption_group`` emits ``-trim +repage -extent``; upstream emits
  ``-trim -extent`` with no ``+repage``. That divergence was MEASURED rather
  than assumed harmless (``.superpowers/run-pdiv-t2-repage.log``): the two
  argvs produce pixel-identical output here, RMSE 0 against each other and
  0.000158 against upstream's shipped file. So the shared builder is reused
  rather than forked.

**Licence, honestly.** ``Kometa-Team/Default-Images`` -- the shipped separator
JPEGs *and* the ``@base`` layers -- carries no LICENSE file, deliberately:
asked directly, the maintainer said "nearly all the default images are based on
other work, so I'm not sure it's reasonable or valid to apply a license to
derivative works" (per the maintainer on Discord, 2026-08-29). Generating does
NOT buy us a cleaner posture than fetching: we do not redistribute upstream's
captioned separators, but the image we produce is upstream's textless layer
with our caption on it, fetched at runtime. Only the caption is ours. That is
the same derivative posture as fetching their finished JPEGs -- see
``posters.py``'s module docstring: private single-operator deployment, fetched
at runtime, revisit alongside ``assets/badges/PROVENANCE.md`` if this
repository is ever published. The FONT is the one piece with a licence of its
own, SIL OFL, vendored with its ``OFL.txt`` under
``assets/fonts/PROVENANCE.md``.

**Determinism and the cache.** Same base + same font + same title + same
ImageMagick build => same bytes, which ``apply_poster``'s sha-compare turns
into "an unchanged pass uploads nothing". Across ImageMagick BUILDS the bytes
may drift (the ``imagemagick(hdri)`` conftest gate exists for exactly this);
the cache absorbs that at runtime -- a rendered file is never re-rendered --
and a lost cache costs one re-upload per divider, which then settles.

**Fetch first, magick second, deliberately.** The base layer is resolved
(cache, then network) before any magick invocation, so an environment where
the fetch fails -- the golden harness 404s ``@base`` on purpose -- degrades
to ``None`` ("no poster source") without needing a magick binary.
"""
import asyncio
import logging
import shutil
from pathlib import Path

from autoposter.assets import asset_path
from autoposter.collections.posters import DEFAULT_IMAGES_BASE, fetch_poster
from autoposter.config.schema import TextStyle
from autoposter.render.compositor import build_text_argv, run
from autoposter.render.textfit import fit_point_size, prepare_text

logger = logging.getLogger(__name__)

# The EXACT bytes upstream's generator uses, vendored from
# ``Defaults-Image-Creation@a9e02e9/create_defaults/fonts/`` -- sha256
# 992f89f3…, pinned by ``tests/test_separator_art.py``. Not Google Fonts'
# variable ``Comfortaa[wght].ttf`` instanced to 500: that is a different file,
# it would throw the RMSE verification away, and under OFL's Reserved Font
# Name clause an instanced copy is a Modified Version. ``OFL.txt`` is vendored
# beside it because neither Kometa repository ships one.
FONT = asset_path("fonts") / "Comfortaa-Medium.ttf"

# The text block, transcribed from ``create_default_posters.ps1`` lines
# 5546-5549 and the composer at ``create_poster.ps1:1453`` -- see the module
# docstring. ``max_point_size`` is 203, not a round 200: every real label
# clamps to it, so 200 would render every divider 1.5% small against
# upstream's own art for no reason. ``min_point_size`` 100 is upstream's
# floor; where upstream logs "text is too small and will be truncated" and
# writes the poster anyway, we REFUSE (see ``_render``) -- a deliberate
# divergence, unreachable for any real divider title.
TEXT = TextStyle(
    font="Comfortaa-Medium.ttf",
    all_caps=True,
    font_color="#FFFFFF",
    min_point_size=100,
    max_point_size=203,
    max_width=1900,
    max_height=1000,
    text_offset="+0",
    gravity="center",
)

# Upstream's ``-quality 100%``. The caption is composited at this quality and
# the final JPEG is written at it, so the only loss between upstream's output
# and ours is the one they take too.
_QUALITY = "100%"


def _cache_root(config) -> Path:
    """``<assets_root>/.generated/separators`` -- deliberately NOT the
    ``<library>/<title>/poster.jpg`` operator-override layout, so generated
    art can never masquerade as a hand-placed file and clobbering one is
    structurally impossible."""
    return Path(config.assets_root) / ".generated" / "separators"


async def _base_layer(config, http, style: str) -> Path | None:
    """The style's textless layer: cache hit, else one validated fetch.

    ``Default-Images/separators/@base/<style>.png`` is the SAME git blob as the
    ``Defaults-Image-Creation@a9e02e9/create_defaults/@base/<style>.png`` the
    verification render used (``5b0413792e4e60f384740d781a84652cf0976e31``,
    152119 B for ``orig``; checked 2026-08-30, task-1 review), and all 22 style
    PNGs are present -- so the RMSE result transfers to this fetch path
    unchanged. That is validated as an image, not pinned by digest -- a
    reworked upstream layer is picked up only on a cache loss.
    """
    target = _cache_root(config) / "@base" / f"{style}.png"
    if target.is_file():
        return target
    if http is None:
        return None
    url = f"{DEFAULT_IMAGES_BASE}/separators/@base/{style}.png"
    data = await fetch_poster(http, url)
    if data is None:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _render(magick: str, base: Path, target: Path, title: str) -> None:
    """Caption ``title`` onto a copy of ``base`` and write ``target``.

    Blocking (two subprocess runs); the caller threads it. The caption is drawn
    onto a lossless PNG copy and only then written as JPEG, so the image is
    compressed once rather than twice. ``-strip`` on that final write drops the
    per-run ``date:create``/``date:modify`` properties ImageMagick would
    otherwise embed, which is what makes equal inputs give equal bytes -- and
    ``apply_poster``'s sha-compare turn into "an unchanged pass uploads
    nothing".

    A title that will not fit above ``min_point_size`` is REFUSED rather than
    drawn. Upstream clamps up to its floor and logs "Text is too small and will
    be truncated"; we follow ``fit_point_size``'s contract in this codebase
    instead, which the render pipeline already follows -- illegible artwork is
    worse than none, because it still gets hashed and so is never retried.
    """
    work = target.with_suffix(".work.png")
    shutil.copyfile(base, work)
    try:
        text = prepare_text(title, TEXT)
        fit = fit_point_size(magick, str(FONT), TEXT, text)
        if fit.truncated:
            raise RuntimeError(
                "%r does not fit the separator text box legibly" % title
            )
        run(build_text_argv(
            magick, str(work), TEXT, str(FONT), fit.point_size, text, _QUALITY,
        ))
        run([magick, str(work), "-strip", "-quality", "100", str(target)])
    finally:
        work.unlink(missing_ok=True)


async def ensure_separator_art(
    config, http, style: str, group: str, title: str
) -> Path | None:
    """The rendered divider art for ``group`` in ``style``, or ``None``.

    Cache first (a rendered file is final -- see the module docstring), the
    base layer second, magick last. ``http`` may be ``None``: that refuses the
    FETCH only, so a style whose base layer is already cached still renders.
    Every failure -- no client and no cached layer, an unfetchable layer, a
    render error -- returns ``None`` and leaves no partial file:
    ``reconcile_separator`` then reports "no poster source" and the NULL
    ``poster_sha256`` lets a later pass retry, the same posture as a 404ing
    hosted default.
    """
    target = _cache_root(config) / style / f"{group}.jpg"
    if target.is_file():
        return target
    base = await _base_layer(config, http, style)
    if base is None:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(_render, config.magick_binary, base, target, title)
    except Exception:
        logger.exception(
            "could not generate separator art for %r (style %r)", title, style
        )
        target.unlink(missing_ok=True)
        return None
    return target
