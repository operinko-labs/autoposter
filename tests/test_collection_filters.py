"""The tier-1 filter attribute table, and one case-set per operator per type.

The module under test is ``src/autoposter/collections/filters.py``. Three things
are being pinned here, and they are not the same kind of thing:

- **the table** (``FILTER_ATTRIBUTES``) is a *transcription* of Kometa's
  documented filter semantics, so what it gets checked for is shape and
  self-consistency -- counts as the transcription's own checksum, every row
  typed and sourced from the fixed vocabularies, every non-obvious cell carrying
  a note. Fidelity to Kometa itself is Task 4's oracle, not these tests;
- **the operator semantics** are the roadmap's named risk: phase 9b translates
  this same vocabulary into a Plex search, so every operator's meaning is pinned
  here as data. ``OPERATOR_CASES`` below is one case-set per ``(value type,
  operator)`` pair, and ``test_every_operator_has_a_case_set_including_a_missing_value``
  is what makes that claim structural rather than aspirational: a pair with no
  cases, or a case-set that forgot the missing-value rule, fails there;
- **the refusals** are the load-time surface Task 3 hooks into. A filter that
  cannot mean anything must say which field it is, because an operator with
  twenty definitions needs that to fix one.

The view is a plain ``dict`` throughout. Task 2 supplies the real one over
resolved plexapi items; the model never imports plexapi, and neither does most
of this file -- the single exception is the operator-mapping test, which reads
plexapi's own ``OPERATORS`` table to prove 9b's translation is a mapping.
"""
import datetime as dt
import pathlib
import re

import pytest

from autoposter.collections.filters import (
    BY_NAME,
    DEFAULT_OPERATOR,
    FILTER_ATTRIBUTES,
    ITEM_KINDS,
    OPERATORS_BY_TYPE,
    PLEXAPI_EQUIVALENT,
    SOURCE_TIERS,
    VALUE_TYPES,
    FilterGroup,
    FilterPredicate,
    RelativeWindow,
    batched_attributes,
    evaluate,
    parse_filters,
    predicates,
    resolve_search_values,
)

# The moment every date case is measured against. Pinned rather than
# ``datetime.now()`` so the "in the last N days" boundaries below are
# arithmetic a reader can check, and so the suite does not change meaning
# overnight. Midnight, so that the case table below reads as whole days: the
# comparisons are made at the moment (``filters._as_moment``, Task 4's oracle),
# and a run moment with a time of day would put every window's lower edge
# part-way through a day, which is correct and unreadable.
NOW = dt.datetime(2026, 8, 25, 0, 0)
TODAY = NOW.date()


def _view(attribute: str, value: object) -> dict:
    """The item view for one attribute. ``None`` means the item has no value."""
    return {attribute: value}


# --- the table ---------------------------------------------------------------


def test_the_table_holds_exactly_the_tier_one_rows():
    """The row list is the plan's, in the plan's order (roadmap.md:538-551), and
    the count is the transcription's checksum: a row lost, duplicated or renamed
    in an edit shows up here rather than as a filter an operator writes and
    nothing applies. Pinning the order too means the table stays readable
    against the roadmap it came from rather than drifting into edit order.

    Phase 9b appended four, at the end rather than interleaved, so the fifteen
    above still read against the roadmap line they came from: ``plays`` and
    ``last_played`` are in BOTH of Kometa's vocabularies and were never probed
    for the client-side one, and ``unplayed`` and ``progress`` are search-only.
    Phase 10a appended two more the same way: ``decade`` (search-only) and
    ``country`` (unprobed, in both vocabularies).

    Phase B appended the four PEOPLE rows the same way again -- ``actor``,
    ``director``, ``writer``, ``producer``. Their existence is what makes a
    per-person query writable at all (roadmap row 194's "blocker one"), and
    three of the four are movie-only as a search because Kometa's
    ``movie_only_searches`` says so.

    Search-tails-1 appended seven the same way: ``title`` and ``edition``
    (row 170, dual-vocabulary on unprobed) and the five media booleans
    (row 172, search-only; ``duplicate`` movie-only per
    ``movie_only_searches``).

    Sub-phase C2c appended two more the same way: ``tmdb_status`` and
    ``last_episode_aired``, the first rows on the ``facts`` source tier --
    values this service holds in its own ``item_facts`` row rather than
    reading from Plex.

    Search-tail E-1 appended TWENTY the same way, in the order roadmap row
    173 names them: family E, the show-library searches whose PREDICATE reads
    episode or season data (``episode_title``, ``unplayed_episodes``, ...).
    All twenty are search-only and show-only, and all twenty render at the
    SHOW search level (``type=2``) exactly as Kometa renders them; the
    season/episode SEARCH level is E-2's (the ``builder_level`` selector).
    """
    assert [row.name for row in FILTER_ATTRIBUTES] == [
        "genre",
        "year",
        "resolution",
        "audience_rating",
        "critic_rating",
        "content_rating",
        "audio_language",
        "subtitle_language",
        "label",
        "added",
        "release",
        "duration",
        "studio",
        "network",
        "collection",
        "plays",
        "last_played",
        "unplayed",
        "progress",
        "decade",
        "country",
        "actor",
        "director",
        "writer",
        "producer",
        "user_rating",
        "title",
        "edition",
        "hdr",
        "dovi",
        "trash",
        "duplicate",
        "unmatched",
        "versions",
        "aspect",
        "tmdb_status",
        "last_episode_aired",
        "season_collection",
        "season_label",
        "episode_collection",
        "episode_label",
        "episode_title",
        "episode_actor",
        "episode_added",
        "episode_air_date",
        "episode_last_played",
        "episode_plays",
        "episode_user_rating",
        "episode_critic_rating",
        "episode_audience_rating",
        "episode_year",
        "episode_unplayed",
        "episode_duplicate",
        "episode_progress",
        "episode_unmatched",
        "show_unmatched",
        "unplayed_episodes",
    ]


def test_every_row_is_typed_sourced_and_scoped_from_the_fixed_vocabularies():
    """The four categorical columns are closed sets. A typo in any of them
    would otherwise produce a row that parses, loads, and matches nothing."""
    assert len(BY_NAME) == len(FILTER_ATTRIBUTES)
    for row in FILTER_ATTRIBUTES:
        assert row.type in VALUE_TYPES, row.name
        assert row.source in SOURCE_TIERS, row.name
        assert row.kinds, row.name
        assert set(row.kinds) <= set(ITEM_KINDS), row.name
        assert row.operators == OPERATORS_BY_TYPE[row.type], row.name
        assert row.note.strip(), row.name


def test_the_column_totals_are_the_transcriptions_checksum():
    """Each column's distribution, spelled out. These are the numbers a
    reviewer checks the table against, and the numbers Task 2's probe moved:
    the seven ``probe`` rows were exactly the ones whose data might not be in
    the listing, and the read-only production probe turned each into ``listing``
    (resolution, alone) or ``tier2-deferred`` (the other six). Each moved row
    carries its probe data in its note, and
    ``tests/test_collection_filter_values.py`` fails if the accessors and these
    tiers ever disagree.

    Phase B moved five of those six again, and this is the second-largest
    tier migration the table has seen: the batched
    ``/library/metadata/{k1,k2,...}`` read returns the families the listing
    truncated or stripped, so ``genre``, ``audio_language``,
    ``subtitle_language``, ``label`` and ``collection`` are ``tier2-batched``
    -- filterable, through the engine's enrichment pass. ``network`` is alone
    on ``tier2-deferred`` now, and stays there because no read at any tier can
    answer an attrib Plex 1.43.4 emits nowhere.

    Phase B also moved ``plays`` and ``last_played`` off ``unprobed`` and
    appended ``user_rating`` on ``listing``: the phase-B probe walked both
    section listings and measured all three present-when-set (probe d), which
    is a verdict where there was none. Three rows, three different reasons for
    why present-when-set is enough -- each on its own row."""
    by_type = {kind: [r.name for r in FILTER_ATTRIBUTES if r.type == kind] for kind in VALUE_TYPES}
    by_source = {t: [r.name for r in FILTER_ATTRIBUTES if r.source == t] for t in SOURCE_TIERS}

    # Search-tail E-1 moved every column but ``duration`` at once: +5 tag,
    # +1 str, +2 int, +3 float, +3 date, +6 bool -- twenty rows, all of them
    # ``search-only`` and none of them a filter, so no SOURCE tier below other
    # than ``search-only`` moved with them.
    assert {k: len(v) for k, v in by_type.items()} == {
        "tag": 19,
        "str": 4,
        "int": 6,
        "float": 7,
        "date": 7,
        "duration": 1,
        "bool": 13,
    }
    assert by_source["listing"] == [
        "year",
        "resolution",
        "audience_rating",
        "critic_rating",
        "content_rating",
        "added",
        "release",
        "duration",
        "studio",
        "plays",
        "last_played",
        "user_rating",
        "versions",
        "aspect",
    ]
    assert by_source["tier2-batched"] == [
        "genre",
        "audio_language",
        "subtitle_language",
        "label",
        "collection",
    ]
    assert by_source["tier2-deferred"] == ["network"]
    assert by_source["probe"] == []
    # 9b's two tiers rather than one because the REASONS differ: ``unprobed``
    # means Kometa filters on it and 9a never asked whether the listing carries
    # it; ``search-only`` means Kometa has no filter of that name at all, so
    # there is nothing to ask.
    #
    # 10a appended ``country`` for the reason 9b appended ``plays``: Kometa
    # filters on it and 9a's probe never asked whether the section listing
    # carries ``<Country>``, so there is no verdict to cite.
    #
    # Phase B appended the four people rows on the same tier and for the same
    # reason: 9a never asked whether ``<Role>``/``<Director>``/``<Writer>``/
    # ``<Producer>`` reach the section listing, and the phase-B credits CACHE
    # is not a listing accessor -- so there is still no verdict to cite.
    #
    # ``plays`` and ``last_played`` LEFT this tier in phase B: probe (d) asked
    # the question 9a never did and answered it, which is the only thing
    # ``unprobed`` was ever waiting for.
    #
    # Search-tails-1 appended ``title`` and ``edition`` here for the same
    # reason again: Kometa filters on both (string_filters) and no probe has
    # ever asked about a listing accessor for either.
    assert by_source["unprobed"] == [
        "country", "actor", "director", "writer", "producer", "title", "edition",
    ]
    # ``decade`` joins the search-only tier: row 96's own 29-name list names it
    # first, and Kometa has no ``decade`` FILTER at all.
    # Search-tail E-1's twenty join here, in table order: Kometa has no FILTER
    # of any of these names (the ``episode_*`` family is the largest block of
    # row 96's 29 search-only names), so a ``filters:`` block refuses each by
    # pointing at the plex_search block it belongs to.
    assert by_source["search-only"] == [
        "unplayed", "progress", "decade",
        "hdr", "dovi", "trash", "duplicate", "unmatched",
        "season_collection", "season_label", "episode_collection",
        "episode_label", "episode_title", "episode_actor", "episode_added",
        "episode_air_date", "episode_last_played", "episode_plays",
        "episode_user_rating", "episode_critic_rating",
        "episode_audience_rating", "episode_year", "episode_unplayed",
        "episode_duplicate", "episode_progress", "episode_unmatched",
        "show_unmatched", "unplayed_episodes",
    ]
    # C2c's new tier, and the first that describes no Plex read at all: the
    # value is in this service's own `item_facts` row. REFUSAL-ONLY on the
    # collections side (`config/schema.py`'s filters gate names row 156);
    # the overlay view supplies both through a separate mechanism.
    assert by_source["facts"] == ["tmdb_status", "last_episode_aired"]


def test_batched_attributes_reports_only_tier2_batched_predicates():
    """What the engine decides the enrichment pass from, and the whole point is
    that it is computed from the TABLE ROW: there is no config knob declaring
    "this definition needs tier 2", so a row moved between tiers changes the
    engine's behaviour with no config edit anywhere.

    Tier-1 names are absent because they cost nothing to read, and a filter
    naming none of the batched rows must produce an empty tuple -- that is the
    "make no batched call at all" signal, and answering ``("genre",)`` for a
    ``year``-only filter would buy one Plex round trip per definition for
    nothing."""
    parsed = parse_filters({"genre": "Horror", "year.gte": 1990, "label.not": "Overlay"})

    assert batched_attributes(parsed) == ("genre", "label")
    assert batched_attributes(parse_filters({"year": 1990})) == ()


def test_batched_attributes_are_distinct_and_in_first_appearance_order():
    """Two predicates on one attribute are one fetch, not two, and the order is
    the filter's own so the action string a refusal writes reads the way the
    operator wrote the block."""
    parsed = parse_filters(
        {"label": "Overlay", "genre": "Horror", "label.not": "Hidden",
         "collection.not": "Marvel"}
    )

    assert batched_attributes(parsed) == ("label", "genre", "collection")


def test_batched_attributes_sees_through_a_nested_group():
    """``predicates`` walks the tree depth-first and this helper rides it, so an
    ``any:`` block naming a batched row still triggers the enrichment. A
    traversal that only looked at the top level would leave the engine
    evaluating ``genre`` with no tags loaded at all."""
    parsed = parse_filters({"any": [{"genre": "Horror"}, {"year.gte": 2000}]})

    assert batched_attributes(parsed) == ("genre",)


