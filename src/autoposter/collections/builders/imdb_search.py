"""``imdb_search``: IMDb's advanced title search, as a builder.

The transport is ``collections/imdb_graphql.py`` -- the same endpoint, the same
mandatory ``x-imdb-client-name`` header, the same level-by-level drift
validation and the same raise-on-anything-unexpected posture the list builders
get, because the failure that matters is identical either way: a wrong
non-empty result overwrites the collection, and an empty one is read one
layer down as "make no changes" (``lists.reconcile_list_collection``) -- so
drift that empties the response silently freezes the collection instead of
erroring, indistinguishable from a healthy no-op until someone notices it
never updates.

What this module owns is the *vocabulary*. IMDb's ``advancedTitleSearch``
validates the **shape** of a constraint and not its **values**: the walk behind
this module found that ``anyTitleTypeIds: ["zzz"]`` and
``allGenreIds: ["ZzzNotAGenre"]`` both answer HTTP 200 with ``total: 0`` and no
error at all. A mis-spelled genre is therefore indistinguishable, on the wire,
from an honest "nothing matched" -- so every value an operator can write is
checked here against a pinned vocabulary and a value outside it is a config-load
error rather than a collection that quietly empties itself. That is the whole
reason this builder has a bigger params model than the list ones.

**Introspection is refused on this endpoint.** The constraint input objects,
their field names, the range shapes, the date format and the sort enums were all
walked live on 2026-08-25 through the GraphQL validator's own error messages;
the full probe log is in ``.superpowers/sdd/archive/p8c-task-3-report.md`` and the fixtures
under ``tests/fixtures/collections/imdb_search_*.json`` are recordings of what
the endpoint answered. Nothing here is recalled.

**Twelve of Kometa's twenty-eight families.** Title type, genre, user rating,
vote count and release-date window were row 81's minimal tier; runtime,
certificate, origin country, language, keyword, title credits, award and list
membership are rows 258-265, each proved on the wire by a read-only probe on
2026-09-08 before it was written. ``AdvancedTitleSearchConstraints`` carries
sixteen more that no roadmap row owns -- title text, interests, data
availability, the eight text-matching families, character, episodic, company,
ranked lists and adult content -- catalogued in
``docs/research/kometa-imdb-search.md`` §5, which is also where the Kometa
source citation for every field below lives.

**The operator keys are Kometa's, with its dots as underscores.** ``rating.gte``
is ``rating_gte`` and ``release.after`` is ``released_after``, and the eight new
families follow the same rule, so an operator porting a Kometa ``imdb_search``
definition maps them one for one:

    runtime.gte/.lte  -> runtime_gte / runtime_lte
    content_rating    -> content_rating   (a bare rating means the US one)
    country[.any/.not/.origin]   -> country / country_any / country_not /
                                    country_origin
    language[.any/.not/.primary] -> language / language_any / language_not /
                                    language_primary
    keyword[.any/.not]           -> keyword / keyword_any / keyword_not
    cast[.any/.not]              -> cast / cast_any / cast_not
    event[.winning]              -> event / event_winning
    list[.any/.not]              -> list / list_any / list_not

One difference worth knowing when porting: Kometa's ceremony alias for the
Academy Awards is ``oscar``; here it is ``oscars``, because the vocabulary is
``builders/imdb_award.py``'s own ``EVENTS`` registry rather than a second copy.
A raw ``ev…`` id is accepted either way.

**Only one field per new family has been on the wire.** The 2026-09-08 probe
sent ``runtimeRangeMinutes``, ``anyRegionCertificateRatings``, ``anyCountries``,
``anyLanguages``, ``anyKeywords``, ``anyCredits``, ``allEventNominations`` and
``inAnyList``, each answering HTTP 200 with a total below the control's 78,732.
Their siblings -- every ``all*``/``exclude*`` form, ``anyPrimaryCountries``,
``anyPrimaryLanguages``, ``inAllLists``, ``notInAnyList`` and
``winnerFilter`` -- are the same GraphQL input objects and ship on the
transcription's authority, not the wire's.

No summary and no poster: an advanced search is the operator's own question, so
its title and summary belong to the definition. That is the same call
``imdb_list`` and ``tmdb_discover`` make.
"""
import datetime
import re
from collections.abc import Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)
from autoposter.collections.builders.imdb_award import EVENTS
from autoposter.collections.imdb_graphql import fetch_search

__all__ = [
    "GENRES",
    "SORTS",
    "TITLE_TYPE_IDS",
    "ImdbSearchBuilder",
    "ImdbSearchParams",
    "search_constraints",
]

