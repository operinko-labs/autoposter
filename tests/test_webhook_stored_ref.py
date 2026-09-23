"""Perf workstream B3: a webhook intent resolves by its stored Plex ref.

Sonarr and Radarr know nothing about any server, so a webhook intent reached
Plex's per-section ``getGuid`` walk (a search, a match and a second search per
section per guid) even for an item this service has resolved a hundred times.
``process_item`` now puts the stored rating key on the intent as the same hint
the full pass carries; ``PlexClient`` already falls back to the walk when the
hint is refused or NotFound (tests/test_plex.py pins that half).
"""
from autoposter import deliveries
from autoposter.config.loader import load_config
from autoposter.db.refs import native_id_by_external_ids
from autoposter.facts.mdblist import NullMDBListClient
from autoposter.intake.arr import RenderIntent
from autoposter.render import pipeline as pipeline_module
from autoposter.servers.presence import ABSENT_DETAIL
from autoposter.servers.registry import Servers
from conftest import seed_media_item
from media_server_doubles import FakeMediaServer, resolved as fake_resolved
from test_pipeline import (
    EXAMPLE,
    INTENT,
    _MinimalTMDBFacts,
    _fake_compose,
    _fake_render_artifact,
    _two_servers,
)


def _ids(**overrides):
    fields = dict(kind="movie", tmdb_id=None, tvdb_id=None, season_number=None, episode_number=None)
    fields.update(overrides)
    return fields


async def test_one_stored_item_answers_its_plex_ref(session):
    await seed_media_item(session, "p1", title="A", tmdb_id=4201)
    assert await native_id_by_external_ids(session, "plex", **_ids(tmdb_id=4201)) == "p1"


async def test_the_same_movie_in_two_libraries_answers_nothing(session):
    """Which library a webhook means is the walk's answer (section order),
    so two candidates give no hint and today's behaviour stands."""
    await seed_media_item(session, "p1", library="Movies", title="A", tmdb_id=4202)
    await seed_media_item(session, "p2", library="4K Movies", title="A", tmdb_id=4202)
    assert await native_id_by_external_ids(session, "plex", **_ids(tmdb_id=4202)) is None


async def test_an_episode_matches_on_its_numbers(session):
    await seed_media_item(session, "e1", kind="episode", library="TV", title="E1",
                          tvdb_id=4203, season_number=1, episode_number=1)
    await seed_media_item(session, "e2", kind="episode", library="TV", title="E2",
                          tvdb_id=4203, season_number=1, episode_number=2)
    found = await native_id_by_external_ids(
        session, "plex", **_ids(kind="episode", tvdb_id=4203, season_number=1, episode_number=2),
    )
    assert found == "e2"


async def test_a_show_sharing_a_movies_tmdb_number_does_not_hide_the_movie(session):
    """TMDB numbers movies and TV separately, so one number can name both a
    movie and a show. The kind keeps them apart: a movie lookup finds only
    the movie, and the show's row does not make the answer ambiguous."""
    await seed_media_item(session, "s1", kind="show", library="TV", title="S", tmdb_id=4205)
    await seed_media_item(session, "m1", title="M", tmdb_id=4205)
    assert await native_id_by_external_ids(session, "plex", **_ids(tmdb_id=4205)) == "m1"


async def test_no_external_id_answers_nothing(session):
    await seed_media_item(session, "p1", title="A")
    assert await native_id_by_external_ids(session, "plex", **_ids()) is None


async def test_another_servers_ref_is_not_a_plex_ref(session):
    await seed_media_item(session, "j1", server="jellyfin", title="A", tmdb_id=4204)
    assert await native_id_by_external_ids(session, "plex", **_ids(tmdb_id=4204)) is None


def _recording(server):
    seen = []
    original = server.resolve

    async def resolve(intent):
        seen.append(intent)
        return await original(intent)

    server.resolve = resolve
    return seen


async def test_a_webhook_intent_reaches_plex_with_its_stored_ref(
    session, config_with_badges, monkeypatch
):
    await seed_media_item(session, "p1", title="Title", tmdb_id=1)
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    plex = FakeMediaServer(name="plex", resolve_any=fake_resolved("plex", "p1"))
    seen = _recording(plex)

    await pipeline_module.process_item(
        session, config_with_badges, None, Servers({"plex": plex}), [],
        RenderIntent(kind="movie", title="Title", tmdb_id=1, year=2020),
    )

    assert [intent.refs for intent in seen] == [{"plex": "p1"}]


async def test_a_webhook_intent_skips_a_server_whose_row_is_absent(session, monkeypatch):
    """B3's side effect, pinned: with the stored ref on the intent, the absent
    set is read BEFORE the resolve fan-out for a webhook too (spec §1), so a
    server that does not carry the item's library is asked nothing -- the
    full pass's behaviour, now also the webhook's."""
    config = load_config(EXAMPLE)
    config.operations.write_to_jellyfin = True
    config.badges.enabled = False
    servers, plex, jf = _two_servers()
    media_item = await pipeline_module._upsert_media_item(session, plex.items[INTENT.dedupe_key])
    await deliveries.record_metadata(
        session, media_item.id, "jellyfin", "absent", detail=ABSENT_DETAIL
    )
    await session.commit()
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    assert INTENT.refs == {}, "the intent must be webhook-shaped: no refs of its own"
    await pipeline_module.process_item(
        session, config, None, servers, [], INTENT,
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(),
    )

    assert jf.resolve_calls == 0, "an absent server is asked nothing, webhook or not"
    assert plex.resolve_calls == 1


async def test_an_intent_that_carries_a_ref_keeps_it(session, config_with_badges, monkeypatch):
    await seed_media_item(session, "p1", title="Title", tmdb_id=1)
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    plex = FakeMediaServer(name="plex", resolve_any=fake_resolved("plex", "p1"))
    seen = _recording(plex)

    await pipeline_module.process_item(
        session, config_with_badges, None, Servers({"plex": plex}), [],
        RenderIntent(kind="movie", title="Title", tmdb_id=1, year=2020, refs={"plex": "p9"}),
    )

    assert [intent.refs for intent in seen] == [{"plex": "p9"}]


async def test_an_unknown_webhook_item_still_resolves_without_a_hint(
    session, config_with_badges, monkeypatch
):
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    plex = FakeMediaServer(name="plex", resolve_any=fake_resolved("plex", "p1"))
    seen = _recording(plex)

    await pipeline_module.process_item(
        session, config_with_badges, None, Servers({"plex": plex}), [],
        RenderIntent(kind="movie", title="Title", tmdb_id=1, year=2020),
    )

    assert [intent.refs for intent in seen] == [{}]
