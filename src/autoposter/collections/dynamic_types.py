"""Which dynamic types this service ships, and what each one asks Plex.

Kometa's dynamic engine is a config-EXPANSION pass: for each distinct value a
library holds it writes one ordinary collection into the same dict a handwritten
``collections:`` block fills (modules/meta.py:805-1465), whose entire body is a
template call. For the twelve tag/scan library types that template is a
``smart_filter`` -- ``{"smart_filter": {"limit": 50, "sort_by":
"critic_rating.desc", "any": {<type>: "<<value>>"}}}`` (meta.py:950), with
``resolution`` the one exception (``{"sort_by": "title.asc", ...}``,
meta.py:947, no limit) -- which Kometa turns into a query string with
``build_filter`` and POSTs as a smart collection (plex.py:1592-1600).

That is the grammar 9b's oracle transcribes and 9c's reconciler writes, which is
why this engine emits 9b-grammar smart collections through
``smart.reconcile_smart_collection`` and not a second write path (10a decision
C1). Every row below therefore carries the same four things: what to enumerate,
what key to write into the query, how to title the result, and how to order it.

**Scope (decision C3).** Only the types ``listFilterChoices`` can enumerate
TODAY, and -- for the two rows whose choices listing was unproven -- only after
the phase's own read-only probe measured them
(``docs/research/plex-dynamic-probe/README.md``: ``network`` 91 values,
``country`` 63). Deliberately absent, each for its own reason:

- ``edition`` -- roadmap row 170 owns the table row it needs.
- ``original_language``/``origin_country`` -- upstream builds these as PLAIN
  collections whose membership comes from a full-library TMDb walk
  (meta.py:36-37, :971-993, builder.py:360-374), which is the metadata-prefetch
  budget family (rows 155/180), not an enumeration.
- show ``decade`` -- Plex's decade filter is movie-only (plex.py:437), so
  upstream falls back to a whole-library scan and REFUSES ``addons`` while doing
  it (meta.py:881-898). Out until that scan has a budget.
- the people types (10c/row 83), ``tmdb_collection``, ``trakt_*``, ``number``,
  ``custom`` and the music types -- none of them is a library enumeration.

**One documented divergence.** Upstream titles a language bucket from TMDb's
``_iso_639_1`` table -- ``en`` becomes "English" -- and falls back to the Plex
choice's own title when TMDb has no entry (meta.py:928-929). This service has no
such table, so a language family is titled from Plex's own ``choice.title``,
which is the fallback branch upstream already ships. The KEYS are identical
either way, so membership is unaffected; only the collection's name differs, and
``title_override`` sets it by hand. On the production library the two agree
anyway for the common codes (``en=English``, ``fi=Finnish``); where they part is
a locale variant such as ``es-419``, which Plex already titles
"Spanish (Latin America)".
"""
from dataclasses import dataclass

__all__ = ["DYNAMIC_TYPES", "DYNAMIC_TYPE_NAMES", "DynamicType"]


@dataclass(frozen=True)
class DynamicType:
    """One row: a dynamic type an operator can write as ``params.type``.

    ``attribute`` is the ``FILTER_ATTRIBUTES`` row used for BOTH halves of the
    job -- ``field_for(libtype)`` gives the ``listFilterChoices`` field (and its
    libtype scope, when the field is dotted), and the row's search half renders
    the query. One column rather than two, because two would be two chances to
    enumerate one attribute and query another.

    ``search_key`` is what goes into the emitted ``any:`` block, and it is
    ``attribute`` for every row but ``studio``: upstream writes ``studio.is``
    (meta.py:950) because a bare ``studio:`` is Kometa's CONTAINS match and a
    bucket wants the exact value.

    ``key_from`` is which column of a ``listFilterChoices`` row is the KEY --
    ``"title"`` for the tag types, whose key and display value are the same
    string (meta.py:936), and ``"key"`` for the rows upstream keys on
    ``choice.key`` (meta.py:933 for ``resolution`` and movie ``decade``,
    :928-929 for the languages). It decides what ``include``/``exclude``/
    ``addons`` are matched against, which is the difference between
    ``exclude: [1080]`` working and doing nothing.

    ``kinds`` is our library-type spelling (``Movie``/``Show``), taken from
    upstream's ``auto`` table (meta.py:15-22) and NOT from the attribute's
    ``search_kinds``: ``country`` searches on both and is a movie-only dynamic
    type upstream.
    """

    name: str
    attribute: str
    search_key: str
    kinds: tuple[str, ...]
    key_from: str
    title_format: str
    sort_by: tuple[str, ...]
    limit: int | None
    note: str


_BOTH = ("Movie", "Show")
_GENERAL_SORT = ("critic_rating.desc",)
_GENERAL_LIMIT = 50
_GENERAL_TITLE = "Top <<key_name>> <<library_type>>s"


