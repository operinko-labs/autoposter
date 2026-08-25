"""THE KOMETA ORACLE -- phase 9a's acceptance.

The filter table (``src/autoposter/collections/filters.py``) is a
*transcription* of Kometa's filter semantics, and the module says in its own
docstring why that is the dangerous kind of guess: a filter with the wrong
meaning still loads, still runs, and produces a full, plausible, wrong
collection. Nothing about the earlier tasks' tests can catch that, because they
assert the transcription against itself.

This file asserts it against Kometa. One library's worth of items, two filter
configs, and member lists produced by **Kometa's own filter code** -- fetched,
transcribed standalone, run, and pinned below as data. Ours must reproduce
them exactly, order included.

## Where Kometa's member lists came from

Kometa publishes no filter fixtures, so the same procedure the 8c award oracle
used applies: fetch the code, run it, pin the answer.

| What | Source (fetched 2026-08-25) |
| --- | --- |
| The filter primitives | ``modules/util.py`` -- ``is_date_filter`` (:598-620), ``is_number_filter`` (:623-632), ``is_string_filter`` (:639-656), ``validate_date`` (:299-307), ``validate_regex`` (:310-323) |
| The per-attribute dispatch | ``modules/plex.py`` -- ``check_filter`` (:2764-2967), ``check_filters`` (:2753-2762), ``split`` (:2735-2747), ``attribute_translation`` (:196-225), ``get_search_choices`` (:1300-1316) |
| The OR across blocks, and value validation | ``modules/builder.py`` -- ``check_filters`` (:4674-4754), ``validate_attribute`` (:4297-4460), the attribute-category lists (:377-447) |
| The version | ``VERSION`` -- 2.4.8 |

All from ``https://raw.githubusercontent.com/Kometa-Team/Kometa/master``. The
oracle script is reproduced verbatim in ``.superpowers/sdd/task-4-report.md``;
it imports **nothing** from this repository -- not ``filters``, not
``filter_values``, not the fixtures' loader. It does import ``plexapi``,
because Kometa reads its item attributes through plexapi and an oracle that
read them some other way would be testing a different program.

Two things the transcription models rather than copies, both stated so a
reader can weigh them:

- **Kometa reloads every item before filtering it** (``plex.py:2785``). The
  oracle's server answers that reload with the same element the listing
  carried. That is sound for these nine attributes precisely because Task 2's
  probe established it -- they are listing-resident and agree with
  ``/library/metadata`` -- and it is the reason the other six tier-1
  attributes are deferred rather than filtered here.
- **Kometa resolves a tag filter's written values against the library's own
  tag vocabulary** at validation time (``get_search_choices`` keys the lookup
  on both ``title`` and ``title.lower()``, so the case-insensitivity an
  operator sees lives there rather than in the comparison, which is a bare set
  intersection). The configs below are written in the library's own spelling,
  which makes that lookup the identity -- so the oracle compares filter
  semantics and not Kometa's vocabulary validation, which this service has no
  equivalent of and does not claim one.

## What the first run found

Red, in four places, every one a transcription judgement this module had made
without a source in front of it. Each was adjudicated in Kometa's favour and
OUR code changed; the details, with Kometa's line references, are on the code
they fixed:

| Divergence | Ours, before | Kometa | Fixed in |
| --- | --- | --- | --- |
| A bare date window had an upper bound at today | future-dated item dropped | one-sided (``util.py:601-604``) | ``filters._matches_one`` |
| ``duration`` was rounded to whole minutes | 149.7-min film failed ``.lt: 150`` | exact quotient (``plex.py:2923-2926``) | ``filter_values._duration_minutes`` |
| ``.regex`` compiled with ``IGNORECASE`` | six extra members | no flags, three call sites | ``filters._as_regex`` |
| Dates compared as calendar dates | item added 09:15 that day failed ``.after`` | compares the moment (``util.py:299-307``, ``builder.py:1165``) | ``filters._as_moment`` |

A fifth came out of reading the source rather than the run and is a refusal
rather than a fix: Kometa accepts ``.gt``/``.gte``/``.lt``/``.lte`` on a date
and rewrites all four to the strict ``.after``/``.before`` (``plex.py:2735-2747``),
so our inclusive ``.gte``/``.lte`` were the same spelling with a different
meaning. They are gone; see ``filters.OPERATORS_BY_TYPE``.

## The library

``tests/fixtures/collections/filter_oracle_listing.xml`` is 120 movies in the
shape of a Plex section listing. The presence and absence of each attribute
follows Task 2's probe of the production server
(``.superpowers/sdd/task-2-report.md`` section 1.7), with the rare-absence
classes deliberately over-represented, and a handful of items pinned so that
each adjudicated rule is actually decided by the operator it belongs to rather
than by some earlier predicate. ``test_the_library_mirrors_the_probes_shapes``
holds that derivation to the numbers, and the coverage tests below name which
item exercises which rule.
"""
import datetime as dt
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from plexapi.video import Movie

