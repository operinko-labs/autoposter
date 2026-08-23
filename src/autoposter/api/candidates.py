"""Browsing every provider's artwork for one item and art kind.

The render path asks the ladder for *one* image (``providers/ladder.py``) and
throws the rest away. This endpoint asks the same clients the same question and
returns the whole answer, so an operator can see what the automatic pick chose
between -- and, in the next task, choose differently.

Two things make a browse different from a selection, and both are deliberate:

*Nothing is discarded.* ``best_candidate`` drops every candidate whose language
is not in the configured order, which is right for an unattended render and
exactly wrong here: those are the images the ladder refused, and hiding them
would leave the picker unable to offer the one an operator went looking for.
They are sorted last instead, using the ladder's own ``rank_key`` so the top of
the grid is the order the renderer would have used.

*Nothing fails the request.* The providers are fanned out concurrently and one
raising costs that provider's rows and nothing else. A 500 here takes the whole
picker away over a single flaky upstream, and the other two providers' images
were already in hand when it happened.

No provider is asked for anything the item's own columns did not supply: the
``ArtRequest`` is built from the database row exactly as ``render_artifact``
builds its own, so the list shown is the list the ladder chose from.
"""
import asyncio
import inspect
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from autoposter.api.auth import require_session
from autoposter.db.models import MediaItem, Render
from autoposter.db.models import Session as SessionModel
from autoposter.providers import base as art
from autoposter.providers.ladder import rank_key
from autoposter.render.pipeline import ART_KINDS_FOR, art_config_for

logger = logging.getLogger(__name__)

router = APIRouter()

# The kinds whose poster render composites a clearlogo (render/pipeline.py), so
# the only kinds for which a picked logo would be consumed by anything.
LOGO_BROWSABLE_ITEM_KINDS = frozenset({"movie", "show"})

# TMDB serves size variants by URL prefix. Its clients build every URL from
# IMAGE_BASE, so the swap is a prefix replacement -- keyed on the URL and not on
# the candidate's provider label, which is a client's own ``name`` attribute and
# must never be what decides that a URL can be rewritten.
TMDB_ORIGINAL_PREFIX = "https://image.tmdb.org/t/p/original"
TMDB_THUMB_PREFIX = "https://image.tmdb.org/t/p/w342"


def thumb_url(url: str) -> str:
    """A grid-sized variant where one exists, otherwise the image itself.

    TVDB and Fanart serve a single size per artwork, so their candidates are
    their own thumbnails; a grid of TMDB originals would be tens of megabytes.
    """
    if url.startswith(TMDB_ORIGINAL_PREFIX):
        return TMDB_THUMB_PREFIX + url[len(TMDB_ORIGINAL_PREFIX):]
    return url


def language_order_for(config, art_kind: str) -> list[str]:
    """The preference list the renderer would rank this art kind with.

    Logos are ranked by ``artwork.logo_language_order``, which is a different
    list with different contents -- no ``xx`` in the shipped config -- so
    reusing the poster's would put a textless logo first in a grid where the
    render would have put it last.
    """
    if art_kind == art.LOGO:
        return config.artwork.logo_language_order
    return art_config_for(config, art_kind).language_order


def _art_request(item: MediaItem, art_kind: str) -> art.ArtRequest:
    """The request ``render_artifact`` builds for this item, field for field.

    ``season_id`` is left unset here as it is there: it is a TVDB-internal id
    that TVDBClient resolves for itself from ``tvdb_id`` and the season number
    (providers/tvdb.py), and filling it from anywhere else would be inventing a
    value the render path never had.
    """
    return art.ArtRequest(
        art_kind=art_kind,
        is_movie=item.kind == "movie",
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        imdb_id=item.imdb_id,
        season_number=item.season_number,
        episode_number=item.episode_number,
    )


async def _fetch(provider, request: art.ArtRequest) -> list[art.ArtCandidate]:
    """One provider's whole list, asking for the widest one it can give.

    TMDB narrows its own response to the configured languages and is the only
    client that does; it takes ``all_languages`` to stop. Detected from the
    signature rather than from the provider's name so a client that grows the
    same keyword gets the same treatment, and one that never will is called the
    way it always was.
    """
    if "all_languages" in inspect.signature(provider.fetch).parameters:
        return await provider.fetch(request, all_languages=True)
    return await provider.fetch(request)


@router.get("/items/{item_id}/candidates/{art_kind}")
async def browse_candidates(
    item_id: int,
    art_kind: str,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Every provider's artwork for one item and art kind, in ladder order.

    ``art_kind`` is checked against the item's own kind before any provider is
    asked -- the same idiom as clear-override, and for the same reason: it is
    caller-supplied, and an unchecked value reaches config lookups and provider
    clients that have no answer for it. ``"logo"`` is allowed on top of
    ``ART_KINDS_FOR`` for movies and shows, because a logo is first-class at the
    provider layer and absent downstream: there is no render row for one, and
    ``current`` is therefore always null for it.
    """
    config = request.app.state.config
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = (
            await session.execute(select(MediaItem).where(MediaItem.id == item_id))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        allowed = set(ART_KINDS_FOR.get(item.kind, ()))
        if item.kind in LOGO_BROWSABLE_ITEM_KINDS:
            allowed.add(art.LOGO)
        if art_kind not in allowed:
            raise HTTPException(status_code=404, detail="unknown art kind for this item")

        current = None
        if art_kind != art.LOGO:
            render = (
                await session.execute(
                    select(Render).where(Render.item_id == item_id, Render.art_kind == art_kind)
                )
            ).scalar_one_or_none()
            if render is not None:
                current = {"source_url": render.source_url, "provider": render.provider}

        art_request = _art_request(item, art_kind)

    providers = request.app.state.providers
    # return_exceptions, so one client raising does not cancel the siblings that
    # had already answered -- a bare gather propagates the first exception and
    # this endpoint would 500 holding two thirds of a usable list.
    results = await asyncio.gather(
        *(_fetch(provider, art_request) for provider in providers), return_exceptions=True
    )

    candidates: list[art.ArtCandidate] = []
    errors: dict[str, str] = {}
    for provider, result in zip(providers, results, strict=True):
        if isinstance(result, BaseException):
            # The type name, never str(exc): httpx puts the full request URL in
            # an HTTPStatusError's message and Fanart passes its API key as a
            # query parameter, so the message is a credential leak into both the
            # response body and this log line. The traceback stays in the log,
            # where the ladder already puts its own provider failures.
            errors[provider.name] = type(result).__name__
            logger.warning(
                "provider %s failed browsing %s for item %d",
                provider.name, art_kind, item_id, exc_info=result,
            )
            continue
        candidates.extend(result)

    order = language_order_for(config, art_kind)
    # rank_key sorts UNRANKED (a language the config never asked for) last of
    # its own accord, which is why the ladder's filter is not reused here.
    candidates.sort(key=lambda candidate: rank_key(candidate, order))

    return {
        "candidates": [
            {
                "provider": candidate.provider,
                "url": candidate.url,
                "thumb_url": thumb_url(candidate.url),
                "language": candidate.language,
                "width": candidate.width,
                "height": candidate.height,
                "score": candidate.score,
                "includes_text": candidate.includes_text,
            }
            for candidate in candidates
        ],
        "errors": errors,
        "current": current,
    }
