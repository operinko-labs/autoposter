"""The tree's one language-code normaliser.

Moved here from ``collections/filters.py`` unchanged, because two callers on
opposite sides of the tree need it and neither may import the other.
``badges/`` carries no dependency on ``collections/`` -- ``badges/values.py``'s
own ``plex_native_ratings`` docstring states that law and duplicates three
lines rather than break it -- and ``collections/filters.py`` is a module
``config/schema.py``'s ``families`` validator deliberately keeps off the
Action Center's request path, naming its transitive ``langcodes`` weight as
the reason. A leaf module importing ``langcodes`` and nothing else settles
both: it is cheap enough for the badge path and neutral enough for the model
layer.

This is the ONLY module under ``src/`` that imports ``langcodes``.
"""

import langcodes


def base_language_code(value: str) -> str:
    """A language value in any common form, reduced to its base ISO 639-1 code.

    Transcribed from Kometa's ``base_language_code`` (modules/plex.py:141-151),
    including its fallback: a value that cannot be parsed comes back unchanged,
    so an unrecognised code targets itself rather than nothing. ``langcodes``
    is the same library Kometa uses, taken as a dependency rather than
    transcribed. It lived in
    ``builders/plex_search.py`` until the location-names phase, then in
    ``collections/filters.py`` because ``_matches_one``'s language fold (row
    204) needed it and the model layer must not import a builder, and it lives
    HERE now because ``badges/values.py`` needs it too and must not import the
    model layer. Its ``LanguageTagError`` is a ``ValueError`` subclass, which
    is what makes the fallback below catch it.

    The fallback is what the badge caller relies on for Plex's own
    ``und``/``mis``/``qaa`` stream tags: ``langcodes`` answers ``und`` with
    ``None`` and the other two with themselves, so all three come back as the
    raw tag, none of them is a key in ``assets/badges/languages.json``, and no
    flag is drawn. A ``[:2]`` slice would have turned ``und`` into ``un`` --
    a real-looking code that is simply not this stream's.
    """
    if not value:
        return value
    try:
        return langcodes.Language.get(str(value)).language or value
    except ValueError:
        return value
