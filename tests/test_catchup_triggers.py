"""The two automatic triggers (spec §3)."""
from types import SimpleNamespace

from autoposter import catchup, deliveries
from autoposter.render import pipeline

from conftest import seed_media_item


def _config(
    *, library_map=None, excluded=None, plex=True, jellyfin=True,
    write_to_plex=True, write_to_jellyfin=True,
    upload_to_plex=True, upload_to_jellyfin=True, libraries=None,
    operations_enabled=True, badges_enabled=True,
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
            enabled=operations_enabled,
            write_to_plex=write_to_plex, write_to_jellyfin=write_to_jellyfin,
        ),
        badges=SimpleNamespace(
            enabled=badges_enabled,
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


def test_a_library_scope_that_exists_only_in_the_new_config_falls_back_to_the_old_global():
    """The ordinary Settings move: an operator adds a per-library override for
    the first time. The library had no block before, so what it was running on
    is the old GLOBAL value, and that is what the flip is measured against."""
    assert catchup.servers_with_changed_libraries(
        _config(write_to_jellyfin=False),
        _config(
            write_to_jellyfin=False,
            libraries={"Movies": _library(operations={"write_to_jellyfin": True})},
        ),
    ) == ["jellyfin"]


def test_a_new_only_library_scope_that_switches_the_toggle_off_names_nobody():
    """The mirror of the above, and the reason the fallback has to be the old
    global rather than "unknown": a first-time override that turns delivery
    OFF for one library owes that server nothing."""
    assert catchup.servers_with_changed_libraries(
        _config(write_to_jellyfin=True),
        _config(
            write_to_jellyfin=True,
            libraries={"Movies": _library(operations={"write_to_jellyfin": False})},
        ),
    ) == []


def test_dropping_a_library_block_that_held_the_toggle_off_names_that_server():
    """The scope disappears rather than its field being nulled (review M2). The
    library falls back to the new global, which is True, so its effective value
    went False -> True and it is owed every row skipped while the block stood.
    A predicate that walked only the new config's scopes would never look."""
    off = _config(
        write_to_jellyfin=True,
        libraries={"Movies": _library(operations={"write_to_jellyfin": False})},
    )
    on = _config(write_to_jellyfin=True)
    assert catchup.servers_with_changed_libraries(off, on) == ["jellyfin"]
    assert catchup.servers_with_changed_libraries(on, off) == []


def test_a_server_with_every_delivery_toggle_off_is_not_delivery_enabled():
    """The boot trigger's filter (review M1). A catch-up for such a server
    writes no outcome row, so `servers_never_seen` would report it again on
    the next boot, and the next, forever."""
    off = _config(
        write_to_jellyfin=False, upload_to_jellyfin=False,
        write_to_plex=False, upload_to_plex=False,
    )
    assert catchup.servers_with_delivery_enabled(off, ["plex", "jellyfin"]) == []
    assert catchup.servers_with_delivery_enabled(
        _config(), ["plex", "jellyfin"]
    ) == ["plex", "jellyfin"]


def test_one_library_that_switches_a_toggle_on_makes_that_server_delivery_enabled():
    """ON in any scope is enough: that library's rows are owed to it."""
    config = _config(
        write_to_jellyfin=False, upload_to_jellyfin=False,
        write_to_plex=False, upload_to_plex=False,
        libraries={"Movies": _library(badges={"upload_to_jellyfin": True})},
    )
    assert catchup.servers_with_delivery_enabled(config, ["plex", "jellyfin"]) == ["jellyfin"]


def test_a_switched_off_section_counts_as_off_for_its_half():
    """`operations.enabled` and `badges.enabled` are the master switches: a
    `write_to_jellyfin: true` under a disabled operations section delivers
    nothing, and a server whose only live toggle is that one is not one this
    deployment writes to."""
    config = _config(
        operations_enabled=False, badges_enabled=False,
        write_to_jellyfin=True, upload_to_jellyfin=True,
    )
    assert catchup.servers_with_delivery_enabled(config, ["jellyfin"]) == []
    assert catchup.servers_with_delivery_enabled(
        _config(badges_enabled=False, upload_to_jellyfin=True, write_to_jellyfin=True),
        ["jellyfin"],
    ) == ["jellyfin"], "operations is still on, so the server is still written to"


def test_an_unknown_server_name_is_never_filtered_out():
    """This module knows two servers' toggles. A name it has none for is not
    one it is entitled to silence."""
    assert catchup.servers_with_delivery_enabled(
        _config(write_to_plex=False, upload_to_plex=False), ["emby"]
    ) == ["emby"]


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
