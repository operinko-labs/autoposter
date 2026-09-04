import logging
from collections.abc import Collection
from dataclasses import dataclass

from autoposter.providers.base import ArtCandidate, ArtRequest

logger = logging.getLogger(__name__)

UNRANKED = 99

# TVDB reports ISO-639-2/T three-letter codes; the config uses two-letter codes.
_THREE_TO_TWO = {"eng": "en", "fin": "fi", "deu": "de", "swe": "sv", "fra": "fr", "spa": "es"}


@dataclass(frozen=True)
class Selection:
    candidate: ArtCandidate | None
    is_fallback: bool


def normalise_language(language: str | None) -> str | None:
    """The two-letter form of a provider's language tag.

    TVDB reports ISO-639-2/T three-letter codes and the config speaks
    two-letter ones, so ``eng`` and ``en`` are the same preference and must
    compare equal. Public because render/pipeline.py stores the normalised
    code as a quality fact: an ``eng`` sitting in that column would read as a
    language miss against every ``en``-preferring order.
    """
    if language is None:
        return None
    return _THREE_TO_TWO.get(language, language)


def language_rank(candidate: ArtCandidate, language_order: list[str]) -> int:
    """Position of this candidate's language in the preference list.

    Textless art always ranks at the position of ``xx``; anything unlisted sorts
    last.
    """
    if candidate.is_textless and "xx" in language_order:
        return language_order.index("xx")
    code = normalise_language(candidate.language)
    if code in language_order:
        return language_order.index(code)
    return UNRANKED


def rank_key(candidate: ArtCandidate, language_order: list[str]) -> tuple:
    """Ascending sort key: language, then explicit textlessness, then quality, then size."""
    pixels = (candidate.width or 0) * (candidate.height or 0)
    text_rank = 0 if candidate.is_textless else 1
    return (language_rank(candidate, language_order), text_rank, -candidate.score, -pixels)


def best_candidate(
    candidates: list[ArtCandidate], language_order: list[str]
) -> ArtCandidate | None:
    usable = [c for c in candidates if language_rank(c, language_order) != UNRANKED]
    if not usable:
        return None
    return sorted(usable, key=lambda c: rank_key(c, language_order))[0]


def prefers_textless(language_order: list[str]) -> bool:
    return bool(language_order) and language_order[0] == "xx"


def textless_only(language_order: list[str]) -> bool:
    return language_order == ["xx"]


async def select_artwork(
    providers: list,
    language_order: list[str],
    request: ArtRequest,
    *,
    exclude_urls: Collection[str] = (),
) -> Selection:
    """Walk providers in order and return the best artwork.

    When textless is preferred but a provider offers only text-bearing art, that
    image is parked and the walk continues. The parked image is used only after
    every provider has been tried, and never in textless-only mode.

    ``exclude_urls`` drops candidates by URL before ``best_candidate`` ranks
    them, which is how a caller asks for the NEXT best. The render path needs
    that because a logo can only be found unusable *after* it has been
    downloaded and decoded (``render/pipeline._validate_image``), and until
    now the ladder had no way to answer "and then what" -- so one corrupt
    clearlogo refused its poster on every visit forever.

    Keyword-only, and empty by default. Every pre-existing call site -- the
    two base-image asks in ``render/pipeline.render_artifact`` and the
    mass-ops logo updater in ``artwork_modes/logo.py`` -- passes nothing and
    gets exactly the walk it always got, which is what keeps an unaffected
    row's fingerprint from moving.
    """
    prefer = prefers_textless(language_order)
    only = textless_only(language_order)
    parked: ArtCandidate | None = None

    for provider in providers:
        try:
            candidates = await provider.fetch(request)
        except Exception:
            logger.warning("provider %s failed, continuing", provider.name, exc_info=True)
            continue

        choice = best_candidate(
            [c for c in candidates if c.url not in exclude_urls], language_order
        )
        if choice is None:
            continue
        if prefer and not choice.is_textless:
            if only:
                continue
            if parked is None:
                parked = choice
            continue
        return Selection(candidate=choice, is_fallback=False)

    if parked is not None and not only:
        return Selection(candidate=parked, is_fallback=True)
    return Selection(candidate=None, is_fallback=False)
