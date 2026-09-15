"""One probe and one library read, shared by the wizard and the application.

The wizard owned both while it was the only caller. The Servers tab is the
second, and a copy is exactly the shape this bug takes: two probes that
disagree about what "refused" means, so a card says "connected" about a server
the wizard would have failed.
"""

import json

import httpx
import pytest

from autoposter.servers import probe


def _plex_sections() -> bytes:
    return json.dumps(
        {
            "MediaContainer": {
                "Directory": [
                    {"key": "1", "title": "Movies", "type": "movie"},
                    {"key": "2", "title": "TV Shows", "type": "show"},
                ]
            }
        }
    ).encode()


def _jellyfin_folders() -> bytes:
    return json.dumps(
        [
            {
                "ItemId": "abc",
                "Name": "Movies",
                "CollectionType": "movies",
                "Locations": ["/data/movies"],
            },
        ]
    ).encode()


async def test_a_plex_check_that_answers_is_ok():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=_plex_sections()))
    result = await probe.check_server("plex", "http://plex:32400", "tok", transport=transport)
    assert result.ok is True and result.refused is False
    assert result.detail == probe.ANSWERED.format(system="Plex")


async def test_a_refused_credential_is_refused_not_unreachable():
    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    result = await probe.check_server("jellyfin", "http://jf:8096", "bad", transport=transport)
    assert result.ok is False and result.refused is True
    assert result.detail == probe.REFUSED.format(system="Jellyfin")


async def test_an_unreachable_server_reports_a_class_name_and_no_url():
    def boom(request):
        raise httpx.ConnectError("connection refused to http://jf:8096/System/Info")

    result = await probe.check_server(
        "jellyfin", "http://jf:8096", "k", transport=httpx.MockTransport(boom)
    )
    assert result.ok is False and result.refused is False
    assert result.failure == "ConnectError"
    assert "http://" not in result.detail


async def test_plex_libraries_arrive_in_the_shared_shape():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=_plex_sections()))
    libraries = await probe.list_libraries(
        "plex", "http://plex:32400", "tok", client_identifier="id", transport=transport
    )
    assert libraries == [
        probe.Library(id="1", name="Movies", kind="movie"),
        probe.Library(id="2", name="TV Shows", kind="show"),
    ]


async def test_jellyfin_libraries_arrive_in_the_same_shape_and_carry_no_paths():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=_jellyfin_folders())
    )
    libraries = await probe.list_libraries(
        "jellyfin", "http://jf:8096", "key", transport=transport
    )
    assert libraries == [probe.Library(id="abc", name="Movies", kind="movies")]


async def test_an_unknown_server_is_refused_by_name():
    with pytest.raises(ValueError, match=probe.NOT_A_SERVER):
        await probe.check_server("emby", "http://emby", "k")


def test_the_wizard_reads_its_credential_names_from_here():
    """One copy. `api/setup.py`'s own map is this one, imported."""
    from autoposter.api import setup

    assert setup._SERVER_CREDENTIAL is probe.SERVER_CREDENTIAL
