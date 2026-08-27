"""``family_titles``: identity, and the places we refuse where Kometa logs.

The oracle next door proves the NAMES. These prove what happens when two of
them collide, what the ``other`` bucket is, and what happens to the one "key"
Plex reports that is not a value at all.
"""
import pytest

from autoposter.collections.dynamic_keys import DerivedKeys, DynamicKey
from autoposter.collections.dynamic_titles import (
    DuplicateFamilyTitle,
    family_titles,
    render_title,
    title_format_names_the_key,
)

FORMAT = "Top <<key_name>> <<library_type>>s"


def _derived(*pairs, other=()):
    return DerivedKeys(
        keys=tuple(DynamicKey(key=k, value=v, values=(k,)) for k, v in pairs),
        other_keys=tuple(other),
    )


def test_two_keys_that_title_the_same_collection_refuse_and_name_both():
    """Kometa warns and skips the second (meta.py:1417-1418). Skipping means
    one of the two keys silently has no collection -- and which one depends on
    the order Plex reported its values, so the same config builds different
    families on different libraries. Refusing names both keys and the title."""
    derived = _derived(("Sci-Fi", "Sci-Fi"), ("SciFi", "SciFi"))
    with pytest.raises(DuplicateFamilyTitle) as refusal:
        family_titles(
            derived, library_type="Movie", title_format=FORMAT,
            key_name_override={"Sci-Fi": "Science Fiction", "SciFi": "Science Fiction"},
        )
    assert "Sci-Fi" in str(refusal.value)
    assert "SciFi" in str(refusal.value)
    assert "Top Science Fiction movies" in str(refusal.value)


def test_the_other_collection_is_held_to_the_same_collision_rule():
    """Upstream writes the ``other`` collection unguarded (meta.py:1455), which
    is exactly how Kometa's ``other_name: Not Rated <<library_typeU>>s`` lands
    on the Common Sense family's own 'Not Rated Movies'
    (``collections/buckets.py:66``) -- predicted in
    ``p4c-task-5-review.md:131-134`` and refused here (decision C6)."""
    derived = _derived(("NR", "NR"), other=["Unrated"])
    with pytest.raises(DuplicateFamilyTitle):
        family_titles(
            derived, library_type="Movie", title_format="<<key_name>>",
            title_override={"NR": "Not Rated Movies"},
            other_name="Not Rated <<library_typeU>>s",
        )


def test_the_other_bucket_holds_the_leftover_keys_and_is_titled_without_one():
    """Its key is the literal ``"other"`` (meta.py:1435), so no override table
    can reach it, and its title takes the library-type substitution and nothing
    else. Its VALUES are the leftovers, which is what makes it a set
    complement rather than a name."""
    derived = _derived(("G", "G"), other=["R", "NC-17"])
    titled = family_titles(
        derived, library_type="Movie", title_format=FORMAT,
        other_name="Everything Else, <<library_typeU>>s",
    )
    assert [one.title for one in titled] == [
        "Top G movies", "Everything Else, Movies",
    ]
    assert titled[-1].key == "other"
    assert titled[-1].values == ("R", "NC-17")


def test_no_other_collection_when_nothing_was_left_over():
    """meta.py:1432-1433 warns "Other Collection not needed" and does not create
    it. An empty ``other`` would be a smart filter with no terms, which
    ``build_search_url`` refuses and which would otherwise match the entire
    library (``reconcile.py:669``)."""
    derived = _derived(("G", "G"))
    titled = family_titles(
        derived, library_type="Movie", title_format=FORMAT, other_name="Leftovers",
    )
    assert [one.key for one in titled] == ["G"]


def test_the_two_library_type_tokens_keep_their_own_case():
    """``<<library_typeU>>`` is ``library.type`` unchanged and
    ``<<library_type>>`` is its lowercase form (meta.py:1268-1271). The two
    tokens do not overlap -- the ``U`` sits between ``type`` and the closing
    ``>>`` -- so both survive whichever is substituted first, and this pins
    that both appear, each in its own case, in one format."""
    assert render_title(
        "<<key_name>> <<library_typeU>>s in <<library_type>>", "5", "Movie",
        key="5", values=("5",),
    ) == "5 Movies in movie"


def test_a_title_format_that_names_no_key_is_recognised_as_such():
    """The predicate the params model refuses on. Upstream reverts to the type
    default and logs (meta.py:1234-1236) -- which builds the family under names
    the operator did not write, and then skips all but the first as
    duplicates."""
    assert title_format_names_the_key("Top <<key_name>> <<library_type>>s")
    assert title_format_names_the_key("<<title>> Cinema")
    assert not title_format_names_the_key("Great <<library_type>>s")


def test_a_literal_none_key_is_the_absent_case_and_gets_no_collection():
    """The Task 2 live probe found the DVR section (an "Other videos" library
    the Plex API types as movie) answering ``content_rating`` with two choices:
    ``16=16`` and ``None=None``. ``None`` is Plex saying "the items with no
    content rating", not a rating -- upstream would title a collection "Top
    None movies" from it, and a zero-count enumeration check never sees it
    because the count is two.

    The override tables cannot resurrect it either: naming the absence is what
    ``other_name`` is for."""
    derived = _derived(("16", "16"), ("None", "None"))
    titled = family_titles(derived, library_type="Movie", title_format=FORMAT)
    assert [one.title for one in titled] == ["Top 16 movies"]

    named = family_titles(
        derived, library_type="Movie", title_format=FORMAT,
        key_name_override={"None": "Unrated"}, title_override={"None": "Unrated Movies"},
    )
    assert [one.title for one in named] == ["Top 16 movies"]