from autoposter.collections.filter_values import PlexItemView
from autoposter.collections.filters import evaluate, parse_filters

LISTING_PATH = Path(__file__).parent / "fixtures" / "collections" / "filter_oracle_listing.xml"

# The section listing's own path, so every item is a PARTIAL object -- what the
# engine's single ``section.all()`` walk hands the filter stage, and what makes
# the zero-request assertion below mean something.
LISTING = "/library/sections/1/all"

# The run moment. Pinned rather than "now", because two of the three rules
# under test are relative to it. The oracle was driven with the same value as
# its ``current_time`` (Kometa's ``builder.py:1165``).
NOW = dt.datetime(2026, 8, 25, 14, 30, 0)


# --- the configs -------------------------------------------------------------
#
# Each is written twice: once in this service's spelling and once in Kometa's,
# so the reader can check that the oracle was asked the same question. The
# shapes are the same because the vocabulary was transcribed from Kometa's --
# which is the claim under test, not an assumption of it.

# Six attribute families in one AND block: tag (content_rating), str (studio,
# negated), float (audience_rating, range), int (year, range), duration
# (range), date (release, the relative form). Kometa's equivalent is one
# filter dict, whose keys it ANDs.
CONFIG_A = {
    "content_rating": ["PG-13", "R"],
    "studio.not": "Hallmark Entertainment",
    "audience_rating.gte": 6.5,
    "year.gte": 1990,
    "duration.lt": 150,
    "release": 10950,
}
KOMETA_CONFIG_A = """
filters:
  content_rating: [PG-13, R]
  studio.not: Hallmark Entertainment
  audience_rating.gte: 6.5
  year.gte: 1990
  duration.lt: 150
  release: 10950
"""

# An OR of two AND blocks, which is the shape Kometa's ``filters:`` has
# natively: it takes a LIST of dicts, ANDs each dict's keys and ORs the dicts
# (``builder.py:4683-4753``). Ours writes that as ``any:`` over a list of
# blocks, so the two are one-to-one and the transcription does not have to
# distribute anything.
CONFIG_B = {
    "any": [
        {"studio.regex": "pictures$", "resolution": "4k"},
        {"content_rating.not": ["R", "NC-17"], "added.after": "2026-06-01"},
    ]
}
KOMETA_CONFIG_B = """
filters:
  - studio.regex: pictures$
    resolution: 4k
  - content_rating.not: [R, NC-17]
    added.after: 2026-06-01
"""

# --- Kometa's answers, pinned ------------------------------------------------
#
# Produced by the oracle script, copied out of its stdout verbatim. Rating
# keys, in the library's own order. Nothing here was computed by this
# repository.
ORACLE_CONFIG_A = [
    100006, 100007, 100008, 100012, 100013, 100023, 100053,
    100060, 100067, 100071, 100079, 100086, 100091,
]
ORACLE_CONFIG_B = [100002, 100011, 100015, 100019, 100020, 100046, 100051]


class NoRequestsServer:
    """Any Plex request during the oracle is a failure, not a slow test: the
    filter stage's whole contract is that evaluating a filter over a library
    costs nothing beyond the walk the engine already paid for."""

    def __init__(self):
        self.calls = []

    def query(self, key, *args, **kwargs):
        self.calls.append(key)
        raise AssertionError(f"the filter stage made a Plex request: {key}")