def test_item_kinds_are_movie_show_or_both():
    movie_only = sorted(r.name for r in FILTER_ATTRIBUTES if r.kinds == ("movie",))
    show_only = sorted(r.name for r in FILTER_ATTRIBUTES if r.kinds == ("show",))

    assert movie_only == [
        "audio_language", "country", "decade", "director", "duplicate",
        "edition", "producer", "progress", "resolution", "subtitle_language",
        "unplayed", "writer",
    ]
    # Twenty of the twenty-three are search-tail E-1's: a search-only row's
    # ``kinds`` follows its ``search_kinds`` (the ``unplayed``/``duplicate``
    # convention); nineteen of the twenty are in Kometa's show_only_searches,
    # and ``episode_actor`` is show-only by this table's own judgement -- a
    # DECLARED DIVERGENCE, see its row note.
    assert show_only == [
        "episode_actor", "episode_added", "episode_air_date",
        "episode_audience_rating", "episode_collection", "episode_critic_rating",
        "episode_duplicate", "episode_label", "episode_last_played",
        "episode_plays", "episode_progress", "episode_title",
        "episode_unmatched", "episode_unplayed", "episode_user_rating",
        "episode_year", "last_episode_aired", "network", "season_collection",
        "season_label", "show_unmatched", "tmdb_status", "unplayed_episodes",
    ]
    assert len([r for r in FILTER_ATTRIBUTES if r.kinds == ("movie", "show")]) == 22


def test_versions_is_filterable_with_the_int_operators():
    """Dialect 1 of A14's both-dialect pin: `versions.gt` in a `filters:`
    block, the way the versions overlay family (T2) will select on it."""
    assert evaluate(parse_filters({"versions.gt": 1}), _view("versions", 2)) is True
    assert evaluate(parse_filters({"versions.gt": 1}), _view("versions", 1)) is False
    assert evaluate(parse_filters({"versions.gt": 1}), _view("versions", None)) is False


def test_versions_is_not_searchable_and_the_refusal_says_where_it_lives():
    """Dialect 2 of A14's both-dialect pin: `versions` is filterable but has
    no Plex search field, so a `plex_search:` use of it is the FIRST REAL
    (non-synthetic) row to reach `_split_key`'s `searching and not
    attribute.searchable` branch -- written and proven with a synthetic row
    since search-tails-1
    (`test_a_search_refuses_a_filter_only_attribute_naming_the_other_block`
    above); reachable for real now."""
    with pytest.raises(ValueError) as error:
        parse_filters({"versions.gt": 1}, searching=True)
    message = str(error.value)
    assert "versions" in message
    assert "no search field" in message
    assert "filters:" in message


def test_aspect_is_filterable_with_the_float_operators():
    """Dialect 1 of A11's both-dialect pin: `aspect.gt`/`aspect.lt` in a
    `filters:` block, the way the aspect overlay family (T2) selects on it.
    The bands are open at BOTH ends upstream (`.gt`/`.lt`, never the
    inclusive forms), which is why the family transcribes 1.32/1.34 around a
    nominal 1.33 rather than 1.325/1.335."""
    band = {"aspect.gt": 1.32, "aspect.lt": 1.34}
    assert evaluate(parse_filters(band), _view("aspect", 1.33)) is True
    assert evaluate(parse_filters(band), _view("aspect", 1.32)) is False
    assert evaluate(parse_filters(band), _view("aspect", 1.34)) is False
    assert evaluate(parse_filters(band), _view("aspect", 1.90)) is False


def test_an_item_with_no_aspect_is_excluded_by_every_operator():
    """The `float` half of the missing-value rule, which is what makes an
    UNANALYSED item draw no aspect badge rather than a wrong one: Plex omits
    `aspectRatio` on a file it has not analysed, and `_is_missing` excludes a
    missing float under every operator, `.not` included."""
    assert evaluate(parse_filters({"aspect.gt": 1.0}), _view("aspect", None)) is False
    assert evaluate(parse_filters({"aspect.not": 1.78}), _view("aspect", None)) is False


def test_aspect_is_not_searchable_and_the_refusal_says_where_it_lives():
    """Dialect 2 of A11's both-dialect pin, the same shape `versions` already
    has: `aspect` is one of the 44 Kometa filter names with no Plex search
    field at all -- `builder.py:474` puts it in `float_attributes`, a
    CLIENT-SIDE comparison, and `plex.searches` has no entry for it -- so a
    `plex_search:` use is refused naming the `filters:` block instead."""
    with pytest.raises(ValueError) as error:
        parse_filters({"aspect.gt": 1.77}, searching=True)
    message = str(error.value)
    assert "aspect" in message
    assert "no search field" in message
    assert "filters:" in message


def test_every_operator_maps_onto_plexapis_own_operator_table():
    """9b translates this vocabulary into a Plex search rather than reinventing
    it, so every operator names the ``plexapi.base.OPERATORS`` key it means --
    or ``None``, explicitly, for the ones plexapi has no equivalent for. This
    is the only test in the file that imports plexapi; the model never does."""
    from plexapi.base import OPERATORS

    pairs = {(t, op) for t, ops in OPERATORS_BY_TYPE.items() for op in ops}

    assert set(PLEXAPI_EQUIVALENT) == pairs
    for pair, key in PLEXAPI_EQUIVALENT.items():
        assert key is None or key in OPERATORS, pair
    # The deliberate gaps: "in the last N days" is a relative window and
    # plexapi's table is all absolute comparisons; `.count_*` (sub-phase
    # C2b) asks how MANY children the item has, which plexapi spells with no
    # operator key at all.
    unmapped = [pair for pair, key in PLEXAPI_EQUIVALENT.items() if key is None]
    assert unmapped == [
        ("tag", "count_gt"), ("tag", "count_gte"),
        ("tag", "count_lt"), ("tag", "count_lte"),
        ("date", "eq"), ("date", "not"),
    ]


def test_every_negative_operators_plexapi_mapping_equals_its_positive_counterparts():
    """The stated convention, pinned structurally rather than left to a
    reviewer rereading 34 rows: a negative operator's ``PLEXAPI_EQUIVALENT``
    entry is the SAME key as the positive operator it negates (``_NEGATES``
    names it, or ``None`` to mean "the type's default"). ``_matches`` runs the
    positive comparison and inverts the boolean -- the negation never touches
    plexapi -- so a 9b translator that also negated the key would double-negate.

    This is what catches the (int, not) / (float, not) / (duration, not) rows,
    which shipped as ``"ne"`` (plexapi's own negation key, and a real key in
    its table) instead of ``"exact"`` (their ``eq`` counterpart's key) --
    a false instance of the convention that a spot check of "is it a valid
    plexapi key" would not have caught.
    """
    from autoposter.collections import filters as filters_module

    checked = 0
    for value_type, operators in OPERATORS_BY_TYPE.items():
        for operator in operators:
            if operator not in filters_module._NEGATES:
                continue
            positive = filters_module._NEGATES[operator] or DEFAULT_OPERATOR[value_type]
            assert PLEXAPI_EQUIVALENT[(value_type, operator)] == PLEXAPI_EQUIVALENT[
                (value_type, positive)
            ], (value_type, operator)
            checked += 1
    assert checked > 0


def test_every_type_has_a_default_operator_that_is_one_of_its_operators():
    """The default is what a bare ``genre: Horror`` means. It has no modifier
    spelling, which is why it is a separate mapping rather than a row in
    ``OPERATORS_BY_TYPE`` that an operator could also write out."""
    assert set(DEFAULT_OPERATOR) == set(OPERATORS_BY_TYPE) == set(VALUE_TYPES)
    for value_type, default in DEFAULT_OPERATOR.items():
        assert default in OPERATORS_BY_TYPE[value_type]


# --- the search half of the table (phase 9b Task 1) ---------------------------


def test_the_search_kinds_column_is_its_own_and_differs_from_kinds():
    """Two kind columns, because they genuinely differ.

    ``resolution`` is movie-only as a CLIENT filter (a show's resolution is a
    property of its episodes, and per-episode traversal is not a tier-1 read)
    and both-kinds as a SEARCH -- Plex answers it at the episode libtype, and
    the server does the traversal for free. ``duration`` goes the other way:
    both-kinds client-side, movie-only as a search, because Kometa's
    ``movie_only_searches`` lists its four range modifiers (plex.py:441-444).

    The ``()`` bucket is 'filterable but not searchable', and C2c doubled it:
    ``tmdb_status`` and ``last_episode_aired`` join ``versions`` and
    ``aspect`` there. Their ``kinds`` is ``("show",)`` while their
    ``search_kinds`` is empty, which is a third way the two columns differ.
    """
    from collections import Counter

    from autoposter.collections.filters import BY_NAME, FILTER_ATTRIBUTES

    # ``("show",)`` was ``network`` alone until search-tail E-1's twenty.
    assert Counter(row.search_kinds for row in FILTER_ATTRIBUTES) == {
        ("movie", "show"): 24, ("movie",): 8, ("show",): 21, (): 4,
    }
    assert BY_NAME["resolution"].kinds == ("movie",)
    assert BY_NAME["resolution"].search_kinds == ("movie", "show")
    assert BY_NAME["duration"].kinds == ("movie", "show")
    assert BY_NAME["duration"].search_kinds == ("movie",)


def test_four_rows_are_unsearchable_and_twentynine_are_filterable():
    """`versions` (C2a, A14) was the table's first filterable-but-not-
    searchable row; `aspect` (C2b, A11) is the second, and for the same
    reason -- Kometa's own `aspect` filter is a client-side `float_attributes`
    comparison (`builder.py:474`) and `plex.searches` spells no search field
    for it. Every other row remains both, unchanged."""
    from autoposter.collections.filters import (
        FILTERABLE_ATTRIBUTES,
        FILTER_ATTRIBUTES,
        SEARCHABLE_ATTRIBUTES,
    )

    assert all(
        row.searchable for row in FILTER_ATTRIBUTES
        if row.name not in ("versions", "aspect", "tmdb_status", "last_episode_aired")
    )
    assert BY_NAME["versions"].searchable is False
    assert BY_NAME["aspect"].searchable is False
    assert BY_NAME["tmdb_status"].searchable is False
    assert BY_NAME["last_episode_aired"].searchable is False
    # 33 -> 53 with search-tail E-1: Kometa's 55 non-music search names minus
    # ``folder_location`` (row 176) and ``audio_codec`` (row 177). The
    # filterable count does not move -- no family-E name is a Kometa filter.
    assert len(SEARCHABLE_ATTRIBUTES) == 53
    assert len(FILTERABLE_ATTRIBUTES) == 29
    assert set(SEARCHABLE_ATTRIBUTES) - set(FILTERABLE_ATTRIBUTES) == {
        "unplayed", "progress", "decade",
        "hdr", "dovi", "trash", "duplicate", "unmatched",
        "season_collection", "season_label", "episode_collection",
        "episode_label", "episode_title", "episode_actor", "episode_added",
        "episode_air_date", "episode_last_played", "episode_plays",
        "episode_user_rating", "episode_critic_rating",
        "episode_audience_rating", "episode_year", "episode_unplayed",
        "episode_duplicate", "episode_progress", "episode_unmatched",
        "show_unmatched", "unplayed_episodes",
    }
    assert set(FILTERABLE_ATTRIBUTES) - set(SEARCHABLE_ATTRIBUTES) == {
        "versions", "aspect", "tmdb_status", "last_episode_aired"
    }


def test_the_show_search_field_rescoping_is_transcribed():
    """``show_translation`` (Kometa modules/plex.py:168-193) re-scopes a search
    field for a show library, and three of them go to the EPISODE libtype
    rather than the show's -- which is the whole reason the column exists."""
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["genre"].show_search_field == "show.genre"
    assert BY_NAME["added"].show_search_field == "show.addedAt"
    assert BY_NAME["resolution"].show_search_field == "episode.resolution"
    assert BY_NAME["audio_language"].show_search_field == "episode.audioLanguage"
    assert BY_NAME["subtitle_language"].show_search_field == "episode.subtitleLanguage"
    # network is already show-scoped by search_translation, so show_translation
    # never sees it -- the two columns are equal, not None.
    assert BY_NAME["network"].search_field == "show.network"
    assert BY_NAME["network"].show_search_field == "show.network"


def test_field_for_picks_the_libtypes_field_and_refuses_a_libtype_it_does_not_serve():
    """``field_for`` raises rather than falling back, and that is the whole
    design: a caller asking for a field on a libtype the row does not serve has
    already skipped the ``search_kinds`` check, and answering with the movie
    field would build a query Plex silently answers with the WRONG SET rather
    than with an error. ``resolution`` is the row that shows why the fallback
    would be wrong even when it "works" -- a show library must be asked at the
    episode libtype."""
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["resolution"].field_for("movie") == "resolution"
    assert BY_NAME["resolution"].field_for("show") == "episode.resolution"
    # No show_search_field means the movie field serves both, not that the row
    # is unanswerable -- ``critic_rating`` has one, so check a row that does
    # not: ``network`` is show-only and its two columns are equal.
    assert BY_NAME["network"].field_for("show") == "show.network"

    with pytest.raises(ValueError, match="not searchable on a show library"):
        BY_NAME["duration"].field_for("show")
    with pytest.raises(ValueError, match="not searchable on a movie library"):
        BY_NAME["network"].field_for("movie")


def test_every_row_searchable_on_show_carries_a_show_search_field():
    """Pins ``field_for``'s show-library safety (line 542-543) against a future
    row: a row with ``"show" in search_kinds`` and ``show_search_field=None``
    would silently return the MOVIE field for a show library, since
    ``field_for`` only rescopes when ``show_search_field`` is set. It holds for
    all thirty-three rows today; nothing but this test pins it, and it is what
    caught phase B's ``actor`` row: ``show_translation`` DOES rename that one
    (``"actor": "show.actor"``, plex.py:168-193), so a ``None`` there would
    have sent a show library the bare ``actor`` field."""
    for row in FILTER_ATTRIBUTES:
        if "show" in row.search_kinds:
            assert row.show_search_field is not None, row.name


