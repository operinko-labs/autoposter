import httpx

from autoposter.plex.health import PlexHealth


def _fake_http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_healthy_server_marks_healthy_and_records_success():
    async def handler(request):
        assert request.url.path == "/identity"
        return httpx.Response(200, json={"MediaContainer": {}})

    async with _fake_http(handler) as http:
        health = PlexHealth(url="https://plex.example.com", token="tok", http=http)
        await health.check_liveness()

        assert health.healthy is True
        assert health.last_error is None
        assert health.last_success is not None


async def test_non_2xx_marks_unhealthy():
    async def handler(request):
        return httpx.Response(500)

    async with _fake_http(handler) as http:
        health = PlexHealth(url="https://plex.example.com", token="tok", http=http)
        await health.check_liveness()

        assert health.healthy is False
        assert "500" in health.last_error


async def test_transport_error_marks_unhealthy():
    async def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    async with _fake_http(handler) as http:
        health = PlexHealth(url="https://plex.example.com", token="tok", http=http)
        await health.check_liveness()

        assert health.healthy is False
        assert health.last_error is not None


async def test_a_transport_error_is_held_as_its_class_name_only(caplog):
    """last_error is not served by anything today, and is kept
    class-name-only so that stays true the day something serves it: an
    httpx error's str() embeds the request URL. The transition WARNING
    keeps the message -- the pod log, the trusted sink."""

    async def handler(request):
        raise httpx.ConnectError(
            "[Errno 111] Connection refused to http://plex.internal:32400/identity",
            request=request,
        )

    async with _fake_http(handler) as http:
        health = PlexHealth(url="https://plex.example.com", token="tok", http=http)
        with caplog.at_level("WARNING"):
            await health.check_liveness()

    assert health.healthy is False
    assert health.last_error == "ConnectError"
    assert "plex.internal" not in health.last_error
    assert "32400" not in health.last_error
    assert (
        "is unreachable: ConnectError: [Errno 111] Connection refused to "
        "http://plex.internal:32400/identity"
    ) in caplog.text


async def test_recovery_flips_state_back():
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200)

    async with _fake_http(handler) as http:
        health = PlexHealth(url="https://plex.example.com", token="tok", http=http)
        await health.check_liveness()
        assert health.healthy is False

        await health.check_liveness()
        assert health.healthy is True


async def test_transitions_log_once_per_change_not_per_poll(caplog):
    async def handler(request):
        return httpx.Response(500)

    async with _fake_http(handler) as http:
        health = PlexHealth(url="https://plex.example.com", token="tok", http=http)
        with caplog.at_level("WARNING"):
            await health.check_liveness()
            await health.check_liveness()
            await health.check_liveness()

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1


async def test_recovery_logs_a_single_info_transition(caplog):
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] == 1 else httpx.Response(200)

    async with _fake_http(handler) as http:
        health = PlexHealth(url="https://plex.example.com", token="tok", http=http)
        with caplog.at_level("INFO"):
            await health.check_liveness()  # unhealthy
            await health.check_liveness()  # recovers
            await health.check_liveness()  # still healthy, no new log

    recovery_logs = [r for r in caplog.records if "reachable again" in r.message]
    assert len(recovery_logs) == 1


async def test_myplexaccount_ping_failure_logs_warning_and_leaves_server_healthy(
    monkeypatch, caplog
):
    # Most important test in the set: plex.tv failures (or a server-scoped
    # token) must never touch server health or job processing.
    async def handler(request):
        return httpx.Response(200)

    def boom(self):
        raise RuntimeError("plex.tv unreachable")

    monkeypatch.setattr("autoposter.plex.health.MyPlexAccount.__init__", lambda self, **kwargs: None)
    monkeypatch.setattr("autoposter.plex.health.MyPlexAccount.ping", boom)

    async with _fake_http(handler) as http:
        health = PlexHealth(url="https://plex.example.com", token="tok", http=http)
        await health.check_liveness()
        assert health.healthy is True

        with caplog.at_level("WARNING"):
            await health.refresh_token()

        assert health.healthy is True
        assert any("token refresh failed" in r.message.lower() for r in caplog.records)


async def test_token_refresh_skipped_when_disabled(monkeypatch):
    async def handler(request):
        return httpx.Response(200)

    called = {"n": 0}

    monkeypatch.setattr(
        "autoposter.plex.health.MyPlexAccount.__init__",
        lambda self, **kwargs: called.__setitem__("n", called["n"] + 1),
    )
    monkeypatch.setattr("autoposter.plex.health.MyPlexAccount.ping", lambda self: None)

    async with _fake_http(handler) as http:
        health = PlexHealth(
            url="https://plex.example.com", token="tok", http=http, refresh_enabled=False
        )
        await health.refresh_token()

    assert called["n"] == 0
