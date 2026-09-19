"""Kometa's `<<variable>>` text grammar.

Banked by the overlay grammar probe, sections 2.1 to 2.4:
the four variable families, the per-family modifier table, and the
substitution order. Transcribed rather than reinvented, including the parts
that look like bugs -- `%` truncates because `int()` truncates, and
`badges/values.py` already records that production output really does.

This module is pure: it formats values it is HANDED. It never reads a Plex
item and never calls a rating API. The 26 external rating-source names are
part of the grammar and appear in the vocabulary; fetching them is row 100's
data half, which the probe's own section 7.2 leaves unprobed.
"""
import re
from collections.abc import Mapping

from num2words import num2words

# Transcribed from `modules/overlay.py:19-49` at the pinned tag
# (`Kometa-Team/Kometa` @ `498b3af6e921fc5e857dd081061000c87482489d`), in the
# source's own order -- 26 of upstream's 29 entries. The three Trakt names
# (`mdb_trakt_rating`, `trakt_rating`, `trakt_user_rating`) are deliberately
# ABSENT: Trakt's API use policy forbids this class of integration, so no
# Trakt source can ever resolve here. An operator's token naming one is
# better an unknown variable than a permanently empty known one.
RATING_SOURCES = (
    "anidb_average_rating",
    "anidb_rating",
    "anidb_score_rating",
    "imdb_rating",
    "floppy_rating",
    "mal_rating",
    "mdb_average_rating",
    "mdb_imdb_rating",
    "mdb_letterboxd_rating",
    "mdb_metacritic_rating",
    "mdb_metacriticuser_rating",
    "mdb_myanimelist_rating",
    "mdb_rating",
    "mdb_tmdb_rating",
    "mdb_tomatoes_rating",
    "mdb_tomatoesaudience_rating",
    "omdb_rating",
    "omdb_imdb_rating",
    "omdb_metascore_rating",
    "omdb_tomatoes_rating",
    "plex_imdb_rating",
    "plex_tmdb_rating",
    "plex_tomatoes_rating",
    "plex_tomatoesaudience_rating",
    "serializd_rating",
    "tmdb_rating",
)

# Probe section 2.2. The three Plex-native ratings are float vars but not
# rating sources: they read off the item object rather than through a fetch.
PLEX_NATIVE_RATINGS = ("audience_rating", "critic_rating", "user_rating")
FLOAT_VARS = frozenset(RATING_SOURCES) | frozenset(PLEX_NATIVE_RATINGS)

STRING_VARS = frozenset({
    "title", "content_rating", "original_title", "edition",
    "show_title", "season_title",
})
INT_VARS = frozenset({
    "runtime", "total_runtime", "season_number", "episode_number",
    "episode_count", "versions",
})
DATE_VAR = "originally_available"

# Probe section 2.3, the modifier table -- one row per variable class.
_RUNTIME_MODS = ("", "H", "M")
_COUNT_MODS = ("", "W", "WU", "WL", "0", "00")
_FLOAT_MODS = ("", "%", "#", "/")
_STRING_MODS = ("", "U", "L", "P")

VAR_MODS: dict[str, tuple[str, ...]] = {
    "bitrate": ("", "H", "L"),
    DATE_VAR: ("", "["),
    **{v: _RUNTIME_MODS for v in ("runtime", "total_runtime")},
    **{v: _COUNT_MODS for v in
       ("season_number", "episode_number", "episode_count", "versions")},
    **{v: _FLOAT_MODS for v in sorted(FLOAT_VARS)},
    **{v: _STRING_MODS for v in sorted(STRING_VARS)},
}

# Probe section 2.3: the deduplicated 1- and 2-character sets, used by the
# matcher to decide how many trailing characters are the modifier. Two-char
# modifiers are tried first, which is why W and WU never collide.
SINGLE_MODS = frozenset(m for mods in VAR_MODS.values() for m in mods if len(m) == 1)
DOUBLE_MODS = frozenset(m for mods in VAR_MODS.values() for m in mods if len(m) == 2)

_TEXT_FORM = re.compile(r"^text\((.*)\)$", re.DOTALL)
_DATE_BRACKET = re.compile(r"<<" + DATE_VAR + r"\[(.+?)\]>>")

# Probe section 2.1 states the token shape as `<<name>>MOD` (modifier after
# the closing bracket, matching e.g. `<<season_number>>WU`). But the probe's
# own real-world example (section 5.2, `runtimes.yml`'s
# `format: "<<runtimeH>>h <<runtimeM>>m"`) places the modifier BEFORE the
# closing bracket instead. Both forms are real, so the token regex captures
# whatever sits between the variable name and `>>` (either the date-bracket
# form or up to two plain characters) and `tokens_in` below tries that
# in-bracket capture before falling back to checking the text just after
# `>>` for the other placement.
_TOKEN = re.compile(r"<<([a-z_]+)(\[.+?\]|[^<>]{0,2})?>>")


