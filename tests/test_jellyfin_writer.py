"""jellyfin/writer.py -- read-modify-write apply_facts, locks only
where the captured MetadataField enum allows.

Every GET/POST below cites docs/reference/2026-09-jellyfin-openapi-12.md,
"/Items/{itemId}" -> get / -> post (UpdateItem, full-body replace).
"""
import json
import logging

import httpx
import pytest

from autoposter.config.schema import OperationsConfig
from autoposter.facts.models import GatheredFacts
from autoposter.jellyfin.client import JellyfinApi
from autoposter.jellyfin.writer import LOCKABLE, apply_facts
from autoposter.servers.base import ServerItemRef


def test_lockable_is_exactly_the_captured_metadata_field_members_that_map():
    # capture -> components -> schemas -> MetadataField: Cast, Genres,
    # ProductionLocations, Studios, Tags, Name, Overview, Runtime, OfficialRating
    assert set(LOCKABLE.values()) == {"Name", "Overview", "Genres", "Studios", "OfficialRating"}
    assert "sort_title" not in LOCKABLE and "critic_rating" not in LOCKABLE


class _Recorder:
    """Records every request an httpx.MockTransport handler sees, and answers
    GET with a fixed dto and POST with 204."""

    def __init__(self, dto: dict):
        self.dto = dto
        self.requests: list[httpx.Request] = []
        self.posted: dict | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Users":
            # The single-item read is made as a user (an API key has none);
            # one administrator answers that once per client.
            return httpx.Response(200, json=[{"Id": "u-admin", "Policy": {"IsAdministrator": True}}])
        self.requests.append(request)
        if request.method == "GET":
            assert request.url.params.get("userId") == "u-admin"
            return httpx.Response(200, json=self.dto)
        self.posted = json.loads(request.read())
        return httpx.Response(204)


async def _apply(dto: dict, facts, operations=None, overrides=None) -> tuple[_Recorder, dict]:
    recorder = _Recorder(dto)
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder)) as http:
        api = JellyfinApi(http, "https://jf", "k", "v")
        ref = ServerItemRef("jellyfin", "m1", "Movies", "movie")
        edits = await apply_facts(api, ref, facts, operations, None, overrides)
    return recorder, edits


@pytest.mark.asyncio
async def test_apply_facts_locks_only_the_lockable_fields_and_echoes_the_rest(caplog):
    # All four fields carry a "lock" verb: the verb REPLACES the provider
    # source for each of them (plex.writer.plan_edits' own rule), so none of
    # their VALUES change -- only their LockedFields membership does. This is
    # what proves the POST carries the WHOLE dto (untouched fields present,
    # unchanged) and not a partial patch.
    dto = {
        "Id": "m1", "Type": "Movie", "Name": "Old", "Overview": "o",
        "Genres": ["Action"], "Studios": [{"Name": "Old Studio"}],
        "OfficialRating": "PG", "ForcedSortName": "old", "LockedFields": [],
    }
    facts = GatheredFacts(
        studio="New Studio", genres=["Action", "Comedy"],
        content_rating="PG-13", sort_title="New Sort",
    )
    operations = OperationsConfig(
        field_verbs={"studio": "lock", "genres": "lock", "content_rating": "lock", "sort_title": "lock"},
        lock_apply=True,
    )
    with caplog.at_level(logging.INFO, logger="autoposter.jellyfin.writer"):
        recorder, edits = await _apply(dto, facts, operations)

    posted = recorder.posted
    assert posted is not None, "a change (LockedFields) must still POST"
    # Untouched fields are echoed unchanged -- the verb replaced their source.
    assert posted["Name"] == "Old"
    assert posted["Genres"] == ["Action"]
    assert posted["Studios"] == [{"Name": "Old Studio"}]
    assert posted["OfficialRating"] == "PG"
    assert posted["ForcedSortName"] == "old"
    # Only the three fields the captured MetadataField enum can lock.
    assert sorted(posted["LockedFields"]) == ["Genres", "OfficialRating", "Studios"]
    assert edits == {"studio": "locked", "genres": "locked", "content_rating": "locked"}
    # sort_title's lock is a no-op: exactly one INFO line names it.
    sort_title_lines = [r for r in caplog.records if "sort_title" in r.getMessage()]
    assert len(sort_title_lines) == 1


@pytest.mark.asyncio
async def test_the_write_line_names_an_episode_like_the_plex_line_does(caplog):
    """The same item, the same shape of line on both servers: title, show,
    season and episode, then the native id for looking it up in Jellyfin."""
    dto = {
        "Id": "9fdc32e6", "Type": "Episode", "Name": "Gordon & Spencer",
        "SeriesName": "Thomas the Tank Engine & Friends",
        "ParentIndexNumber": 7, "IndexNumber": 23, "CriticRating": 70.0,
        "LockedFields": [],
    }
    with caplog.at_level(logging.INFO, logger="autoposter.jellyfin.writer"):
        await _apply(dto, GatheredFacts(critic_rating=8.3))

    assert [r.getMessage() for r in caplog.records if "wrote" in r.getMessage()] == [
        "jellyfin: wrote critic_rating to episode 'Gordon & Spencer' "
        "(Thomas the Tank Engine & Friends S07E23) [m1]"
    ]


