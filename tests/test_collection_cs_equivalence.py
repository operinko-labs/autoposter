"""The mandatory equivalence proof for the Common Sense grammar unification.

Because the golden gate
records ``updated_filters`` -- the plexapi CALL SHAPE the port removes -- it
stops covering the changed cell, so the proof that the port does not move a
single item is this file and not that fixture.

Every bucket shape in the SHIPPED table, against two library types: a bucket
that is its own key alone, one whose key the library does not hold and whose
addons it does, one with several present addons, one that matches nothing at
all, and the ``other`` bucket's complement. For each: plexapi's
real ``_buildSearchKey`` and this engine's real ``build_search_url``, decoded
and evaluated to member sets over a fixture library, asserted equal.

## What this file proves

For EVERY rating in the shipped table, the two grammars select identical items
-- which is claim C2, that an ``any:`` base reproduces plexapi's comma-joined
OR.

"Every" is recent, and the history is the point of the file. As first written it
held only for the URL-SAFE ratings, and the shipped table carries ten that are
not (``G - All Ages``, ``12+``, ``R - 17+ (violence & profanity)`` and the
rest). plexapi percent-encodes its values; ``build_search_url`` inserted a
resolved TAG value raw -- the ``str`` branch quoted, the tag branch did not --
which had never mattered because every tag key 9b resolved was an opaque numeric
id, and ``content_rating`` is the one tag family whose Plex key IS its title.
Three ``xfail(strict=True)`` tests pinned that divergence and were built to
retire themselves: the moment the tag branch quoted they XPASSed, strict turned
the XPASS into a failure, and the fix could not land without deleting the
markers and getting a real green. That is what happened
(``search_url.py``'s tag branch, phase 10a-2 task 3.5); the three tests are
still at the bottom of this file, now unmarked and passing on their own terms,
and a fourth -- which measured the broken shape and was deliberately unmarked so
it would RED on the same fix -- was deleted with them.

**The shape it took was not an empty collection but a SILENT SUBSET.** Every one
of the ten co-occurs with safe ratings in the shipped table, and a bucket's terms
sit in an ``or=1`` group, so the working terms still matched: what shipped was a
plausible-but-short collection. The mixed-bucket test at the bottom is that
bucket, now asserting equality where it once asserted the subset.

**The divergence did not depend on how the server decodes ``+``.** Premise P5
(``member_sets.py``) is live-measured -- the server folds ``+`` to a space --
but it only decided WHICH six of the ten moved and whose side was at fault.
Under the other model the same ten still diverged with the OLD side at fault,
and the ``&`` case is URL syntax rather than server interpretation, so it held
under both. There was no decoding model under which the two grammars agreed,
which is why ``quote()`` -- emitting ``%2B``/``%20``/``%26``, identical on
decode under either -- is a premise-independent fix.

## The falsifiability of the whole thing

Three separate constructions, because "old == new" on its own passes whenever
both sides are wrong in the same way:

- ``test_the_member_sets_are_the_ones_written_here`` pins LITERAL expected sets
  for seven named buckets. Nothing derives them; they were written by hand off
  the shipped table.
- every case asserts the ABSOLUTE claim (the old query selects exactly the
  bucket's ratings, and never the control item) before the relative one.
- ``test_an_all_base_query_diverges_and_the_machinery_says_so`` builds a query
  that is deliberately wrong and asserts the comparison NOTICES.
"""
import importlib.util
from pathlib import Path

import pytest

from autoposter.collections.buckets import derive_buckets, load_table

ORACLE = Path(__file__).parent / "oracle" / "10a2"