# IMDb's genre ids, exactly as IMDb spells them. Every one of these returned a
# non-zero ``total`` from the live endpoint on 2026-08-25 and an id IMDb does
# not know returns zero -- so a non-zero total is the proof that each of these
# is real, and the reason this list is a recording rather than a transcription
# from memory. Case-sensitive on the wire; ``_must_be_known_genres`` below folds
# case for the operator and sends IMDb's spelling.
GENRES: tuple[str, ...] = (
    "Action", "Adult", "Adventure", "Animation", "Biography", "Comedy", "Crime",
    "Documentary", "Drama", "Family", "Fantasy", "Film-Noir", "Game-Show",
    "History", "Horror", "Music", "Musical", "Mystery", "News", "Reality-TV",
    "Romance", "Sci-Fi", "Short", "Sport", "Talk-Show", "Thriller", "War",
    "Western",
)
_GENRE_BY_FOLDED = {genre.casefold(): genre for genre in GENRES}

# Plex library type -> the IMDb title type ids that mean "the things this
# library holds". A Show library gets series and mini-series and not ``tvMovie``
# or ``tvSpecial``: those are one-offs that Plex files as movies, so including
# them would put ids in a Show collection that can never resolve. All four ids
# were confirmed live; ``tvEpisode`` exists too and is deliberately absent --
# 9.5 million of IMDb's 31 million titles are episodes.
TITLE_TYPE_IDS: dict[str, tuple[str, ...]] = {
    "Movie": ("movie",),
    "Show": ("tvSeries", "tvMiniSeries"),
}

# What an operator's ``type:`` means in library terms, for the cross-check.
TYPE_LIBRARIES: dict[str, str] = {"movie": "Movie", "tv": "Show"}

# Operator's name -> (AdvancedTitleSearchSortBy, SortOrder).
#
# **The direction is not always IMDb's.** ``POPULARITY`` sorts IMDb's popularity
# *rank*, where 1 is the most popular title, so "most popular first" is
# ``ASC`` -- verified live, and the trap here is that ``DESC`` does not error, it
# returns the least popular titles that match. Every other key sorts the value
# itself, where descending means "most" as it reads. The operator-facing name
# always describes the quantity ("popularity.desc" = the most popular first);
# this table is the only place the two conventions meet.
#
# Five sort keys, each in both directions. These five were accepted by the live
# endpoint (along with ``RANKING``, which behaved identically to ``POPULARITY``,
# and ``MY_RATING``, which is meaningless to an unauthenticated caller); ``TITLE``
# and ``BOX_OFFICE`` were rejected outright.
SORTS: dict[str, tuple[str, str]] = {
    "popularity.desc": ("POPULARITY", "ASC"),
    "popularity.asc": ("POPULARITY", "DESC"),
    "rating.desc": ("USER_RATING", "DESC"),
    "rating.asc": ("USER_RATING", "ASC"),
    "votes.desc": ("USER_RATING_COUNT", "DESC"),
    "votes.asc": ("USER_RATING_COUNT", "ASC"),
    "release_date.desc": ("RELEASE_DATE", "DESC"),
    "release_date.asc": ("RELEASE_DATE", "ASC"),
    "runtime.desc": ("RUNTIME", "DESC"),
    "runtime.asc": ("RUNTIME", "ASC"),
}

# The three params that scope or order a search rather than filter it, so the
# "at least one constraint" guard must not count them -- the judgement
# ``builders/tmdb_discover.py`` makes about ``sort_by``/``region``/``language``,
# for the same reason. ``limit`` is the newest of the three and the easiest to
# get wrong: "the 200 most popular titles of any kind" is the popularity chart
# with a smaller page, not a search. The *filtering* set is derived as everything
# else in the model rather than hand-listed, so a param added later is a filter
# by default and the guard cannot silently forget it.
_NON_FILTERING = frozenset({"type", "sort", "limit"})

# ``list`` is one of Kometa's operator keys (the document's §2 row 19), so the
# params model below has a field named ``list`` -- and a name bound in a class
# body shadows the builtin for every annotation written after it, validators'
# signatures included ("TypeError: 'FieldInfo' object is not subscriptable", at
# import). These two aliases are what every annotation inside
# ``ImdbSearchParams`` uses instead, so the shadowing is one named constant
# rather than an ordering trap the next field steps on. Method *bodies* are
# unaffected: a class namespace is not an enclosing scope for a function, which
# is why the shipped ``type`` field and ``type(self).model_fields`` already
# coexist two screens apart.
_Strings = list[str]
_Certificates = list[dict[str, str]]

# Kometa's default region for a bare ``content_rating`` value
# (``modules/builder.py:2502-2506``).
_DEFAULT_REGION = "US"

