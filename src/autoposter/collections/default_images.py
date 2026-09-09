"""Which upstream default poster, if any, a managed collection carries.

``collections/posters.py`` answers that question for the five kinds it still
owns -- awards, content ratings, separators -- from a hand-checked table of
Kometa's own folder names. ``chart`` was a sixth until roadmap row 252 moved it
here, where the disk cache it had been doing without already lived. This module
answers it for the families whose members are enumerated rather than curated:
one row per family we BUILD, naming the ``Kometa-Team/Default-Images``
directory its art lives in and the key scheme that directory is named by.

**The table is DATA, and the data was listed rather than recalled.**
``.superpowers/sdd/p-defimg-probe.md`` records the live listings -- §1 through
§5 from the phase's first probe, §6 from this task's supplementary capture --
and every directory, every key scheme and every code in the three lookup
tables below is transcribed from those listings. Upstream keys by *name*, never
by id: the probe found no TMDb-collection-id naming anywhere in the repository,
so a franchise is ``franchise/Jurassic Park.jpg`` and not
``franchise/1241.jpg``.

**Four key schemes, and the split is upstream's, not ours.**

- *exact display name* -- ``franchise``, ``genre``, ``studio``, ``country``,
  ``network``, ``streaming``, ``chart``. Spaces and punctuation are literal in
  the filename and percent-encoded in the URL.
- *lowercase ISO code* -- ``audio_language``, ``subtitle_language``.
- *number* -- ``decade``, ``year``.
- *short lowercase code* -- ``universe`` (``mcu``, ``dcu``, ``dca`` ...), and
  ``resolution``'s labels (``4k``, ``1080``), which are neither names nor
  numbers.

**Two directory shapes, and no code derives one from the other.** Most families
keep their full posters flat at ``<dir>/<name>.jpg``. Four -- ``network``,
``country``, ``streaming`` and ``chart`` -- keep nothing at their base but an
index image, with the full poster under ``<dir>/color/`` (probe §4,
"Pattern B"). Each row spells its directory in full for that reason.
``logos/``, ``overlays/``, ``white/``, ``best/`` and ``standards/`` are never
read: they are logo cutouts, name-stamped overlays and rendering variants, out
of scope by the phase's own C1.6, and the probe measured that their casing and
membership do not even match the base set.

**A miss is the common case, and it is silent.** Upstream curates 116
franchises; our franchise family enumerates whatever the library holds. So
``ensure_default_image`` answers ``None`` on a 404, logs one DEBUG line, and the
collection keeps exactly the poster it had -- the operator's own file, or
nothing. Never a WARNING: "where available" is the specification, not a
degraded state.

**Fetching and caching, the ``separator_art`` precedent.** A resolved image is
written under ``<assets_root>/.generated/collection-posters/<family>/`` --
deliberately NOT the ``<library>/<title>/poster.jpg`` operator-override layout,
so a fetched default can never masquerade as a hand-placed file and clobbering
one is structurally impossible. ``.generated`` is already exempt from the asset
prune (``scheduler/jobs.py``). The cache stem is OUR key, percent-encoded with
nothing safe -- so no collection title can add a path segment, and a name
variant that answered is not re-walked on the next pass. A MISS is cached too,
but only a PROVEN one: a 404 on every candidate name writes an empty ``.miss``
marker, because the display-name families try several candidate names and
without the marker a library of 250 unmatched franchises would spend a
thousand requests per pass proving the same absence. Anything else -- a 429, a
5xx, a timeout, a connection error, a 200 that doesn't decode as an image -- is
an UNPROVEN failure and writes nothing, so the next pass retries it; that
mirrors the shipped award/chart path, where a failed fetch simply leaves
``poster_sha256`` NULL for the next pass to pick up. The consequence of a
proven miss is stated rather than hidden -- newly-added upstream art is picked
up on cache loss, and deleting the cache directory is the way to re-check.

**Licence, unchanged and not re-litigated here.** ``Default-Images`` carries no
LICENSE file, deliberately: asked directly, the maintainer said "nearly all the
default images are based on other work, so I'm not sure it's reasonable or
valid to apply a license to derivative works" (per the maintainer on Discord,
2026-08-29). This is a private single-operator deployment, the images are
fetched at runtime rather than vendored, and it replaces a tool that does
exactly the same thing. That is ``collections/posters.py``'s module docstring's
posture verbatim, and it is the posture here; if this repository is ever
published, revisit both alongside ``assets/fonts/PROVENANCE.md`` and
``assets/badges/PROVENANCE.md``.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from autoposter.collections.posters import DEFAULT_IMAGES_BASE, _is_image

logger = logging.getLogger(__name__)

__all__ = [
    "FAMILIES",
    "RESOLUTION_KEYS",
    "STREAMING_NAMES",
    "UNIVERSE_CODES",
    "Family",
    "candidate_urls",
    "default_image_url",
    "ensure_default_image",
]


@dataclass(frozen=True)
class Family:
    """One family's upstream directory and how that directory is named.

    ``directory`` is the path under the repository root, spelled in full --
    ``genre`` for a flat family, ``network/color`` for a Pattern B one. Never
    derived, because the two shapes have no rule connecting them.

    ``variants`` says whether the display-name ladder applies. It is True only
    where OUR key is a name we may have decorated and upstream's is a name
    Kometa chose -- the franchise family, in practice. A code family gets a
    single candidate: 'fi-FI' is not 'fi' by any rule upstream publishes, and
    inventing one would fetch a plausible wrong flag.
    """

    directory: str
    key_scheme: str
    variants: bool
    note: str


FAMILIES: dict[str, Family] = {
    "franchise": Family(
        "franchise", "display name", True,
        "Probe §1: flat, 116 entries, exact display name. Ours are TMDb's "
        "with the pack's ' Collection' suffix stripped; upstream's are "
        "Kometa's own bucket names, so this is the one family whose ladder "
        "earns its keep -- and the one where a miss is ordinary rather than "
        "surprising.",
    ),
    "genre": Family(
        "genre", "display name", False,
        "Probe §5 `genre/`: flat, 278 entries, exact display name. Our keys "
        "are Kometa's own include-list names (`packs.GENRE_PARAMS`), which is "
        "the same vocabulary that named these files.",
    ),
    "studio": Family(
        "studio", "display name", False,
        "Probe §5 `studio/`: flat, 491 entries, exact display name. Our keys "
        "are Kometa's own 485-name include list.",
    ),
    "country": Family(
        "country/color", "display name", False,
        "Probe §4/§6: Pattern B -- the base directory holds only an index "
        "image and the full poster is the `color/` rendering. Our keys are "
        "Kometa's own 255-name include list.",
    ),
    "network": Family(
        "network/color", "display name", False,
        "Probe §4/§6: Pattern B. Our keys are Kometa's own 272-name include "
        "list. The probe measured that `color/`, `white/` and `logos/` do not "
        "hold identical sets, which is why only `color/` is ever read.",
    ),
    "streaming": Family(
        "streaming/color", "display name", False,
        "Probe §4/§6: Pattern B. Our key is not a title -- it is the TMDb "
        "watch-provider id the definition carries, mapped through "
        "`STREAMING_NAMES` -- because the builder is the generic "
        "`tmdb_discover` and the provider id is the only thing on it that "
        "names the service.",
    ),
    "chart": Family(
        "chart/color", "display name", False,
        "Probe §4/§6: Pattern B, 591 entries under `chart/color/`. Ours is the "
        "one family whose keys were already upstream's own mapping names "
        "before it was a family -- the three `builders/imdb_chart.CHART_TITLES` "
        "titles and the five `builders/tmdb.CHART_TITLES` ones, each of the "
        "latter fetched live (200) by row 146 before the key was chosen -- so "
        "a miss is impossible for everything that ships and the `.miss` marker "
        "guards a FUTURE key rather than a live population. The URL is "
        "byte-identical to the one `posters.hosted_poster_url` built until "
        "roadmap row 252 deleted that branch, because `candidate_urls` "
        "percent-encodes the key with nothing safe exactly as that branch did; "
        "the branch went rather than staying as a fallback because with the "
        "kind here as well, a proven 404 would have been fetched twice per "
        "pass.",
    ),
    "audio_language": Family(
        "audio_language", "iso code", False,
        "Probe §2: flat, lowercase ISO 639-1/639-2 codes. Our keys are Plex's "
        "own language keys, which is the same vocabulary -- and where it is "
        "not (a locale variant such as `es-419`), the 404 fallback is the "
        "answer rather than a normalisation nobody published.",
    ),
    "subtitle_language": Family(
        "subtitle_language", "iso code", False,
        "Probe §3: `audio_language`'s sibling in every respect, upstream as "
        "well as here.",
    ),
    "decade": Family(
        "decade", "number", False,
        "Probe §5 `decade/`: flat decade numbers, `1880.jpg` onward. Our key "
        "is `choice.key` (`1980`), not `choice.title` (`1980s`) -- the split "
        "`DynamicType.key_from` exists to keep straight. `best/` is a "
        "'best of' variant and is not read.",
    ),
    "year": Family(
        "year", "number", False,
        "Probe §5 `year/`: flat year numbers, 153 entries. Read two ways: the "
        "`time_year` pack's eleven windowed collections and any hand-written "
        "`type: year` definition, both keyed on the year itself. `best/` is a "
        "'best of' variant and is not read -- upstream's own `year.yml` points "
        "at `year/best/<<key>>`, and this service serves the flat file for "
        "every year family rather than one directory for the pack and another "
        "for a definition an operator wrote.",
    ),
    "resolution": Family(
        "resolution", "short code", False,
        "Probe §5 `resolution/`: flat labels, `4k.jpg`/`1080.jpg`/`720.jpg`/"
        "`480.jpg`. Reached two ways: the shipped `type: resolution` dynamic "
        "row keys on `choice.key`, and the `media_resolution` pack's four "
        "`plex_all` definitions key on their own `resolution` filter's first "
        "value -- the same four strings either way. `standards/` was never "
        "drilled into and is not read.",
    ),
    "universe": Family(
        "universe", "short code", False,
        "Probe §5 `universe/` and §6's full listing: flat, 22 entries, short "
        "lowercase codes rather than display names. Our key is the LIST REF "
        "the definition carries, mapped through `UNIVERSE_CODES` -- the three "
        "universe builders are generic list builders and the ref is the only "
        "thing on them that names the universe.",
    ),
}


# Our universe collections' list refs -> upstream's short code. Written from
# the §6 listing and this repository's own catalog rows (`catalog._UNIVERSE_LISTS`
# and `catalog._DC_LISTS`) -- a ref on one side, a filename on the other, and
# nothing inferred in between. A universe with no upstream entry is ABSENT here
# rather than mapped to a near miss: 'In Association With DC' is not `dca`
# (which §5 names as DC ANIMATED), and giving it that art would be a plausible
# wrong poster, which is worse than none.
UNIVERSE_CODES: dict[str, str] = {
    "ls543971628": "avp",         # Alien / Predator
    "ls566667558": "arrow",       # Arrowverse
    "ls068768438": "conjuring",   # Conjuring Universe
    "ls4102351575": "fast",       # Fast & Furious
    "ls539646485": "mcu",         # Marvel Cinematic Universe
    "8642250": "dcu",             # DC Universe (TMDb list)
    "ls547463722": "trek",        # Star Trek (§6 `universe/`: `trek.jpg`)
    "ls501373412": "star",        # Star Wars Universe (§6: `star.jpg`)
    "ls567618635": "xmen",        # X-Men Universe (§6: `xmen.jpg`)
    # DC Extended Universe reuses DC Universe's `dcu`. §6's full `universe/`
    # listing holds only `dca` (DC Animated) and `dcu`, no third DC entry, and
    # T1 of the posters phase therefore left this ref out under its own
    # "a code the listing does not show is LEFT OUT, not guessed" rule. This
    # entry is not a guess overturning that rule -- it is an operator
    # RATIFICATION (2026-09-01) of the reuse, recorded in p-defimg-probe.md §6
    # beside the original finding. 'In Association With DC' is NOT ratified
    # and stays out.
    "fa11en82/dc-extended-universe": "dcu",
}


# TMDb watch-provider id -> upstream's service filename. The ids are this
# repository's own (`catalog._STREAMING_SERVICES`); the names are §6's listing.
# The two compound ids are written whole, exactly as the catalog carries them:
# `531|1770` is one collection ("either of these providers"), not two.
STREAMING_NAMES: dict[str, str] = {
    "350": "Apple TV",
    "1759": "BET+",
    "283": "Crunchyroll",
    "510": "discovery+",
    "337": "Disney+",
    "1899": "HBO Max",
    "223": "hayu",
    "15": "Hulu",
    "8": "Netflix",
    "531|1770": "Paramount+",
    "387": "Peacock",
    "9": "Prime Video",
    "528|1854": "AMC+",
    "188": "YouTube",
    "73": "tubi",
}


# The `media_resolution` pack's four bucket keys, which are also the four
# filenames §5 `resolution/` shows. A `plex_all` definition filtered on some
# other resolution value gets no poster rather than a guessed one.
RESOLUTION_KEYS = frozenset({"4k", "1080", "720", "480"})


# What our own titles may carry that upstream's filenames do not. Each entry is
# backed by a §1 sample: `Mission Impossible.jpg` against TMDb's
# 'Mission: Impossible', `Alien Predator.jpg` against our 'Alien / Predator',
# and the bare `Alien.jpg`/`Batman.jpg` against a decorated form. Applied only
# where `Family.variants` is set, and only AFTER the exact name has been tried,
# so a variant can never shadow a real hit.
_SUFFIXES = (" Collection", " Universe", " Saga", " Franchise")
_SUBSTITUTIONS = ((": ", " "), (" / ", " "), (":", ""))


def _variant_names(key: str) -> tuple[str, ...]:
    """``key`` and every documented strip-variant of it, in order, deduped."""
    seen = [key]
    for suffix in _SUFFIXES:
        for name in list(seen):
            if name.endswith(suffix) and len(name) > len(suffix):
                stripped = name[: -len(suffix)]
                if stripped not in seen:
                    seen.append(stripped)
    for old, new in _SUBSTITUTIONS:
        for name in list(seen):
            replaced = name.replace(old, new)
            if replaced != name and replaced not in seen:
                seen.append(replaced)
    return tuple(seen)


def candidate_urls(family: str, key: str) -> tuple[str, ...]:
    """Every URL this family/key could be at, exact first, or ``()``.

    An unrecognised family or an empty key answers with nothing rather than
    guessing -- ``hosted_poster_url``'s own rule, for its own reason: a wrong
    URL 404s and the collection quietly keeps no poster, which is harder to
    spot than an error.
    """
    row = FAMILIES.get(family)
    if row is None or not key:
        return ()
    names = _variant_names(key) if row.variants else (key,)
    return tuple(
        "%s/%s/%s.jpg" % (DEFAULT_IMAGES_BASE, row.directory, quote(name, safe=""))
        for name in names
    )


def default_image_url(family: str, key: str) -> str | None:
    """The first URL this family/key could be at, or ``None``.

    Pure, like everything in ``posters.py``'s three decisions: no request is
    made here. ``ensure_default_image`` is what walks the rest of the ladder.
    """
    urls = candidate_urls(family, key)
    return urls[0] if urls else None


def _cache_root(config) -> Path:
    """``<assets_root>/.generated/collection-posters`` -- beside the separator
    cache and for its reason: deliberately NOT the operator-override layout, so
    a fetched default can never masquerade as a hand-placed file."""
    return Path(config.assets_root) / ".generated" / "collection-posters"


def _cache_paths(config, family: str, key: str) -> tuple[Path, Path]:
    """Where this family/key's image and its miss marker live.

    The stem is OUR key percent-encoded with nothing safe. A collection title
    is not a value this service chose -- it comes from operator config or from
    a collection adopted out of Plex, where ``/`` and ``..`` are both legal --
    and encoding every separator is what makes the stem incapable of adding a
    path segment (the same objection ``posters._poster_candidates`` answers
    with its realpath containment).
    """
    folder = _cache_root(config) / family
    stem = quote(key, safe="")
    return folder / (stem + ".jpg"), folder / (stem + ".miss")


async def _fetch_candidate(http: httpx.AsyncClient, url: str) -> tuple[bytes | None, bool]:
    """One candidate URL's fetch, plus whether the miss PROVES an absence.

    A 404 is upstream's proof that no such file exists -- the only failure
    allowed to write the ``.miss`` marker. Everything else (a 429/403/5xx, a
    timeout, a connection error, a 200 that doesn't decode as an image) is an
    UNPROVEN failure: it could be a rate limit or a network blip rather than a
    real absence, so it must not poison the cache -- the caller retries it on
    the next pass instead. This is ``posters.fetch_poster``'s validation, split
    so the 404 case can be told apart from the rest.
    """
    try:
        response = await http.get(url)
    except httpx.HTTPError:
        logger.info("could not fetch poster from %s", url)
        return None, False
    if response.status_code == 404:
        return None, True
    try:
        response.raise_for_status()
    except httpx.HTTPError:
        logger.info("could not fetch poster from %s", url)
        return None, False
    data = response.content
    if not _is_image(data):
        logger.info("poster at %s did not decode as an image", url)
        return None, False
    return data, False


async def ensure_default_image(config, http, family: str, key: str) -> Path | None:
    """This collection's cached default poster file, or ``None``.

    Cache first (a cached file is final -- see the module docstring), then one
    validated fetch per candidate name, stopping at the first that answers with
    a real image. Every failure -- an unknown family, no client and no cached
    file, a 404 on every candidate, a body no decoder accepts -- returns
    ``None`` and leaves no partial file, and the caller then reports "no poster
    source" exactly as it does today. The ``.miss`` marker is written only if
    every candidate came back a PROVEN 404; any unproven failure along the way
    means nothing is written, so the next pass retries.

    ``http`` may be ``None``: that refuses the FETCH only, so a family whose
    image is already cached still resolves. Same posture as
    ``separator_art.ensure_separator_art``.
    """
    urls = candidate_urls(family, key)
    if not urls:
        return None
    target, marker = _cache_paths(config, family, key)
    if target.is_file():
        return target
    if marker.is_file() or http is None:
        return None
    proved_absent = True
    for url in urls:
        data, absent = await _fetch_candidate(http, url)
        if data is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return target
        if not absent:
            proved_absent = False
    logger.debug(
        "no Default-Images asset for %s %r (tried %d candidate name(s))",
        family, key, len(urls),
    )
    if proved_absent:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"")
    return None
