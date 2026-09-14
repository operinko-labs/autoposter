"""The two automatic triggers (spec §3)."""
from types import SimpleNamespace

from autoposter import catchup, deliveries
from autoposter.render import pipeline

from conftest import seed_media_item


def _config(
    *, library_map=None, excluded=None, plex=True, jellyfin=True,
    write_to_plex=True, write_to_jellyfin=True,
    upload_to_plex=True, upload_to_jellyfin=True, libraries=None,
):
    """A config-shaped stand-in carrying only what the predicate reads.

    ``operations``/``badges``/``libraries`` are here because the delivery
    toggles live there: the predicate reads every one of them with
    ``getattr(..., None)``, so a config that lacks the section is safe, but a
    test that wants to flip a toggle needs the section to exist.
    """
    return SimpleNamespace(
        plex=SimpleNamespace(excluded_libraries=["Home Videos"]) if plex else None,
        jellyfin=SimpleNamespace(
            library_map=dict(library_map or {}),
            excluded_libraries=list(excluded or []),
        ) if jellyfin else None,
        operations=SimpleNamespace(
            write_to_plex=write_to_plex, write_to_jellyfin=write_to_jellyfin,
        ),
        badges=SimpleNamespace(
            upload_to_plex=upload_to_plex, upload_to_jellyfin=upload_to_jellyfin,
        ),
        libraries=dict(libraries or {}),
        configured_servers=[n for n, on in (("plex", plex), ("jellyfin", jellyfin)) if on],
    )


def _library(*, operations=None, badges=None):
    return SimpleNamespace(
        operations=SimpleNamespace(**operations) if operations is not None else None,
        badges=SimpleNamespace(**badges) if badges is not None else None,
    )


def test_a_changed_library_map_names_that_server():
    old = _config(library_map={"Movies": "Films"})
    new = _config(library_map={"Movies": "Movies"})
    assert catchup.servers_with_changed_libraries(old, new) == ["jellyfin"]


def test_changed_exclusions_name_that_server():
    assert catchup.servers_with_changed_libraries(
        _config(excluded=[]), _config(excluded=["Photos"])
    ) == ["jellyfin"]


def test_an_unrelated_save_names_nobody():
    assert catchup.servers_with_changed_libraries(_config(), _config()) == []


def test_a_server_that_only_appears_in_one_config_is_left_to_the_boot_trigger():
    assert catchup.servers_with_changed_libraries(
        _config(jellyfin=False), _config()
    ) == []


def test_switching_a_delivery_toggle_on_names_that_server():
    """Controller ruling (task 10's review): the rows the pipeline skipped
    while the toggle was off are owed to that server the moment it is on, and
    nothing but the next full pass would otherwise heal them."""
    assert catchup.servers_with_changed_libraries(
        _config(write_to_jellyfin=False), _config(write_to_jellyfin=True)
    ) == ["jellyfin"]
    assert catchup.servers_with_changed_libraries(
        _config(upload_to_plex=False), _config(upload_to_plex=True)
    ) == ["plex"]


def test_switching_a_delivery_toggle_off_names_nobody():
    """The other direction owes nothing: a server that is no longer written to
    has no backlog to catch up on, and a run for it would mark rows the
    pipeline is now configured to skip."""
    assert catchup.servers_with_changed_libraries(
        _config(write_to_jellyfin=True), _config(write_to_jellyfin=False)
    ) == []
    assert catchup.servers_with_changed_libraries(
        _config(upload_to_plex=True), _config(upload_to_plex=False)
    ) == []


def test_a_per_library_toggle_switched_on_names_that_server():
    """The same flip one level down. A library's override is the value that
    library actually runs on, so a global that never moved does not make the
    change invisible."""
    off = _config(
        write_to_jellyfin=False,
        libraries={"Movies": _library(operations={"write_to_jellyfin": False})},
    )
    on = _config(
        write_to_jellyfin=False,
        libraries={"Movies": _library(operations={"write_to_jellyfin": True})},
    )
    assert catchup.servers_with_changed_libraries(off, on) == ["jellyfin"]
    assert catchup.servers_with_changed_libraries(on, off) == []


def test_a_config_without_the_toggle_sections_names_nobody():
    """The predicate is handed whatever two objects the swap holds, including
    a generation that predates a section. Absent must read as "no flip",
    never as a raise."""
    # The same library shape as ``_config`` builds, so the only difference
    # between the two is the missing sections.
    bare = SimpleNamespace(
        plex=SimpleNamespace(excluded_libraries=["Home Videos"]),
        jellyfin=SimpleNamespace(library_map={}, excluded_libraries=[]),
    )
    assert catchup.servers_with_changed_libraries(bare, bare) == []
    assert catchup.servers_with_changed_libraries(bare, _config(excluded=[])) == []


async def test_a_server_with_no_recorded_outcome_is_reported_on_a_populated_library(session):
    item = await seed_media_item(session, "t1", title="A")
    render = await pipeline._get_or_create_render(session, item, "poster", "/a/p.jpg")
    await deliveries.record(session, render.id, "plex", "uploaded", fingerprint="fp1")
    await session.commit()

    assert await catchup.servers_never_seen(session, ["plex", "jellyfin"]) == ["jellyfin"]


async def test_nothing_is_reported_on_an_empty_library(session):
    assert await catchup.servers_never_seen(session, ["plex", "jellyfin"]) == []


async def test_a_metadata_row_alone_counts_as_seen(session):
    item = await seed_media_item(session, "t2", title="A")
    await deliveries.record_metadata(session, item.id, "jellyfin", "written")
    await session.commit()

    assert await catchup.servers_never_seen(session, ["jellyfin"]) == []
