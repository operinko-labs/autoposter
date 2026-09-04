"""The strings badges display, and the media attributes badges are derived from.

Formats are Kometa's, transcribed from its overlay definitions -- including
the parts that look like bugs. The critic rating really is emitted with no
rounding, and the audience percentage really does truncate rather than round.
Reproducing those exactly is the whole point; "fixing" them here would show up
as every affected badge differing from the tool being replaced.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from autoposter.assets import asset_path
from autoposter.lang import base_language_code

@lru_cache(maxsize=1)
def _languages() -> dict[str, dict]:
    """The language/flag table, loaded on first use.

    Deliberately lazy: reading it at import time turned a missing asset into
    an ImportError that took the whole application down at startup.
    """
    with open(asset_path("badges", "languages.json"), encoding="utf-8") as handle:
        return json.load(handle)

RESOLUTIONS = {"4k": "4k", "1080": "1080p", "720": "720p", "576": "576p", "480": "480p"}


def runtime_text(duration_ms: int | None) -> str | None:
    """``"Runtime: 1h 20m"`` -- neither number is zero-padded."""
    if not duration_ms:
        return None
    minutes = duration_ms / 60000
    return "Runtime: %dh %dm" % (int(minutes // 60), int(minutes % 60))


def episode_text(season: int | None, episode: int | None) -> str | None:
    """``"S01E01"`` -- both numbers zero-padded to at least two digits."""
    if season is None or episode is None:
        return None
    return "S%02dE%02d" % (season, episode)


def commonsense_text(content_rating: str | None) -> str | None:
    """``"17"`` -> ``"17+"``; ``"NR"`` stays ``"NR"``.

    Anything non-numeric that is not ``NR`` is rejected. That is not
    defensiveness for its own sake: ten shows in this library carry the literal
    string ``"tmdb"`` in ``contentRating``, mass-written by an earlier
    misconfiguration, and a badge reading ``"tmdb+"`` is worse than no badge.
    """
    if not content_rating or not content_rating.strip():
        return None
    value = content_rating.strip()
    if value.upper() == "NR":
        return "NR"
    if not value.isdigit():
        return None
    return value + "+"


def critic_text(rating: float | None) -> str | None:
    """The critic rating, unformatted.

    Kometa applies no rounding to this field, so neither do we -- the usual
    one-decimal appearance comes from Plex storing it that way.
    """
    if not rating:
        return None
    return str(rating)


def audience_text(rating: float | None) -> str | None:
    """The audience rating as a percentage: ``6.3`` -> ``"63%"``.

    Multiply by ten and truncate, which is what Kometa's ``%`` modifier does --
    ``8.65`` becomes ``"86%"``, not ``"87%"``.
    """
    if not rating:
        return None
    return "%d%%" % int(float(rating) * 10)


@dataclass(frozen=True)
class MediaInfo:
    """The media attributes badges are derived from -- ITEM-LEVEL, not
    primary-version.

    Kometa never picks a `<Media>` (roadmap row 106, adjudication A-1): every
    badge-relevant filter in the four shipped default files is an any-of read
    across every version, and the weight tables arbitrate between the overlays
    that matched. So the three fields a badge is derived from are the item's
    tuples, not one version's scalars -- `video_resolutions`, not
    `video_resolution`; `audio_track_titles` (which is what
    `audio_codec.yml:98-100` actually reads), not `audio_codec`; `file_paths`
    (Kometa's `item.locations`), not `file_path`. `hdr_flags` is the union
    across every version.

    The field COUNT and ORDER are unchanged, deliberately: every construction
    site in the tree passes the first eight or nine fields POSITIONALLY.
    """

    video_resolutions: tuple[str, ...]
    audio_track_titles: tuple[str, ...]
    # Unused by any badge and by anything else in the tree; kept because it
    # predates this phase, read off the FIRST version because that is where it
    # was read from before and nothing consumes it either way.
    audio_channels: int | None
    duration_ms: int | None
    audio_languages: tuple[str, ...]
    hdr_flags: frozenset[str]
    season_number: int | None
    episode_number: int | None
    # Every file of every version -- Kometa's `filepath`, which is
    # `self.cached_item_attr(item, "locations")` (`modules/plex.py:2804-2805`
    # at the pinned digest). Three badge inputs read it: the `video_format`
    # label, the HDR10+ resolution variant, and (as one half of an OR with the
    # track titles) the audio codec. Defaulted because every construction site
    # predating the video_format badge passes the other eight positionally.
    file_paths: tuple[str, ...] = ()
    # The FILTER dialect's stream languages, ISO 639-1, in listing order --
    # Kometa's own `audio_language`/`subtitle_language` value, transcribed
    # from `modules/plex.py:2915-2922` at the pinned digest:
    #
    #     for media in item.media:
    #         for part in media.parts:
    #             test_number.extend([a.language for a in part.audioStreams()])
    #
    # EVERY stream, across EVERY `<Media>`, NOT deduplicated -- and
    # `.count_*` is `len()` of exactly that list (`plex.py:2931-2932`). So a
    # film with an English track plus an English commentary track is "Dual"
    # upstream, and three English subtitle tracks satisfy
    # `subtitle_language.count_gte: 2`. That is upstream's arithmetic and it
    # is transcribed, not corrected (roadmap row 100, sub-phase C2b,
    # adjudication A-3). A stream with no language code counts too, and is
    # never skipped: Kometa's `a.language for a in part.audioStreams()`
    # appends whatever `a.language` is, so an unlabelled stream is one MORE
    # (empty-string) entry, mirrored here rather than dropped.
    #
    # DISTINCT FROM `audio_languages` ABOVE, deliberately and permanently:
    # that field is DISTINCT codes off the PRIMARY version and it feeds
    # `language_slots`' flag badge, which would draw a duplicate flag if it
    # counted streams. Two fields, two contracts, neither bent to serve the
    # other. `_stream_languages` in `plex/client.py` is the collections
    # engine's sibling read and it ends in `_uniq` -- that dedupe is
    # pre-existing and out of this sub-phase's scope; the divergence it
    # leaves between the two item views is recorded in
    # `overlays/selection.py` rather than papered over.
    #
    # Defaulted for the same reason `file_path` is: every construction site
    # predating this phase passes the fields before them POSITIONALLY,
    # several of them in parity-pin files this phase may not edit.
    #
    # `()` rather than `None` for "no such streams": `filters._is_missing`
    # reads an empty sequence as missing for a `tag`, and the `.count_*`
    # operators reduce both shapes to zero before that rule runs, so one
    # shape keeps `overlays/selection.py::OverlayItemView.get`'s branches
    # uniform.
    audio_stream_languages: tuple[str, ...] = ()
    subtitle_stream_languages: tuple[str, ...] = ()


def media_info_from_plex(item) -> MediaInfo:
    """Read badge inputs off a Plex item, across EVERY `<Media>` version.

    Calls ``reload()`` when media is absent, because a search result carries no
    stream detail. That is ``reload()``, not ``refresh()`` -- the latter asks
    Plex to re-scan from its metadata agents, which can overwrite the artwork
    this project just uploaded.

    ONE walk of ``item.media``, where sub-phase C2b had two. C2b's comment
    said widening its primary-version loop "would change ``audio_languages``,
    ``hdr_flags`` and ``file_path`` for every multi-version item" -- and that
    is exactly what roadmap row 106 is, done deliberately with Kometa's weight
    tables as the arbiter. Two of those three are widened here.
    ``audio_languages`` is the exception and stays PRIMARY-VERSION-ONLY
    (adjudication A-4): it feeds ``language_slots``' flag badge, which draws
    DISTINCT codes and would draw duplicate flags off a whole-item read, and
    C2b already built the undeduplicated whole-item pair beside it for the
    filter dialect. Hence the ``index == 0`` gate below, which is the only
    place in this function that knows a primary version exists.

    Both reads walk objects ``item.reload()`` already fetched, so the cost
    claim C2b rests on -- zero extra Plex requests, zero extra bytes -- is
    unchanged and this phase inherits it verbatim.
    """
    if not getattr(item, "media", None):
        item.reload()
    versions = getattr(item, "media", None) or []

    resolutions: list[str] = []
    titles: list[str] = []
    paths: list[str] = []
    flags: set[str] = set()
    languages: list[str] = []
    audio_streams: list[str] = []
    subtitle_streams: list[str] = []

    for index, version in enumerate(versions):
        resolution = getattr(version, "videoResolution", None)
        if resolution:
            resolutions.append(resolution)
        for part in getattr(version, "parts", []) or []:
            path = getattr(part, "file", None)
            if path:
                paths.append(path)
            for stream in getattr(part, "streams", []) or []:
                kind = stream.streamType
                if kind == 1:
                    if getattr(stream, "DOVIPresent", None):
                        flags.add("dv")
                    trc = getattr(stream, "colorTrc", None)
                    if trc == "smpte2084":
                        flags.add("hdr")
                    elif trc == "arib-std-b67":
                        flags.add("hlg")
                    continue
                # `base_language_code`, not a two-character slice of
                # `languageCode`. Plex's code is ISO 639-2 and
                # `languages.json` is keyed by ISO 639-1, and truncation is
                # not a conversion between them: `swe` sliced to `sw`, which
                # is a REAL key -- Swahili -- so a Swedish track drew a
                # Tanzanian flag, while `ger`, `cze`, `dut`, `gre`, `ice` and
                # `chi` sliced to nothing the table holds and lost their flag
                # entirely. A code the converter cannot reduce comes back
                # unchanged and simply is not a table key, which is the same
                # no-flag outcome Plex's `und` had before, reached honestly.
                code = base_language_code(getattr(stream, "languageCode", None) or "").lower()
                if kind == 2:
                    # Kometa's `audio_track_title` (`modules/plex.py:2791-2794`
                    # at the pinned digest): every audio stream's
                    # `extendedDisplayTitle`, across every version, in listing
                    # order, falsy titles dropped -- upstream's own
                    # `if a.extendedDisplayTitle` guard.
                    title = getattr(stream, "extendedDisplayTitle", None)
                    if title:
                        titles.append(title)
                    audio_streams.append(code)
                    if index == 0 and code and code not in languages:
                        languages.append(code)
                elif kind == 3:
                    subtitle_streams.append(code)

    # Kometa reads HDR10+ off the path, and off EVERY path: the `plus` alt is
    # a `filepath.regex` (`resolution.yml:222-223`) and `filepath` is
    # `item.locations`.
    if any(_HDR10_PLUS.search(path) for path in paths):
        flags.add("plus")

    return MediaInfo(
        video_resolutions=tuple(resolutions),
        audio_track_titles=tuple(titles),
        # NOT `aspectRatio`, and that is deliberate (roadmap row 100,
        # sub-phase C2b, adjudication A-2). A-2 ruled that `aspect` answers
        # through the same whole-`<Media>`-list walk `resolution` uses, so the
        # read lives in `collections/filter_values.py::_aspect`, shared
        # verbatim by both item views, and `MediaInfo` deliberately carries no
        # aspect field: a second copy of the same value is exactly the drift
        # `overlays/selection.py`'s own R2 note ("one accessor, not two copies
        # that can drift") rules out, and nothing would consume it --
        # `overlays/variables.py`'s grammar has no `<<aspect>>` token in any
        # of its variable classes.
        audio_channels=getattr(versions[0], "audioChannels", None) if versions else None,
        duration_ms=getattr(item, "duration", None),
        audio_languages=tuple(languages),
        hdr_flags=frozenset(flags),
        season_number=getattr(item, "seasonNumber", None),
        episode_number=getattr(item, "episodeNumber", None),
        file_paths=tuple(paths),
        audio_stream_languages=tuple(audio_streams),
        subtitle_stream_languages=tuple(subtitle_streams),
    )


def plex_native_ratings(item) -> dict[str, float | None]:
    """The overlay grammar's `user_rating` plus the four `plex_*` sources --
    the one rating family needing no external call at all (probe section
    2.3.6, transcribed verbatim below, including the `rottentomatoes://`
    URL-suffix discrimination -- ripe/rotten is the critic score, anything
    else is the audience one).

    Reads through `object.__getattribute__`, not a plain `getattr`: `item` is
    very likely the same PARTIAL plexapi object `apply_badges` uploads to,
    and a plain read of an unset attribute (an unrated item's `.userRating`,
    or one with no MDBList-sourced Plex ratings) trips
    `PlexPartialObject.__getattribute__`'s reload branch -- one synchronous
    blocking `requests` GET, inline, on the event loop. Same discipline
    `collections/filter_values.py::_listing_value` and
    `overlays/selection.py::OverlayItemView.get` already use; duplicated
    (three lines) rather than imported, because `badges/` has no other
    dependency on `collections/` and this is the only thing that would
    create one.
    """

    def read(name):
        try:
            return object.__getattribute__(item, name)
        except AttributeError:
            return None

    result: dict[str, float | None] = {
        "user_rating": read("userRating"),
        "plex_imdb_rating": None,
        "plex_tmdb_rating": None,
        "plex_tomatoes_rating": None,
        "plex_tomatoesaudience_rating": None,
    }
    for rating in read("ratings") or ():
        image = getattr(rating, "image", "") or ""
        if image.startswith("imdb://"):
            result["plex_imdb_rating"] = rating.value
        elif image.startswith("themoviedb://"):
            result["plex_tmdb_rating"] = rating.value
        elif image.startswith("rottentomatoes://"):
            if image.endswith("ripe") or image.endswith("rotten"):
                result["plex_tomatoes_rating"] = rating.value
            else:
                result["plex_tomatoesaudience_rating"] = rating.value
    return result


# Kometa reads HDR10+ off the file path, not off Plex's stream metadata --
# `resolution.yml` gates the `plus` and `dvhdrplus` variants on
# `filepath.regex: (?i)\bhdr10(\+|p(lus)?\b)`. It has to: Plex's video stream
# exposes `colorTrc` and the Dolby Vision fields but carries no HDR10+ marker
# at all (plexapi 4.18.2 `VideoStream` has no such attribute), so the file name
# is the only signal available. HDR10+ streams are backwards-compatible HDR10
# and report `smpte2084` too, hence `plus` outranking `hdr` below.
_HDR10_PLUS = re.compile(r"(?i)\bhdr10(\+|p(lus)?\b)")


# Kometa's group arbitration and its four weight tables, transcribed from the
# pinned image `kometateam/kometa`
# sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a.
#
# The finding these encode, because it is not what the roadmap cell said:
# Kometa never picks a `<Media>`. Every badge-relevant filter in the four
# shipped default files is an ANY-OF read across EVERY version of the item --
# `resolution.yml:243-251`'s whole-item `plex_search` plus `has_dolby_vision`
# (`modules/plex.py:2832-2838`) and `filepath.regex` (`:2804-2805`, i.e.
# `item.locations`), `audio_codec.yml:98-100`'s `audio_track_title.regex` OR
# `filepath.regex` (`modules/plex.py:2791-2794`), `video_format.yml:64-65`'s
# `filepath.regex`. The weight table then arbitrates between the OVERLAYS that
# matched, not between the versions. So an item with a 4K SDR version and a
# 1080p Dolby Vision version is awarded `4kdv`: the resolution predicate and
# the DV predicate are two independent item-level reads, not one per-version
# conjunction. That is upstream's arithmetic and it is transcribed, not
# corrected (roadmap row 106, adjudication A-1).


def _award(candidates) -> str | None:
    """The highest-weighted candidate, ties going to the first one offered.

    `modules/overlays.py:582-586` at the pinned digest:

        for v in gv:
            if final is None or overlay_groups[gk][v] > overlay_groups[gk][final]:
                final = v

    STRICTLY greater, so equal weights keep the incumbent -- which is config
    order, which is the order the tables below are written in.
    `badges/compose.py:428` already arbitrates definition groups by the same
    `current is None or definition.weight > current.weight`; this is that one
    rule, once more, over the value tables rather than over definitions.
    """
    final: tuple[str, int] | None = None
    for name, weight in candidates:
        if final is None or weight > final[1]:
            final = (name, weight)
    return None if final is None else final[0]


# What each Kometa `alt` requires, as a flag set. `resolution.yml:210-227`
# keys each alt off a different signal -- `dv` off the `has_dolby_vision`
# filter, `hdr` off the search's `hdr: true`, and `hlg`/`plus`/`dvhdr`/
# `dvhdrplus` off `filepath.regex` -- but `media_info_from_plex` already
# reduces every one of those signals to a member of `hdr_flags`, so the
# requirement is stated here as the flag set the alt needs. There is no
# `dvhlg`: upstream ships no such overlay row and this repository vendors no
# such PNG, so Dolby Vision over an HLG base layer resolves to `dv` on weight
# (120 against 111 at 1080p) rather than by a special case.
_ALT_FLAGS: dict[str, frozenset[str]] = {
    "dvhdrplus": frozenset({"dv", "plus"}),
    "dvhdr": frozenset({"dv", "hdr"}),
    "plus": frozenset({"plus"}),
    "dv": frozenset({"dv"}),
    "hlg": frozenset({"hlg"}),
    "hdr": frozenset({"hdr"}),
    "": frozenset(),
}


# `resolution.yml:255-371`, verbatim, in the file's own order -- which is
# non-increasing in weight and is therefore also the tie-break order the
# strict `>` above implies. 39 rows: seven each for 4k, 1080p and 720p, six
# each for 576p and 480p (upstream ships NO `hlg` row for either, so an HLG
# flag at those tiers draws the plain badge and the vendored `576phlg.png`
# and `480phlg.png` are unreachable), and six resolution-less rows at the end
# (`:354-371`) that fire for every item and are what an item whose
# `videoResolution` is none of the five tiers gets. Five of those six carry
# `all: true`; `HDR-Dovetail` (`:369-370`) does not, which is upstream's own
# inconsistency, transcribed rather than corrected -- it makes no difference
# here because this derivation issues no `plex_search`.
RESOLUTION_WEIGHTS: tuple[tuple[str, str, int], ...] = (
    ("4k", "dvhdrplus", 160), ("4k", "dvhdr", 158), ("4k", "plus", 155),
    ("4k", "dv", 150), ("4k", "hlg", 141), ("4k", "hdr", 140), ("4k", "", 130),
    ("1080p", "dvhdrplus", 130), ("1080p", "dvhdr", 128), ("1080p", "plus", 125),
    ("1080p", "dv", 120), ("1080p", "hlg", 111), ("1080p", "hdr", 110),
    ("1080p", "", 100),
    ("720p", "dvhdrplus", 99), ("720p", "dvhdr", 98), ("720p", "plus", 95),
    ("720p", "dv", 90), ("720p", "hlg", 81), ("720p", "hdr", 80),
    ("720p", "", 70),
    ("576p", "dvhdrplus", 69), ("576p", "dvhdr", 68), ("576p", "plus", 65),
    ("576p", "dv", 60), ("576p", "hdr", 50), ("576p", "", 40),
    ("480p", "dvhdrplus", 39), ("480p", "dvhdr", 38), ("480p", "plus", 35),
    ("480p", "dv", 30), ("480p", "hdr", 20), ("480p", "", 10),
    ("", "dvhdrplus", 9), ("", "dvhdr", 8), ("", "plus", 7), ("", "dv", 5),
    ("", "hlg", 2), ("", "hdr", 1),
)


# `audio_codec.yml:61-95`'s sixteen regexes against `:104-166`'s sixteen
# weights, in weight order. These match AUDIO TRACK TITLES and FILE PATHS
# (`:98-100`), never Plex's `media.audioCodec` -- upstream does not read that
# attribute for this overlay at all, which is why the ten-entry
# `AUDIO_CODECS` map this replaces could never reach `truehd_atmos`, `dtsx`,
# `plus_atmos`, `dolby_atmos`, `hra` or `dtses`. Two upstream quirks are
# transcribed rather than corrected, and both are pinned by tests: `aac` also
# matches "stereo" and "2.0", and `digital`'s `ac3` alternative is unanchored,
# so it matches inside "EAC3" too -- harmless, because `plus` (70) outranks
# `digital` (40) and matches the same string.
AUDIO_CODEC_WEIGHTS: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    ("truehd_atmos", 160,
     re.compile(r"(?i)^(?=.*\btrue[ ._-]?hd(\b|\d))(?=.*\batmos(\b|\d))")),
    ("dtsx", 150, re.compile(r"(?i)\b(dts[-_. ]?x7?)\b(?![-_. ]?(26[456]))")),
    ("plus_atmos", 140,
     re.compile(r"(?i)^(?=.*\b((dd[p+])|(dolby[ ._-]digital[ ._-]plus)|(e[ ._-]?ac3)\b))"
                r"(?=.*\batmos(\b|\d))")),
    ("dolby_atmos", 130, re.compile(r"(?i)\batmos(\b|\d)")),
    ("truehd", 120, re.compile(r"(?i)\btrue[ ._-]?hd(\b|\d)")),
    ("ma", 110, re.compile(r"(?i)\bdts[ ._-]?(hd[ ._-])?(ma|xll|hd)(\b|\d)(?![ ._-]hra)")),
    ("flac", 100, re.compile(r"(?i)\bflac(\b|\d)")),
    ("pcm", 90, re.compile(r"(?i)\bl?pcm(\b|\d)")),
    ("hra", 80, re.compile(r"(?i)\bdts[ ._-]?(hd[ ._-])?(hr|hra|hi|ra)(\b|\d)")),
    ("plus", 70,
     re.compile(r"(?i)\b(dd[p+])|(dolby[ ._-]digital[ ._-]plus)|(e[ ._-]?ac3)\b")),
    ("dtses", 60, re.compile(r"(?i)\bdts[ ._-]?es(\b|\d)")),
    ("dts", 50, re.compile(r"(?i)\bdts(\b|\d)")),
    ("digital", 40, re.compile(r"(?i)\b(dd)|(ac3)|(dolby)(\b|\d)")),
    ("aac", 30, re.compile(r"(?i)\b(aac|stereo|2\.0)\b")),
    ("mp3", 20, re.compile(r"(?i)\bmp3(\b|\d)")),
    ("opus", 10, re.compile(r"(?i)\b(?<!-)OPUS(\b|\d)")),
)


# Kometa's `video_format.yml`, transcribed verbatim: each overlay filters on
# `filepath.regex` (`:64-65`, `plex_all: true`, i.e. `item.locations` -- every
# path of every version) and displays its own key as the badge text
# (`text_<<key>>` defaults to `<<overlay_name>>`, no case transform). All
# eight share `group: quality`, so only the highest-weighted match is drawn;
# the weights are `:69-99` and they are carried in the tuple now rather than
# implied by its order, so this table has the same shape as the two above and
# the same `_award` arbitrates it. `bluray` still precedes `dvd`, which no
# longer matters for correctness (the weights decide) but keeps the file
# readable against upstream's own ordering.
_VIDEO_FORMATS: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    ("REMUX", 60, re.compile(r"(?i)\bremux\b")),
    ("BLU-RAY", 50, re.compile(r"(?i)\b(blu[ ._-]?ray|bd|br|hd[ ._-]?dvd)\b")),
    ("WEB", 40, re.compile(r"(?i)web[ ._-]?(dl|rip)")),
    ("HDTV", 30, re.compile(r"(?i)\bhd[ ._-]?tv\b")),
    ("DVD", 20, re.compile(r"(?i)\bdvd\b")),
    ("SDTV", 10, re.compile(r"(?i)\bsd[ ._-]?tv\b")),
    ("TELESYNC", 9, re.compile(r"(?i)\b(TS|HDTS|TELESYNC)\b")),
    ("CAM", 8, re.compile(r"(?i)\b(HQ|HD)?CAM\b")),
)


def resolution_image(info: MediaInfo) -> str | None:
    """Filename stem under ``images/resolution/``, e.g. ``1080pdvhdr``.

    Kometa never picks a `<Media>`: `resolution.yml:243-251` is a whole-item
    `plex_search` for the resolution, plus two whole-item filters
    (`has_dolby_vision`, `filepath.regex`). All three are ANY-OF reads across
    every version, so each of the 39 overlays is evaluated against the ITEM
    and the weight table arbitrates between the ones that matched. The tiers
    below are therefore every version's, the flags are the union across every
    version, and the award is the highest weight under the strict `>`.

    The consequence is real and deliberate: an item with a 4K SDR version and
    a 1080p Dolby Vision version is awarded `4kdv`, because upstream's
    resolution predicate and its DV predicate are two independent item-level
    reads and not one per-version conjunction.

    An unknown resolution no longer suppresses the badge outright -- the six
    resolution-less rows (`resolution.yml:354-371`) still fire on the alt
    alone -- but an unknown resolution with NO flags matches nothing, which is
    the same no-badge outcome as before, reached from the table.
    """
    tiers = {
        RESOLUTIONS[value.lower()]
        for value in info.video_resolutions
        if value and value.lower() in RESOLUTIONS
    }
    return _award(
        (key + alt, weight)
        for key, alt, weight in RESOLUTION_WEIGHTS
        if (not key or key in tiers) and _ALT_FLAGS[alt] <= info.hdr_flags
    )


def audio_codec_image(info: MediaInfo) -> str | None:
    """Filename stem under ``images/audio_codec/compact/``.

    `audio_codec.yml:98-100` filters on `audio_track_title.regex` OR
    `filepath.regex` -- it never reads Plex's `media.audioCodec` for this
    overlay at all -- and both reads span every version
    (`modules/plex.py:2791-2794` and `:2804-2805`). So the haystack is every
    audio track title followed by every file path, and the sixteen regexes are
    weighed against it.

    This is a shipped-output change beyond the multi-version fix and it is
    disclosed on roadmap row 106: six stems the retired ten-entry
    `AUDIO_CODECS` map could not reach are reachable now (`truehd_atmos`,
    `dtsx`, `plus_atmos`, `dolby_atmos`, `hra`, `dtses`), and an item with no
    codec token in any title or path draws no badge where the identifier map
    always drew one.
    """
    haystack = info.audio_track_titles + info.file_paths
    return _award(
        (stem, weight)
        for stem, weight, pattern in AUDIO_CODEC_WEIGHTS
        if any(pattern.search(value) for value in haystack)
    )


def video_format_text(info: MediaInfo) -> str | None:
    """The video_format badge string, e.g. ``"WEB"``, or ``None`` to suppress.

    ``None`` for paths that match nothing is Kometa's
    ``ignore_blank_results: true`` -- no overlay in the group runs, so no
    badge is drawn. An item with no file at all (a show or a season, which
    have no media of their own) likewise gets no badge.

    Every path, not the first one: `video_format.yml:64-65` is a
    `filepath.regex` under `plex_all: true`, and `filepath` is
    `item.locations` (`modules/plex.py:2804-2805`). A web-dl beside a remux is
    REMUX, because the weights arbitrate across the whole item.
    """
    return _award(
        (label, weight)
        for label, weight, pattern in _VIDEO_FORMATS
        if any(pattern.search(path) for path in info.file_paths)
    )


# Plex tags Norwegian audio `nob` (Bokmål) or `nno` (Nynorsk), which
# `base_language_code` reduces to `nb` and `nn` -- and `languages.json` is
# keyed by ISO 639-1 and carries `no` but neither of those, so both codes
# reached the table and drew nothing. Upstream points both at the same
# country: `defaults/overlays/languages.yml:403` is
# `{key: nb, text: NB, weight: 120, country: no}` and `:407` is
# `{key: nn, text: NN, weight: 110, country: no}`, read from the pinned image
# `kometateam/kometa`
# sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a
# (`languages.yml` sha256 dcd91ab2...c8f1).
#
# The alias is applied HERE, at lookup, and deliberately NOT by adding `nb`
# and `nn` rows to `assets/badges/languages.json`: that file is listed in
# `assets/badges/MANIFEST.sha256`, so editing it moves `asset_manifest_sha`,
# which is a `badge_fingerprint` input, which re-badges and re-uploads the
# whole library for two codes.
#
# Both resolve to the existing `no` ROW, so they draw `("no", "NO")` at
# weight 450 -- upstream's own `norwegian` row (`languages.yml:267`,
# `{key: no, text: NO, weight: 450}`) -- rather than `NB`/120 and `NN`/110.
# The flag is the same either way and one row stays the single source. An
# item carrying both a `nob` and a `nor` track therefore yields two identical
# slots; that is accepted, not overlooked.
_FLAG_ALIASES: dict[str, str] = {"nb": "no", "nn": "no"}


def language_slots(info: MediaInfo, limit: int = 3) -> list[tuple[str, str]]:
    """``(flag_stem, label)`` pairs, highest Kometa weight first.

    The flag is a country code, not a language code -- English shows the US
    flag -- so it comes from the mapping table rather than the language
    itself. ``_FLAG_ALIASES`` is consulted BEFORE the membership test, so a
    code the table does not carry but upstream points at a country the table
    does carry still draws its flag.
    """
    table = _languages()
    aliased = [_FLAG_ALIASES.get(code, code) for code in info.audio_languages]
    known = [(code, table[code]) for code in aliased if code in table]
    known.sort(key=lambda pair: pair[1]["weight"], reverse=True)
    return [(entry["country"], entry["text"]) for _, entry in known[:limit]]
