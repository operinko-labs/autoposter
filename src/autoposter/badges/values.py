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

import langcodes

from autoposter.assets import asset_path

@lru_cache(maxsize=1)
def _languages() -> dict[str, dict]:
    """The language/flag table, loaded on first use.

    Deliberately lazy: reading it at import time turned a missing asset into
    an ImportError that took the whole application down at startup.
    """
    with open(asset_path("badges", "languages.json"), encoding="utf-8") as handle:
        return json.load(handle)

RESOLUTIONS = {"4k": "4k", "1080": "1080p", "720": "720p", "576": "576p", "480": "480p"}

# Plex's codec identifiers on the left, Kometa's image filenames on the right.
AUDIO_CODECS = {
    "eac3": "plus", "ac3": "digital", "truehd": "truehd", "dca": "dts",
    "dca-ma": "ma", "aac": "aac", "flac": "flac", "mp3": "mp3",
    "opus": "opus", "pcm": "pcm",
}


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
    """The media attributes badges are derived from."""

    video_resolution: str | None
    audio_codec: str | None
    audio_channels: int | None
    duration_ms: int | None
    audio_languages: tuple[str, ...]
    hdr_flags: frozenset[str]
    season_number: int | None
    episode_number: int | None
    # The media file's path. Kometa derives both the video_format badge and the
    # HDR10+ resolution variant from it by regex, so it is a badge input in its
    # own right, not just diagnostics. Defaulted because every construction
    # site predating the video_format badge passes the other eight positionally.
    file_path: str | None = None
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
    """Read badge inputs off a Plex item.

    Calls ``reload()`` when media is absent, because a search result carries no
    stream detail. That is ``reload()``, not ``refresh()`` -- the latter asks
    Plex to re-scan from its metadata agents, which can overwrite the artwork
    this project just uploaded.
    """
    if not getattr(item, "media", None):
        item.reload()
    media = (getattr(item, "media", None) or [None])[0]
    if media is None:
        return MediaInfo(None, None, None, getattr(item, "duration", None), (),
                         frozenset(), getattr(item, "seasonNumber", None),
                         getattr(item, "episodeNumber", None))

    languages: list[str] = []
    flags: set[str] = set()
    file_path: str | None = None
    for part in getattr(media, "parts", []) or []:
        if file_path is None:
            file_path = getattr(part, "file", None)
        for stream in getattr(part, "streams", []) or []:
            if stream.streamType == 1:
                if getattr(stream, "DOVIPresent", None):
                    flags.add("dv")
                trc = getattr(stream, "colorTrc", None)
                if trc == "smpte2084":
                    flags.add("hdr")
                elif trc == "arib-std-b67":
                    flags.add("hlg")
            elif stream.streamType == 2:
                code = (getattr(stream, "languageCode", None) or "")[:2].lower()
                if code and code not in languages:
                    languages.append(code)

    if file_path and _HDR10_PLUS.search(file_path):
        flags.add("plus")

    # Kometa's own filter read, transcribed (`modules/plex.py:2915-2922`):
    # every audio/subtitle stream's language, across EVERY `<Media>`, in
    # listing order, with no dedupe and no drop for an unlabelled stream --
    # Kometa's own `a.language for a in part.audioStreams()` never skips
    # one, so a stream with no language code still contributes one
    # (empty-string) entry, and `.count_*`'s `len()` sees it. A SEPARATE
    # walk from the loop above rather than an `elif` inside it, for two
    # reasons that are both
    # behavioural: that loop is scoped to `media` (the PRIMARY version) and
    # widening it would change `audio_languages`, `hdr_flags` and
    # `file_path` for every multi-version item; and this walk must NOT
    # deduplicate, which is the opposite of what the loop above does. Both
    # walk objects `item.reload()` already fetched, so the cost claim (zero
    # extra Plex requests, zero extra bytes) is unchanged.
    audio_streams: list[str] = []
    subtitle_streams: list[str] = []
    for version in getattr(item, "media", []) or []:
        for part in getattr(version, "parts", []) or []:
            for stream in getattr(part, "streams", []) or []:
                # `langcodes` (the same library `collections/filters.py`'s
                # `base_language_code` reduces a language value with) rather
                # than a bare `[:2]` slice of `languageCode`: the stream's
                # code is ISO 639-2 (three letters) and a handful of common
                # languages do not truncate to their ISO 639-1 form --
                # Swedish's `swe` is `sv`, not `sw` (Swahili). The width
                # policy is "ISO 639-1 when known, else the raw tag": a code
                # `langcodes` cannot map -- Plex's own `und` ("undetermined")
                # among them -- passes through UNCHANGED rather than through
                # a `raw[:2]` slice, which would have turned `und` into the
                # real-looking but wrong code `un`. Absent code falls back to
                # the empty string, matching "no language" rather than a
                # guess.
                raw = getattr(stream, "languageCode", None) or ""
                if raw:
                    try:
                        code = (langcodes.Language.get(raw).language or raw).lower()
                    except ValueError:
                        code = raw.lower()
                else:
                    code = ""
                if stream.streamType == 2:
                    audio_streams.append(code)
                elif stream.streamType == 3:
                    subtitle_streams.append(code)

    return MediaInfo(
        video_resolution=getattr(media, "videoResolution", None),
        # NOT `aspectRatio`, and that is deliberate (roadmap row 100,
        # sub-phase C2b, adjudication A-2). The value rides on this same
        # already-in-hand `<Media>` object -- zero extra Plex requests,
        # which is the cost claim C2b rests on -- but `media` here is
        # `item.media[0]`, ONE version, and A-2 rules that `aspect` answers
        # through the SAME whole-`<Media>`-list walk `resolution` uses. So
        # the read lives in `collections/filter_values.py::_aspect`, shared
        # verbatim by both item views, and `MediaInfo` deliberately carries
        # no aspect field: a second, `media[0]`-shaped copy of the same
        # value is exactly the drift `overlays/selection.py`'s own R2 note
        # ("one accessor, not two copies that can drift") rules out, and
        # nothing would consume it -- `overlays/variables.py`'s grammar has
        # no `<<aspect>>` token in any of its variable classes.
        audio_codec=getattr(media, "audioCodec", None),
        audio_channels=getattr(media, "audioChannels", None),
        duration_ms=getattr(item, "duration", None),
        audio_languages=tuple(languages),
        hdr_flags=frozenset(flags),
        season_number=getattr(item, "seasonNumber", None),
        episode_number=getattr(item, "episodeNumber", None),
        file_path=file_path,
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


# Only these suffix combinations are vendored. Dolby Vision over an HLG base
# layer is a real (if uncommon) stream, but there is no `dvhlg` asset for any
# resolution, so naively concatenating every flag names a file that does not
# exist. Most specific match wins; DV outranks HLG when both are present, and
# `plus` outranks `hdr` because an HDR10+ stream always also reports plain
# HDR10 -- checking `hdr` first would make every `*plus.png` unreachable.
_HDR_SUFFIXES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"dv", "plus"}), "dvhdrplus"),
    (frozenset({"dv", "hdr"}), "dvhdr"),
    (frozenset({"plus"}), "plus"),
    (frozenset({"dv"}), "dv"),
    (frozenset({"hdr"}), "hdr"),
    (frozenset({"hlg"}), "hlg"),
)


