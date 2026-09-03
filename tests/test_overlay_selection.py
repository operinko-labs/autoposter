"""The overlay-side selection seam: the item view, the vocabulary, the refusals.

The view is deliberately NOT `collections/filter_values.py::PlexItemView`.
That one is bound by "reading a filter value never costs a Plex request",
because the collections engine walks the library once with `section.all()`
and holds PARTIAL objects. The badge pipeline holds a richer context: by the
time `apply_badges` builds this view, `media_info_from_plex` has already
called `reload()` and walked `part.streams`, and `facts` and `plex_item` are
handed in directly. So the four attributes the collections table defers or
scopes for that reason (`audio_language`, `resolution`'s movie-only kind,
`duplicate`, `network`) do not bind here -- the recon's own table, adjudication
A3b answered by construction rather than by relaxing a column.

What DOES bind, and is pinned below: wherever the two views both answer, they
must answer identically. A disagreement would be the same-name-different-filter
class `collections/filters.py`'s module docstring rules out.
"""
import pytest

from autoposter.badges.values import MediaInfo, media_info_from_plex
from autoposter.collections.filter_values import PlexItemView
from autoposter.overlays.selection import (
    OVERLAY_ATTRIBUTES,
    AttributeNotOnItem,
    OverlayItemView,
)
from autoposter.overlays.schema import OverlayDefinition

# `compiled_condition`, `parse_condition` and `select` are imported below, in
# Step 9 -- Step 6 (next) writes only the view half of this module, so
# importing all six names here would make Step 7's "7 passed" an
# `ImportError` at collection instead. Splitting the import keeps each RED
# step failing for the one reason it names.


class _Media:
    def __init__(self, resolution):
        self.videoResolution = resolution
        self.audioCodec = "eac3"
        self.audioChannels = 6
        self.parts = []


class _Item:
    """A plexapi-shaped stand-in. Plain attributes, so
    `object.__getattribute__` (which PlexItemView uses) reaches them."""

    def __init__(self, resolutions=("1080",), content_rating="PG-13"):
        self.media = [_Media(r) for r in resolutions]
        self.contentRating = content_rating
        self.duration = 4845912
        self.seasonNumber = None
        self.episodeNumber = None

    def reload(self):
        """No-op. `badges/values.py::media_info_from_plex` calls this
        unconditionally when `.media` is empty -- matching the real function,
        which does not gate the call on the attribute's presence -- so a fake
        with no `.media` needs somewhere for that call to land."""


class _Facts:
    """`item_facts` -- whose `content_rating` is MDBList's COMMON SENSE age
    rating, a different value space from Plex's certification (A5)."""

    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = "3"


def _view(item, facts=None):
    return OverlayItemView(media_info_from_plex(item), facts=facts, plex_item=item)


def test_the_vocabulary_is_exactly_what_this_slice_supplies():
    assert OVERLAY_ATTRIBUTES == ("content_rating", "resolution")


def test_content_rating_comes_from_plex_not_from_item_facts():
    """Adjudication A5. The item carries BOTH values and they differ; the
    view must answer Plex's certification, not Common Sense's age."""
    item = _Item(content_rating="PG-13")
    assert _view(item, _Facts()).get("content_rating") == "PG-13"


def test_resolution_answers_every_version_not_just_the_first():
    """`MediaInfo` keeps `media[0]` only, so the view reads the item's own
    `media` list -- which `media_info_from_plex` has already reloaded, so
    this costs no request. Reading MediaInfo instead would answer ("4k",)
    for an item PlexItemView answers ("4k", "1080") for."""
    item = _Item(resolutions=("4k", "1080"))
    assert _view(item).get("resolution") == ("4k", "1080")


def test_an_item_with_no_media_has_no_resolution():
    """A show or a season carries no `media`. That is an ANSWER -- None,
    which the tag missing-value rule turns into "a positive filter excludes
    it" -- not an error."""
    item = _Item()
    item.media = []
    assert _view(item).get("resolution") is None


def test_an_attribute_outside_the_vocabulary_raises_rather_than_answering_none():
    """None means "this item has no value", which the table turns into a
    defined match result. Answering None for something we simply cannot read
    would be indistinguishable from a real answer -- the same discipline
    `filter_values.AttributeNotInListing` holds."""
    with pytest.raises(AttributeNotOnItem) as caught:
        _view(_Item()).get("genre")
    assert "content_rating" in str(caught.value)


def test_the_two_views_agree_on_content_rating():
    """Global Constraint 9."""
    item = _Item(content_rating="TV-MA")
    assert _view(item).get("content_rating") == PlexItemView(item).get("content_rating")


def test_the_two_views_agree_on_resolution_including_a_multi_version_item():
    """Global Constraint 9, on the attribute where a naive MediaInfo-backed
    accessor would have diverged."""
    for resolutions in (("1080",), ("4k", "1080"), ()):
        item = _Item(resolutions=resolutions)
        assert _view(item).get("resolution") == PlexItemView(item).get("resolution")