_CONTENT_RATING_SHAPE = (
    "`content_rating` takes either a rating on its own -- `PG-13`, which means "
    "the rating in the US -- or a mapping with a `rating:` and an optional "
    "2-letter `region:`. One of the values written here is neither."
)
_CONTENT_RATING_NEEDS_RATING = (
    "`content_rating` mappings need a `rating:` key naming an IMDb certificate. "
    "One of the values written here has none, or has an empty one."
)
_CONTENT_RATING_REGION = (
    "`content_rating`'s `region:` is a 2-letter country code and one of the "
    "values written here is not two letters. Kometa warns and falls back to the "
    "US region here; this refuses instead, because a certificate filtered in "
    "the wrong region matches nothing, and an empty result one layer down means "
    "'remove every member'."
)

_LIMIT_SHAPE = (
    "`limit` is how many titles to keep and must be a whole number of at least "
    "one. Kometa spells 'no limit at all' as zero; here it is the key left out, "
    "because a limit of zero keeps nothing and an empty result one layer down "
    "means 'remove every member'."
)

_COUNTRY_CODE = re.compile(r"^[A-Za-z]{2}\Z")

# The list-valued families' dispatch table: one row per operator key, in the
# document's §2 order within each family. ``(param, constraint object, GraphQL
# field, element wrapper)`` -- the wrapper is the key each element is wrapped in
# when IMDb's field takes objects rather than scalars, and ``None`` when it
# takes the value as written.
#
# A table rather than seventeen near-identical branches, for the reason
# ``search_constraints``' docstring already gives: an unwritten constraint must
# be ABSENT, never sent wide open, and seventeen hand-written absence guards is
# seventeen chances to write one wrong. The bare key is the ``all*`` field
# wherever the family has one (the document's §2, ``check_constraint``'s empty
# suffix); ``.any`` and ``.not`` are the ``any*``/``exclude*`` siblings.
#
# ``.not`` is real for every family here. Kometa accepts and then silently DROPS
# ``.not`` for its eight text-matching families (§2.1) -- none of ours is among
# them, so every exclude field below is sent.
_LIST_CONSTRAINTS: tuple[tuple[str, str, str, str | None], ...] = (
    ("country", "originCountryConstraint", "allCountries", None),
    ("country_any", "originCountryConstraint", "anyCountries", None),
    ("country_not", "originCountryConstraint", "excludeCountries", None),
    ("country_origin", "originCountryConstraint", "anyPrimaryCountries", None),
    ("language", "languageConstraint", "allLanguages", None),
    ("language_any", "languageConstraint", "anyLanguages", None),
    ("language_not", "languageConstraint", "excludeLanguages", None),
    ("language_primary", "languageConstraint", "anyPrimaryLanguages", None),
    ("keyword", "keywordConstraint", "allKeywords", None),
    ("keyword_any", "keywordConstraint", "anyKeywords", None),
    ("keyword_not", "keywordConstraint", "excludeKeywords", None),
    ("cast", "titleCreditsConstraint", "allCredits", "nameId"),
    ("cast_any", "titleCreditsConstraint", "anyCredits", "nameId"),
    ("cast_not", "titleCreditsConstraint", "excludeCredits", "nameId"),
    # ``list``'s three fields do NOT follow the all*/any*/exclude* spelling the
    # other four families use -- they are ``inAllLists``, ``inAnyList`` (note
    # the singular) and ``notInAnyList`` (the document's §2 row 19).
    ("list", "listConstraint", "inAllLists", None),
    ("list_any", "listConstraint", "inAnyList", None),
    ("list_not", "listConstraint", "notInAnyList", None),
)

_NAME_ID = re.compile(r"^nm\d+\Z")
_EVENT_ID = re.compile(r"^ev\d+\Z")
_LIST_ID = re.compile(r"^ls\d+\Z")

# The ceremony vocabulary, DERIVED from the award builder's own registry rather
# than re-spelled here: ``imdb_award.EVENTS`` is where the sixteen ceremonies
# and their event ids already live, and ``EVENTS["oscars"].event_id`` is the
# ``ev0000003`` that ``collections/awards.py`` has always carried. A seventeenth
# ceremony added there is writable here the same day, with no edit.
#
# Kometa's own aliases are its own: four of the ones the document names
# (``emmy``, ``bafta``, ``cannes``, ``razzie``) already match a key here
# exactly, and its ``oscar`` is this repository's ``oscars``. Kometa's two
# category-carrying aliases (``oscar_picture``, ``oscar_director``) are NOT
# ported -- their ``searchAwardCategoryId`` is transcription-only, and
# ``imdb_award`` already builds both collections deliberately.
_EVENT_IDS: dict[str, str] = {key: event.event_id for key, event in EVENTS.items()}


