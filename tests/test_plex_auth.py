import httpx
import pytest

from autoposter.plex.auth import PlexAuthError, PlexPinAuth


def _fake_http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_create_pin_posts_to_right_url_with_strong_and_headers():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        return httpx.Response(200, json={"id": 123, "code": "ABCD"})

    async with _fake_http(handler) as http:
        auth = PlexPinAuth(http, client_identifier="test-client-id")
        pin = await auth.create_pin()

    assert pin.id == 123
    assert pin.code == "ABCD"
    assert seen["method"] == "POST"
    assert seen["url"] == "https://plex.tv/api/v2/pins?strong=true"

    headers = seen["headers"]
    assert headers["Accept"] == "application/json"
    assert headers["X-Plex-Product"] == "autoposter"
    assert headers["X-Plex-Version"]
    assert headers["X-Plex-Client-Identifier"] == "test-client-id"
    assert headers["X-Plex-Device"]
    assert headers["X-Plex-Platform"]
    assert headers["X-Plex-Device-Name"] == "autoposter"


async def test_auth_url_contains_client_identifier_and_encoded_code():
    async def handler(request):
        return httpx.Response(200, json={"id": 1, "code": "x"})

    async with _fake_http(handler) as http:
        auth = PlexPinAuth(http, client_identifier="abc-123")
        url = auth.auth_url("some code/with+chars")

    assert "clientID=abc-123" in url
    assert "code=some+code%2Fwith%2Bchars" in url
    assert url.startswith("https://app.plex.tv/auth#?")


async def test_poll_returns_token_as_soon_as_it_appears():
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(200, json={"id": 1, "authToken": None})
        return httpx.Response(200, json={"id": 1, "authToken": "the-token"})

    async with _fake_http(handler) as http:
        auth = PlexPinAuth(http, client_identifier="test-client-id")
        token = await auth.poll_for_token(1, timeout_seconds=5, interval_seconds=0.01)

    assert token == "the-token"
    assert calls["n"] == 3


async def test_poll_returns_none_on_timeout():
    async def handler(request):
        return httpx.Response(200, json={"id": 1, "authToken": None})

    async with _fake_http(handler) as http:
        auth = PlexPinAuth(http, client_identifier="test-client-id")
        token = await auth.poll_for_token(1, timeout_seconds=0.05, interval_seconds=0.01)

    assert token is None


async def test_poll_uses_identical_client_identifier_as_create():
    create_headers = {}
    poll_headers = {}

    async def handler(request):
        if request.method == "POST":
            create_headers.update(request.headers)
            return httpx.Response(200, json={"id": 42, "code": "ZZZZ"})
        poll_headers.update(request.headers)
        return httpx.Response(200, json={"id": 42, "authToken": "tok"})

    async with _fake_http(handler) as http:
        auth = PlexPinAuth(http)
        pin = await auth.create_pin()
        token = await auth.poll_for_token(pin.id, timeout_seconds=5, interval_seconds=0.01)

    assert token == "tok"
    assert create_headers["x-plex-client-identifier"] == poll_headers["x-plex-client-identifier"]
    assert create_headers["x-plex-client-identifier"] == auth.client_identifier


async def test_create_pin_raises_on_non_2xx():
    async def handler(request):
        return httpx.Response(401)

    async with _fake_http(handler) as http:
        auth = PlexPinAuth(http, client_identifier="test-client-id")
        with pytest.raises(PlexAuthError):
            await auth.create_pin()


async def test_poll_raises_on_non_2xx_rather_than_returning_none():
    async def handler(request):
        return httpx.Response(500)

    async with _fake_http(handler) as http:
        auth = PlexPinAuth(http, client_identifier="test-client-id")
        with pytest.raises(PlexAuthError):
            await auth.poll_for_token(1, timeout_seconds=5, interval_seconds=0.01)
