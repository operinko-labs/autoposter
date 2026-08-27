"""``derive_keys``' own surface: ordering, purity and the CS equivalence.

The oracle next door proves the ANSWERS. These prove the promises the rest of
the phase makes about them.
"""
from autoposter.collections.buckets import derive_buckets, load_table
from autoposter.collections.dynamic_keys import derive_keys


def test_the_family_keeps_the_librarys_own_order_and_appends_synthetic_buckets():
    """Order is the collection-creation order and the sort-title order an
    operator sees, so it is a promise, not an accident: enumerated keys in the
    order Plex reported them, then any synthetic bucket."""
    derived = derive_keys(
        [("b", "b"), ("a", "a")], addons={"Zed": ["a"]},
    )
    assert [k.key for k in derived.keys] == ["b", "Zed"]


def test_deriving_twice_from_the_same_input_gives_the_same_answer():
    """No mutation of the caller's data, no hidden state. The builder calls
    this once per pass per definition and the config layer may call it never;
    a function that edited its ``addons`` argument in place would make the
    second pass differ from the first."""
    addons = {"Kids": ["G", "PG"]}
    pairs = [("G", "G"), ("PG", "PG"), ("R", "R")]
    first = derive_keys(pairs, addons=addons, include=["Kids"])
    second = derive_keys(pairs, addons=addons, include=["Kids"])
    assert first == second
    assert addons == {"Kids": ["G", "PG"]}


def test_the_common_sense_table_derives_the_same_values_this_engine_would():
    """The equivalence phase 10a-2's port rests on, asserted now rather than
    discovered then: fed the SHIPPED Common Sense table as
    ``include`` + ``addons``, this engine produces each bucket's values exactly
    as ``buckets.derive_buckets`` does -- same members, same order after
    sorting, and the same leftovers for the ``other`` bucket.

    The one deliberate difference, asserted rather than glossed: a bucket no
    present rating matches gets no collection here (meta.py:1224-1225), where
    ``derive_buckets`` returns it with empty ``values`` because production
    keeps such collections rather than deleting them. Buckets 15 and 16 are
    that case for the ratings below.

    It is a value-level claim only. Whether the resulting QUERY is the same
    membership is the golden gate's and the port's own equivalence proof, and
    that is 10a-2's task, not this one's.
    """
    table = load_table()
    present = {"G", "PG", "PG-13", "R", "NR", "TV-MA"}
    enumerated = [(one, one) for one in sorted(present)]

    derived = derive_keys(
        enumerated,
        include=list(table["include"]),
        addons={key: list(values) for key, values in table["addons"].items()},
    )
    ours = {k.key: tuple(sorted(k.values)) for k in derived.keys}

    matched = 0
    dropped = 0
    for bucket in derive_buckets(present, "Movie"):
        if bucket.key == "other":
            assert tuple(sorted(derived.other_keys)) == bucket.values
            continue
        if not bucket.values:
            # Addendum 3: buckets 15 and 16 are the two the shipped CS table
            # resolves to an empty bucket for this ``present`` set -- assert
            # the branch is actually exercised, so the difference this test
            # documents cannot silently stop being tested.
            assert bucket.key not in ours, bucket.key
            dropped += 1
            continue
        assert ours[bucket.key] == bucket.values, bucket.key
        matched += 1
    assert matched == len(ours)
    assert dropped == 2


def test_an_addon_key_the_library_carries_is_not_shadowed_by_a_synthetic_one():
    """meta.py:1217-1218's guard, stated as behaviour: ``addons: {PG: [G]}`` on
    a library that HAS a PG rating widens the real PG bucket. It does not
    create a second, synthetic 'PG'."""
    derived = derive_keys(
        [("G", "G"), ("PG", "PG")], addons={"PG": ["G"]},
    )
    assert [k.key for k in derived.keys] == ["PG"]
    assert derived.keys[0].values == ("PG", "G")


def test_a_synthetic_bucket_with_no_present_member_builds_nothing():
    """meta.py:1224-1225. The library has none of the members, so there is no
    collection -- rather than an empty one, which on a smart filter would match
    the entire library (``reconcile.py:669``)."""
    derived = derive_keys([("R", "R")], addons={"Kids": ["G", "PG"]})
    assert [k.key for k in derived.keys] == ["R"]


def test_a_bare_scalar_include_is_one_key_not_its_characters():
    """T3 review, deferred minor: ``_strlist``'s non-iterable branch
    (util.py:933) was reachable from a YAML scalar and untested. ``include:
    Horror`` is one key -- not six -- and the params model's ``list[str]``
    coercion is not what makes that true, because ``derive_keys`` is public and
    pure."""
    derived = derive_keys([("Horror", "Horror"), ("Drama", "Drama")],
                          include="Horror")
    assert [one.key for one in derived.keys] == ["Horror"]