class ImdbSearchParams(BaseModel):
    """``imdb_search``'s params: twelve constraint families.

    ``extra="forbid"`` is what makes a mis-spelled constraint a load error
    rather than a filter that quietly never applies, and it is load-enforced for
    free through ``CollectionDefinition``'s params check. It is also why the
    keys here are snake_case and not Kometa's dotted spellings: a dotted
    attribute name is not a pydantic field.

    Every field's refusal is a FIXED sentence naming the key and the shape it
    wanted, and never the value the operator wrote (roadmap row 213). The
    reason each vocabulary is checked at all is in the module docstring: IMDb
    validates a constraint's shape and not its values, so a typo is an emptied
    collection rather than an error.
    """

    model_config = ConfigDict(extra="forbid")

    # Optional. ``build`` always derives the title-type ids it sends from
    # ``ctx.library_type`` -- an explicit ``type:`` never supplies them itself,
    # it only narrows which libraries the definition is allowed to run against
    # (see ``build``'s cross-check). Shadows the builtin as an attribute name
    # only, and ``type:`` is what IMDb's own advanced-search form calls this.
    type: str | None = None
    genres: _Strings | None = Field(default=None, min_length=1)
    # IMDb's user rating is 1.0-10.0. A ``rating_gte: 0`` is a filter that
    # filters nothing while still satisfying the one-constraint guard, so the
    # floor is IMDb's floor rather than zero.
    rating_gte: float | None = Field(default=None, ge=1.0, le=10.0)
    rating_lte: float | None = Field(default=None, ge=1.0, le=10.0)
    # A ``votes_gte: 0`` is a filter that filters nothing while still satisfying
    # the one-constraint guard -- the exact hole ``rating_gte``'s ``ge=1.0``
    # floor above closes for the rating side, so the vote floor gets IMDb's
    # minimum count of one rather than zero for the same reason.
    votes_gte: int | None = Field(default=None, ge=1)
    released_after: datetime.date | None = None
    released_before: datetime.date | None = None
    # Row 258 (``runtime.gte``/``runtime.lte``, the document's §2 row 27).
    # Whole minutes. The floor is 1 rather than Kometa's 0 for ``votes_gte``'s
    # reason: a bound of zero filters nothing while still satisfying the
    # one-constraint guard. Not a bare ``ge=1``, because pydantic's own
    # constraint message carries the operator's number and row 213's sentences
    # do not.
    runtime_gte: int | None = None
    runtime_lte: int | None = None
    # Row 259 (``content_rating``, the document's §2 row 23). Each value is
    # either a bare rating -- the US one -- or a ``{region, rating}`` mapping;
    # both normalise to the pair ``anyRegionCertificateRatings`` takes, because
    # the region is carried PER VALUE and is not a constraint-wide setting.
    content_rating: _Certificates | None = Field(default=None, min_length=1)
    # Row 260 (``country``/``.any``/``.not``/``.origin``, the document's §2
    # row 20). 2-letter codes; Kometa checks only the length and pins no
    # vocabulary, so neither does this -- what it can check is the shape.
    country: _Strings | None = Field(default=None, min_length=1)
    country_any: _Strings | None = Field(default=None, min_length=1)
    country_not: _Strings | None = Field(default=None, min_length=1)
    country_origin: _Strings | None = Field(default=None, min_length=1)
    # Row 261 (``language``/``.any``/``.not``/``.primary``, §2 row 21). Kometa
    # validates NOTHING here, not even a length; the case fold and the
    # blank refusal are this repository's, for the tier-3 reason in §4.
    language: _Strings | None = Field(default=None, min_length=1)
    language_any: _Strings | None = Field(default=None, min_length=1)
    language_not: _Strings | None = Field(default=None, min_length=1)
    language_primary: _Strings | None = Field(default=None, min_length=1)
    # Row 262 (``keyword``/``.any``/``.not``, §2 row 22). Lowercase, and spaces
    # become hyphens -- that rewrite is the whole normalisation.
    keyword: _Strings | None = Field(default=None, min_length=1)
    keyword_any: _Strings | None = Field(default=None, min_length=1)
    keyword_not: _Strings | None = Field(default=None, min_length=1)
    # Row 263 (``cast``/``.any``/``.not``, the document's §2 row 17). IMDb
    # person ids: this root has no name lookup, and neither does Kometa.
    cast: _Strings | None = Field(default=None, min_length=1)
    cast_any: _Strings | None = Field(default=None, min_length=1)
    cast_not: _Strings | None = Field(default=None, min_length=1)
    # Row 264 (``event``/``.winning``, §2 row 25). The two keys MERGE into one
    # ``allEventNominations`` list, which is Kometa's own behaviour and the only
    # thing GraphQL will accept -- one constraint object, one field.
    event: _Strings | None = Field(default=None, min_length=1)
    event_winning: _Strings | None = Field(default=None, min_length=1)
    sort: str = "popularity.desc"
    # Kometa's own key, with Kometa's own arithmetic (the transcription's §3):
    # a REQUEST-level cap, not a post-filter. The page size becomes the limit
    # when the limit is under a page, and the walk stops as soon as that many
    # ids are in hand. Without it a broad keyword search -- `based on book`
    # matches tens of thousands of titles -- walks to the ten-page cap on every
    # run and logs a warning it cannot act on. None is "no limit", which is what
    # every definition written before this param existed means.
    limit: int | None = Field(default=None, ge=1)
    # Row 265 (``list``/``.any``/``.not``, §2 row 19). ``list`` shadows the
    # builtin inside this class body, which is why every annotation here reads
    # ``_Strings``; declared last as well, so the shadowing has nothing left to
    # break even if the alias is ever removed.
    list: _Strings | None = Field(default=None, min_length=1)
    list_any: _Strings | None = Field(default=None, min_length=1)
    list_not: _Strings | None = Field(default=None, min_length=1)

    @field_validator("type")
    @classmethod
    def _must_be_movie_or_tv(cls, value: str | None) -> str | None:
        if value is not None and value not in TYPE_LIBRARIES:
            raise ValueError(
                f"{value!r} is not an `imdb_search` type: write `movie` or `tv`. "
                "(IMDb's own title-type ids -- 'tvSeries', 'tvMiniSeries' -- are "
                "what this builder sends; they are not what it accepts.)"
            )
        return value

    @field_validator("genres")
    @classmethod
    def _must_be_known_genres(cls, value: _Strings | None) -> _Strings | None:
        """Case-folded to IMDb's spelling, or refused naming the vocabulary.

        Not a passthrough, deliberately: IMDb matches genre ids case-sensitively
        and answers one it does not know with ``total: 0`` and no error, so
        ``genres: [crime]`` would build an empty collection that looks like a
        working one.
        """
        if value is None:
            return None
        genres = []
        for genre in value:
            known = _GENRE_BY_FOLDED.get(genre.casefold())
            if known is None:
                raise ValueError(
                    f"{genre!r} is not an IMDb genre. IMDb answers an unknown "
                    "genre with an empty result rather than an error, so this is "
                    "refused here instead. The genres are: " + ", ".join(GENRES)
                )
            genres.append(known)
        return genres

    @field_validator("sort")
    @classmethod
    def _must_be_a_known_sort(cls, value: str) -> str:
        if value not in SORTS:
            raise ValueError(
                f"unknown `imdb_search` sort {value!r}: the sorts are "
                + ", ".join(sorted(SORTS))
            )
        return value

    @field_validator("limit", mode="before")
    @classmethod
    def _must_keep_at_least_one_title(cls, value):
        """``mode="before"`` so this sentence wins over the ``ge=1`` constraint's.

        pydantic's own constraint message is not one of row 213's sentences and
        its error carries the number the operator wrote; a before-validator runs
        first, so the fixed sentence is what a zero produces. The ``ge=1`` stays
        as the structural pin for anything this does not see.
        """
        if isinstance(value, int) and value < 1:
            raise ValueError(_LIMIT_SHAPE)
        return value

    @field_validator("runtime_gte", "runtime_lte")
    @classmethod
    def _must_be_a_whole_minute_count(
        cls, value: int | None, info: ValidationInfo
    ) -> int | None:
        if value is not None and value < 1:
            raise ValueError(
                f"`{info.field_name}` is a runtime in whole minutes and must be "
                "at least 1: a bound of zero filters nothing while still "
                "satisfying the one-constraint guard, which is the hole "
                "`votes_gte`'s floor closes for the same reason."
            )
        return value

    @field_validator("content_rating", mode="before")
    @classmethod
    def _must_be_a_rating_or_a_region_and_rating(cls, value):
        """Kometa's own shape, normalised to one: ``{region, rating}``.

        ``mode="before"`` because the operator may write a bare string where the
        field's type says mapping -- that is Kometa's documented input and the
        shape most definitions will carry.

        Kometa warns and falls back to ``US`` when a region is not two
        characters (``modules/builder.py:2511-2515``). This refuses instead:
        IMDb validates a constraint's shape and not its values, so a certificate
        filtered in the wrong region is an HTTP 200 with ``total: 0``, and an
        empty result one layer down means "remove every member".
        """
        if value is None or not hasattr(value, "__iter__"):
            return value
        # A mapping (or a bare string) is iterable too: unguarded, the loop
        # below would walk a mapping's KEYS -- or a string's characters -- as
        # if they were list entries, producing nonsense certificates rather
        # than refusing the wrong shape.
        if isinstance(value, (Mapping, str, bytes)):
            raise ValueError(_CONTENT_RATING_SHAPE)
        certificates = []
        for entry in value:
            if isinstance(entry, str):
                entry = {"rating": entry}
            if not isinstance(entry, Mapping):
                raise ValueError(_CONTENT_RATING_SHAPE)
            # An unknown key inside an entry (a typo'd ``region:``, say) would
            # otherwise be dropped silently and the entry would fall back to
            # the US region -- exactly the wrong-region emptiness
            # ``_CONTENT_RATING_REGION`` exists to refuse.
            if not set(entry) <= {"rating", "region"}:
                raise ValueError(_CONTENT_RATING_SHAPE)
            rating = entry.get("rating")
            if not isinstance(rating, str) or not rating.strip():
                raise ValueError(_CONTENT_RATING_NEEDS_RATING)
            region = entry.get("region", _DEFAULT_REGION)
            if not isinstance(region, str) or not _COUNTRY_CODE.match(region):
                raise ValueError(_CONTENT_RATING_REGION)
            certificates.append({"region": region.upper(), "rating": rating.strip()})
        return certificates

    @field_validator(
        "country", "country_any", "country_not", "country_origin", mode="before"
    )
    @classmethod
    def _must_be_two_letter_country_codes(cls, value, info: ValidationInfo):
        """``mode="before"``, matching ``_must_be_a_rating_or_a_region_and_rating``:
        a bare string is iterable too, and unguarded would walk its characters
        rather than refuse the shape (``country: US`` becoming ``["U", "S"]``)."""
        if value is None or not hasattr(value, "__iter__"):
            return value
        if isinstance(value, (Mapping, str, bytes)):
            raise ValueError(
                f"`{info.field_name}` takes IMDb's 2-letter country codes as "
                "a list, not a single value typed alone. IMDb answers a code "
                "it does not know with an empty result rather than an error, "
                "so this is refused at load instead."
            )
        not_two_letters = (
            f"`{info.field_name}` takes IMDb's 2-letter country codes "
            "(`US`, `GB`, `JP`) and one of the values written here is "
            "not two letters. IMDb answers a code it does not know with "
            "an empty result rather than an error, so this is refused "
            "at load instead."
        )
        codes = []
        for code in value:
            if not isinstance(code, str):
                raise ValueError(not_two_letters)
            stripped = code.strip()
            if not _COUNTRY_CODE.match(stripped):
                raise ValueError(not_two_letters)
            codes.append(stripped.upper())
        return codes

    @field_validator(
        "language", "language_any", "language_not", "language_primary", mode="before"
    )
    @classmethod
    def _must_be_language_codes(cls, value, info: ValidationInfo):
        if value is None or not hasattr(value, "__iter__"):
            return value
        if isinstance(value, (Mapping, str, bytes)):
            raise ValueError(
                f"`{info.field_name}` takes IMDb's language codes as a list, "
                "not a single value typed alone. IMDb pins no vocabulary and "
                "answers a code it does not know with an empty result rather "
                "than an error, so a value that cannot be one is refused at "
                "load."
            )
        blank = (
            f"`{info.field_name}` takes IMDb's language codes (`en`, "
            "`fr`, `ja`) and one of the values written here is blank. "
            "IMDb pins no language vocabulary and answers a code it "
            "does not know with an empty result rather than an error, "
            "so a value that cannot be one is refused at load."
        )
        languages = []
        for language in value:
            if not isinstance(language, str):
                raise ValueError(blank)
            folded = language.strip().casefold()
            if not folded:
                raise ValueError(blank)
            languages.append(folded)
        return languages

    @field_validator("keyword", "keyword_any", "keyword_not", mode="before")
    @classmethod
    def _must_be_hyphenated_keywords(cls, value, info: ValidationInfo):
        if value is None or not hasattr(value, "__iter__"):
            return value
        if isinstance(value, (Mapping, str, bytes)):
            raise ValueError(
                f"`{info.field_name}` takes IMDb keyword phrases as a list, "
                "not a single value typed alone. Spaces are rewritten to "
                "hyphens; a bare string here is not a list of keywords."
            )
        blank = (
            f"`{info.field_name}` takes IMDb keyword phrases "
            "(`time-travel`, `heist`) and one of the values written "
            "here is blank. Spaces are rewritten to hyphens; a blank is "
            "not a keyword IMDb can match."
        )
        keywords = []
        for keyword in value:
            if not isinstance(keyword, str):
                raise ValueError(blank)
            folded = keyword.strip().casefold().replace(" ", "-")
            if not folded:
                raise ValueError(blank)
            keywords.append(folded)
        return keywords

    @field_validator("cast", "cast_any", "cast_not", mode="before")
    @classmethod
    def _must_be_imdb_name_ids(cls, value, info: ValidationInfo):
        """``mode="before"``, matching the country/language/keyword families: a
        bare string is iterable too, and a non-``str`` element (YAML 1.1 makes
        ``NO`` into ``False``) must be refused rather than handed to
        ``re.match``/``.strip()``, which raise a raw exception instead of a
        clean ``ValidationError``."""
        if value is None or not hasattr(value, "__iter__"):
            return value
        if isinstance(value, (Mapping, str, bytes)):
            raise ValueError(
                f"`{info.field_name}` takes IMDb person ids as a list, not a "
                "single value typed alone. This search root has no name "
                "lookup, and Kometa's operators write ids here too, so a "
                "person's name cannot be accepted."
            )
        not_a_name_id = (
            f"`{info.field_name}` takes IMDb person ids -- `nm` followed by "
            "digits, the id in the person's own URL -- and one of the values "
            "written here is not one. This search root has no name lookup, "
            "and Kometa's operators write ids here too, so a person's name "
            "cannot be accepted."
        )
        name_ids = []
        for name_id in value:
            if not isinstance(name_id, str):
                raise ValueError(not_a_name_id)
            stripped = name_id.strip()
            if not _NAME_ID.match(stripped):
                raise ValueError(not_a_name_id)
            name_ids.append(stripped)
        return name_ids

    @field_validator("event", "event_winning", mode="before")
    @classmethod
    def _must_be_a_known_ceremony_or_an_event_id(cls, value, info: ValidationInfo):
        """``mode="before"``, for the same reason as ``_must_be_imdb_name_ids``."""
        if value is None or not hasattr(value, "__iter__"):
            return value
        if isinstance(value, (Mapping, str, bytes)):
            raise ValueError(
                f"`{info.field_name}` takes an IMDb event id or a known "
                "ceremony name as a list, not a single value typed alone."
            )
        unknown_ceremony = (
            f"`{info.field_name}` takes an IMDb event id -- `ev` followed by "
            "digits -- or one of the ceremonies this service already builds "
            "award collections for: " + ", ".join(sorted(_EVENT_IDS))
        )
        events = []
        for event in value:
            if not isinstance(event, str):
                raise ValueError(unknown_ceremony)
            stripped = event.strip()
            if _EVENT_ID.match(stripped):
                events.append(stripped)
                continue
            event_id = _EVENT_IDS.get(stripped.casefold())
            if event_id is None:
                raise ValueError(unknown_ceremony)
            events.append(event_id)
        return events

    @field_validator("list", "list_any", "list_not", mode="before")
    @classmethod
    def _must_be_imdb_list_ids(cls, value, info: ValidationInfo):
        """``mode="before"``, for the same reason as ``_must_be_imdb_name_ids``."""
        if value is None or not hasattr(value, "__iter__"):
            return value
        if isinstance(value, (Mapping, str, bytes)):
            raise ValueError(
                f"`{info.field_name}` takes IMDb list ids as a list, not a "
                "single value typed alone."
            )
        not_a_list_id = (
            f"`{info.field_name}` takes IMDb list ids -- `ls` followed by "
            "digits, the id in the list's own URL -- and one of the values "
            "written here is not one."
        )
        list_ids = []
        for list_id in value:
            if not isinstance(list_id, str):
                raise ValueError(not_a_list_id)
            stripped = list_id.strip()
            if not _LIST_ID.match(stripped):
                raise ValueError(not_a_list_id)
            list_ids.append(stripped)
        return list_ids

    @model_validator(mode="after")
    def _windows_must_not_be_inside_out(self) -> "ImdbSearchParams":
        if (
            self.rating_gte is not None
            and self.rating_lte is not None
            and self.rating_gte > self.rating_lte
        ):
            raise ValueError(
                f"rating_gte ({self.rating_gte}) is above rating_lte "
                f"({self.rating_lte}), so no title can match and the collection "
                "would empty itself"
            )
        if (
            self.released_after is not None
            and self.released_before is not None
            and self.released_after > self.released_before
        ):
            raise ValueError(
                f"released_after ({self.released_after}) is after "
                f"released_before ({self.released_before}), so no title can match "
                "and the collection would empty itself"
            )
        if (
            self.runtime_gte is not None
            and self.runtime_lte is not None
            and self.runtime_gte > self.runtime_lte
        ):
            # Its two neighbours above interpolate their values; this one does
            # not, because C1/row 213 postdates them and binds every sentence
            # this row writes. The two keys are the whole diagnosis anyway.
            raise ValueError(
                "`runtime_gte` is above `runtime_lte`, so no title can match "
                "and the collection would empty itself"
            )
        return self

    @model_validator(mode="after")
    def _needs_at_least_one_constraint(self) -> "ImdbSearchParams":
        """An unconstrained advanced search is a chart in disguise.

        IMDb answers one with every title of the type, in popularity order,
        which this builder would then truncate at its page cap -- "2,500
        arbitrarily popular films", which ``imdb_chart`` already builds
        deliberately and which ``sync_mode`` would write over a collection that
        was meant to say something. An operator who wrote no constraints did not
        mean this, and neither did one who wrote only ``type`` or ``sort``:
        neither filters anything.
        """
        if not any(
            name not in _NON_FILTERING and getattr(self, name) is not None
            for name in type(self).model_fields
        ):
            raise ValueError(
                "imdb_search needs at least one constraint ("
                + ", ".join(
                    name
                    for name in type(self).model_fields
                    if name not in _NON_FILTERING
                )
                + "): an unconstrained IMDb search is the popularity chart, "
                "which `imdb_chart` already builds"
            )
        return self