@pytest.fixture(scope="module")
def listing():
    root = ET.parse(LISTING_PATH).getroot()
    server = NoRequestsServer()
    items = [Movie(server, element, initpath=LISTING) for element in root]
    return items, server


def _members(items, config):
    group = parse_filters(config)
    return [int(item.ratingKey) for item in items if evaluate(group, PlexItemView(item), now=NOW)]


# --- the oracle --------------------------------------------------------------


@pytest.mark.parametrize(
    "config,expected",
    [(CONFIG_A, ORACLE_CONFIG_A), (CONFIG_B, ORACLE_CONFIG_B)],
    ids=["config-a", "config-b"],
)
def test_our_members_are_kometas_members(listing, config, expected):
    """Exact, order included. A difference of one item is a filter that means
    something different in the two systems, which is the whole risk 9a set out
    to close -- and 9b will translate this same vocabulary into a Plex search,
    so a disagreement here would become a collection that means one thing
    normal and another smart."""
    items, _ = listing

    assert _members(items, config) == expected


def test_the_whole_oracle_run_costs_no_plex_requests(listing):
    """The positive half of the reload budget, over a real library rather than
    a handful of fixtures: 120 partial items, two configs, nine attributes,
    zero requests. ``NoRequestsServer`` raises rather than answering, so a
    reload would surface as a failure in the tests above too -- this one names
    it."""
    items, server = listing
    _members(items, CONFIG_A)
    _members(items, CONFIG_B)

    assert server.calls == []


# --- the derivation ----------------------------------------------------------


def test_the_library_mirrors_the_probes_shapes(listing):
    """The fixture's presence/absence classes against the probe's own counts
    (``.superpowers/sdd/task-2-report.md`` 1.7). Deliberately
    over-represented, not proportional: ``rating`` was absent on 1 movie of
    1955 and ``contentRating`` on 14, which at 120 items rounds to nothing at
    all -- and a missing-value rule the data never exercises is a rule nobody
    tested. The multi-version count is the one that IS proportional, because
    the probe's histogram ({1:1905, 2:46, 3:4}) is the real distribution of
    file versions rather than a truncation.
    """
    items, _ = listing
    present = Counter()
    for item in items:
        view = PlexItemView(item)
        for attribute in ("year", "audience_rating", "critic_rating", "content_rating",
                          "added", "release", "duration", "studio", "resolution"):
            if view.get(attribute) is not None:
                present[attribute] += 1

    assert len(items) == 120
    assert present["year"] == 120           # probe: 1955/1955
    assert present["added"] == 120          # probe: 1955/1955
    assert present["release"] == 120        # probe: 1955/1955
    assert present["duration"] == 120       # probe: 1955/1955
    assert present["resolution"] == 120     # probe: <Media> on 1955/1955
    assert present["audience_rating"] == 118    # probe: movies 1955/1955, shows 281/284
    assert present["critic_rating"] == 117      # probe: 1954/1955
    assert present["content_rating"] == 117     # probe: 1941/1955
    # Two items have no studio attrib and one has an empty one -- the second
    # shape is present-but-blank, which reads as a value here and as MISSING to
    # the filter (``filters._is_missing``), so it is counted separately rather
    # than folded in.
    assert present["studio"] == 118             # probe: 1950/1955
    blank = [item for item in items if PlexItemView(item).get("studio") == ""]
    assert len(blank) == 1

    versions = Counter(len(PlexItemView(item).get("resolution")) for item in items)
    assert versions == {1: 115, 2: 4, 3: 1}     # probe: {1: 1905, 2: 46, 3: 4}


# --- coverage: which item decides which adjudicated rule ---------------------
#
# The oracle above is one equality per config. These name the items that make
# it a real comparison, so that a regression in any single adjudication reads
# as "the 149.7-minute film left the collection" rather than "a list changed".


def test_the_runtime_edge_is_decided_by_the_exact_quotient(listing):
    """``duration.lt: 150`` over a 149.7-minute film and a 150.3-minute one.
    Rounding put both at 150 and dropped the first; the exact quotient keeps
    it, which is Kometa's answer."""
    items, _ = listing
    by_key = {int(item.ratingKey): item for item in items}

    assert PlexItemView(by_key[100007]).get("duration") == pytest.approx(149.7)
    assert PlexItemView(by_key[100038]).get("duration") == pytest.approx(150.3)
    assert 100007 in ORACLE_CONFIG_A
    assert 100038 not in ORACLE_CONFIG_A


