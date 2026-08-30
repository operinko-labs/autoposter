"""ensure_tags: the per-run cache beside run_cache (engine.py:306).

Scoped to the asked-for keys, never the whole library; the failure is
memoised too (BuilderContext.run_cache's own law), and so is a fetched-but-
absent key, so a gone item costs one fetch per pass, not one per definition.
"""
import pytest

from autoposter.collections.enrichment import EnrichmentUnavailable, ensure_tags


class FakeSection:
    def __init__(self, items, fail=False):
        self._items = items
        self.fail = fail
        self.calls = []

    def fetchItems(self, ekey):
        self.calls.append(list(ekey))
        if self.fail:
            raise RuntimeError("boom")
        return [self._items[k] for k in ekey if k in self._items]


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key
        self.genres = []
        self.labels = []
        self.collections = []
        self.media = []


def _section(*keys):
    return FakeSection({int(k): FakeItem(k) for k in keys})


async def test_second_call_fetches_only_the_missing_keys():
    section = _section("1", "2", "3")
    run_cache = {}
    first = await ensure_tags(section, run_cache, ["1", "2"])
    assert set(first) >= {"1", "2"} and len(section.calls) == 1
    second = await ensure_tags(section, run_cache, ["2", "3"])
    assert "3" in second
    assert section.calls[1] == [3], "keys already cached must not be re-fetched"


async def test_fully_cached_ask_makes_no_call_at_all():
    section = _section("1")
    run_cache = {}
    await ensure_tags(section, run_cache, ["1"])
    await ensure_tags(section, run_cache, ["1"])
    assert len(section.calls) == 1


async def test_failure_is_memoised_for_the_pass():
    section = FakeSection({}, fail=True)
    run_cache = {}
    with pytest.raises(EnrichmentUnavailable) as first:
        await ensure_tags(section, run_cache, ["1"])
    assert "RuntimeError" in str(first.value) and "boom" not in str(first.value)
    with pytest.raises(EnrichmentUnavailable):
        await ensure_tags(section, run_cache, ["1"])
    assert len(section.calls) == 1, "a dead server is one fetch per pass, not one per definition"


async def test_gone_key_is_memoised_as_missing_not_refetched():
    section = _section("1")
    run_cache = {}
    result = await ensure_tags(section, run_cache, ["1", "99"])
    assert "99" not in result
    await ensure_tags(section, run_cache, ["99"])
    assert len(section.calls) == 1, "a key Plex does not answer is not asked again this pass"


async def test_empty_ask_makes_no_call():
    section = _section()
    assert await ensure_tags(section, {}, []) == {}
    assert section.calls == []
