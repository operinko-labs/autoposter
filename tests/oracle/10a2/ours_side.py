"""Our side of the equivalence: ``parse_filters`` + ``build_search_url``.

Unlike ``member_sets.py`` this one DOES import from the repository -- that is
the point of it. It builds the query the Common Sense write path will send once
Task 4 lands, using the same three calls the ``plex_search`` builder makes, so
that what the proof compares is this engine's real grammar and not a
hand-written approximation of it.

Loaded by PATH by the gate, never imported by name: ``tests/oracle`` is not a
package (9b's driver is loaded the same way,
``tests/test_collection_search_oracle.py:276``), and a module name starting
with a digit could not be imported anyway.

The side-by-side dump of both grammars, and the command that produces it, are
in ``README.md`` beside this file.
"""
from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import build_search_url

# The ratings each library type is given for the proof. Chosen so every bucket
# shape is reachable against the SHIPPED table: a bucket that is its own key
# alone, a bucket whose key is absent and exactly one addon present, a bucket
# with several present addons, a bucket that matches nothing at all
# (Addendum 3), and a non-empty complement for the ``other`` bucket.
MOVIE_RATINGS = ("G", "PG", "PG-13", "R", "NC-17", "NR", "Unrated", "TV-MA")

# ``13`` is a bucket KEY carried as a bare content rating, which is the one
# shape the plain age-word vocabularies cannot reach: every bucket key from
# ``1`` to ``18`` has addons that overlap the common English ratings, so a
# library holding only those never exercises ``derive_buckets``'s
# ``[key] if key in present`` branch (buckets.py:51). Against this show
# vocabulary bucket ``13``'s addons are all absent, so its filter is its own
# key and nothing else.
SHOW_RATINGS = ("TV-Y", "TV-G", "TV-14", "TV-MA", "NR", "13")


def resolver(present):
    """The library's tag vocabulary for ``content_rating``, as a callable.

    ``content_rating`` is the one tag type whose Plex key and title are the
    same string on this server (``dynamic_types.py``'s content_rating note,
    measured by the 10a-1 probe), so the resolver is the identity over the
    present set. Saying that out loud is what stops it being mistaken for a
    shortcut: plexapi's ``_validateFieldValueTag`` resolves against
    ``listFilterChoices`` and falls back to the written value, and the other
    side of this proof stubs those choices with ``key == title`` for exactly
    the same reason. Both sides therefore send the written rating, which is
    what production sends today.
    """

    def resolve(attribute, value, /):
        return (str(value),) if str(value) in present else ()

    return resolve


def new_query(libtype: str, values, present, base: str = "any") -> str:
    """The query string this engine builds for one bucket's ratings.

    ``base="any"`` is the whole claim under proof (p10a-facts.md C2): several
    values under an ``any:`` base render as ``push=1&f=a&or=1&f=b&pop=1``,
    which is meant to select what plexapi's comma-joined ``f=a,b`` selects.
    ``sort_by`` is ``release.desc`` because that is this engine's own
    spelling, in its sort table, of the sort this family asked plexapi for,
    ``originallyAvailableAt:desc``, and ``limit`` is ``None`` on both sides.

    ``base`` is settable for one reason only: the gate's negative control
    builds the deliberately WRONG ``all:`` query through this same real code
    path, so that "the machinery would notice a difference" is demonstrated
    rather than asserted. Nothing in production passes anything but ``any``.
    """
    group = parse_filters(
        {"content_rating": list(values)},
        field="params", searching=True, base=base,
    )
    return build_search_url(
        group, libtype=libtype, sort_by=("release.desc",), limit=None,
        resolve_tag=resolver(present),
    )
