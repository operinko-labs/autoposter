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
    evaluate,
    parse_filters,
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
    tiers ever disagree."""
    by_type = {kind: [r.name for r in FILTER_ATTRIBUTES if r.type == kind] for kind in VALUE_TYPES}
    by_source = {t: [r.name for r in FILTER_ATTRIBUTES if r.source == t] for t in SOURCE_TIERS}

    assert {k: len(v) for k, v in by_type.items()} == {
        "tag": 8,
        "str": 1,
        "int": 2,
        "float": 2,
        "date": 3,
        "duration": 1,
        "bool": 2,
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
    ]
    assert by_source["tier2-deferred"] == [
        "genre",
        "audio_language",
        "subtitle_language",
        "label",
        "network",
        "collection",
    ]
    assert by_source["probe"] == []
    # 9b's two, and they are two tiers rather than one because the REASONS
    # differ: ``unprobed`` means Kometa filters on it and 9a never asked
    # whether the listing carries it; ``search-only`` means Kometa has no
    # filter of that name at all, so there is nothing to ask.
    assert by_source["unprobed"] == ["plays", "last_played"]
    assert by_source["search-only"] == ["unplayed", "progress"]


def test_item_kinds_are_movie_show_or_both():
    movie_only = sorted(r.name for r in FILTER_ATTRIBUTES if r.kinds == ("movie",))
    show_only = sorted(r.name for r in FILTER_ATTRIBUTES if r.kinds == ("show",))

    assert movie_only == [
        "audio_language", "progress", "resolution", "subtitle_language", "unplayed",
    ]
    assert show_only == ["network"]
    assert len([r for r in FILTER_ATTRIBUTES if r.kinds == ("movie", "show")]) == 13


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
    # The one deliberate gap: "in the last N days" is a relative window and
    # plexapi's table is all absolute comparisons.
    unmapped = [pair for pair, key in PLEXAPI_EQUIVALENT.items() if key is None]
    assert unmapped == [("date", "eq"), ("date", "not")]


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
    """
    from collections import Counter

    from autoposter.collections.filters import BY_NAME, FILTER_ATTRIBUTES

    assert Counter(row.search_kinds for row in FILTER_ATTRIBUTES) == {
        ("movie", "show"): 15, ("movie",): 3, ("show",): 1,
    }
    assert BY_NAME["resolution"].kinds == ("movie",)
    assert BY_NAME["resolution"].search_kinds == ("movie", "show")
    assert BY_NAME["duration"].kinds == ("movie", "show")
    assert BY_NAME["duration"].search_kinds == ("movie",)


def test_every_row_is_searchable_and_seventeen_are_filterable():
    """The set arithmetic, pinned so it cannot rot silently.

    Kometa's search vocabulary is 55 non-music attributes and its filter
    vocabulary is 70 names; this table covers 19 of the first and 17 of the
    second. The module docstring carries the full derivation.
    """
    from autoposter.collections.filters import (
        FILTERABLE_ATTRIBUTES,
        FILTER_ATTRIBUTES,
        SEARCHABLE_ATTRIBUTES,
    )

    assert all(row.searchable for row in FILTER_ATTRIBUTES)
    assert len(SEARCHABLE_ATTRIBUTES) == 19
    assert len(FILTERABLE_ATTRIBUTES) == 17
    assert set(SEARCHABLE_ATTRIBUTES) - set(FILTERABLE_ATTRIBUTES) == {
        "unplayed", "progress",
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
    "int": "year",
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
    """
    negative = {"not", "isnot"}
    always_excluded = {"int", "float", "date", "duration"}
    for (value_type, operator), cases in OPERATOR_CASES.items():
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
    """Two vocabularies, two lists. ``aspect`` is one of the 44 Kometa filter
    names with no Plex search field, and no row names it yet, so both blocks
    answer "unknown" -- but each names ITS OWN vocabulary, not the table."""
    with pytest.raises(ValueError) as error:
        parse_filters({"aspect": "1.78"}, searching=True)
    message = str(error.value)
    assert "aspect" in message
    assert "plex_search" in message
    assert "unplayed" in message        # a searchable name is offered
    assert "plays" in message

    with pytest.raises(ValueError) as error:
        parse_filters({"aspect": "1.78"})
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


def test_a_search_refuses_regex_and_says_why():
    with pytest.raises(ValueError) as error:
        parse_filters({"genre.regex": "^Hor"}, searching=True)
    message = str(error.value)
    assert ".regex" in message
    assert "filters:" in message


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
