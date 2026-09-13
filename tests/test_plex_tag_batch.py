"""fetch_tag_index: the chunked multi-key metadata read, plain data out.

The call-count pin is the point (probe F's own recommendation): a regression to
per-item fetching must be a red test, not a slow night.
"""
import math

import pytest

from autoposter.plex.client import TAG_BATCH_CHUNK, ItemTags, fetch_tag_index


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakeStream:
    """Carries both attribs D6 measured populated, so reading the display
    title instead of the ISO code is a red test, not a silent mismatch."""

    def __init__(self, stream_type, language_tag, language="Display Title"):
        self.streamType = stream_type
        self.languageTag = language_tag
        self.language = language


class FakePart:
    def __init__(self, streams):
        self.streams = streams


class FakeMedia:
    def __init__(self, parts):
        self.parts = parts


class FakeItem:
    def __init__(self, rating_key, genres=(), labels=(), collections=(), streams=()):
        self.ratingKey = rating_key
        self.genres = [FakeTag(g) for g in genres]
        self.labels = [FakeTag(x) for x in labels]
        self.collections = [FakeTag(c) for c in collections]
        self.media = [FakeMedia([FakePart(list(streams))])] if streams else []


class FakeSection:
    """Counts fetchItems calls and records each call's key list."""

    def __init__(self, items):
        self._items = {int(i.ratingKey): i for i in items}
        self.calls: list[list[int]] = []

    def fetchItems(self, ekey):
        assert isinstance(ekey, list) and all(isinstance(k, int) for k in ekey), (
            "the batch seam is plexapi's list-of-ints translation (base.py:334-335); "
            "anything else is a different endpoint"
        )
        self.calls.append(list(ekey))
        return [self._items[k] for k in ekey if k in self._items]


def _items(n):
    return [FakeItem(str(k), genres=("Action", "Crime")) for k in range(1, n + 1)]


def test_the_default_chunk_is_the_probed_two_hundred():
    # The ceil pin below passes chunk_size explicitly, so the DEFAULT was free
    # to drift (TAG_BATCH_CHUNK = 1 ships green). Probe decision D2 chose 200:
    # 8-12 ms/item at 50-200 against 15.5 at 400 and 36.7 at 1200.
    assert TAG_BATCH_CHUNK == 200


def test_call_count_is_ceil_n_over_chunk_exactly():
    section = FakeSection(_items(1955))
    keys = [str(k) for k in range(1, 1956)]
    result = fetch_tag_index(section, keys, chunk_size=200)
    assert len(section.calls) == math.ceil(1955 / 200) == 10
    assert all(len(call) <= 200 for call in section.calls)
    assert len(result) == 1955


def test_result_is_plain_data_keyed_by_string_rating_key():
    item = FakeItem("7", genres=("Horror",), labels=("Overlay",),
                    collections=("Alien Collection",),
                    streams=(FakeStream(2, "en"), FakeStream(3, "fi"),
                             FakeStream(1, None)))
    result = fetch_tag_index(FakeSection([item]), ["7"], chunk_size=50)
    tags = result["7"]
    assert isinstance(tags, ItemTags)
    assert tags.genres == ("Horror",)
    assert tags.labels == ("Overlay",)
    assert tags.collections == ("Alien Collection",)
    # the ISO code, never the display title (probe decision D6)
    assert tags.audio_languages == ("en",)
    assert tags.subtitle_languages == ("fi",)


def test_item_with_no_tags_is_present_with_empty_tuples_not_absent():
    # "found nothing" is an ANSWER; absence from the dict means "not fetched".
    result = fetch_tag_index(FakeSection([FakeItem("9")]), ["9"], chunk_size=50)
    assert result["9"] == ItemTags((), (), (), (), ())


def test_key_plex_no_longer_answers_is_absent_from_the_result():
    result = fetch_tag_index(FakeSection([FakeItem("1")]), ["1", "2"], chunk_size=50)
    assert "1" in result and "2" not in result


def test_duplicate_tags_deduplicate_preserving_order():
    item = FakeItem("3", streams=(FakeStream(2, "en"), FakeStream(2, "en"),
                                  FakeStream(2, "fi")))
    result = fetch_tag_index(FakeSection([item]), ["3"])
    assert result["3"].audio_languages == ("en", "fi")


def test_duplicate_rating_keys_are_asked_for_once():
    # duplicates cost chunks, never characters in a chunk -- but the ceil pin
    # is only honest over DISTINCT keys.
    section = FakeSection(_items(3))
    result = fetch_tag_index(section, ["1", "1", "2", "1", "3"], chunk_size=200)
    assert section.calls == [[1, 2, 3]]
    assert len(result) == 3


def test_non_numeric_rating_key_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        fetch_tag_index(FakeSection([]), ["not-a-key"])
