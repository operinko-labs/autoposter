"""The strings badges display.

Formats are Kometa's, transcribed from its overlay definitions -- including
the parts that look like bugs. The critic rating really is emitted with no
rounding, and the audience percentage really does truncate rather than round.
Reproducing those exactly is the whole point; "fixing" them here would show up
as every affected badge differing from the tool being replaced.
"""


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
