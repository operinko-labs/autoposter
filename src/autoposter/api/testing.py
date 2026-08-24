"""On-demand sample renders for the config editor.

Testing mode answers one question an operator has while tuning the artwork
settings: *what does a poster actually look like with these values?* It renders
one styled artifact of the requested kind against a generated sample canvas and
hands the bytes straight back -- no provider, no Plex, no database row, no file
left on any mount.

The canvas is a solid colour rather than a real base image, exactly as the tool
being replaced does it: there is no title-card or season base fixture to render
against, and shipping one real poster as *the* sample base would be an arbitrary
choice masquerading as a neutral one. A solid fill makes it obvious the sample
is a sample.

Two things make this honest rather than a demo:

* The config comes from the live holder (``config_holder.current``), so an edit
  saved through the config editor is visible in the very next sample -- the
  roadmap's acceptance line, "sample sheets change when config changes".
* A title that will not fit at the minimum point size is reported as the
  *truncation outcome* the pipeline actually produces, not smoothed over. The
  pipeline writes no file at all in that case (render/pipeline.py), so testing
  mode's job is to show that, not to invent artwork the running system never
  would.

``compose_styled`` is awaited directly. It is async and does its own
per-invocation ``to_thread`` offloading for each ImageMagick call, so wrapping
it in a thread would hand a coroutine to a thread and never run it (its own
docstring warns about exactly that). The blocking bits this module owns -- the
one ``compositor.run`` that paints the sample canvas, and reading the finished
bytes off disk -- are the ones offloaded here.
"""
import asyncio
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from autoposter.api.auth import require_session
from autoposter.db.models import Session as SessionModel
from autoposter.plex.client import ResolvedItem
from autoposter.render import compositor
from autoposter.render.pipeline import _CANVAS, compose_styled, title_text_for

router = APIRouter()

# The kinds that are rendered on their own. This is exactly ``_CANVAS``'s key
# set -- poster, season_poster, background, title_card -- and deliberately NOT
# ``logo``: a logo is composited over a poster and has no canvas of its own, so
# there is nothing to render a sample of.
SampleKind = Literal["poster", "season_poster", "background", "title_card"]

# Fixed sample titles, one per length, so a sample is reproducible and the
# operator can compare two configs on the same text.
SAMPLE_TITLES: dict[str, str] = {
    # Fits any box at a large point size -- shows the settings at their best.
    "short": "Up",
    # ~30 characters: a realistic title that wraps but still fits.
    "medium": "The Grand Budapest Hotel Deluxe",
    # Deliberately far too long to fit at the minimum point size, so the
    # truncation outcome can be seen without contriving a tiny box.
    "long": (
        "The Extraordinarily Prolonged and Utterly Unwieldy Title That No "
        "Reasonable Poster Box Could Ever Hope to Contain in Full Legibly"
    ),
}

# Numbers for the title card's secondary line, so ``title_text_for`` renders it
# (Season 2 - Episode 5) rather than leaving it blank.
SAMPLE_SEASON = 2
SAMPLE_EPISODE = 5

# A fixed placeholder fill. The exact shade is not load-bearing -- only that the
# canvas is a solid generated colour rather than a real image; the tool being
# replaced uses a pink for the same purpose.
SAMPLE_CANVAS_COLOR = "#FF4FA3"


class SampleRequest(BaseModel):
    """What to render a sample of.

    Both fields are ``Literal`` unions, so an unknown ``art_kind`` (``logo``
    included) or ``length`` is a 422 from validation before the handler runs --
    nothing is rendered for a request there is no sample for.
    """

    art_kind: SampleKind
    length: Literal["short", "medium", "long"]


def _sample_item(length: str) -> ResolvedItem:
    """A synthetic item carrying only what ``title_text_for`` reads: the title
    and, for the title card, the season and episode numbers."""
    return ResolvedItem(
        rating_key="sample", library="sample", kind="movie",
        title=SAMPLE_TITLES[length], year=None,
        season_number=SAMPLE_SEASON, episode_number=SAMPLE_EPISODE,
        root_folder="sample", file_path=None, art_url=None,
        tmdb_id=None, tvdb_id=None, imdb_id=None,
    )


@router.post("/testing/sample")
async def render_sample(
    body: SampleRequest,
    request: Request,
    _: SessionModel = Depends(require_session),
):
    """Render one styled sample and return it inline.

    The styled JPEG bytes when the text fits, or a 200 JSON body reporting the
    truncation outcome when it does not -- the pipeline produces no artifact in
    that case, and this says so rather than pretending otherwise.
    """
    config = request.app.state.config_holder.current
    art_kind = body.art_kind
    item = _sample_item(body.length)
    primary_text, secondary_text = title_text_for(art_kind, item, config)

    with tempfile.TemporaryDirectory() as tmpdir:
        working = Path(tmpdir) / "sample.jpg"
        # One magick invocation paints the solid sample canvas at the kind's
        # own size. Offloaded because it is blocking subprocess work, like the
        # compositor calls compose_styled makes below.
        await asyncio.to_thread(
            compositor.run,
            [
                config.magick_binary, "-size", _CANVAS[art_kind],
                f"xc:{SAMPLE_CANVAS_COLOR}", str(working),
            ],
        )

        result = await compose_styled(
            config, art_kind, working,
            primary_text=primary_text, secondary_text=secondary_text,
            draw_text=True, logo_path=None,
        )
        if result.truncated:
            return {"truncated": True, "art_kind": art_kind, "length": body.length}

        data = await asyncio.to_thread(result.output.read_bytes)

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