def test_the_people_rows_are_searchable_and_libtype_gated():
    """Roadmap row 194's "blocker one", closed: the four people rows exist, so
    a per-person query is WRITABLE at all.

    Three of the four are movie-only as a SEARCH -- ``director``, ``producer``
    and ``writer`` are the first six entries of Kometa's
    ``movie_only_searches`` (plex.py:430-436) -- so a show library asking for
    one refuses BY NAME rather than being sent a query Plex answers with the
    wrong set. ``actor`` answers for both, re-scoped to ``show.actor`` by
    ``show_translation`` (plex.py:168-193): the brief for this task said that
    entry did not exist, and the repo's own verbatim transcription of that
    table (``tests/oracle/9b/kometa_build_filter.py``) says it does.

    All four are ``unprobed``, which is a SOURCE tier and not an accessor: a
    ``filters:`` block naming one refuses saying exactly that, and this task
    ships no client-side read for them -- the counts come from the credits
    CACHE, which is a different question (roadmap row 156 owns it).
    """
    for name in ("actor", "director", "writer", "producer"):
        row = BY_NAME[name]
        assert row.type == "tag", name
        assert row.source == "unprobed", name
        assert row.filterable, name
        assert row.searchable, name

    assert BY_NAME["actor"].search_kinds == ("movie", "show")
    assert BY_NAME["actor"].kinds == ("movie", "show")
    assert BY_NAME["actor"].field_for("movie") == "actor"
    assert BY_NAME["actor"].field_for("show") == "show.actor"

    for name in ("director", "writer", "producer"):
        assert BY_NAME[name].search_kinds == ("movie",), name
        assert BY_NAME[name].kinds == ("movie",), name
        assert BY_NAME[name].field_for("movie") == name
        with pytest.raises(ValueError, match="not searchable on a show library"):
            BY_NAME[name].field_for("show")


def test_a_people_filter_block_refuses_by_naming_its_tier():
    """The other half of ``unprobed``: the rows are in Kometa's FILTER
    vocabulary too (``builder.filters_by_type``), so ``filters: {actor: ...}``
    PARSES -- and then has no accessor at any tier, which is the refusal
    ``country`` already established. What must never happen is the third thing:
    parsing, finding nothing, and reporting a confident empty membership."""
    from autoposter.collections.filter_values import (
        AttributeNotInListing,
        PlexItemView,
    )
    from autoposter.collections.filters import predicates

    parsed = parse_filters({"actor": "Toshiro Mifune"})
    assert [one.attribute.name for one in predicates(parsed)] == ["actor"]

    with pytest.raises(AttributeNotInListing, match="unprobed"):
        PlexItemView(object()).get("actor")


def test_decades_search_operator_set_is_the_bare_form_alone():
    """``decade`` is in Kometa's ``no_not_mods`` (plex.py:593), and that list
    does two things at once: it drops ``.not`` from the tag-modifier half of
    ``searches`` and it drops the attribute from the number-modifier half
    ENTIRELY (plex.py:597-599). So ``decade: 1980`` is the only decade search
    Kometa will build -- not ``decade.gte``, which reads like an int operator
    and is not one. ``resolution`` is the sibling row that only loses ``.not``,
    which is why the two subtractions differ. ``decade`` is transcribed as
    ``int`` here, unlike ``resolution`` (``tag``), so roadmap row 178's
    ``.regex`` addition (tag/str only) does not reach it -- ``resolution``
    picks up ``.regex`` alongside its surviving bare form."""
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["decade"].search_operators == ("eq",)
    assert BY_NAME["resolution"].search_operators == ("eq", "regex")
    assert BY_NAME["year"].search_operators == ("eq", "not", "gt", "gte", "lt", "lte")


def test_country_is_rescoped_for_a_show_library_and_decade_refuses_one():
    """The two new rows' libtype behaviour, which is where a wrong
    transcription would silently build the wrong query rather than fail."""
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["country"].field_for("movie") == "country"
    assert BY_NAME["country"].field_for("show") == "show.country"
    assert BY_NAME["decade"].field_for("movie") == "decade"
    with pytest.raises(ValueError, match="not searchable on a show library"):
        BY_NAME["decade"].field_for("show")


def test_the_text_rows_take_the_string_operators_and_rescope():
    """Row 170's two rows. ``title`` is the bare field re-scoped to
    ``show.title`` (kometa_build_filter.py:180); ``edition`` is the one row in
    the table that composes BOTH translation tables -- search_translation's
    ``editionTitle`` (:102), then show_translation's entry for the TRANSLATED
    name (:196). Both are dual-vocabulary on ``unprobed`` (the ``country``
    pattern): a ``filters:`` block parses the key and refuses at the accessor
    by naming the tier."""
    from autoposter.collections.filter_values import (
        AttributeNotInListing,
        PlexItemView,
    )

    for name in ("title", "edition"):
        row = BY_NAME[name]
        assert row.type == "str", name
        assert row.source == "unprobed", name
        assert row.filterable, name
        assert row.search_operators == (
            "contains", "not", "is", "isnot", "begins", "ends", "regex",
        ), name

    assert BY_NAME["title"].kinds == ("movie", "show")
    assert BY_NAME["title"].field_for("movie") == "title"
    assert BY_NAME["title"].field_for("show") == "show.title"
    assert BY_NAME["edition"].kinds == ("movie",)
    assert BY_NAME["edition"].field_for("movie") == "editionTitle"
    assert BY_NAME["edition"].field_for("show") == "show.editionTitle"

    [title_clause] = parse_filters({"title": "Dune"}).children
    assert title_clause.operator == "contains"
    with pytest.raises(AttributeNotInListing, match="unprobed"):
        PlexItemView(object()).get("title")


def test_the_media_booleans_are_search_only_and_libtype_gated():
    """Row 172's five rows. ``duplicate`` is movie-only (movie_only_searches,
    kometa_build_filter.py:296), so a show library refuses it BY NAME;
    ``hdr``/``dovi``/``trash`` re-scope to the EPISODE libtype on a show
    library (:198-202) exactly as ``resolution`` does, and ``unmatched`` to
    ``show.unmatched`` (:189) -- the SHOW level, because a match belongs to
    the item and not the file. All five are ``search-only``: Kometa has no
    filter of any of these names (row 96's 29-name search-only list), so a
    ``filters:`` block refuses by pointing at the search block."""
    for name in ("hdr", "dovi", "trash", "duplicate", "unmatched"):
        row = BY_NAME[name]
        assert row.type == "bool", name
        assert row.source == "search-only", name
        assert not row.filterable, name
        assert row.search_operators == ("eq",), name

    assert BY_NAME["hdr"].field_for("show") == "episode.hdr"
    assert BY_NAME["dovi"].field_for("show") == "episode.dovi"
    assert BY_NAME["trash"].field_for("show") == "episode.trash"
    assert BY_NAME["unmatched"].field_for("show") == "show.unmatched"
    assert BY_NAME["duplicate"].field_for("movie") == "duplicate"
    with pytest.raises(ValueError, match="not searchable on a show library"):
        BY_NAME["duplicate"].field_for("show")

    with pytest.raises(ValueError, match="plex_search attribute"):
        parse_filters({"hdr": True})


def test_the_modifier_table_is_not_invertible():
    """Why ``SEARCH_MODIFIERS`` is keyed on a PAIR.

    Kometa's own ``modifier_translation`` (modules/plex.py:195) maps four wire
    strings from two different modifiers each, and every collision is between
    two DIFFERENT value types -- so a one-level dict keyed on the modifier
    cannot represent the table without picking a winner. This test fails the
    moment somebody "simplifies" the key.

    Two exclusions, and both are about what the claim above actually is rather
    than about making the numbers work:

    - the two date-WINDOW entries are not ``modifier_translation`` entries at
      all. Kometa takes their wire string from ``last_mod``
      (builder.py:4224), and ``SEARCH_MODIFIERS`` carries them only so the
      renderer has one lookup instead of two. They are excluded by name;
    - a collision is a wire reached by more than one distinct OPERATOR. That
      is precisely what makes a modifier-keyed dict lossy. ``!`` is reached
      from three of our pairs -- ``(tag, not)``, ``(str, not)``, ``(int,
      not)`` -- but from ONE modifier, ``.not``, so Kometa stores it once and
      means one thing by it, and it is not a collision.
    """
    from collections import defaultdict

    from autoposter.collections.filters import SEARCH_MODIFIERS

    from_modifier_translation = {
        key: wire
        for key, wire in SEARCH_MODIFIERS.items()
        if key not in (("date", "eq"), ("date", "not"))
    }
    reached_by = defaultdict(set)
    for (value_type, operator), wire in from_modifier_translation.items():
        reached_by[wire].add((value_type, operator))

    collisions = {
        wire: keys
        for wire, keys in reached_by.items()
        if wire != "" and len({operator for _, operator in keys}) > 1
    }
    # The four pairs, by operator. The types differ within every pair, which is
    # the load-bearing half.
    assert {wire: sorted({op for _, op in keys}) for wire, keys in collisions.items()} == {
        "%3E": ["ends", "gte"],
        "%3C": ["begins", "lte"],
        "%3E%3E": ["after", "gt"],
        "%3C%3C": ["before", "lt"],
    }
    # And the pairs in full, so that flattening the key to the operator alone
    # cannot leave this test green by accident.
    assert collisions["%3E"] == {
        ("str", "ends"), ("int", "gte"), ("float", "gte"), ("duration", "gte"),
    }
    assert collisions["%3C"] == {
        ("str", "begins"), ("int", "lte"), ("float", "lte"), ("duration", "lte"),
    }
    assert collisions["%3E%3E"] == {
        ("date", "after"), ("int", "gt"), ("float", "gt"), ("duration", "gt"),
    }
    assert collisions["%3C%3C"] == {
        ("date", "before"), ("int", "lt"), ("float", "lt"), ("duration", "lt"),
    }
    for wire, keys in collisions.items():
        assert len({value_type for value_type, _ in keys}) > 1, wire


def test_the_modifier_table_is_total_over_the_search_operators():
    """Every (type, operator) an attribute can actually be written with has a
    wire string. A missing entry would be a KeyError at URL-build time, on a
    config that loaded clean."""
    from autoposter.collections.filters import (
        FILTER_ATTRIBUTES,
        SEARCH_MODIFIERS,
    )

    for row in FILTER_ATTRIBUTES:
        for operator in row.search_operators:
            assert (row.type, operator) in SEARCH_MODIFIERS, (row.name, operator)


def test_resolution_has_no_negated_search():
    """``no_not_mods`` (modules/plex.py:593). Plex will not answer a negated
    resolution filter, so the table must not offer one."""
    from autoposter.collections.filters import BY_NAME

    assert "not" not in BY_NAME["resolution"].search_operators
    assert "not" in BY_NAME["genre"].search_operators


def test_duration_ships_only_its_range_operators_as_a_search():
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["duration"].search_operators == ("gt", "gte", "lt", "lte")
    assert BY_NAME["duration"].operators == ("eq", "not", "gt", "gte", "lt", "lte")


def test_a_rating_and_a_play_count_search_take_their_ranges_only():
    """Task 1's transcription CORRECTION, pinned so it cannot drift back.

    The plan's text gave ``float`` the bare form and ``.not`` as searches. A
    live fetch of Kometa v2.4.8 says otherwise: ``float_attributes`` take
    ``float_modifiers`` and nothing else (plex.py:549-550, :600), so neither
    ``critic_rating:`` nor ``critic_rating.not:`` is in ``plex.searches`` --
    and ``Builder._filter`` checks a written key against exactly that list
    (builder.py:4194-4195), so Kometa answers both with "attribute is not
    valid". ``plays`` is the same shape one type along: it is a
    ``number_attribute`` and NOT a ``year_attribute`` (plex.py:547, :599), so
    it gets ``number_modifiers`` alone, while ``year`` -- which is both --
    keeps the bare form and ``.not``.

    The CLIENT-side operator sets are untouched by any of this, which is the
    whole point of the two columns.
    """
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["critic_rating"].search_operators == ("gt", "gte", "lt", "lte", "rated")
    assert BY_NAME["audience_rating"].search_operators == ("gt", "gte", "lt", "lte", "rated")
    assert BY_NAME["plays"].search_operators == ("gt", "gte", "lt", "lte")
    assert BY_NAME["year"].search_operators == ("eq", "not", "gt", "gte", "lt", "lte")

    assert BY_NAME["critic_rating"].operators == ("eq", "not", "gt", "gte", "lt", "lte")
    assert BY_NAME["plays"].operators == ("eq", "not", "gt", "gte", "lt", "lte")

    for written in ({"critic_rating": 8}, {"plays": 3}):
        with pytest.raises(ValueError, match="is not a plex_search"):
            parse_filters(written, searching=True)


# --- the accepted YAML shapes ------------------------------------------------


def test_a_bare_key_is_the_types_default_operator():
    group = parse_filters({"genre": "Horror"})

    assert group.op == "all"
    (predicate,) = group.children
    assert isinstance(predicate, FilterPredicate)
    assert predicate.attribute.name == "genre"
    assert predicate.operator == "eq"
    assert predicate.values == ("Horror",)
    assert predicate.field == "filters.genre"


def test_a_bare_string_key_defaults_to_contains_not_to_equality():
    """``studio`` is the one tier-1 string attribute and its default is
    Kometa's: substring, not exact. The difference is the whole reason the
    ``str``/``tag`` split exists in the table."""
    (predicate,) = parse_filters({"studio": "Warner"}).children

    assert (predicate.attribute.type, predicate.operator) == ("str", "contains")


def test_a_list_value_means_any_of():
    (predicate,) = parse_filters({"genre": ["Horror", "Thriller"]}).children

    assert predicate.values == ("Horror", "Thriller")


