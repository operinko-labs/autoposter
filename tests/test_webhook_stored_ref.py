"""Perf workstream B3: a webhook intent resolves by its stored Plex ref.

Sonarr and Radarr know nothing about any server, so a webhook intent reached
Plex's per-section ``getGuid`` walk (a search, a match and a second search per
section per guid) even for an item this service has resolved a hundred times.
``process_item`` now puts the stored rating key on the intent as the same hint
the full pass carries; ``PlexClient`` already falls back to the walk when the
hint is refused or NotFound (tests/test_plex.py pins that half).
"""
from autoposter.db.refs import native_id_by_external_ids
from autoposter.intake.arr import RenderIntent
from autoposter.render import pipeline as pipeline_module
from autoposter.servers.registry import Servers
from conftest import seed_media_item
from media_server_doubles import FakeMediaServer, resolved as fake_resolved
from test_pipeline import _fake_compose, _fake_render_artifact


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