class UnresolvedVariable(Exception):
    """A token named a variable the caller supplied no value for.

    Probe section 2.4: Kometa adds the overlay to an `unresolved` set and
    skips it for that item with a warning rather than aborting the run.
    Callers are expected to catch this per item, not per pass.
    """


def literal_of(name: str) -> str | None:
    """The literal inside `text(...)`, or None when the name is not a text
    overlay. Probe section 2.1."""
    match = _TEXT_FORM.match(name)
    return match.group(1) if match else None


def tokens_in(literal: str) -> list[tuple[str, str]]:
    """Every `(variable, modifier)` pair the literal carries, in the order the
    tokens appear. Probe sections 2.1 and 2.3.

    Two-character modifiers are checked before one-character ones, and a
    modifier is only recognised when it is legal for that variable -- the
    table is per variable class, not global, so `<<runtime>>U` is the bare
    runtime token followed by a literal `U`. A modifier may appear either
    inside the brackets (`<<runtimeH>>`) or after them (`<<season_number>>WU`)
    -- see `_TOKEN`'s comment.
    """
    found: list[tuple[str, str]] = []
    for match in _TOKEN.finditer(literal):
        var = match.group(1)
        if var not in VAR_MODS:
            continue
        legal = VAR_MODS[var]
        inner = match.group(2) or ""
        if inner.startswith("["):
            if "[" in legal:
                found.append((var, inner))
            continue
        mod = ""
        if inner and inner in DOUBLE_MODS and inner in legal:
            mod = inner
        elif len(inner) == 1 and inner in SINGLE_MODS and inner in legal:
            mod = inner
        if not mod:
            tail = literal[match.end():]
            if len(tail) >= 2 and tail[:2] in DOUBLE_MODS and tail[:2] in legal:
                mod = tail[:2]
            elif tail[:1] in SINGLE_MODS and tail[:1] in legal:
                mod = tail[:1]
        found.append((var, mod))
    return found


def format_value(var: str, mod: str, value: object) -> str:
    """One resolved value, formatted per its variable class's modifier.

    Probe section 2.3's table, one branch per row.
    """
    if var == DATE_VAR:
        return value.strftime(mod[1:-1]) if mod.startswith("[") else value.isoformat()
    if var in ("runtime", "total_runtime"):
        minutes = int(value)
        if mod == "H":
            return str(minutes // 60)
        if mod == "M":
            return str(minutes % 60)
        return str(minutes)
    if var in FLOAT_VARS:
        number = float(value)
        if mod == "%":
            # Truncation, not rounding -- probe section 2.3 gives x10-as-int,
            # and badges/values.py records that production truncates.
            return str(int(number * 10))
        if mod == "#":
            text = str(number)
            return text[:-2] if text.endswith(".0") else text
        if mod == "/":
            return f"{number / 2:.1f}"
        # Bare: the float's own repr, trailing .0 and all -- `#` is the one
        # that strips it. `f"{number:g}"` would strip it here too, making the
        # two modifiers indistinguishable (probe section 2.3's float row: the
        # bare form is "/10 as given", `#` is "/10 minus trailing .0").
        return str(number)
    if var in INT_VARS or var == "bitrate":
        number = int(value)
        if mod == "W":
            return num2words(number)
        if mod == "WU":
            return num2words(number).upper()
        if mod == "WL":
            return num2words(number).lower()
        if mod == "0":
            return "%02d" % number
        if mod == "00":
            return "%03d" % number
        return str(number)
    text = str(value)
    if mod == "U":
        return text.upper()
    if mod == "L":
        return text.lower()
    if mod == "P":
        return text.title()
    return text


def render_text(literal: str, values: Mapping[str, object]) -> str:
    """Substitute every token in `literal` from `values`.

    Probe section 2.4 step 4: the date-bracket form goes through `re.sub`
    (its format string may contain regex metacharacters), every other token
    through a plain `str.replace`. The modifier may have been written inside
    the brackets (`<<runtimeH>>`) or after them (`<<season_number>>WU`) --
    whichever form `tokens_in` actually matched is the one replaced.
    """
    full = literal
    for var, mod in tokens_in(literal):
        if var not in values or values[var] is None:
            raise UnresolvedVariable(var)
        rendered = format_value(var, mod, values[var])
        if mod.startswith("["):
            full = _DATE_BRACKET.sub(rendered, full, count=1)
        else:
            inside = f"<<{var}{mod}>>"
            outside = f"<<{var}>>{mod}"
            full = full.replace(inside if inside in full else outside, rendered)
    return full