def test_a_dotted_key_names_the_operator():
    (year,) = parse_filters({"year.gte": 2000}).children
    (label,) = parse_filters({"label.not": "skip"}).children
    (added,) = parse_filters({"added.before": dt.date(2024, 1, 1)}).children

    assert (year.attribute.name, year.operator, year.values) == ("year", "gte", (2000,))
    assert (label.attribute.name, label.operator, label.values) == ("label", "not", ("skip",))
    assert (added.attribute.name, added.operator, added.values) == (
        "added",
        "before",
        (dt.date(2024, 1, 1),),
    )


def test_a_date_accepts_both_iso_and_kometas_us_spelling():
    """ISO (dashes, 4-digit year leading) is this module's own form;
    ``MM/DD/YYYY`` (slashes) is how Kometa's own configs spell it. The two are
    told apart by punctuation, not position, so accepting both is unambiguous:
    an operator pasting a date straight out of an existing Kometa config
    should not have to reformat it."""
    (iso,) = parse_filters({"added.before": "2024-01-31"}).children
    (us,) = parse_filters({"added.before": "01/31/2024"}).children

    assert iso.values == (dt.date(2024, 1, 31),)
    assert us.values == (dt.date(2024, 1, 31),)


def test_an_unparseable_us_spelled_date_is_refused_naming_the_field():
    with pytest.raises(ValueError, match=re.escape("filters.added.before")):
        parse_filters({"added.before": "13/40/2024"})


def test_several_keys_in_one_block_are_all_of():
    group = parse_filters({"genre": "Horror", "year.gte": 2000})

    assert group.op == "all"
    assert [child.attribute.name for child in group.children] == ["genre", "year"]


def test_an_any_block_written_as_a_mapping_makes_each_key_an_alternative():
    group = parse_filters({"any": {"genre": "Horror", "label": "keep"}})

    (nested,) = group.children
    assert isinstance(nested, FilterGroup)
    assert nested.op == "any"
    assert [child.attribute.name for child in nested.children] == ["genre", "label"]
    assert nested.children[0].field == "filters.any.genre"


def test_an_any_block_written_as_a_list_makes_each_mapping_an_alternative():
    """The list form is what a two-attribute alternative needs: each element is
    a block whose own keys are ANDed, and the elements are ORed."""
    group = parse_filters({"any": [{"genre": "Horror", "year.gte": 2000}, {"label": "keep"}]})

    (nested,) = group.children
    assert nested.op == "any"
    first, second = nested.children
    assert first.op == "all"
    assert [child.attribute.name for child in first.children] == ["genre", "year"]
    assert [child.attribute.name for child in second.children] == ["label"]
    assert first.children[0].field == "filters.any[0].genre"


def test_an_all_block_nests_the_same_way():
    group = parse_filters({"all": [{"genre": "Horror"}, {"any": {"year.gte": 2000, "label": "keep"}}]})

    (nested,) = group.children
    assert nested.op == "all"
    assert nested.children[1].children[0].op == "any"


def test_nesting_is_arbitrarily_deep():
    raw = {"any": [{"all": [{"any": [{"genre": "Horror"}]}]}]}

    group = parse_filters(raw)

    node = group
    depth = 0
    while isinstance(node, FilterGroup):
        (node,) = node.children
        depth += 1

    assert isinstance(node, FilterPredicate)
    assert depth == 7
    assert node.field == "filters.any[0].all[0].any[0].genre"


# --- what refuses, and what the refusal says ---------------------------------


def test_an_unknown_attribute_is_refused_and_the_known_ones_listed():
    with pytest.raises(ValueError) as caught:
        parse_filters({"genree": "Horror"})

    assert "filters.genree" in str(caught.value)
    assert "genre" in str(caught.value)


def test_an_operator_the_type_does_not_have_is_refused_naming_the_field():
    with pytest.raises(ValueError) as caught:
        parse_filters({"year.regex": "^20"})

    message = str(caught.value)
    assert "filters.year.regex" in message
    assert "int" in message
    assert ".gte" in message


@pytest.mark.parametrize(
    "key",
    [
        "genre.gt", "studio.gte", "added.gt", "added.gte", "release.lt", "release.lte",
        "duration.regex", "content_rating.before",
    ],
    ids=[
        "tag-gt", "str-gte", "date-gt", "date-gte", "date-lt", "date-lte",
        "duration-regex", "tag-before",
    ],
)
def test_each_type_refuses_the_operators_it_does_not_have(key):
    """All four range modifiers on a date in particular: ``.before``/``.after``
    are Kometa's spellings, and accepting a second one for the same meaning is
    how two vocabularies start. ``.gte``/``.lte`` shipped here as INCLUSIVE
    forms until Task 4's oracle read Kometa's ``split`` -- which accepts all
    four and rewrites every one to the strict form -- so the same spelling
    meant two different things in the two systems."""
    with pytest.raises(ValueError, match=re.escape(f"filters.{key}")):
        parse_filters({key: "x"})


def test_a_date_range_modifiers_refusal_says_what_kometa_does_with_it():
    """A refusal that only said "not supported" would read as a gap. The point
    is the opposite: Kometa DOES accept it, and quietly makes it strict
    (``plex.py:2735-2747``), so an operator who wrote ``.gte`` meaning "on or
    after" was never getting that from Kometa either."""
    with pytest.raises(ValueError) as caught:
        parse_filters({"release.gte": "2000-01-01"})

    message = str(caught.value)
    assert ".after/.before" in message
    assert "strict" in message


def test_the_operator_refusal_for_a_date_explains_the_bare_form_correctly():
    """A date's bare form is not literally its ``default_operator`` name
    (``eq``) -- ``added: 30`` is a window in days, not "added eq 30" -- so the
    refusal for an operator a date attribute lacks must say so, not
    "... which means eq", which would teach the wrong thing about what a bare
    ``added:`` does."""
    with pytest.raises(ValueError) as caught:
        parse_filters({"added.gt": "2024-01-01"})

    message = str(caught.value)
    assert "within-the-last-N-days" in message
    assert "which means eq" not in message


@pytest.mark.parametrize(
    "raw",
    [
        {"year.gte": "2000s"},
        {"year": 2000.5},
        {"audience_rating": "high"},
        {"added.before": "not-a-date"},
        {"added": -1},
        {"added": "30"},
        {"duration.gte": "1x"},
        {"genre": True},
        {"studio.regex": "["},
    ],
    ids=[
        "int-not-a-number",
        "int-with-a-fraction",
        "float-not-a-number",
        "date-not-a-date",
        "date-negative-days",
        "date-days-not-a-number",
        "duration-unparseable",
        "tag-boolean",
        "bad-regex",
    ],
)
def test_an_unparseable_value_is_refused_naming_the_field(raw):
    (key,) = raw
    with pytest.raises(ValueError, match=re.escape(f"filters.{key}")):
        parse_filters(raw)


def test_an_empty_filters_block_is_refused():
    with pytest.raises(ValueError, match="filters"):
        parse_filters({})


def test_an_empty_list_value_is_refused():
    with pytest.raises(ValueError, match=re.escape("filters.genre")):
        parse_filters({"genre": []})


def test_a_non_mapping_block_is_refused():
    with pytest.raises(ValueError, match=re.escape("filters.any[0]")):
        parse_filters({"any": ["Horror"]})


def test_the_refusal_names_the_full_path_of_a_nested_field():
    with pytest.raises(ValueError) as caught:
        parse_filters({"any": [{"genre": "Horror"}, {"all": [{"yearr": 2000}]}]})

    assert "filters.any[1].all[0].yearr" in str(caught.value)


# --- one case-set per operator per value type --------------------------------
#
# Each entry is ``(what the view has, what the config says, expected)``. Every
# set carries at least one missing-value case (``None``) because the
# missing-value rule is a table-level invariant, not a per-operator detail --
# see the coverage test below, which enforces exactly that. The invariant
# itself splits by type family (SETTLED-BY-REVIEW; see the module docstring in
# ``src/autoposter/collections/filters.py``): ``tag``/``str`` missing values are
# excluded by a positive operator and included by a negative one, while
# ``int``/``float``/``date``/``duration`` missing values are excluded by every
# operator, ``.not`` included.
#
# One attribute stands in for each value type: the operators are properties of
# the type, and the table's own test above pins that every row of a type gets
# that type's operator set.

