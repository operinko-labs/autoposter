"""The strings badges display, and the media attributes badges are derived from.

Formats are Kometa's, transcribed from its overlay definitions -- including
the parts that look like bugs. The critic rating really is emitted with no
rounding, and the audience percentage really does truncate rather than round.
Reproducing those exactly is the whole point; "fixing" them here would show up
as every affected badge differing from the tool being replaced.
"""

import json
from dataclasses import dataclass
from pathlib import Path

_ASSETS = Path(__file__).resolve().parents[3] / "assets" / "badges"

with open(_ASSETS / "languages.json", encoding="utf-8") as handle:
    LANGUAGES: dict[str, dict] = json.load(handle)

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
    for part in getattr(media, "parts", []) or []:
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

    return MediaInfo(
        video_resolution=getattr(media, "videoResolution", None),
        audio_codec=getattr(media, "audioCodec", None),
        audio_channels=getattr(media, "audioChannels", None),
        duration_ms=getattr(item, "duration", None),
        audio_languages=tuple(languages),
        hdr_flags=frozenset(flags),
        season_number=getattr(item, "seasonNumber", None),
        episode_number=getattr(item, "episodeNumber", None),
    )


# Only these suffix combinations are vendored. Dolby Vision over an HLG base
# layer is a real (if uncommon) stream, but there is no `dvhlg` asset for any
# resolution, so naively concatenating every flag names a file that does not
# exist. Most specific match wins; DV outranks HLG when both are present.
_HDR_SUFFIXES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"dv", "hdr", "plus"}), "dvhdrplus"),
    (frozenset({"dv", "hdr"}), "dvhdr"),
    (frozenset({"dv"}), "dv"),
    (frozenset({"hdr"}), "hdr"),
    (frozenset({"hlg"}), "hlg"),
    (frozenset({"plus"}), "plus"),
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


def language_slots(info: MediaInfo, limit: int = 3) -> list[tuple[str, str]]:
    """``(flag_stem, label)`` pairs, highest Kometa weight first.

    The flag is a country code, not a language code -- English shows the US
    flag -- so it comes from the mapping table rather than the language itself.
    """
    known = [(code, LANGUAGES[code]) for code in info.audio_languages if code in LANGUAGES]
    known.sort(key=lambda pair: pair[1]["weight"], reverse=True)
    return [(entry["country"], entry["text"]) for _, entry in known[:limit]]
