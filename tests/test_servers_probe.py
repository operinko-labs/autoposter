"""One probe and one library read, shared by the wizard and the application.

The wizard owned both while it was the only caller. The Servers tab is the
second, and a copy is exactly the shape this bug takes: two probes that
disagree about what "refused" means, so a card says "connected" about a server
the wizard would have failed.
"""

import json

import httpx
import pytest

from autoposter.api import setup_checks
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


async def test_each_server_states_its_version_on_its_own_path():
    """Spec section 5's connection pill. Plex states it on ``/identity``, which
    the section list the probe reads does not carry; Jellyfin states it in the
    very body the probe already asks for."""

    def handler(request):
        if request.url.path == "/identity":
            return httpx.Response(
                200,
                content=json.dumps({"MediaContainer": {"version": "1.41.2"}}).encode(),
            )
        if request.url.path == "/System/Info":
            return httpx.Response(200, content=json.dumps({"Version": "10.11.0"}).encode())
        return httpx.Response(200, content=_plex_sections())

    transport = httpx.MockTransport(handler)
    plex = await probe.check_server("plex", "http://plex:32400", "tok", transport=transport)
    assert plex.ok is True and plex.version == "1.41.2"

    jellyfin = await probe.check_server("jellyfin", "http://jf:8096", "k", transport=transport)
    assert jellyfin.ok is True and jellyfin.version == "10.11.0"


async def test_a_version_longer_than_a_version_is_not_passed_on():
    """The one value these probes answer with that is the third party's own
    text, so it is bounded rather than trusted: too long is answered as none
    rather than truncated, which would report a version no server stated."""
    body = json.dumps({"Version": "9" * (setup_checks.VERSION_LIMIT_CHARS + 1)}).encode()
    result = await probe.check_server(
        "jellyfin",
        "http://jf:8096",
        "k",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )
    assert result.ok is True and result.version is None


async def test_a_refused_server_is_not_asked_for_a_version():
    """The version is a second question, asked only of a server that answered:
    a refusal must not turn into a second outbound call."""
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(401)

    result = await probe.check_server(
        "jellyfin", "http://jf:8096", "bad", transport=httpx.MockTransport(handler)
    )
    assert result.refused is True and result.version is None
    assert len(seen) == 1


async def test_a_jellyfin_folder_with_null_fields_is_served_with_empty_ones():
    """``ItemId`` and ``CollectionType`` are nullable in Jellyfin's own schema,
    and a key that is PRESENT and null takes the null rather than a ``get``
    default -- which served the literal string "None" as an id and a null on a
    field typed ``str``. The folder is still listed, because the operator can
    see it in their own server, and an empty kind is in neither server's
    indexable set, so it cannot be paired into a library map."""
    body = json.dumps(
        [{"ItemId": None, "Name": "Odd", "CollectionType": None}]
    ).encode()
    libraries = await probe.list_libraries(
        "jellyfin",
        "http://jf:8096",
        "key",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )
    assert libraries == [probe.Library(id="", name="Odd", kind="")]


async def test_an_unknown_server_is_refused_by_name():
    with pytest.raises(ValueError, match=probe.NOT_A_SERVER):
        await probe.check_server("emby", "http://emby", "k")


def test_the_wizard_reads_its_credential_names_from_here():
    """One copy. `api/setup.py`'s own map is this one, imported."""
    from autoposter.api import setup

    assert setup._SERVER_CREDENTIAL is probe.SERVER_CREDENTIAL
