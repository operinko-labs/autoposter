"""Oscars award dataset handling.

Never fetches the real dataset -- MockTransport and a committed fixture.
"""
from pathlib import Path

import httpx
import pytest

from autoposter.collections.awards import (
    BEST_DIRECTOR,
    BEST_PICTURE,
    fetch_event,
    recent_years,
    winners_for_categories,
    winners_for_year,
)

FIXTURE = Path("tests/fixtures/collections/ev0000003.yml").read_text(encoding="utf-8")
MIXED_KEYS_FIXTURE = Path(
    "tests/fixtures/collections/ev0000003_mixed_year_keys.yml"
).read_text(encoding="utf-8")


def _client(body=None, status=200):
    def handler(request):
        return httpx.Response(status, text=body if body is not None else FIXTURE)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_event_parses_the_dataset():
    async with _client() as http:
        event = await fetch_event(http)
    assert "2026" in event
    assert event["2026"]["oscar"]["best motion picture of the year"]["winner"] == ["tt31193180"]


async def test_fetch_event_raises_on_an_http_error():
    """An empty result would empty every Oscars collection."""
    async with _client(status=404) as http:
        with pytest.raises(Exception):
            await fetch_event(http)


async def test_recent_years_skips_empty_placeholder_years():
    """The live dataset carries '2027': {} for a ceremony that has not
    happened. Including it would create an empty Oscars Winners 2027."""
    async with _client() as http:
        event = await fetch_event(http)
    assert recent_years(event, count=5) == ["2026", "2025", "2024", "2023", "2022"]


async def test_recent_years_is_newest_first():
    async with _client() as http:
        event = await fetch_event(http)
    years = recent_years(event, count=3)
    assert years == sorted(years, reverse=True)


async def test_best_picture_winners_span_category_renames():
    """'best motion picture of the year' and 'best picture' are the same
    award under names used in different eras."""
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_PICTURE)
    assert "tt31193180" in winners  # 2026, "best motion picture of the year"
    assert "tt2000001" in winners  # 2024, "best picture"


async def test_best_director_winners_exclude_other_categories():
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_DIRECTOR)
    assert "tt30144839" in winners
    assert "tt31193180" not in winners


async def test_nominees_are_never_included():
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_PICTURE)
    assert "tt30144839" not in winners  # a 2026 best-picture nominee, not the winner


async def test_winners_are_newest_year_first():
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_PICTURE)
    assert winners.index("tt31193180") < winners.index("tt1000003")


async def test_winners_for_year_returns_every_category():
    async with _client() as http:
        event = await fetch_event(http)
    assert sorted(winners_for_year(event, "2026")) == ["tt30144839", "tt31193180"]


async def test_an_unquoted_year_in_the_dataset_does_not_crash_the_run():
    """One unquoted key parses as an int, and ``sorted`` over a mix of int
    and str raises TypeError -- outside the caller's try, taking the whole
    run down. 2025 and 2023 are unquoted in this fixture."""
    async with _client(body=MIXED_KEYS_FIXTURE) as http:
        event = await fetch_event(http)

    assert 2025 in event and "2024" in event, "the fixture must mix int and str keys"
    assert recent_years(event, count=5) == ["2026", "2025", "2024", "2023", "2022"]
    assert winners_for_categories(event, BEST_PICTURE)[0] == "tt31193180"


async def test_an_unquoted_year_is_still_looked_up_by_its_string_key():
    """``recent_years`` hands back str years, so ``winners_for_year`` must
    find the int-keyed ones too -- otherwise that year silently resolves to
    no winners and its collection is left empty."""
    async with _client(body=MIXED_KEYS_FIXTURE) as http:
        event = await fetch_event(http)

    for year in recent_years(event, count=5):
        assert winners_for_year(event, year), "no winners resolved for %r" % year
    assert sorted(winners_for_year(event, "2025")) == ["tt1000002", "tt1000003"]


async def test_duplicates_are_removed_keeping_first_occurrence():
    event = {
        "2026": {"oscar": {"best picture": {"winner": ["ttX"]},
                           "best motion picture of the year": {"winner": ["ttX"]}}},
    }
    assert winners_for_categories(event, BEST_PICTURE) == ["ttX"]