def search_constraints(
    params: ImdbSearchParams, title_type_ids: tuple[str, ...]
) -> dict:
    """The ``AdvancedTitleSearchConstraints`` value for one build.

    Only the constraints the operator actually wrote are sent. An unwritten one
    is left out rather than sent wide open, because a constraint object IMDb
    does not recognise the *shape* of is an error and one it does recognise but
    that filters nothing is a silent behaviour change in the cursor's filter
    signature.
    """
    constraints: dict = {"titleTypeConstraint": {"anyTitleTypeIds": list(title_type_ids)}}
    if params.genres is not None:
        # ``allGenreIds`` and not ``anyGenreIds``: both exist, and AND is what
        # IMDb's own advanced-search form does with two ticked genres, so
        # ``genres: [Crime, Drama]`` means what an operator reading the IMDb UI
        # would expect it to mean.
        constraints["genreConstraint"] = {"allGenreIds": params.genres}
    ratings: dict = {}
    if params.rating_gte is not None or params.rating_lte is not None:
        window = {}
        if params.rating_gte is not None:
            window["min"] = params.rating_gte
        if params.rating_lte is not None:
            window["max"] = params.rating_lte
        ratings["aggregateRatingRange"] = window
    if params.votes_gte is not None:
        ratings["ratingsCountRange"] = {"min": params.votes_gte}
    if ratings:
        constraints["userRatingsConstraint"] = ratings
    if params.released_after is not None or params.released_before is not None:
        window = {}
        if params.released_after is not None:
            # ISO ``YYYY-MM-DD``: IMDb's ``Date`` scalar refuses anything else,
            # and refuses it as an HTTP 200 with an ``errors`` array.
            window["start"] = params.released_after.isoformat()
        if params.released_before is not None:
            window["end"] = params.released_before.isoformat()
        constraints["releaseDateConstraint"] = {"releaseDateRange": window}
    if params.runtime_gte is not None or params.runtime_lte is not None:
        window = {}
        if params.runtime_gte is not None:
            window["min"] = params.runtime_gte
        if params.runtime_lte is not None:
            window["max"] = params.runtime_lte
        constraints["runtimeConstraint"] = {"runtimeRangeMinutes": window}
    if params.content_rating is not None:
        # Already normalised to ``{region, rating}`` pairs by the validator, so
        # this is a placement and not a transformation.
        constraints["certificateConstraint"] = {
            "anyRegionCertificateRatings": params.content_rating
        }
    for name, obj, field_name, wrap in _LIST_CONSTRAINTS:
        values = getattr(params, name)
        if values is None:
            # Absent, never wide open: see this function's docstring.
            continue
        constraints.setdefault(obj, {})[field_name] = (
            [{wrap: value} for value in values] if wrap else [*values]
        )
    # ``event`` and ``event.winning`` are ONE field of one constraint object, so
    # they merge rather than each writing their own -- Kometa's own behaviour
    # (``imdb.py:667-676``), and the same event may legitimately appear twice
    # with different filters.
    nominations = []
    if params.event is not None:
        nominations += [{"eventId": event_id} for event_id in params.event]
    if params.event_winning is not None:
        nominations += [
            {"eventId": event_id, "winnerFilter": "WINNER_ONLY"}
            for event_id in params.event_winning
        ]
    if nominations:
        constraints["awardConstraint"] = {"allEventNominations": nominations}
    return constraints


class ImdbSearchBuilder:
    """One IMDb advanced search, in the order the sort produced."""

    type_name = "imdb_search"
    params_model = ImdbSearchParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbSearchParams.model_validate(ctx.config)
        # Two checks, and they mean different things. The first is "this library
        # is one an IMDb title search can mean anything for at all"; the second
        # is the cross-check on an explicit ``type:``, which is a build-time
        # question because a definition with no ``libraries:`` key runs against
        # every library in the pass.
        require_library_type("the `imdb_search` builder", ctx.library_type, TITLE_TYPE_IDS)
        if params.type is not None:
            require_library_type(
                f"`imdb_search` with `type: {params.type}`",
                ctx.library_type,
                {TYPE_LIBRARIES[params.type]},
            )
        constraints = search_constraints(params, TITLE_TYPE_IDS[ctx.library_type])
        sort_by, sort_order = SORTS[params.sort]
        ids = await fetch_search(
            ctx.http,
            constraints,
            {"sortBy": sort_by, "sortOrder": sort_order},
            f"the imdb_search of {ctx.library!r}",
            limit=params.limit,
        )
        return BuilderResult(ids=[("imdb", value) for value in ids])
