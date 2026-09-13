import httpx
from autoposter.jellyfin.health import JellyfinHealth


async def test_401_marks_the_credential_rejected_not_merely_unhealthy():
    async def handler(request): return httpx.Response(401)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        h = JellyfinHealth("https://jf", "k", http)
        await h.check_liveness()
    assert h.healthy is False and h.credential_rejected is True and h.last_error == "status 401"


async def test_a_200_is_healthy_and_a_connect_error_names_the_class_only():
    async def ok(request):
        return httpx.Response(200, json={"Version": "12.0.0"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as http:
        h = JellyfinHealth("https://jf", "k", http)
        await h.check_liveness()
    assert h.healthy is True and h.credential_rejected is False
    async def boom(request):
        raise httpx.ConnectError("https://jf/System/Info")
    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        h = JellyfinHealth("https://jf", "k", http)
        await h.check_liveness()
    assert h.healthy is False and h.last_error == "ConnectError"