OPERATOR_CASES: dict[tuple[str, str], list[tuple[object, object, bool]]] = {
    # -- tag: exact match against the item's tag list, case-insensitive --------
    ("tag", "eq"): [
        (["Horror"], "Horror", True),
        (["Horror"], "horror", True),
        (["Horror", "Thriller"], "Thriller", True),
        (["Horror"], "Hor", False),
        (["Horror"], ["Comedy", "Horror"], True),
        (["Horror"], ["Comedy", "Drama"], False),
        ("Horror", "Horror", True),
        (None, "Horror", False),
        ([], "Horror", False),
    ],
    ("tag", "not"): [
        (["Horror"], "Horror", False),
        (["Horror"], "horror", False),
        (["Horror"], "Comedy", True),
        (["Horror", "Comedy"], ["Comedy", "Drama"], False),
        (["Horror"], ["Comedy", "Drama"], True),
        (None, "Horror", True),
        ([], "Horror", True),
    ],
    # ``.regex`` is the one CASE-SENSITIVE operator, which is Kometa's
    # (SETTLED-BY-ORACLE; see ``filters._as_regex``) and the opposite of every
    # other comparison in this table. ``(?i)`` is the spelling that still works
    # and means the same thing in both systems.
    ("tag", "regex"): [
        (["Science Fiction"], "^Science", True),
        (["Science Fiction"], "^Fiction", False),
        (["Science Fiction"], "FICTION$", False),
        (["Science Fiction"], "(?i)FICTION$", True),
        (["Horror", "Sci-Fi"], ["^Doc", "^Sci"], True),
        (["Horror"], ["^Doc", "^Sci"], False),
        (None, ".", False),
    ],
    # `.count_*` (roadmap row 100, sub-phase C2b, adjudication A-1): Kometa's
    # own modifier for "how many tags does this item have"
    # (`builder.py:419` declares the four, `builder.py:4350` parses their
    # value as an int), legal on every tag row. The written value is an int.
    #
    # THE MISSING CASE IS ZERO, NOT EXCLUDED, and that is Kometa's rule
    # rather than an exception someone invented for these four:
    # `plex.py:2931-2932` reduces the collected list with
    # `len(test_number) if test_number else 0` BEFORE `plex.py:2934` applies
    # any missing-value test, so `None` and `[]` both arrive at the
    # comparison as 0. `is_number_filter(0, ".lt", 3)` is `0 >= 3` -> False
    # -> kept (`util.py:623-632`). Every set below therefore carries a
    # `None` row (the coverage test demands one) whose expectation is
    # whatever the comparison says about ZERO -- which is why
    # `test_the_missing_value_rule_splits_by_type_family` exempts these four
    # by name.
    ("tag", "count_gt"): [
        (["Horror", "Thriller"], 1, True),
        (["Horror", "Thriller"], 2, False),
        (["Horror", "Horror"], 1, True),
        (["Horror"], 0, True),
        ("Horror", 0, True),
        (None, 0, False),
        ([], 0, False),
    ],
    ("tag", "count_gte"): [
        (["Horror", "Thriller"], 2, True),
        (["Horror", "Thriller"], 3, False),
        (["Horror"], 1, True),
        (None, 1, False),
        ([], 0, True),
    ],
    ("tag", "count_lt"): [
        (["Horror"], 2, True),
        (["Horror", "Thriller"], 2, False),
        (None, 5, True),
        ([], 5, True),
    ],
    ("tag", "count_lte"): [
        (["Horror", "Thriller"], 2, True),
        (["Horror", "Thriller"], 1, False),
        (None, 5, True),
        ([], 5, True),
    ],
    # -- str: substring by default, the rest spelled out ----------------------
    ("str", "contains"): [
        ("Warner Bros. Pictures", "warner", True),
        ("Warner Bros. Pictures", "Bros", True),
        ("Warner Bros. Pictures", "Universal", False),
        ("Warner Bros. Pictures", ["Universal", "Pictures"], True),
        ("", "warner", False),
        (None, "warner", False),
    ],
    ("str", "not"): [
        ("Warner Bros. Pictures", "warner", False),
        ("Warner Bros. Pictures", "Universal", True),
        ("Warner Bros. Pictures", ["Universal", "Pictures"], False),
        ("", "warner", True),
        (None, "warner", True),
    ],
    ("str", "is"): [
        ("Warner Bros.", "warner bros.", True),
        ("Warner Bros.", "Warner", False),
        ("Warner Bros.", ["Universal", "Warner Bros."], True),
        (None, "Warner Bros.", False),
    ],
    ("str", "isnot"): [
        ("Warner Bros.", "warner bros.", False),
        ("Warner Bros.", "Warner", True),
        (None, "Warner Bros.", True),
    ],
    ("str", "begins"): [
        ("Warner Bros.", "war", True),
        ("Warner Bros.", "Warner Bros.", True),
        ("Warner Bros.", "Bros", False),
        (None, "War", False),
    ],
    ("str", "ends"): [
        ("Warner Bros.", "bros.", True),
        ("Warner Bros.", "Warner Bros.", True),
        ("Warner Bros.", "Warner", False),
        (None, "bros.", False),
    ],
    ("str", "regex"): [
        ("Warner Bros.", "^Warner", True),
        ("Warner Bros.", "^warner", False),
        ("Warner Bros.", "(?i)^warner", True),
        ("Warner Bros.", "^Bros", False),
        (None, ".", False),
    ],
    # -- int ------------------------------------------------------------------
    ("int", "eq"): [
        (2000, 2000, True),
        (2000, 1999, False),
        (2000, [1999, 2000], True),
        (2000, "2000", True),
        (None, 2000, False),
    ],
    ("int", "not"): [
        (2000, 2000, False),
        (2000, 1999, True),
        (2000, [1999, 2000], False),
        (None, 2000, False),
    ],
    ("int", "gt"): [
        (2000, 1999, True),
        (2000, 2000, False),
        (2000, 2001, False),
        (None, 1999, False),
    ],
    ("int", "gte"): [
        (2000, 2000, True),
        (2000, 1999, True),
        (2000, 2001, False),
        (None, 2000, False),
    ],
    ("int", "lt"): [
        (2000, 2001, True),
        (2000, 2000, False),
        (2000, 1999, False),
        (None, 2001, False),
    ],
    ("int", "lte"): [
        (2000, 2000, True),
        (2000, 2001, True),
        (2000, 1999, False),
        (None, 2000, False),
    ],
    # -- float ----------------------------------------------------------------
    ("float", "eq"): [
        (7.5, 7.5, True),
        (7.5, 7, False),
        (7.0, 7, True),
        (7.5, [6.5, 7.5], True),
        (None, 7.5, False),
    ],
    ("float", "not"): [
        (7.5, 7.5, False),
        (7.5, 8, True),
        (None, 7.5, False),
    ],
    ("float", "gt"): [
        (7.5, 7.4, True),
        (7.5, 7.5, False),
        (7.5, 7.6, False),
        (None, 7, False),
    ],
    ("float", "gte"): [
        (7.5, 7.5, True),
        (7.5, 7.4, True),
        (7.5, 7.6, False),
        (None, 7, False),
    ],
    ("float", "lt"): [
        (7.5, 7.6, True),
        (7.5, 7.5, False),
        (7.5, 7.4, False),
        (None, 8, False),
    ],
    ("float", "lte"): [
        (7.5, 7.5, True),
        (7.5, 7.6, True),
        (7.5, 7.4, False),
        (None, 8, False),
    ],
    # -- duration: the view is minutes as a FLOAT, the exact quotient of Plex's
    #    milliseconds and 60000 (see ItemView.get's docstring); the config is
    #    Kometa's minutes, plus the written forms an operator reaches for. The
    #    whole numbers below are view values a caller supplies directly, so
    #    they stay whole -- what the real accessor hands back almost never is,
    #    which is why ``.eq`` is a float-equality test here and in Kometa -----
    ("duration", "eq"): [
        (90, 90, True),
        (90, "90m", True),
        (90, "1h30m", True),
        (90, "1:30", True),
        (60, "1h", True),
        (90, 91, False),
        (None, 90, False),
    ],
    ("duration", "not"): [
        (90, 90, False),
        (90, 91, True),
        (None, 90, False),
    ],
    ("duration", "gt"): [
        (90, 89, True),
        (90, 90, False),
        (90, "1h29m", True),
        (None, 89, False),
    ],
    ("duration", "gte"): [
        (90, 90, True),
        (90, 91, False),
        (None, 90, False),
    ],
    ("duration", "lt"): [
        (90, 91, True),
        (90, 90, False),
        (None, 91, False),
    ],
    ("duration", "lte"): [
        (90, 90, True),
        (90, 89, False),
        (None, 90, False),
    ],
    # -- date: the bare form is a window in days measured back from the run
    #    moment, with NO upper bound (a future date passes -- SETTLED-BY-ORACLE
    #    against Kometa's one-sided ``value < current_time - timedelta(days)``,
    #    util.py:601-604); ``.before``/``.after`` are absolute and strict.
    #    Every comparison is made at the MOMENT, and a bare date reads as that
    #    day's midnight -- with ``NOW`` at midnight these cases are whole days.
    ("date", "eq"): [
        (TODAY, 0, True),
        (dt.date(2026, 8, 20), 30, True),
        (dt.date(2026, 7, 26), 30, True),
        (dt.date(2026, 7, 25), 30, False),
        (dt.datetime(2026, 8, 20, 13, 5), 30, True),
        (dt.date(2026, 9, 1), 30, True),
        (None, 30, False),
    ],
    ("date", "not"): [
        (dt.date(2026, 7, 26), 30, False),
        (dt.date(2026, 7, 25), 30, True),
        (dt.date(2026, 9, 1), 30, False),
        (None, 30, False),
    ],
    ("date", "before"): [
        (dt.date(2024, 1, 1), dt.date(2024, 1, 2), True),
        (dt.date(2024, 1, 1), dt.date(2024, 1, 1), False),
        (dt.date(2024, 1, 2), dt.date(2024, 1, 1), False),
        (dt.date(2024, 1, 1), "2024-01-02", True),
        (dt.datetime(2024, 1, 1, 0, 1), dt.date(2024, 1, 1), False),
        (dt.date(2026, 8, 24), "today", True),
        (TODAY, "today", False),
        (None, dt.date(2024, 1, 1), False),
    ],
    ("date", "after"): [
        (dt.date(2024, 1, 2), dt.date(2024, 1, 1), True),
        (dt.date(2024, 1, 1), dt.date(2024, 1, 1), False),
        (dt.datetime(2024, 1, 1, 0, 1), dt.date(2024, 1, 1), True),
        (dt.date(2024, 1, 1), dt.date(2024, 1, 2), False),
        (dt.date(2026, 8, 26), "today", True),
        (TODAY, "today", False),
        (None, dt.date(2024, 1, 1), False),
    ],
}

# One attribute per value type, so a case-set can be turned into a real config
# key. The table's own tests pin that every row of a type gets that type's
# operators, which is what makes one representative enough.
REPRESENTATIVE = {
    "tag": "genre",
    "str": "studio",
    # ``plays``, not ``year``: ``year`` is the one int whose bare/``.not``
    # missing-value routing diverges (row 159's table below pins it), and the
    # generic case-sets exist to pin the TYPE's uniform rule.
    "int": "plays",
    "float": "audience_rating",
    "date": "added",
    "duration": "duration",
}


def _operator_case_params():
    for (value_type, operator), cases in OPERATOR_CASES.items():
        for index, case in enumerate(cases):
            yield pytest.param(value_type, operator, *case, id=f"{value_type}-{operator}-{index}")


@pytest.mark.parametrize(
    "value_type,operator,have,written,expected", list(_operator_case_params())
)
def test_operator_semantics(value_type, operator, have, written, expected):
    attribute = REPRESENTATIVE[value_type]
    key = attribute if operator == DEFAULT_OPERATOR[value_type] else f"{attribute}.{operator}"

    group = parse_filters({key: written})

    assert evaluate(group, _view(attribute, have), now=NOW) is expected


# Roadmap row 159, SETTLED against the fetched transcription rather than a
# recollection: Kometa 2.4.8 routes a BARE or ``.not`` ``year`` through its
# tag/set-intersection branch, not its number branch -- ``check_filter``'s
# condition opens ``filter_attr != "year"``
# (.superpowers/oracle/9a/kometa_oracle.py:190, transcribing
# modules/plex.py:2895) -- so an item with no year is KEPT by ``year.not:``
# (empty intersection, negated) and dropped by the bare form, while the four
# range modifiers stay on the number branch and drop it unconditionally.
YEAR_MISSING_CASES = [
    ("year", 2000, False),
    ("year.not", 2000, True),
    ("year.gt", 1999, False),
    ("year.gte", 2000, False),
    ("year.lt", 2001, False),
    ("year.lte", 2000, False),
]


@pytest.mark.parametrize(
    "key,written,expected",
    YEAR_MISSING_CASES,
    ids=[key for key, _, _ in YEAR_MISSING_CASES],
)
def test_a_missing_year_follows_kometas_tag_branch_routing(key, written, expected):
    group = parse_filters({key: written})
    assert evaluate(group, _view("year", None), now=NOW) is expected


def test_every_operator_has_a_case_set_including_a_missing_value():
    """The structural half of the claim "every operator is table-driven".

    A ``(type, operator)`` pair with no case-set fails here rather than
    quietly shipping untested, and so does a case-set that forgot the
    missing-value rule -- which is a table-level invariant every operator has
    to honour, not a per-operator detail someone may reasonably skip.

    ``bool`` is excluded, and it is the one exclusion this test will accept: it
    is a SEARCH-ONLY type (phase 9b), every ``bool`` row is
    ``filterable=False``, and ``_split_key`` therefore refuses one in a
    ``filters:`` block before ``evaluate`` can ever see it. Its entry in
    ``OPERATORS_BY_TYPE`` exists only to keep ``FilterAttribute.operators``
    total over ``VALUE_TYPES``; a case-set for it could not be written as a
    config key at all. A future FILTERABLE boolean row would have to delete
    this exclusion, which is the point of spelling it out rather than
    filtering on ``OPERATOR_CASES``.
    """
    pairs = {
        (t, op) for t, ops in OPERATORS_BY_TYPE.items() for op in ops if t != "bool"
    }

    assert set(OPERATOR_CASES) == pairs
    assert all(row.filterable is False for row in FILTER_ATTRIBUTES if row.type == "bool")
    for pair, cases in OPERATOR_CASES.items():
        assert cases, pair
        assert any(have is None for have, _, _ in cases), pair


def test_the_missing_value_rule_splits_by_type_family():
    """Stated once, as the (split) invariant it is. ``tag``/``str``: an item
    with no value for the attribute is EXCLUDED by a positive filter and
    INCLUDED by a negative one. ``int``/``float``/``date``/``duration``: a
    missing value is EXCLUDED by every operator, ``.not`` included --
    SETTLED-BY-REVIEW against Kometa's own number/date filter, whose
    missing-value check ignores the modifier entirely.

    Read off the case table rather than re-listed, so the two cannot disagree.

    THE ONE EXEMPTION, and it is upstream's own (sub-phase C2b): the four
    ``.count_*`` modifiers never reach this rule in either system. Kometa
    reduces the collected list to ``len(test_number) if test_number else 0``
    BEFORE its missing-value test (``modules/plex.py:2931-2932``), so a
    missing value is compared as the NUMBER zero; this table does the same,
    in ``filters._matches``, above ``_is_missing``. Excluded by name here
    rather than by filtering the table, so a future count-like operator has
    to justify itself in this docstring instead of quietly inheriting the
    exemption.
    """
    negative = {"not", "isnot"}
    counting = {"count_gt", "count_gte", "count_lt", "count_lte"}
    always_excluded = {"int", "float", "date", "duration"}
    for (value_type, operator), cases in OPERATOR_CASES.items():
        if operator in counting:
            continue
        for have, _, expected in cases:
            if have is None:
                if value_type in always_excluded:
                    assert expected is False, (value_type, operator)
                else:
                    assert expected is (operator in negative), (value_type, operator)


# --- dates: the convention ---------------------------------------------------


def test_a_datetime_is_compared_at_its_own_moment_not_its_calendar_date():
    """The convention, and a correction (SETTLED-BY-ORACLE). This module
    compared date-granularly at first -- the time of day was dropped on both
    sides -- so ``added.after: 2024-01-01`` excluded an item added at 23:59
    THAT DAY. Kometa compares the plexapi value as it stands against
    ``validate_date``'s result, which is midnight, so it keeps that item; the
    oracle found three such items in one 120-item library. The bare date on
    the config side still reads as midnight, which is the only thing that
    makes ``.after: <the day itself>`` mean anything at all.
    """
    group = parse_filters({"added.after": dt.date(2024, 1, 1)})

    assert evaluate(group, _view("added", dt.datetime(2024, 1, 1, 0, 0)), now=NOW) is False
    assert evaluate(group, _view("added", dt.datetime(2024, 1, 1, 0, 1)), now=NOW) is True
    assert evaluate(group, _view("added", dt.datetime(2024, 1, 1, 23, 59)), now=NOW) is True


def test_an_aware_datetime_keeps_its_own_wall_clock_and_is_not_converted():
    """The other half of the convention, unchanged by the oracle: an aware
    value keeps the wall-clock reading it already has, with no conversion to
    UTC or to this machine's zone. Converting would make the same library
    filter differently depending on where the run happens, which is not a
    property a collection should have.

    2026-08-20 23:00-08:00 is 2026-08-21 07:00 UTC. It filters as 23:00 on the
    20th, so it is before the 21st.
    """
    aware = dt.datetime(2026, 8, 20, 23, 0, tzinfo=dt.timezone(dt.timedelta(hours=-8)))
    group = parse_filters({"added.before": dt.date(2026, 8, 21)})

    assert evaluate(group, _view("added", aware), now=NOW) is True


def test_today_resolves_against_the_run_moment_not_the_wall_clock():
    """``today`` is the run's moment, which is Kometa's reading of the same
    word (``datetime.now() if data == "today"``, builder.py:4443) -- so
    ``release.before: today`` keeps something released earlier today, and a
    run at midnight is the degenerate case where it does not."""
    group = parse_filters({"release.before": "today"})

    assert evaluate(group, _view("release", dt.date(2026, 8, 24)), now=NOW) is True
    assert evaluate(group, _view("release", dt.date(2026, 8, 25)), now=NOW) is False
    afternoon = dt.datetime(2026, 8, 25, 14, 30)
    assert evaluate(group, _view("release", dt.date(2026, 8, 25)), now=afternoon) is True


def test_a_run_date_is_accepted_and_read_as_that_days_midnight():
    """``evaluate`` takes a moment, but a caller that only has a run date is
    not made to invent a time: a ``date`` reads as midnight, which is what it
    means."""
    group = parse_filters({"added.before": dt.date(2026, 8, 25)})

    assert evaluate(group, _view("added", dt.datetime(2026, 8, 24, 23, 59)), now=TODAY) is True
    assert evaluate(group, _view("added", dt.datetime(2026, 8, 25, 0, 1)), now=TODAY) is False