DYNAMIC_TYPES: dict[str, DynamicType] = {
    row.name: row
    for row in (
        DynamicType(
            "year", "year", "year", _BOTH, "title",
            "Best <<library_type>>s of <<key_name>>", _GENERAL_SORT, _GENERAL_LIMIT,
            "meta.py:955 for the title, :919-923 and :936 for the enumeration. "
            "The type whose fan-out the roadmap's own risk note names: a "
            "70-year library is 70 collections, which is why the builder's "
            "``max_collections`` floor defaults below that. The phase's probe "
            "measured 87 on the production movie library and 39 on the shows, "
            "and found a WORSE case than this one -- see ``studio``.",
        ),
        DynamicType(
            "decade", "decade", "decade", ("Movie",), "key",
            "Best <<library_type>>s of the <<key_name>>", _GENERAL_SORT, _GENERAL_LIMIT,
            "MOVIE-ONLY: Plex's decade filter is movie-only (plex.py:437) and "
            "upstream's show fallback is a full library scan (meta.py:881-898). "
            "Keys on ``choice.key`` (``1980``) and titles from ``choice.title`` "
            "(``1980s``), meta.py:933 -- so the query says ``decade=1980`` and "
            "the collection is called 'Best movies of the 1980s'; the probe "
            "confirmed that key/title split on the live library. The emitted "
            "form is the BARE ``decade:`` and nothing else: Kometa's "
            "``no_not_mods`` (plex.py:593, :597-599) strips ``.not`` AND all "
            "four ranges from this attribute, so ``decade.gte`` is a search "
            "Kometa refuses to build and no oracle config can cover -- which "
            "makes this sentence, and the table test beside it, the only thing "
            "holding the subtraction for every downstream emitter.",
        ),
        DynamicType(
            "content_rating", "content_rating", "content_rating", _BOTH, "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "The shape the Common Sense family is one hardcoded instance of "
            "(``collections/buckets.py``). Its production block is "
            "``type: content_rating`` with ``include`` == the ``addons`` keys, "
            "which is exactly what ``dynamic_keys.derive_keys`` reproduces. "
            "Key and title are the same string on this server (``13``/``13``), "
            "which is why ``key_from`` is the title like every other tag row "
            "rather than a special case.",
        ),
        DynamicType(
            "studio", "studio", "studio.is", _BOTH, "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "The one row whose query key is not its attribute: upstream writes "
            "``f'{auto_type}.is'`` for studio alone (meta.py:950), because a "
            "bare ``studio:`` is a CONTAINS match and a per-studio bucket wants "
            "the exact value. ``key_from`` is the title for a second, measured "
            "reason: Plex answers this filter's ``choice.key`` DOUBLE "
            "percent-encoded (``20th%2520Century%2520Fox``), so keying on it "
            "would write a query that matches nothing. Also the phase's "
            "fan-out worst case at **824** values on the production movie "
            "library -- an order of magnitude past the ``year`` example the "
            "roadmap's risk note uses.",
        ),
        DynamicType(
            "genre", "genre", "genre", _BOTH, "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "Row 155's listing truncation does NOT reach this type: membership "
            "is decided inside Plex by the stored filter, so the two-tags-per-"
            "item cap that makes a client-side genre FILTER wrong never touches "
            "a genre SEARCH (9b's probe 3, 5/5). ``key_from`` is the title "
            "because the key is an opaque server-side tag id (``482`` for "
            "Action), which the resolver maps back to on the way out.",
        ),
        DynamicType(
            "country", "country", "country", ("Movie",), "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "Movie-only as a dynamic type (meta.py:18-19) even though the "
            "search answers for both library types -- upstream's type table is "
            "the authority on which libraries get the family, and the attribute "
            "row is the authority on how to ask. Enumerability PROBED before it "
            "shipped rather than assumed (T1 review carry): 63 values on the "
            "production movie library, "
            "``docs/research/plex-dynamic-probe/README.md``. Its keys are "
            "opaque tag ids like ``genre``'s, so ``key_from`` is the title.",
        ),
        DynamicType(
            "resolution", "resolution", "resolution", _BOTH, "key",
            "<<key_name>> <<library_type>>s", ("title.asc",), None,
            "The one type with its own template (meta.py:947): ``title.asc`` "
            "and NO limit, because 'every 4k movie' is not a top-50 question. "
            "Enumerated at the EPISODE libtype on a show library "
            "(``episode.resolution``), which is the server doing the traversal "
            "our client-side filter refuses to pay for. Keys on ``choice.key`` "
            "(``4k``) and titles from ``choice.title`` (``4K``) -- one letter "
            "apart on the live library, which is exactly why the column is "
            "written down rather than inferred.",
        ),
        DynamicType(
            "audio_language", "audio_language", "audio_language", _BOTH, "key",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "Keys are Plex's own language keys (meta.py:928-929 keys on "
            "``choice.key``); the emitted term goes through the resolver's "
            "``_language_keys`` expansion, which is Kometa's "
            "``get_language_search_values`` -- and because the emitted block is "
            "``any:``, a base code expanding to three locale variants is an OR, "
            "not the ``all:`` AND that made row 182's probe answer 0 instead of "
            "442. Titled from Plex rather than from TMDb's English name; see the "
            "module docstring's divergence note.",
        ),
        DynamicType(
            "subtitle_language", "subtitle_language", "subtitle_language",
            _BOTH, "key", _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "``audio_language``'s sibling in every respect, including the "
            "``any:``-base reason its terms are OR-ed. The larger of the two by "
            "a wide margin on the production libraries -- 115 values against "
            "audio's 46 -- so it is the fan-out cap's second worst case after "
            "``studio``.",
        ),
        DynamicType(
            "network", "network", "network", ("Show",), "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "SHOW-ONLY (meta.py:19), and the row whose enumerability was "
            "PROBED before it shipped rather than assumed: 9b proved the SEARCH "
            "field answers (91 networks) while the client-side listing strands "
            "it outright, and the choices listing is the same family but was "
            "unproven until phase 10a's probe answered it with the same 91 "
            "(``docs/research/plex-dynamic-probe/README.md``). Upstream "
            "additionally gates this type on the New Plex TV Agent "
            "(meta.py:820-821); we have no agent check, so a library whose "
            "agent cannot answer enumerates nothing, which Tasks 4/5's "
            "empty-enumeration refusal is being built against -- the probe "
            "found a section in that state (the ``DVR`` section answers "
            "``country`` with zero values).",
        ),
    )
}

DYNAMIC_TYPE_NAMES: tuple[str, ...] = tuple(DYNAMIC_TYPES)
