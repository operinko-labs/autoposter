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
    evaluate,
    parse_filters,
)

# The date every date case is measured against. Pinned rather than
# ``date.today()`` so the "in the last N days" boundaries below are arithmetic
# a reader can check, and so the suite does not change meaning overnight.
TODAY = dt.date(2026, 8, 25)


def _view(attribute: str, value: object) -> dict:
    """The item view for one attribute. ``None`` means the item has no value."""
    return {attribute: value}


# --- the table ---------------------------------------------------------------


def test_the_table_holds_exactly_the_tier_one_rows():
    """The row list is the plan's, in the plan's order (roadmap.md:538-551), and
    the count is the transcription's checksum: a row lost, duplicated or renamed
    in an edit shows up here rather than as a filter an operator writes and
    nothing applies. Pinning the order too means the table stays readable
    against the roadmap it came from rather than drifting into edit order."""
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
    reviewer checks the table against, and the numbers Task 2's probe moves:
    the seven ``probe`` rows are exactly the ones whose data may not be in the
    listing, and each becomes ``listing`` or ``tier2-deferred``."""
    by_type = {kind: [r.name for r in FILTER_ATTRIBUTES if r.type == kind] for kind in VALUE_TYPES}
    by_source = {t: [r.name for r in FILTER_ATTRIBUTES if r.source == t] for t in SOURCE_TIERS}

    assert {k: len(v) for k, v in by_type.items()} == {
        "tag": 8,
        "str": 1,
        "int": 1,
        "float": 2,
        "date": 2,
        "duration": 1,
    }
    assert by_source["listing"] == [
        "year",
        "audience_rating",
        "critic_rating",
        "content_rating",
        "added",
        "release",
        "duration",
        "studio",
    ]
    assert by_source["probe"] == [
        "genre",
        "resolution",
        "audio_language",
        "subtitle_language",
        "label",
        "network",
        "collection",
    ]
    assert by_source["tier2-deferred"] == []


def test_item_kinds_are_movie_show_or_both():
    movie_only = sorted(r.name for r in FILTER_ATTRIBUTES if r.kinds == ("movie",))
    show_only = sorted(r.name for r in FILTER_ATTRIBUTES if r.kinds == ("show",))

    assert movie_only == ["audio_language", "resolution", "subtitle_language"]
    assert show_only == ["network"]
    assert len([r for r in FILTER_ATTRIBUTES if r.kinds == ("movie", "show")]) == 11


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
    ["genre.gt", "studio.gte", "added.gt", "duration.regex", "content_rating.before"],
    ids=["tag-gt", "str-gte", "date-gt", "duration-regex", "tag-before"],
)
def test_each_type_refuses_the_operators_it_does_not_have(key):
    """``.gt`` on a date in particular: ``.before``/``.after`` are Kometa's
    spellings and accepting a second one for the same meaning is how two
    vocabularies start."""
    with pytest.raises(ValueError, match=re.escape(f"filters.{key}")):
        parse_filters({key: "x"})


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
    ("tag", "regex"): [
        (["Science Fiction"], "^Science", True),
        (["Science Fiction"], "^Fiction", False),
        (["Science Fiction"], "FICTION$", True),
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
        ("Warner Bros.", "^warner", True),
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
    # -- duration: the view is minutes as an INT, ROUNDED (see ItemView.get's
    #    docstring); the config is Kometa's minutes, plus the written forms an
    #    operator reaches for -------------------------------------------------
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
    # -- date: the bare form is a window in days, bounded at today; the rest
    #    are absolute -----------------------------------------------------
    ("date", "eq"): [
        (TODAY, 0, True),
        (dt.date(2026, 8, 20), 30, True),
        (dt.date(2026, 7, 26), 30, True),
        (dt.date(2026, 7, 25), 30, False),
        (dt.datetime(2026, 8, 20, 13, 5), 30, True),
        (dt.date(2026, 9, 1), 30, False),
        (None, 30, False),
    ],
    ("date", "not"): [
        (dt.date(2026, 7, 26), 30, False),
        (dt.date(2026, 7, 25), 30, True),
        (dt.date(2026, 9, 1), 30, True),
        (None, 30, False),
    ],
    ("date", "before"): [
        (dt.date(2024, 1, 1), dt.date(2024, 1, 2), True),
        (dt.date(2024, 1, 1), dt.date(2024, 1, 1), False),
        (dt.date(2024, 1, 2), dt.date(2024, 1, 1), False),
        (dt.date(2024, 1, 1), "2024-01-02", True),
        (dt.date(2026, 8, 24), "today", True),
        (TODAY, "today", False),
        (None, dt.date(2024, 1, 1), False),
    ],
    ("date", "after"): [
        (dt.date(2024, 1, 2), dt.date(2024, 1, 1), True),
        (dt.date(2024, 1, 1), dt.date(2024, 1, 1), False),
        (dt.date(2024, 1, 1), dt.date(2024, 1, 2), False),
        (dt.date(2026, 8, 26), "today", True),
        (TODAY, "today", False),
        (None, dt.date(2024, 1, 1), False),
    ],
    ("date", "gte"): [
        (dt.date(2024, 1, 1), dt.date(2024, 1, 1), True),
        (dt.date(2024, 1, 2), dt.date(2024, 1, 1), True),
        (dt.date(2023, 12, 31), dt.date(2024, 1, 1), False),
        (None, dt.date(2024, 1, 1), False),
    ],
    ("date", "lte"): [
        (dt.date(2024, 1, 1), dt.date(2024, 1, 1), True),
        (dt.date(2023, 12, 31), dt.date(2024, 1, 1), True),
        (dt.date(2024, 1, 2), dt.date(2024, 1, 1), False),
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

    assert evaluate(group, _view(attribute, have), today=TODAY) is expected


def test_every_operator_has_a_case_set_including_a_missing_value():
    """The structural half of the claim "every operator is table-driven".

    A ``(type, operator)`` pair with no case-set fails here rather than
    quietly shipping untested, and so does a case-set that forgot the
    missing-value rule -- which is a table-level invariant every operator has
    to honour, not a per-operator detail someone may reasonably skip.
    """
    pairs = {(t, op) for t, ops in OPERATORS_BY_TYPE.items() for op in ops}

    assert set(OPERATOR_CASES) == pairs
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


def test_a_datetime_is_compared_as_its_own_calendar_date():
    """Plex writes ``addedAt`` as a naive datetime in the server's own clock,
    and every date operator here is date-granular. The time is dropped."""
    group = parse_filters({"added.after": dt.date(2024, 1, 1)})

    assert evaluate(group, _view("added", dt.datetime(2024, 1, 1, 23, 59)), today=TODAY) is False
    assert evaluate(group, _view("added", dt.datetime(2024, 1, 2, 0, 0)), today=TODAY) is True


def test_an_aware_datetime_keeps_its_own_calendar_date_and_is_not_converted():
    """The convention, stated: an aware value is reduced to the calendar date
    it already reads as, with no conversion to UTC or to this machine's zone.
    Converting would make the same library filter differently depending on
    where the run happens, which is not a property a collection should have.

    2026-08-20 23:00-08:00 is 2026-08-21 07:00 UTC. It filters as the 20th.
    """
    aware = dt.datetime(2026, 8, 20, 23, 0, tzinfo=dt.timezone(dt.timedelta(hours=-8)))
    group = parse_filters({"added.before": dt.date(2026, 8, 21)})

    assert evaluate(group, _view("added", aware), today=TODAY) is True


def test_today_resolves_against_the_run_date_not_the_wall_clock():
    group = parse_filters({"release.before": "today"})

    assert evaluate(group, _view("release", dt.date(2026, 8, 24)), today=TODAY) is True
    assert evaluate(group, _view("release", dt.date(2026, 8, 25)), today=TODAY) is False


# --- nesting -----------------------------------------------------------------


def test_an_all_block_needs_every_child():
    group = parse_filters({"genre": "Horror", "year.gte": 2000})

    assert evaluate(group, {"genre": ["Horror"], "year": 2001}, today=TODAY) is True
    assert evaluate(group, {"genre": ["Horror"], "year": 1999}, today=TODAY) is False
    assert evaluate(group, {"genre": ["Comedy"], "year": 2001}, today=TODAY) is False


def test_an_any_block_needs_one_child():
    group = parse_filters({"any": {"genre": "Horror", "year.gte": 2000}})

    assert evaluate(group, {"genre": ["Comedy"], "year": 2001}, today=TODAY) is True
    assert evaluate(group, {"genre": ["Horror"], "year": 1999}, today=TODAY) is True
    assert evaluate(group, {"genre": ["Comedy"], "year": 1999}, today=TODAY) is False


def test_any_of_all_of_nests_both_ways():
    """Horror from this century, or anything at all labelled ``keep``."""
    group = parse_filters(
        {"any": [{"genre": "Horror", "year.gte": 2000}, {"label": "keep"}]}
    )

    assert evaluate(group, {"genre": ["Horror"], "year": 2001, "label": []}, today=TODAY) is True
    assert evaluate(group, {"genre": ["Horror"], "year": 1999, "label": []}, today=TODAY) is False
    assert evaluate(group, {"genre": ["Comedy"], "year": 1999, "label": ["keep"]}, today=TODAY) is True


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

    assert evaluate(group, {**horror, "year": 2005}, today=TODAY) is True
    assert evaluate(group, {**horror, "year": 2015}, today=TODAY) is False
    assert evaluate(group, {**horror, "year": 2015, "label": ["keep"]}, today=TODAY) is True
    assert evaluate(group, {"genre": ["Comedy"], "year": 2005, "label": []}, today=TODAY) is False


def test_evaluate_defaults_today_to_the_current_date():
    """``today`` is a keyword with a default so callers that have no run date
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
        evaluate(group, {"year": "2001"}, today=TODAY)


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