# --- the current_year value grammar (roadmap row 171) -----------------------


def test_current_year_bare_resolves_to_the_runs_year():
    group = parse_filters({"year": "current_year"})
    assert evaluate(group, _view("year", 2026), now=NOW) is True
    assert evaluate(group, _view("year", 2025), now=NOW) is False


def test_current_year_with_an_offset_subtracts():
    """Kometa's own transcription (tests/oracle/9b/kometa_build_filter.py:
    768-788): ``datetime.now().year - int(year_values[1])`` -- subtraction,
    confirmed against the vendored oracle rather than an external citation."""
    group = parse_filters({"year": "current_year-5"})
    assert evaluate(group, _view("year", 2021), now=NOW) is True
    assert evaluate(group, _view("year", 2026), now=NOW) is False


def test_current_year_resolves_against_the_run_moment_not_the_parse_moment():
    """The whole point of the sentinel, mirroring ``_Today``: the same parsed
    group answers differently against a different run moment."""
    group = parse_filters({"year": "current_year"})
    assert evaluate(group, _view("year", 2026), now=NOW) is True
    later = dt.datetime(2027, 1, 1)
    assert evaluate(group, _view("year", 2026), now=later) is False
    assert evaluate(group, _view("year", 2027), now=later) is True


def test_current_year_is_case_insensitive_like_today_is():
    """Consistency with the ONE other sentinel word this module already has
    (``_as_date``'s ``.casefold() == "today"``, filters.py:1550) -- a
    deliberate divergence from Kometa's own literal ``str(value).
    startswith("current_year")`` (case-sensitive), in the direction this
    module already chose for ``today``."""
    group = parse_filters({"year": "Current_Year"})
    assert evaluate(group, _view("year", 2026), now=NOW) is True


def test_current_year_gt_and_lt_use_the_resolved_year():
    group = parse_filters({"year.gt": "current_year-3"})
    assert evaluate(group, _view("year", 2024), now=NOW) is True  # 2024 > 2023
    assert evaluate(group, _view("year", 2023), now=NOW) is False


def test_current_year_refuses_a_non_digit_suffix():
    with pytest.raises(ValueError, match="whole number"):
        parse_filters({"year": "current_year-abc"})


def test_current_year_refuses_whitespace_around_the_dash():
    """A tighter grammar than Kometa's own tolerant one (which strips
    whitespace inside ``year_values[1]``) -- an explicit narrowing, not an
    oversight: this module's ``.strip().lower()`` normalises the OUTER
    string, as every other string-form value in this module does, and does
    not special-case internal whitespace the way no other grammar here does
    either."""
    with pytest.raises(ValueError, match="whole number"):
        parse_filters({"year": "current_year - 5"})


def test_current_year_zero_offset_is_the_same_as_bare():
    group_bare = parse_filters({"year": "current_year"})
    group_zero = parse_filters({"year": "current_year-0"})
    assert evaluate(group_bare, _view("year", 2026), now=NOW) is True
    assert evaluate(group_zero, _view("year", 2026), now=NOW) is True


def test_plain_year_numbers_still_parse_as_before():
    group = parse_filters({"year": 1990})
    assert evaluate(group, _view("year", 1990), now=NOW) is True
    assert evaluate(group, _view("year", 1991), now=NOW) is False


# --- resolve_search_values: the plex_search half of current_year/today ------
#
# ``evaluate``/``_matches_one`` resolve ``_CurrentYear``/``_Today`` at compare
# time; a ``plex_search`` has no compare step, so ``build_search_url`` needs a
# concrete value handed to it. Controller ruling (search-tails-2 Task 3
# review): before this function existed, a ``plex_search:`` config writing
# ``year: current_year`` parsed without error -- ``year`` is searchable and
# ``_as_current_year`` is not gated by ``searching`` -- and then reached
# ``search_url``'s plain ``str(value)`` int/float fallback carrying the
# unresolved sentinel, rendering its own ``repr()`` into the query Plex
# actually received.


def test_resolve_search_values_replaces_a_bare_current_year():
    group = parse_filters({"year": "current_year"}, searching=True)
    resolved = resolve_search_values(group, now=NOW)
    predicate = resolved.children[0]
    assert predicate.values == (NOW.year,)


def test_resolve_search_values_subtracts_the_offset():
    group = parse_filters({"year": "current_year-5"}, searching=True)
    resolved = resolve_search_values(group, now=NOW)
    assert resolved.children[0].values == (NOW.year - 5,)


def test_resolve_search_values_replaces_today_with_the_moments_date():
    """A bare date, not a timestamp: ``search_url._arguments``' date branch is
    ``value.isoformat()`` and every OTHER date on this path is a ``dt.date``
    (``_as_date`` returns one), so a resolved ``_Today`` has to match --
    Kometa's own driver truncates to the day too, its ``.before``/``.after``
    branch being ``return_as="%Y-%m-%d"``
    (``tests/oracle/9b/kometa_build_filter.py:814``). A full ``datetime``
    here would render ``YYYY-MM-DDTHH:MM:SS.ffffff``, which is a query
    string Plex does not parse as a date."""
    group = parse_filters({"release.after": "today"}, searching=True)
    resolved = resolve_search_values(group, now=NOW)
    assert resolved.children[0].values == (NOW.date(),)


def test_resolve_search_values_leaves_an_ordinary_value_alone():
    """No relative sentinel anywhere -- the common case, and the one
    ``resolve_search_values`` should touch least: the returned tree is the
    SAME object, not a rebuilt copy, when nothing needed resolving."""
    group = parse_filters({"year": 1990, "studio": "A24"}, searching=True)
    resolved = resolve_search_values(group, now=NOW)
    assert resolved is group


def test_resolve_search_values_reaches_into_a_nested_group():
    group = parse_filters({"any": {"year": "current_year"}}, searching=True)
    resolved = resolve_search_values(group, now=NOW)
    nested = resolved.children[0]
    assert nested.children[0].values == (NOW.year,)


def test_resolve_search_values_only_rebuilds_the_predicate_that_changed():
    group = parse_filters({"year": "current_year", "studio": "A24"}, searching=True)
    resolved = resolve_search_values(group, now=NOW)
    original_studio = next(c for c in group.children if c.field == "filters.studio")
    resolved_studio = next(c for c in resolved.children if c.field == "filters.studio")
    assert resolved_studio is original_studio


# --- the language base-code fold (roadmap row 204) ---------------------------


class _FoldView:
    """The minimal ItemView the base-code fold tests need."""

    def __init__(self, **values):
        self._values = values

    def get(self, name):
        return self._values.get(name)


def test_a_language_filter_folds_written_spellings_to_the_base_code():
    """Row 204's offline fold: `eng` (ISO 639-2) meets a stream tagged `en`,
    and a written base code meets a regional stream tag (`pt` against
    `pt-BR`) -- the two spellings the row names -- with zero Plex reads,
    which is this layer's whole law."""
    group = parse_filters({"audio_language": "eng"})
    assert evaluate(group, _FoldView(audio_language=("en",)))
    group = parse_filters({"audio_language": "pt"})
    assert evaluate(group, _FoldView(audio_language=("pt-BR",)))
    group = parse_filters({"subtitle_language": "fin"})
    assert evaluate(group, _FoldView(subtitle_language=("fi",)))


def test_a_language_display_title_still_matches_nothing():
    """`English` is row 204's OPEN half: langcodes cannot reduce a display
    title, the fallback returns it unchanged, and no stream tag equals it.
    The table that half awaits is `iso_names.LANGUAGE_NAMES`."""
    group = parse_filters({"audio_language": "English"})
    assert not evaluate(group, _FoldView(audio_language=("en",)))


def test_a_regional_written_value_widens_to_its_base_deliberately():
    """The fold's one judgement, pinned so it cannot drift silently: a
    regional written value matches at its base here, where `plex_search`
    targets an exact library value only. Disclosed on both language rows'
    notes."""
    group = parse_filters({"audio_language": "es-419"})
    assert evaluate(group, _FoldView(audio_language=("es",)))


def test_the_fold_is_scoped_to_the_two_language_attributes():
    """Every other tag row keeps exact (casefolded) matching."""
    group = parse_filters({"genre": "Horror"})
    assert evaluate(group, _FoldView(genre=("Horror",)))
    assert not evaluate(group, _FoldView(genre=("Hor",)))


def test_a_negative_language_filter_negates_the_folded_match():
    group = parse_filters({"audio_language.not": "eng"})
    assert not evaluate(group, _FoldView(audio_language=("en",)))
    assert evaluate(group, _FoldView(audio_language=("fi",)))


# --- nesting -----------------------------------------------------------------


def test_an_all_block_needs_every_child():
    group = parse_filters({"genre": "Horror", "year.gte": 2000})

    assert evaluate(group, {"genre": ["Horror"], "year": 2001}, now=NOW) is True
    assert evaluate(group, {"genre": ["Horror"], "year": 1999}, now=NOW) is False
    assert evaluate(group, {"genre": ["Comedy"], "year": 2001}, now=NOW) is False


def test_an_any_block_needs_one_child():
    group = parse_filters({"any": {"genre": "Horror", "year.gte": 2000}})

    assert evaluate(group, {"genre": ["Comedy"], "year": 2001}, now=NOW) is True
    assert evaluate(group, {"genre": ["Horror"], "year": 1999}, now=NOW) is True
    assert evaluate(group, {"genre": ["Comedy"], "year": 1999}, now=NOW) is False


def test_any_of_all_of_nests_both_ways():
    """Horror from this century, or anything at all labelled ``keep``."""
    group = parse_filters(
        {"any": [{"genre": "Horror", "year.gte": 2000}, {"label": "keep"}]}
    )

    assert evaluate(group, {"genre": ["Horror"], "year": 2001, "label": []}, now=NOW) is True
    assert evaluate(group, {"genre": ["Horror"], "year": 1999, "label": []}, now=NOW) is False
    assert evaluate(group, {"genre": ["Comedy"], "year": 1999, "label": ["keep"]}, now=NOW) is True


def test_a_deeply_nested_group_evaluates_all_the_way_down():
    group = parse_filters(
        {
            "genre": "Horror",
            "any": [
                {"all": [{"year.gte": 2000}, {"year.lt": 2010}]},
                {"label": "keep"},
            ],
        }
    )
    horror = {"genre": ["Horror"], "label": []}

    assert evaluate(group, {**horror, "year": 2005}, now=NOW) is True
    assert evaluate(group, {**horror, "year": 2015}, now=NOW) is False
    assert evaluate(group, {**horror, "year": 2015, "label": ["keep"]}, now=NOW) is True
    assert evaluate(group, {"genre": ["Comedy"], "year": 2005, "label": []}, now=NOW) is False


def test_evaluate_defaults_now_to_the_current_moment():
    """``now`` is a keyword with a default so callers that have no run moment
    still get the documented behaviour rather than a TypeError."""
    group = parse_filters({"added.before": "today"})

    assert evaluate(group, {"added": dt.date(2000, 1, 1)}) is True


# --- the view's contract -----------------------------------------------------


def test_a_view_value_of_the_wrong_type_is_loud_rather_than_silently_missing():
    """A view that hands back a string where a number belongs is a bug in the
    accessor, not an item without the attribute. Treating it as missing would
    hide it behind a plausible, wrong collection; the engine stage contains the
    exception."""
    group = parse_filters({"year.gte": 2000})

    with pytest.raises(TypeError, match="year"):
        evaluate(group, {"year": "2001"}, now=NOW)


# --- the model never touches plexapi ------------------------------------------


def test_filters_module_never_imports_plexapi():
    """The module docstring's central claim, pinned structurally: read the
    module's own import list rather than trust the docstring to stay true. The
    operator-mapping test above is the only place in this test file plexapi is
    imported -- the model under test never is."""
    import ast

    import autoposter.collections.filters as filters_module

    tree = ast.parse(pathlib.Path(filters_module.__file__).read_text(encoding="utf-8"))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert "plexapi" not in imported_roots


# --- the parser's search mode (phase 9b Task 1) -------------------------------


def _only(group):
    """The single predicate in a one-key block."""
    (child,) = group.children
    return child


def test_a_bare_date_in_a_search_is_a_relative_window():
    predicate = _only(parse_filters({"added": 30}, searching=True))
    assert predicate.values == (RelativeWindow(30, "d"),)


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        (30, RelativeWindow(30, "d")),
        ("30", RelativeWindow(30, "d")),
        ("30d", RelativeWindow(30, "d")),
        ("6o", RelativeWindow(6, "o")),
        ("2y", RelativeWindow(2, "y")),
        ("90m", RelativeWindow(90, "m")),
        ("12h", RelativeWindow(12, "h")),
        ("4w", RelativeWindow(4, "w")),
        ("45s", RelativeWindow(45, "s")),
    ],
)
def test_every_relative_window_unit_parses(written, expected):
    predicate = _only(parse_filters({"last_played.not": written}, searching=True))
    assert predicate.values == (expected,)


def test_a_bare_date_in_a_filter_is_still_a_day_count():
    """The client-side grammar is unchanged: ``added: 30`` is an int, and the
    unit suffixes are refused, because ``filters:`` evaluates in python and has
    no server to hand ``30d`` to."""
    predicate = _only(parse_filters({"added": 30}))
    assert predicate.values == (30,)
    with pytest.raises(ValueError, match="not a number of days"):
        parse_filters({"added": "30d"})


