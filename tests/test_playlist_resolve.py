"""``resolve_external_across`` -- the same resolution, several libraries deep.

The whole point is what it does NOT do: merge the per-library indexes. A Movie
library's ``tmdb://123`` and a Show library's ``tmdb://123`` are different id
spaces sharing one key, and a merged dict would let a film id resolve to a
series. ``build_owned_index``'s own docstring already refuses that class of
merge one level down, for season/episode indexes, and the reason is identical.
"""
from autoposter.collections.resolve import (
    build_owned_index,
    resolve_external,
    resolve_external_across,
)


class FakeGuid:
    def __init__(self, value):
        self.id = value


class FakeItem:
    def __init__(self, rating_key, guids=()):
        self.ratingKey = rating_key
        self.guids = [FakeGuid(g) for g in guids]


class FakeSection:
    def __init__(self, items, section_type="movie"):
        self._items = list(items)
        self.type = section_type

    def all(self):
        return list(self._items)

    def search(self, libtype=None):
        return list(self._items)


MOVIE = FakeItem("1", ["tmdb://123", "imdb://tt0001"])
SHOW = FakeItem("2", ["tmdb://123", "tvdb://999"])


def _movies():
    return build_owned_index(FakeSection([MOVIE]))


def _shows():
    return build_owned_index(FakeSection([SHOW], section_type="show"))


def test_the_first_library_that_owns_an_id_claims_it():
    resolved = resolve_external_across([_movies(), _shows()], [("tmdb", "123")])

    assert [i.ratingKey for i in resolved.items] == ["1"]
    assert resolved.unresolved == 0


def test_reversing_the_library_order_reverses_the_winner():
    """The tie-break IS the library order, and nothing else. This is the whole
    contract: an id in two libraries' id spaces resolves to the one the
    definition named first."""
    resolved = resolve_external_across([_shows(), _movies()], [("tmdb", "123")])

    assert [i.ratingKey for i in resolved.items] == ["2"]


def test_ids_only_a_later_library_owns_still_resolve():
    resolved = resolve_external_across(
        [_movies(), _shows()], [("imdb", "tt0001"), ("tvdb", "999")]
    )

    assert [i.ratingKey for i in resolved.items] == ["1", "2"]
    assert resolved.unresolved == 0


def test_source_order_survives_and_each_item_appears_once():
    """One Plex item commonly carries an imdb AND a tmdb guid, and a list may
    name both; the item belongs in the playlist once, at the position of
    whichever id came first.

    "Once" here means **once per Plex item**, which is the dedup key
    (``_identity`` is ``str(item.ratingKey)``) -- not once per title. A film
    held in a 4K section and an HD section is two Plex items with two rating
    keys, and if the source names one guid the 4K section owns and another the
    HD section owns, both enter the playlist. That is intended and is stated in
    ``resolve_external_across``' docstring: a Plex item is a Plex item, Kometa
    behaves the same way, and the alternative -- a title-level dedup -- would
    have to guess which copy the operator meant."""
    resolved = resolve_external_across(
        [_movies(), _shows()],
        [("tvdb", "999"), ("imdb", "tt0001"), ("tmdb", "123")],
    )

    assert [i.ratingKey for i in resolved.items] == ["2", "1"]


def test_an_id_no_library_owns_is_counted_once_and_never_guessed():
    resolved = resolve_external_across(
        [_movies(), _shows()], [("tmdb", "404"), ("tmdb", "404"), ("nope", "1")]
    )

    assert resolved.items == []
    assert resolved.unresolved == 2


def test_the_single_index_form_is_the_same_function():
    """``resolve_external`` is the one-index case, and is written as a delegate
    rather than a second implementation -- so the two can never disagree about
    dedup, ordering or the unresolved count."""
    ids = [("tmdb", "123"), ("imdb", "tt0001"), ("tmdb", "404")]

    assert resolve_external(_movies(), ids) == resolve_external_across([_movies()], ids)


def test_an_empty_index_list_resolves_nothing_rather_than_raising():
    """A definition whose every scoped library failed to open still has to
    reach the empty-result law rather than crash the pass."""
    resolved = resolve_external_across([], [("tmdb", "123")])

    assert resolved.items == []
    assert resolved.unresolved == 1
