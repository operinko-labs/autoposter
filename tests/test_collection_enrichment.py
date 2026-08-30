"""ensure_tags: the per-run cache beside run_cache (engine.py:306).

Scoped to the asked-for keys, never the whole library; the failure is
memoised too (BuilderContext.run_cache's own law), and so is a fetched-but-
absent key, so a gone item costs one fetch per pass, not one per definition.
"""
import ast
from pathlib import Path
from xml.etree import ElementTree

import pytest
import requests
from plexapi.exceptions import BadRequest

from autoposter.collections.enrichment import EnrichmentUnavailable, ensure_tags


class FakeSection:
    def __init__(self, items, fail=None):
        self._items = items
        # ``fail`` is the exception CLASS to raise, so both arms of the
        # narrowed catch can be driven through the same section.
        self.fail = fail
        self.calls = []

    def fetchItems(self, ekey):
        self.calls.append(list(ekey))
        if self.fail is not None:
            raise self.fail("boom")
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


# All three arms of ``enrichment.py``'s narrowed catch: ``BadRequest`` is D2's
# measured refusal shape (a ``PlexApiException`` subclass); ``ConnectionError``
# is the dropped connection that comes off plexapi's bare ``requests`` call
# unwrapped; ``ParseError`` is the 200-with-an-HTML-body a reverse proxy or
# captive portal answers, which escapes plexapi's unguarded cleaned retry
# (utils.py:836-844) as a ``SyntaxError`` subclass in NEITHER hierarchy
# (roadmap row 205).
@pytest.mark.parametrize(
    "failure", [BadRequest, requests.ConnectionError, ElementTree.ParseError]
)
async def test_failure_is_memoised_for_the_pass(failure):
    section = FakeSection({}, fail=failure)
    run_cache = {}
    with pytest.raises(EnrichmentUnavailable) as first:
        await ensure_tags(section, run_cache, ["1"])
    assert failure.__name__ in str(first.value) and "boom" not in str(first.value)
    with pytest.raises(EnrichmentUnavailable):
        await ensure_tags(section, run_cache, ["1"])
    assert len(section.calls) == 1, "a dead server is one fetch per pass, not one per definition"


def test_the_two_stream_read_catch_tuples_cannot_drift_apart():
    """``enrichment.py``'s catch and ``builders/plex_search.py``'s second one
    wrap the SAME mechanism -- a batched read off plexapi's bare ``requests``
    call -- and both comments say so, but nothing made the class lists move
    together: row 205 had to add ``ParseError`` to each by hand, and the next
    such class can as easily land in one and not the other.

    Compared, not shared. A module-level constant tuple would couple two
    builders through a new import edge to spare one duplicated line; this
    proves the equality the comments claim without either module learning
    about the other. Each module's stream-read handler is the one that names
    ``PlexApiException`` itself -- ``plex_search``'s FIRST handler catches the
    ``NotFound``/``BadRequest`` subclasses, a different claim ("Plex has no
    such filter") that is deliberately NOT this set. Both modules spell the
    classes identically (bare ``PlexApiException``, ``requests.``- and
    ``ElementTree.``-qualified), which is what makes the unparsed names
    directly comparable; a future module that imports one of them under
    another name would fail here and should be spelled to match."""
    root = Path(__file__).parent.parent / "src" / "autoposter" / "collections"

    def stream_read_catch(path):
        assert path.is_file(), f"source not found at {path}"
        handlers = [
            {ast.unparse(element) for element in node.type.elts}
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple)
            and "PlexApiException" in {ast.unparse(e) for e in node.type.elts}
        ]
        assert len(handlers) == 1, f"{path.name}: expected one stream-read catch, saw {handlers}"
        return handlers[0]

    assert stream_read_catch(root / "enrichment.py") == stream_read_catch(
        root / "builders" / "plex_search.py"
    ), "the two stream-read catch tuples have drifted apart (roadmap row 205)"


async def test_a_fully_cached_ask_survives_an_earlier_failure_in_the_pass():
    """The memo's ORDER, and it is load-bearing. The pass-level failure is a
    fact about FETCHING, so it may only refuse an ask that would have to
    fetch. A definition whose every key is already in the cache is answerable
    without touching Plex, and consulting the memo above the already-cached
    short-circuit refused it anyway -- one earlier failure in the pass turning
    into a refusal for definitions the cache could have served in full."""
    section = _section("1")
    run_cache = {}
    await ensure_tags(section, run_cache, ["1"])

    section.fail = BadRequest
    with pytest.raises(EnrichmentUnavailable):
        await ensure_tags(section, run_cache, ["2"])

    assert "1" in await ensure_tags(section, run_cache, ["1"])
    assert len(section.calls) == 2, "the cached ask costs no third fetch either"


async def test_caller_bug_propagates_rather_than_being_memoised():
    # A non-numeric key is a CALLER bug, not a fact about the library: it must
    # reach the caller intact, and must not refuse every later definition this
    # pass (review round 1; plex_search.py:459-497 is the standing precedent).
    section = _section("1")
    run_cache = {}
    with pytest.raises(ValueError):
        await ensure_tags(section, run_cache, ["not-a-key"])
    assert "1" in await ensure_tags(section, run_cache, ["1"])
    assert len(section.calls) == 1


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