@pytest.mark.asyncio
async def test_the_write_line_names_a_movie_with_its_year(caplog):
    dto = {
        "Id": "m1", "Type": "Movie", "Name": "Heat", "ProductionYear": 1995,
        "CriticRating": 70.0, "LockedFields": [],
    }
    with caplog.at_level(logging.INFO, logger="autoposter.jellyfin.writer"):
        await _apply(dto, GatheredFacts(critic_rating=8.3))

    assert [r.getMessage() for r in caplog.records if "wrote" in r.getMessage()] == [
        "jellyfin: wrote critic_rating to movie 'Heat' (1995) [m1]"
    ]


@pytest.mark.asyncio
async def test_an_unchanged_dto_produces_no_post():
    dto = {
        "Id": "m1", "Type": "Movie", "Name": "Old", "Overview": "o",
        "Studios": [{"Name": "Same"}], "LockedFields": [],
    }
    recorder, edits = await _apply(dto, GatheredFacts(studio="Same"))
    assert recorder.posted is None
    assert edits == {}


@pytest.mark.asyncio
async def test_critic_rating_round_trips_the_0_to_100_and_0_to_10_scales():
    # Steady state: CriticRating=91 is Plex-scale 9.1, matching the fact
    # exactly -- proves the read side divides by 10 (a forgotten division
    # would read 91 and wrongly see a difference against 9.1).
    steady_dto = {"Id": "m1", "Type": "Movie", "CriticRating": 91, "LockedFields": []}
    recorder, edits = await _apply(steady_dto, GatheredFacts(critic_rating=9.1))
    assert recorder.posted is None
    assert edits == {}

    # A real change: CriticRating=87 (Plex-scale 8.7) differs from the fact's
    # 9.1, and the write side must multiply back up by 10.
    changed_dto = {"Id": "m1", "Type": "Movie", "CriticRating": 87, "LockedFields": []}
    recorder, edits = await _apply(changed_dto, GatheredFacts(critic_rating=9.1))
    assert recorder.posted["CriticRating"] == 91.0
    assert edits == {"critic_rating": 9.1}


@pytest.mark.asyncio
async def test_the_genre_plans_add_and_remove_keys_become_one_replaced_list():
    dto = {
        "Id": "m1", "Type": "Movie", "Genres": ["Action", "Horror"],
        "LockedFields": [],
    }
    recorder, edits = await _apply(dto, GatheredFacts(genres=["Action", "Comedy"]))
    assert recorder.posted["Genres"] == ["Action", "Comedy"]
    assert edits["genres"] == ["Action", "Comedy"]
    # A genuine genre change locks the field, mirroring Plex's own
    # addGenre/removeGenre(locked=True) -- otherwise the new list has no
    # protection against Jellyfin's own scanner reverting it.
    assert recorder.posted["LockedFields"] == ["Genres"]


@pytest.mark.asyncio
async def test_an_override_on_title_and_summary_writes_and_locks_both():
    # title/summary are real DTO properties an override can
    # set (WRITABLE_BY_KIND permits it), and dropping the value while still
    # honouring its paired lock would lock Name/Overview to a STALE value.
    dto = {
        "Id": "m1", "Type": "Movie", "Name": "Old Title", "Overview": "Old overview",
        "LockedFields": [],
    }
    overrides = {"title": "New Title", "summary": "New overview"}
    recorder, edits = await _apply(dto, GatheredFacts(), overrides=overrides)
    assert recorder.posted["Name"] == "New Title"
    assert recorder.posted["Overview"] == "New overview"
    assert sorted(recorder.posted["LockedFields"]) == ["Name", "Overview"]
    assert edits == {"title": "New Title", "summary": "New overview"}


@pytest.mark.asyncio
async def test_a_tagline_override_writes_the_value_with_no_lock_entry():
    # Tagline is not a captured MetadataField member (not in LOCKABLE), so an
    # override on it is value-only -- its paired ".locked" edit key has no
    # Jellyfin home and must not appear in LockedFields.
    dto = {"Id": "m1", "Type": "Movie", "Taglines": ["Old tagline"], "LockedFields": []}
    recorder, edits = await _apply(dto, GatheredFacts(), overrides={"tagline": "New tagline"})
    assert recorder.posted["Taglines"] == ["New tagline"]
    assert recorder.posted.get("LockedFields") == []
    assert edits == {"tagline": "New tagline"}