def test_a_relative_window_refuses_an_unknown_unit_naming_all_seven():
    with pytest.raises(ValueError) as error:
        parse_filters({"added": "30x"}, searching=True)
    message = str(error.value)
    assert "filters.added" in message
    assert "o = months" in message
    assert "m = minutes" in message


def test_an_attribute_no_row_names_is_refused_with_the_right_vocabulary():
    """Two vocabularies, two lists. ``height`` is one of row 96's 44 Kometa
    filter-only names, and no row names it yet (``aspect`` -- the row this
    test originally used as its example -- graduated into the table in
    sub-phase C2b, so the example moved rather than the assertion), so both
    blocks answer "unknown" -- but each names ITS OWN vocabulary, not the
    table."""
    with pytest.raises(ValueError) as error:
        parse_filters({"height": "1000"}, searching=True)
    message = str(error.value)
    assert "height" in message
    assert "plex_search" in message
    assert "unplayed" in message        # a searchable name is offered
    assert "plays" in message

    with pytest.raises(ValueError) as error:
        parse_filters({"height": "1000"})
    message = str(error.value)
    assert "filters:" in message
    assert "unplayed" not in message    # search-only names are NOT offered


def test_a_search_refuses_a_filter_only_attribute_naming_the_other_block(monkeypatch):
    """The cross-reference D2(c) requires, exercised with a synthetic row.

    Unreachable from the shipped table -- all nineteen rows are searchable --
    and written anyway, because the first filter-only row (row 96's 44-name
    residue) must land on a refusal that says where the attribute does live,
    not on a KeyError. A synthetic row is the only way to reach it today, and
    a test that cannot reach the branch it names is worse than none.
    """
    from autoposter.collections import filters as module

    row = module.FilterAttribute(
        "aspect", "float", ("movie", "show"), "tier2-deferred", "synthetic",
        search_field=None, show_search_field=None,
        search_kinds=("movie", "show"), filterable=True,
    )
    monkeypatch.setitem(module.BY_NAME, "aspect", row)
    with pytest.raises(ValueError) as error:
        parse_filters({"aspect.gte": 1.78}, searching=True)
    message = str(error.value)
    assert "aspect" in message
    assert "no search field" in message
    assert "filters:" in message


def test_a_filter_refuses_a_search_only_attribute_and_says_where_it_lives():
    with pytest.raises(ValueError) as error:
        parse_filters({"unplayed": True})
    message = str(error.value)
    assert "'unplayed' is a plex_search attribute" in message
    assert "not a client-side filter" in message


def test_a_search_accepts_regex_on_a_tag_attribute():
    """Roadmap row 178: search-side ``.regex`` on a tag/str attribute is now
    a real, distinct mechanism (vocabulary expansion, proven in
    ``test_collection_search_url.py``) -- parsing accepts it rather than
    refusing it."""
    group = parse_filters({"genre.regex": "^Hor"}, searching=True)
    [predicate] = group.children
    assert predicate.attribute.name == "genre"
    assert predicate.operator == "regex"


def test_a_search_refuses_regex_on_a_type_that_never_had_it():
    """``.regex`` is a tag/str-only mechanism in Kometa too (validate_attribute
    checks the tag list, then the string list -- never an int/float/date/
    duration/bool one). ``year`` (int) still refuses, now through the
    generic operator-table message rather than the old blanket one."""
    with pytest.raises(ValueError) as error:
        parse_filters({"year.regex": "^20"}, searching=True)
    message = str(error.value)
    assert ".regex does not apply to 'year'" in message


def test_a_search_refuses_a_bare_duration_and_names_the_ranges():
    """A CORRECTION to the plan's text, which said a bare ``duration:`` reaches
    Plex unconverted and therefore asks about milliseconds. It does not reach
    Plex at all: ``duration`` is a ``float_attribute`` and takes only the four
    range modifiers (plex.py:549, :600), so a bare ``duration:`` is not in
    ``plex.searches`` and Kometa refuses it outright (builder.py:4194-4195).
    The two blocks agree on the UNIT for the ranges that do exist -- Kometa
    multiplies a search duration by 60000 (builder.py:4234) exactly as the
    client-side view divides by it -- so there is no millisecond trap to warn
    about, and the refusal must not invent one."""
    with pytest.raises(ValueError) as error:
        parse_filters({"duration": 90}, searching=True)
    message = str(error.value)
    assert "filters.duration" in message
    assert "plex_search" in message
    assert "`duration.gt`" in message
    assert "millisecond" not in message


def test_a_filter_refuses_rated_and_points_at_plex_search():
    with pytest.raises(ValueError) as error:
        parse_filters({"critic_rating.rated": True})
    assert "plex_search" in str(error.value)


def test_the_and_suffix_is_refused_in_both_blocks():
    for searching in (True, False):
        with pytest.raises(ValueError) as error:
            parse_filters({"genre.and": ["Horror", "Comedy"]}, searching=searching)
        message = str(error.value)
        assert ".and" in message
        assert "all:" in message


def test_a_boolean_search_takes_a_real_boolean_only():
    predicate = _only(parse_filters({"unplayed": True}, searching=True))
    assert predicate.values == (True,)
    with pytest.raises(ValueError, match="true or false"):
        parse_filters({"unplayed": "yes"}, searching=True)


def test_rated_takes_a_boolean_not_a_number():
    predicate = _only(parse_filters({"critic_rating.rated": False}, searching=True))
    assert predicate.operator == "rated"
    assert predicate.values == (False,)


def test_a_search_list_element_takes_the_written_conjunction_and_is_inline():
    """The nesting divergence, pinned. ``build_filter`` renders each element of
    a list with the WRITTEN key's conjunction (builder.py:4214) and joins them
    with the CONTAINING block's; ``check_filters`` -- the client-side path --
    ANDs each element instead. One grammar, two renderings, one parameter."""
    searched = parse_filters(
        {"any": [{"studio": "A24"}, {"year.gte": 2020}]}, searching=True
    )
    (wrapper,) = searched.children
    assert wrapper.inline is True
    assert wrapper.op == "all"          # the CONTAINING block's op
    assert [child.op for child in wrapper.children] == ["any", "any"]

    filtered = parse_filters({"any": [{"studio": "A24"}, {"year.gte": 2020}]})
    (wrapper,) = filtered.children
    assert wrapper.inline is False
    assert wrapper.op == "any"
    assert [child.op for child in wrapper.children] == ["all", "all"]


def test_a_mapping_shaped_nested_block_is_identical_in_both_modes():
    for searching in (True, False):
        group = parse_filters(
            {"any": {"studio": "A24", "year.gte": 2020}}, searching=searching
        )
        (wrapper,) = group.children
        assert wrapper.op == "any"
        assert wrapper.inline is False
        assert len(wrapper.children) == 2


def test_the_base_conjunction_is_the_written_one_and_adds_no_nesting():
    """Kometa's base_dict IS the inner mapping (builder.py:4278-4284), so the
    parsed tree must be one level deep, not two -- an extra level would become
    a push/pop pair the URL builder emits and Kometa does not."""
    group = parse_filters({"studio": "A24", "year.gte": 2020}, base="any", searching=True)
    assert group.op == "any"
    assert len(group.children) == 2
    assert all(isinstance(child, FilterPredicate) for child in group.children)

    with pytest.raises(ValueError, match="is not a base"):
        parse_filters({"studio": "A24"}, base="either")


def test_a_refusal_never_renders_an_empty_list_of_modifiers():
    """A ``bool`` row as a SEARCH is the shape whose whole operator set is the
    bare form, so the list of writable modifiers is EMPTY -- and the message
    used to read "it takes  (or no modifier at all, which means eq)": a
    dangling phrase, a double space, and no options. The operator reading
    that has been told nothing about what to write.

    ``resolution`` was this test's original row, back when its whole search
    operator set was the bare form too (Kometa's ``no_not_mods``); roadmap
    row 178 gave every ``tag``/``str`` row (``resolution`` included)
    ``.regex``, so ``hdr`` (``bool``, untouched by that change) is what now
    exercises the empty-list branch.

    Pinned rather than eyeballed because an empty collection rendered into a
    sentence is the failure mode that looks fine in every test that only checks
    a substring.
    """
    from autoposter.collections.filters import parse_filters

    with pytest.raises(ValueError) as error:
        parse_filters(
            {"hdr.not": True}, field="params", searching=True, base="all"
        )
    message = str(error.value)
    assert "it takes no modifier at all, which means eq" in message
    assert "it takes  " not in message
    assert "takes  (or" not in message


def test_the_refusal_gets_the_article_right_for_an_int_attribute():
    """"a int attribute" was visible on every mis-modified int and ``year``.

    Cosmetic, and pinned anyway: the refusal grammar is this phase's most-read
    surface, and it is quoted back in the roadmap as evidence the messages are
    written for a person.
    """
    from autoposter.collections.filters import parse_filters

    with pytest.raises(ValueError) as error:
        parse_filters({"year.begins": 2010}, field="params", searching=True, base="all")
    message = str(error.value)
    assert "an int attribute" in message
    assert "a int attribute" not in message

    # and the consonant case is unchanged
    with pytest.raises(ValueError) as error:
        parse_filters({"studio.gt": "A24"}, field="params", searching=True, base="all")
    assert "a str attribute" in str(error.value)


# --- Concern D: Kometa's .count_* tag modifiers (adjudication A-1) ----------


def test_the_tag_type_carries_kometas_own_count_modifiers():
    """C7's adjudication A-1. Kometa spells "how many tags does this item
    have" as a MODIFIER on the tag attribute itself (`builder.py:4350`), not
    as a separate `*_count` attribute -- so a Kometa config ports verbatim
    and this table's `name` column keeps its promise (every name is Kometa's
    own). This reverses the phase-C recon's A8 recommendation, which is
    recorded on the operator table's own comment rather than left implicit."""
    assert OPERATORS_BY_TYPE["tag"] == (
        "eq", "not", "regex", "count_gt", "count_gte", "count_lt", "count_lte",
    )
    assert BY_NAME["audio_language"].operators == OPERATORS_BY_TYPE["tag"]
    assert BY_NAME["subtitle_language"].operators == OPERATORS_BY_TYPE["tag"]


def test_a_count_modifier_takes_an_int_whatever_the_rows_own_type_is():
    """`.count_gte` asks HOW MANY, so the written value is a number even
    though the row is a `tag` -- the same shape `.rated` already has for a
    float row. A word here is a refusal at load, not a comparison that
    silently never matches."""
    group = parse_filters({"audio_language.count_gte": 2})
    [written] = predicates(group)
    assert written.operator == "count_gte"
    assert written.values == (2,)

    with pytest.raises(ValueError) as caught:
        parse_filters({"audio_language.count_gte": "two"})
    assert "audio_language" in str(caught.value)


def test_count_operators_compare_the_number_of_tags_the_view_answered():
    view = _view("audio_language", ("en", "fi", "sv"))
    assert evaluate(parse_filters({"audio_language.count_gte": 2}), view) is True
    assert evaluate(parse_filters({"audio_language.count_gte": 4}), view) is False
    assert evaluate(parse_filters({"audio_language.count_gt": 3}), view) is False
    assert evaluate(parse_filters({"audio_language.count_lt": 4}), view) is True
    assert evaluate(parse_filters({"audio_language.count_lte": 3}), view) is True


def test_a_repeated_tag_counts_twice_because_kometa_counts_entries_not_distinct_values():
    """**Kometa's rule, transcribed** (`modules/plex.py:2915-2922`): the
    value a count compares is a flat `extend`-ed list of STREAMS with no
    dedupe, and `.count_*` is `len()` of it (`plex.py:2931-2932`). So the
    count is of ENTRIES as the view answered them, repeats included -- there
    is no `set()` anywhere in this path, and `_as_tags` above already
    preserves duplicates. An item whose two audio tracks are both English is
    "Dual" upstream, and this table agrees with it.

    Written on `genre` as well as `audio_language` because the rule belongs
    to the OPERATOR, not to the two language rows: `_as_tags` is the only
    thing between the view and `len`."""
    assert evaluate(
        parse_filters({"audio_language.count_gte": 2}),
        _view("audio_language", ("en", "en")),
    ) is True
    assert evaluate(
        parse_filters({"genre.count_gte": 3}), _view("genre", ["Horror", "Horror"])
    ) is False
    assert evaluate(
        parse_filters({"genre.count_gte": 2}), _view("genre", ["Horror", "Horror"])
    ) is True


def test_a_bare_string_tag_value_counts_as_one():
    """`_as_tags` already reads a bare string as a one-element list, which is
    what `content_rating` (a tag over a single Plex string) relies on. The
    count follows it rather than special-casing."""
    assert evaluate(
        parse_filters({"content_rating.count_gte": 1}), _view("content_rating", "PG-13")
    ) is True
    assert evaluate(
        parse_filters({"content_rating.count_gte": 2}), _view("content_rating", "PG-13")
    ) is False


def test_the_dual_band_is_expressible_as_one_two_key_condition():
    """The exact shape `language_count`'s Dual overlay uses (T2): a two-key
    mapping is already an AND, because `parse_filters`' `base` defaults to
    `all`. `.count_gte: 2` with `.count_lt: 3` is "exactly two"."""
    dual = {"audio_language.count_gte": 2, "audio_language.count_lt": 3}
    assert evaluate(parse_filters(dual), _view("audio_language", ("en", "fi"))) is True
    assert evaluate(parse_filters(dual), _view("audio_language", ("en",))) is False
    assert evaluate(
        parse_filters(dual), _view("audio_language", ("en", "fi", "sv"))
    ) is False