def _driver(name):
    """Load one oracle driver by PATH, the way 9b's gate loads its own
    (``tests/test_collection_search_oracle.py:276``). ``tests/oracle`` is not a
    package -- deliberately, since a module name starting with a digit cannot
    be imported -- so a path load is the only way in, and it is the established
    one."""
    spec = importlib.util.spec_from_file_location(
        "cs_equivalence_" + name, ORACLE / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


member_sets = _driver("member_sets")
ours_side = _driver("ours_side")
plexapi_side = _driver("plexapi_side")

TABLE = load_table()

CONTROL = "ZZ-NOT-A-RATING"

# (libtype, ratings, library_type, field_key). The show case runs twice: whether
# a Plex section answers with a bare ``contentRating`` or a dotted
# ``show.contentRating`` is a fact about the SERVER, and premise P4 says the two
# name one field. Be exact about what running both buys: it MEASURES which
# spelling plexapi emits (``plexapi_side.py`` drives the real
# ``_validateFilterField``) and varies the input across both. It does not test
# P4 -- ``member_sets._strip_libtype`` removes the difference before either side
# is evaluated, so their semantic equivalence stays a premise. The live evidence
# for that premise is 9b's: ``?type=2&sort=titleSort&show.network=126689``
# answered on a real show section
# (``docs/research/plex-search-probe/README.md:217``), and 9b ships the dotted
# spelling for this very field (``filters.py:613``) -- which is also why the
# bare-``field_key`` row is the case where the two sides disagree on the field's
# spelling and still agree on the members.
CASES = [
    ("movie", "MOVIE_RATINGS", "Movie", "contentRating"),
    ("show", "SHOW_RATINGS", "Show", "contentRating"),
    ("show", "SHOW_RATINGS", "Show", "show.contentRating"),
]

# The characters that change a query string's MEANING when they appear
# unencoded in a value: ``&`` ends the parameter, ``+`` decodes as a space, and
# a literal space is not a legal URL character at all. Derived from the shipped
# table rather than listed, so a table edit that added another one is covered
# without editing this file.
UNSAFE_CHARS = set(" &+")
UNSAFE_RATINGS = sorted(
    {
        value
        for candidates in TABLE["addons"].values()
        for value in candidates
        if UNSAFE_CHARS & set(value)
    }
)
# The subset whose mis-encoding changed the MEMBER SET rather than only the
# bytes: ``&`` truncates the term and ``+`` becomes a space, so the value the
# server matches on is not the value that was sent. A space-only value survives
# a lenient parser unchanged, which is why it is pinned on the bytes test alone.
MEANING_CHANGING_RATINGS = sorted(
    value for value in UNSAFE_RATINGS if {"&", "+"} & set(value)
)

# The MIXED bucket, and the shape the defect would actually have taken. Every
# one of the ten unsafe ratings co-occurs with SAFE ones in the shipped table --
# bucket ``17`` carries ``gb/14+`` and ``R - 17+ (violence & profanity)`` next
# to ``R``, ``TV-14`` and ``TV-MA``; bucket ``18`` carries three unsafe values
# next to ``TV-MA`` and ``NC-17`` -- so a single-value bucket was the RARE case,
# not the realistic one. In a mixed bucket the terms sit in an ``or=1`` group, so
# a broken term did not empty the collection: the surviving terms still matched
# and what shipped was a SILENT SUBSET. That is the worse class, because an empty
# collection is at least noticeable.
MIXED_RATINGS = ("R", "TV-14", "TV-MA", "gb/14+", "R - 17+ (violence & profanity)")
MIXED_UNSAFE = {"gb/14+", "R - 17+ (violence & profanity)"}


def _ratings(name):
    return getattr(ours_side, name)


def _library(ratings):
    """One item per rating, plus one carrying a rating no bucket claims -- the
    negative control. Without it, a decoder whose every predicate answered True
    would make both sides select everything and the comparison would pass."""
    items = [{"ratingKey": rating, "contentRating": rating} for rating in ratings]
    items.append({"ratingKey": "control", "contentRating": CONTROL})
    return items


def _shape(bucket):
    """Which of the five shapes Addendum 2 names this resolved bucket is."""
    if not bucket.values:
        return "empty"
    if bucket.key == "other":
        return "other"
    if bucket.values == (bucket.key,):
        return "key_alone"
    return "one_addon" if len(bucket.values) == 1 else "several_addons"


@pytest.mark.parametrize("libtype,ratings_name,library_type,field_key", CASES)
def test_every_bucket_selects_the_same_items_through_both_grammars(
    libtype, ratings_name, library_type, field_key
):
    ratings = _ratings(ratings_name)
    present = set(ratings)
    library = _library(ratings)
    compared = 0

    for bucket in derive_buckets(present, library_type):
        if not bucket.values:
            # Addendum 3: a bucket that matches nothing is still returned, and
            # neither grammar is ever asked to build a query for it -- here or
            # in production (``reconcile.py:669``, ``if not bucket.values``).
            # An empty filter would mean the whole library.
            continue
        old = plexapi_side.old_query(libtype, bucket.values, ratings, field_key)
        new = ours_side.new_query(libtype, bucket.values, present)
        old_members = member_sets.members(old, library, libtype)
        new_members = member_sets.members(new, library, libtype)

        # The absolute claim first: what the OLD query selects is exactly the
        # bucket's own ratings and nothing else. Without this, two decoders that
        # are both wrong in the same direction would agree below and the whole
        # comparison would prove nothing.
        assert old_members == set(bucket.values), (bucket.key, old)
        assert "control" not in old_members, (bucket.key, old)
        # Then the relative one, which is what Addendum 2 point 2 asks for.
        assert old_members == new_members, (bucket.key, old, new)
        compared += 1

    assert compared, "no bucket was compared, so this case asserted nothing"


def test_every_bucket_shape_was_exercised():
    """Every shape Addendum 2 names was actually reached, not merely available.

    Across the cases rather than within one: ``key_alone`` is only reachable
    from the show vocabulary (bucket ``13``, whose addons that library holds
    none of), and no single vocabulary reaches all five.
    """
    seen = set()
    for _libtype, ratings_name, library_type, _field_key in CASES:
        present = set(_ratings(ratings_name))
        seen.update(_shape(b) for b in derive_buckets(present, library_type))

    assert seen == {"empty", "key_alone", "one_addon", "several_addons", "other"}, seen


# (case index, bucket key, the members that bucket must select). Written by
# hand off ``assets/collections/content_rating_cs.json`` and the two
# vocabularies -- not derived from ``bucket.values``, because a proof whose
# expectations come from the code under test is a proof that the code agrees
# with itself.
LITERAL_MEMBERS = [
    (0, "1", {"G"}),                        # key absent, one addon present
    (0, "17", {"R", "TV-MA"}),              # several addons, verified live
    (0, "18", {"NC-17", "R", "TV-MA"}),     # the widest movie bucket
    (0, "other", {"NR", "Unrated"}),        # the complement
    (1, "1", {"TV-G", "TV-Y"}),             # several addons, show
    (1, "13", {"13"}),                      # the bucket's own key, alone
    (1, "other", {"NR"}),                   # the complement, one value
]


@pytest.mark.parametrize("case_index,bucket_key,expected", LITERAL_MEMBERS)
def test_the_member_sets_are_the_ones_written_here(case_index, bucket_key, expected):
    libtype, ratings_name, library_type, field_key = CASES[case_index]
    ratings = _ratings(ratings_name)
    present = set(ratings)
    library = _library(ratings)
    bucket = next(b for b in derive_buckets(present, library_type) if b.key == bucket_key)

    old = plexapi_side.old_query(libtype, bucket.values, ratings, field_key)
    new = ours_side.new_query(libtype, bucket.values, present)

    assert member_sets.members(old, library, libtype) == expected, old
    assert member_sets.members(new, library, libtype) == expected, new


@pytest.mark.parametrize("libtype,ratings_name,library_type,field_key", CASES)
def test_the_two_grammars_disagree_on_bytes_and_that_is_the_point(
    libtype, ratings_name, library_type, field_key
):
    """The proof is worthless if the two sides are the same string: it would be
    proving that a query equals itself. Pin the difference so a future change
    that accidentally made them identical would have to say so."""
    present = set(_ratings(ratings_name))
    multi = next(
        b for b in derive_buckets(present, library_type) if len(b.values) > 1
    )
    old = plexapi_side.old_query(libtype, multi.values, _ratings(ratings_name), field_key)
    new = ours_side.new_query(libtype, multi.values, present)

    assert old != new
    # ``%2C`` and not ``,``: plexapi joins the values with a comma and then
    # ``urlencode``s the joined string (``library.py:1102-1103``), so the
    # separator reaches the wire percent-encoded.
    assert "%2C" in old and "or=1" not in old and "push=1" not in old, old
    assert "or=1" in new and "push=1" in new and "%2C" not in new, new


@pytest.mark.parametrize("libtype,ratings_name,library_type,field_key", CASES)
def test_an_all_base_query_diverges_and_the_machinery_says_so(
    libtype, ratings_name, library_type, field_key
):
    """The negative control for the COMPARISON itself.

    An ``all:`` base renders the same values as ``f=a&and=1&f=b``, and no item
    holds two content ratings, so it selects nothing. That is the 9b probe's
    measured 442-under-``any`` versus 0-under-``all`` (premise P2) reproduced in
    the evaluator: if this test passed vacuously the equivalence above would be
    unable to tell a right query from a wrong one.
    """
    ratings = _ratings(ratings_name)
    present = set(ratings)
    library = _library(ratings)
    multi = next(
        b for b in derive_buckets(present, library_type) if len(b.values) > 1
    )

    old_members = member_sets.members(
        plexapi_side.old_query(libtype, multi.values, ratings, field_key),
        library, libtype,
    )
    wrong = ours_side.new_query(libtype, multi.values, present, base="all")
    wrong_members = member_sets.members(wrong, library, libtype)

    assert old_members == set(multi.values)
    assert wrong_members == set(), wrong
    assert wrong_members != old_members


def test_the_shipped_table_is_what_was_proven():
    """The proof is over the SHIPPED table, so a table edit that added a bucket
    would otherwise leave a proof about the old one standing. Pinned by count
    rather than by content -- the content is the table's own test's business."""
    assert len(TABLE["include"]) == len(TABLE["addons"])
    assert TABLE["include"], "an empty table would make every assertion vacuous"
    assert UNSAFE_RATINGS, "the encoding tests below would assert nothing"
    # An empty parametrize is a silent SKIP, not an error, so the
    # meaning-changing case would quietly stop covering anything.
    assert MEANING_CHANGING_RATINGS, "the meaning test below would assert nothing"
    # The mixed-bucket case is written against bucket 17 by hand; if a table
    # edit moved either value out of it, that test would stop being the mixed
    # shape it claims to be.
    assert MIXED_UNSAFE <= set(TABLE["addons"]["17"]), sorted(MIXED_UNSAFE)


# --- the ten the fix bought ---------------------------------------------------
#
# These three were born ``xfail(strict=True)``: they were the finding, and strict
# meant that the day ``build_search_url`` percent-encoded a resolved tag value
# they would XPASS, the XPASS would become a failure, and whoever fixed it had to
# delete the markers to get a green. That day came, the markers are gone, and
# what is left is ordinary coverage of the part of the shipped table the proof
# above could not reach before. A fourth test measured the broken shape -- a
# non-empty proper subset -- and was deliberately left unmarked so it would RED
# on the same fix; it was deleted at that moment, as its own docstring asked.


@pytest.mark.parametrize("rating", UNSAFE_RATINGS)
def test_the_new_grammar_encodes_every_shipped_rating(rating):
    """The BYTES half: no raw value reaches the query string verbatim.

    Not byte-identical to plexapi even now -- ``quote``'s default ``safe='/'``
    leaves a slash alone, so ``gb/0+`` goes out as ``gb/0%2B`` where plexapi
    sends ``gb%2F0%2B``. The two decode to the same value, which is the claim
    below and the one that decides membership.
    """
    new = ours_side.new_query("movie", (rating,), {rating})
    assert rating not in new, new


@pytest.mark.parametrize("rating", MEANING_CHANGING_RATINGS)
def test_a_plus_or_ampersand_rating_still_selects_the_same_items(rating):
    """The MEANING half: for these ten-minus-the-space-only ones, the member
    set used to move. ``&`` ended the parameter early and ``+`` decoded as a
    space, so the value the server matched was not the value that was meant."""
    library = _library((rating,))
    old = plexapi_side.old_query("movie", (rating,), (rating,), "contentRating")
    new = ours_side.new_query("movie", (rating,), {rating})

    assert member_sets.members(old, library, "movie") == {rating}, old
    assert member_sets.members(new, library, "movie") == {rating}, new


def _mixed_bucket():
    """Bucket ``17`` against a vocabulary holding both safe and unsafe ratings.

    Built through the same real ``derive_buckets`` as everything above, so the
    bucket's values are the shipped table's and not a hand-made list.
    """
    present = set(MIXED_RATINGS)
    bucket = next(b for b in derive_buckets(present, "Movie") if b.key == "17")
    library = _library(MIXED_RATINGS)
    old = plexapi_side.old_query("movie", bucket.values, MIXED_RATINGS, "contentRating")
    new = ours_side.new_query("movie", bucket.values, present)
    return bucket, library, old, new


def test_a_mixed_bucket_selects_the_same_items_through_both_grammars():
    """The MEANING half again, in the shape that would actually have occurred.

    The two tests above build single-value buckets, which is the rare case; this
    is the realistic one. It used to be the interesting one for the opposite
    reason: a single-value bucket went EMPTY, but here the two broken terms sat
    in an ``or=1`` group beside three working ones, so the group still matched
    and the collection shipped PLAUSIBLE-BUT-SHORT -- the "silent wrongness"
    class ``search_url.py``'s own module docstring names as the risk the phase
    sits on, and strictly worse than empty because nobody notices it. The
    deleted fourth test measured that subset; this one now measures the equality
    that replaced it.

    The absolute pin travelled here from that test: without it, two decoders
    wrong in the same direction would satisfy the equality below.
    """
    bucket, library, old, new = _mixed_bucket()
    old_members = member_sets.members(old, library, "movie")
    new_members = member_sets.members(new, library, "movie")

    # The absolute claim first, as everywhere else in this file.
    assert old_members == set(bucket.values), old
    assert "control" not in old_members, old
    # ``MIXED_UNSAFE`` is what used to go missing; nothing does now.
    assert new_members == old_members, (sorted(new_members), sorted(old_members))