def test_the_relative_window_has_no_upper_bound(listing):
    """Two items Plex dates in the FUTURE, inside a ``release: 10950`` window.
    An upper bound at today dropped both; Kometa's one-sided comparison keeps
    them."""
    items, _ = listing
    by_key = {int(item.ratingKey): item for item in items}

    for key in (100023, 100067):
        assert PlexItemView(by_key[key]).get("release") > NOW
        assert key in ORACLE_CONFIG_A


def test_the_added_boundary_is_decided_at_the_moment_not_the_date(listing):
    """Three items added ON 2026-06-01, at 00:05, 09:15 and 22:40, against
    ``added.after: 2026-06-01``. A calendar-date comparison dropped all three;
    Kometa compares against that day's midnight and keeps them.

    This also guards the fixture's timezone assumption. plexapi converts
    ``addedAt`` with ``datetime.fromtimestamp`` and no ``tz``, so the wall time
    an epoch reads back as is the RUNNER's; the fixture's epochs are UTC,
    which is the container the suite runs in. On a runner in another zone this
    assertion fails first and says why, instead of the member lists differing
    for a reason that looks like a filter bug.
    """
    items, _ = listing
    by_key = {int(item.ratingKey): item for item in items}
    clocks = {100051: (0, 5), 100002: (9, 15), 100019: (22, 40)}

    for key, (hour, minute) in clocks.items():
        assert PlexItemView(by_key[key]).get("added") == dt.datetime(2026, 6, 1, hour, minute)
        assert key in ORACLE_CONFIG_B


def test_a_regex_is_case_sensitive_like_kometas(listing):
    """``studio.regex: pictures$`` matches nothing, because every studio in
    the library that ends in the word spells it ``Pictures``. Under
    ``IGNORECASE`` it matched six items, all of which had to be 4k as well to
    reach config B's first block -- so the block contributes NO members and
    every member of ``ORACLE_CONFIG_B`` comes from the second block.

    The control is item 100029: ``Universal Pictures``, 4k, and out."""
    items, _ = listing
    by_key = {int(item.ratingKey): item for item in items}

    assert PlexItemView(by_key[100029]).get("studio") == "Universal Pictures"
    assert "4k" in PlexItemView(by_key[100029]).get("resolution")
    assert 100029 not in ORACLE_CONFIG_B
    # The same pattern with an inline flag -- the spelling that still works --
    # does match it, which is what makes the point above a semantic and not a
    # broken fixture.
    assert evaluate(
        parse_filters({"studio.regex": "(?i)pictures$"}), PlexItemView(by_key[100029]), now=NOW
    ) is True


def test_the_missing_value_rule_is_exercised_by_the_library(listing):
    """The brief's first target, over real data rather than a case table.

    ``studio.not: Hallmark Entertainment`` is in config A, and three items have
    no studio to compare -- ``.not`` on a ``str`` KEEPS them (they clear that
    predicate and are decided by the others). ``audience_rating.gte`` is in the
    same block, and the two unrated items are dropped by it whatever else they
    have, because a missing number is excluded under every operator. Both
    behaviours are Kometa's, and both are visible in the pinned list:
    ``100012`` is a member with no studio at all; ``100017`` and ``100061``
    are unrated and absent.
    """
    items, _ = listing
    by_key = {int(item.ratingKey): item for item in items}

    assert PlexItemView(by_key[100012]).get("studio") is None
    assert 100012 in ORACLE_CONFIG_A

    for key in (100017, 100061):
        assert PlexItemView(by_key[key]).get("audience_rating") is None
        assert key not in ORACLE_CONFIG_A
        # ... and not because of some other predicate: negating the rating
        # predicate alone does not rescue it either, which is the point of
        # "excluded under EVERY operator".
        assert evaluate(
            parse_filters({"audience_rating.not": 6.5}), PlexItemView(by_key[key]), now=NOW
        ) is False