def test_a_stream_less_item_counts_as_zero_the_way_kometa_counts_it():
    """**Kometa's rule, transcribed** -- and it is the OPPOSITE of what this
    plan's first draft pinned. `plex.py:2931-2932` reduces the collected list
    to a number BEFORE any missing-value test runs:

        test_number = len(test_number) if test_number else 0

    and `plex.py:2934` then asks `util.is_number_filter(test_number, modifier,
    filter_data)`, which returns True to REJECT (`util.py:623-632`). For an
    item with no audio streams that is `is_number_filter(0, ".lt", 3)` ->
    `0 >= 3` -> False -> **the item is KEPT**. So a stream-less item counts as
    ZERO, is compared like any other number, and `audio_language.count_lt: 3`
    lets it through.

    This is why the count branch sits in `_matches` ABOVE the missing-value
    rule rather than inside `_matches_one`: the tag rule ("a positive
    operator excludes a missing value") is real and stays, but a count
    modifier never reaches it, exactly as upstream never reaches its own.
    Immaterial to the two shipped families -- both `language_count` bands
    open at `count_gte: 2`, which zero fails either way -- but it is a real
    behaviour of the one module whose standing claim is oracle-proven Kometa
    parity, so it is transcribed rather than argued."""
    for missing in (None, ()):
        view = _view("audio_language", missing)
        assert evaluate(parse_filters({"audio_language.count_lt": 3}), view) is True
        assert evaluate(parse_filters({"audio_language.count_lte": 0}), view) is True
        assert evaluate(parse_filters({"audio_language.count_gte": 1}), view) is False
        assert evaluate(parse_filters({"audio_language.count_gt": 0}), view) is False


def test_a_count_modifier_is_refused_in_a_plex_search_block():
    """The second dialect. `count_*` is a client-side filter modifier only --
    it is absent from `SEARCH_OPERATORS_BY_TYPE["tag"]`, so a `plex_search`
    writing it is refused naming what the search half does take, exactly the
    way `.contains` on a tag already is."""
    with pytest.raises(ValueError) as caught:
        parse_filters({"audio_language.count_gte": 2}, searching=True)
    message = str(caught.value)
    assert "count_gte" in message
    assert "plex_search" in message


def test_the_count_modifiers_have_no_plexapi_equivalent_and_say_so():
    """`PLEXAPI_EQUIVALENT` must stay total over `OPERATORS_BY_TYPE` (the
    coverage test above asserts it), and the honest key for a count
    comparison is None: plexapi's table is all per-value comparisons and has
    no "how many children" key at all."""
    for operator in ("count_gt", "count_gte", "count_lt", "count_lte"):
        assert PLEXAPI_EQUIVALENT[("tag", operator)] is None


# --- roadmap row 100 sub-phase C2c: the two status rows on the `facts` tier -


def test_tmdb_status_is_a_show_only_tag_row_on_the_facts_tier():
    """EXACT-SET MEMBERSHIP, which is why this is `tag` and not `str`:
    Kometa computes `check_value = discover_status[item.status]` and then
    tests membership of the written set outright -- `(modifier == "" and
    check_value not in filter_data)` (`/modules/tmdb.py:685-693` at the
    pinned digest). A `str` row would default to CONTAINS and make
    `tmdb_status: ended` match nothing and `tmdb_status: end` match
    everything ended, which is the same-name-different-filter class this
    module exists to refuse.

    SHOW-ONLY because `filters_by_type["show"]` carries it and no other
    libtype does (`/modules/builder.py:334-345`). TMDb, never TVDb:
    `tmdb_filters` lists it (`:371`) and `tvdb_status` is a separate name in
    `tvdb_filters` (`:375`) this service does not ship -- roadmap row 100's
    A12 correction, re-confirmed by direct read of the pinned image."""
    row = BY_NAME["tmdb_status"]
    assert (row.type, row.kinds, row.source) == ("tag", ("show",), "facts")
    assert row.filterable is True
    assert row.searchable is False
    assert row.operators == ("eq", "not", "regex",
                             "count_gt", "count_gte", "count_lt", "count_lte")
    assert row.default_operator == "eq"

    view = {"tmdb_status": "returning"}
    assert evaluate(parse_filters({"tmdb_status": "returning"}), view) is True
    assert evaluate(parse_filters({"tmdb_status": "ended"}), view) is False
    assert evaluate(
        parse_filters({"tmdb_status": ["ended", "returning"]}), view
    ) is True, "a list value means ANY-OF, which is how a multi-band config reads"
    assert evaluate(parse_filters({"tmdb_status.not": "ended"}), view) is True


def test_last_episode_aired_is_a_show_only_date_row_whose_bare_form_is_a_window():
    """THE BARE INTEGER IS A WINDOW IN DAYS, confirmed BOTH ways in the
    pinned image and closing the datasources probe's open item 6:
    `util.is_date_filter`'s blank-modifier branch is `threshold_date =
    current_time - timedelta(days=data)` rejecting `value < threshold_date`
    (`/modules/util.py:601-604`), and the SEARCH half renders the same window
    as `>>=-14d`, human-readable "is in the last"
    (`/modules/builder.py:4222-4233`). `_as_days` already reads it
    identically and cites the same lines, so this row needs no operator work
    at all -- it is the first `date` row added since that reading was
    settled."""
    row = BY_NAME["last_episode_aired"]
    assert (row.type, row.kinds, row.source) == ("date", ("show",), "facts")
    assert row.filterable is True
    assert row.searchable is False
    assert row.operators == ("eq", "not", "before", "after")

    now = dt.datetime(2026, 9, 5, 12, 0)
    recent = {"last_episode_aired": dt.date(2026, 8, 30)}   # 6 days ago
    stale = {"last_episode_aired": dt.date(2026, 7, 1)}     # 66 days ago
    window = parse_filters({"last_episode_aired": 14})
    assert evaluate(window, recent, now=now) is True
    assert evaluate(window, stale, now=now) is False
    absolute = parse_filters({"last_episode_aired.after": "2026-08-01"})
    assert evaluate(absolute, recent, now=now) is True
    assert evaluate(absolute, stale, now=now) is False


def test_a_show_with_no_last_air_date_is_excluded_by_every_operator():
    """Upstream and here reach the same verdict by different routes, and
    both are worth stating because the plan's `airing` band rests on it.
    Upstream: `is_date_filter` opens `if value is None: return True`, and
    True is a REJECTION (`/modules/util.py:598-600`). Here:
    `_MISSING_ALWAYS_EXCLUDES` contains `date`, so a missing value is
    excluded under EVERY operator, `.not` included.

    This is also the shape of every row in the library on the upgrade that
    ships C2c -- both columns NULL until the next facts refresh -- so
    "excluded" here is not an edge case, it is the initial condition."""
    now = dt.datetime(2026, 9, 5, 12, 0)
    empty = {"last_episode_aired": None}
    for block in (
        {"last_episode_aired": 14},
        {"last_episode_aired.not": 14},
        {"last_episode_aired.after": "2020-01-01"},
        {"last_episode_aired.before": "2030-01-01"},
    ):
        assert evaluate(parse_filters(block), empty, now=now) is False, block


def test_neither_status_row_is_searchable_and_the_refusal_says_where_it_lives():
    """The third and fourth of the 44 filter-only names to arrive under a
    table row, after `versions` (C2a) and `aspect` (C2b) -- and the first two
    that are filter-only because the value is not Plex's at all rather than
    because Plex spells no search field for a value it holds. The
    cross-reference refusal must therefore still fire: a `plex_search:`
    naming either is refused pointing at `filters:`."""
    for name in ("tmdb_status", "last_episode_aired"):
        with pytest.raises(ValueError) as caught:
            parse_filters({name: "ended"}, searching=True)
        assert "filters:" in str(caught.value), name
        assert name in str(caught.value), name


def test_a_collection_filtering_on_a_facts_row_is_refused_naming_row_156():
    """ADJUDICATION A-2, the refusal-only half. The `facts` tier exists so
    the OVERLAY side can read `item_facts`; it does NOT make a collection
    filterable on it, and the refusal has to say that in a way an operator
    can act on -- where the value is, that an overlay condition CAN use it,
    and which roadmap row owns the question. Deliberately not the `unprobed`
    sentence, which claims a missing PROBE verdict: there is no Plex listing
    that could ever carry a TMDb field, so 'nobody probed the listing' would
    be false rather than cautious."""
    from autoposter.config.schema import CollectionDefinition

    for name, value in (("tmdb_status", "ended"), ("last_episode_aired", 14)):
        with pytest.raises(ValueError) as caught:
            CollectionDefinition(
                title="Ended Shows", builder="plex_all", filters={name: value},
            )
        message = str(caught.value)
        assert name in message, name
        assert "'facts'" in message, name
        assert "row 156" in message, name
        assert "condition:" in message, name


# --- search tail E-1: the twenty show-only rows (roadmap row 173) -------------
#
# Every cell is Kometa's, cited to the vendored driver
# tests/oracle/9b/kometa_build_filter.py.
# FIELD: ``episode_actor`` is dotted already (kometa_build_filter.py:99-133, plex.py:61-95).
# KIND: ``season_collection`` is show-only (kometa_build_filter.py:307-365, plex.py:446-506).
# TYPE, from the category lists: ``episode_title`` is string (kometa_build_filter.py:369).
# ``episode_unmatched`` is boolean (kometa_build_filter.py:371-387).
# ``episode_last_played`` is date (kometa_build_filter.py:390-406).
# ``episode_year`` is year (kometa_build_filter.py:408).
# ``episode_plays`` is number (kometa_build_filter.py:409).
# ``episode_critic_rating`` is float (kometa_build_filter.py:411).
# ``episode_label`` is tag (kometa_build_filter.py:414-435).
# One tuple per row so a reviewer checks the TABLE against the TRANSCRIPTION
# rather than against this file's prose. The order is roadmap row 173's,
# which is also the table's.
FAMILY_E = [
    ("season_collection", "tag", "season.collection"),
    ("season_label", "tag", "season.label"),
    ("episode_collection", "tag", "episode.collection"),
    ("episode_label", "tag", "episode.label"),
    ("episode_title", "str", "episode.title"),
    ("episode_actor", "tag", "episode.actor"),
    ("episode_added", "date", "episode.addedAt"),
    ("episode_air_date", "date", "episode.originallyAvailableAt"),
    ("episode_last_played", "date", "episode.lastViewedAt"),
    ("episode_plays", "int", "episode.viewCount"),
    ("episode_user_rating", "float", "episode.userRating"),
    ("episode_critic_rating", "float", "episode.rating"),
    ("episode_audience_rating", "float", "episode.audienceRating"),
    ("episode_year", "int", "episode.year"),
    ("episode_unplayed", "bool", "episode.unwatched"),
    ("episode_duplicate", "bool", "episode.duplicate"),
    ("episode_progress", "bool", "episode.inProgress"),
    ("episode_unmatched", "bool", "episode.unmatched"),
    ("show_unmatched", "bool", "show.unmatched"),
    ("unplayed_episodes", "bool", "show.unwatchedLeaves"),
]


@pytest.mark.parametrize(
    ("name", "value_type", "field"), FAMILY_E, ids=[row[0] for row in FAMILY_E]
)
def test_a_family_e_row_is_typed_and_routed_as_kometa_routes_it(name, value_type, field):
    """The ``network`` shape, twenty times: a dotted field that
    ``search_translation`` already scopes, so ``show_translation`` never sees
    it and the two field columns are EQUAL rather than the second being None;
    show-only in both kind columns; unfilterable; search-only. ``field_for``
    refuses a movie library rather than falling back, because a movie library
    has no episodes. For nineteen of the twenty that is Kometa's own refusal
    (kometa_build_filter.py:919,
    ``is_movie and final_attr in show_only_searches``); ``episode_actor`` is
    in neither of Kometa's kind lists and is show-only here by this table's
    own judgement -- a DECLARED DIVERGENCE, argued on its row note."""
    row = BY_NAME[name]
    assert row.type == value_type
    assert row.search_field == field
    assert row.show_search_field == field
    assert row.field_for("show") == field
    assert row.search_kinds == ("show",)
    assert row.kinds == ("show",)
    assert row.filterable is False
    assert row.source == "search-only"
    with pytest.raises(ValueError):
        row.field_for("movie")


def test_episode_plays_takes_the_ranges_only_and_episode_year_the_full_int_set():
    """The ``plays``/``year`` split, repeated one level down. ``episode_plays``
    is a ``number_attribute`` only (kometa_build_filter.py:409) and so takes
    ``number_modifiers`` alone -- no bare form, no ``.not``
    (show_only_searches lists exactly ``.gt``/``.gte``/``.lt``/``.lte``,
    :335-338); ``episode_year`` is a ``year_attribute`` (:408) and reaches
    ``tag_modifiers`` as well, so its bare form and ``.not`` survive
    (:354-359). Same two rows as ``plays``/``year``, same
    ``SEARCH_OPERATORS_EXCLUDED`` mechanism."""
    assert BY_NAME["episode_plays"].search_operators == ("gt", "gte", "lt", "lte")
    assert BY_NAME["episode_year"].search_operators == (
        "eq", "not", "gt", "gte", "lt", "lte",
    )
    with pytest.raises(ValueError) as error:
        parse_filters({"episode_plays": 3}, field="params", searching=True)
    assert "episode_plays" in str(error.value)
    parse_filters({"episode_year": 2010}, field="params", searching=True)


def test_a_family_e_name_in_a_filters_block_is_pointed_at_plex_search():
    """The D2c cross-reference, for the family that makes it matter most:
    an operator who writes ``episode_title`` in a ``filters:`` block is told
    it is a plex_search attribute and where to move it, rather than being
    told the name is unknown."""
    with pytest.raises(ValueError) as error:
        parse_filters({"episode_title": "Pilot"}, field="filters")
    message = str(error.value)
    assert "episode_title" in message
    assert "plex_search" in message