def _hdr_suffix(flags: frozenset[str]) -> str:
    """The most specific vendored suffix these flags support, or ``""``."""
    for required, suffix in _HDR_SUFFIXES:
        if required <= flags:
            return suffix
    return ""


def resolution_image(info: MediaInfo) -> str | None:
    """Filename stem under ``images/resolution/``, e.g. ``1080pdvhdr``.

    An unknown resolution suppresses the badge rather than guessing at a
    filename that may not exist, and the HDR suffix is chosen from the
    combinations actually vendored -- see ``_HDR_SUFFIXES``.
    """
    base = RESOLUTIONS.get((info.video_resolution or "").lower())
    if base is None:
        return None
    return base + _hdr_suffix(info.hdr_flags)


def audio_codec_image(info: MediaInfo) -> str | None:
    """Filename stem under ``images/audio_codec/standard/``."""
    return AUDIO_CODECS.get((info.audio_codec or "").lower())


# Kometa's `video_format.yml`, transcribed verbatim: each overlay filters on
# `filepath.regex` and displays its own key as the badge text (`text_<<key>>`
# defaults to `<<overlay_name>>`, no case transform). All eight share
# `group: quality`, so only the highest-weighted match is drawn -- this tuple is
# in descending weight order (REMUX 60, BLU-RAY 50, WEB 40, HDTV 30, DVD 20,
# SDTV 10, TELESYNC 9, CAM 8), which is also why `bluray` must be tested before
# `dvd`: an "HD-DVD" path matches both and Kometa awards it to BLU-RAY.
_VIDEO_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("REMUX", re.compile(r"(?i)\bremux\b")),
    ("BLU-RAY", re.compile(r"(?i)\b(blu[ ._-]?ray|bd|br|hd[ ._-]?dvd)\b")),
    ("WEB", re.compile(r"(?i)web[ ._-]?(dl|rip)")),
    ("HDTV", re.compile(r"(?i)\bhd[ ._-]?tv\b")),
    ("DVD", re.compile(r"(?i)\bdvd\b")),
    ("SDTV", re.compile(r"(?i)\bsd[ ._-]?tv\b")),
    ("TELESYNC", re.compile(r"(?i)\b(TS|HDTS|TELESYNC)\b")),
    ("CAM", re.compile(r"(?i)\b(HQ|HD)?CAM\b")),
)


def video_format_text(info: MediaInfo) -> str | None:
    """The video_format badge string, e.g. ``"WEB"``, or ``None`` to suppress.

    ``None`` for a path that matches nothing is Kometa's
    ``ignore_blank_results: true`` -- no overlay in the group runs, so no badge
    is drawn. An item with no file path at all (a show or a season, which have
    no media of their own) likewise gets no badge.
    """
    if not info.file_path:
        return None
    for label, pattern in _VIDEO_FORMATS:
        if pattern.search(info.file_path):
            return label
    return None


def language_slots(info: MediaInfo, limit: int = 3) -> list[tuple[str, str]]:
    """``(flag_stem, label)`` pairs, highest Kometa weight first.

    The flag is a country code, not a language code -- English shows the US
    flag -- so it comes from the mapping table rather than the language itself.
    """
    table = _languages()
    known = [(code, table[code]) for code in info.audio_languages if code in table]
    known.sort(key=lambda pair: pair[1]["weight"], reverse=True)
    return [(entry["country"], entry["text"]) for _, entry in known[:limit]]
