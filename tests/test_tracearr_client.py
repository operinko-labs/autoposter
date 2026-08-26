"""Tracearr's public v2 read API: the transport under the activity builder.

Never touches the real instance -- MockTransport only.

Four properties carry the weight here, and each is a way a *silent* wrong
answer could reach a sync-mode collection or a credential could reach a log:

- **the single-page-app 200.** Tracearr serves its SPA on every unmatched path,
  so a mistyped base URL answers ``200 text/html`` rather than a 404. Every
  matched v2 route carries ``x-ratelimit-*`` headers and both fallbacks carry
  none, so header presence is the route-matched tell (harvest
  ``docs/research/tracearr-api-harvest.md:669-678``).
- **cursor paging is followed and capped.** ``meta.nextCursor`` goes back as
  ``cursor`` until it is null; the cap keeps a runaway upstream bounded.
- **a 404 raises rather than returning nothing.** ``fetch_json`` turns 404 into
  ``None`` -- right for artwork, and an empty membership one layer down means
  "remove every member".
- **neither the base URL nor the API key can reach a log.** The engine logs a
  failed build with ``logger.exception``, traceback included, and
  ``httpx.HTTPStatusError`` puts the whole URL in its own message.

Fixtures: ``tracearr_media_show.json`` / ``tracearr_media_movie.json`` are the
verbatim ``body`` objects of ``docs/research/tracearr/payloads/
v2-media-show-by-uuid.json`` and ``v2-media-movie-by-uuid.json``. The paging
test walks ``tracearr_history_window.json`` (the banked 50-record window, cursor
included) into ``tracearr_history_page2.json`` (the page the harvest got by
following that cursor) and out through ``tracearr_history_end.json`` -- the one
CONSTRUCTED fixture in the set, because the live instance's history never ran
out inside a page budget and no empty page was ever banked.
"""
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from autoposter.collections.service import build_source_clients
from autoposter.config.schema import Config, Secrets, TracearrConfig
from autoposter.providers.tracearr import (
    API_PREFIX,
    MAX_PAGES,
    TracearrClient,
    TracearrNotFound,
    TracearrRefused,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"

BASE_URL = "http://tracearr.test.invalid"
API_KEY = "trr_pub_test"
SHOW_UUID = "faf036e2-8459-4ace-a8ba-19486b6289c6"

# The cursor ``tracearr_history_page2.json`` carried when it was banked
# (``docs/research/tracearr/payloads/v2-history-page2-cursor.json``). The fixture
# cut nulled it so the file stands alone as a single page; the paging test below
# restores it rather than inventing one, so every value in that walk is
# Tracearr's own.
PAGE2_CURSOR = (
    "eyJ0IjoiMjAyNi0wOC0xOVQxMTo1NDo0NC4wMDBaIiwiaWQiOiIyMjIyMjIyMi0yMjIyLTQyMjItO"
    "DIyMi0wMDAwMDAwMDAwMjEifQ"
)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _matched(payload, status=200):
    """A response from a route that matched: rate-limit headers present.

    The headers are what tell a matched route from the SPA fallback, so a test
    helper that forgot them would make every test look like the trap.
    """
    return httpx.Response(
        status,
        json=payload,
        headers={
            "x-ratelimit-limit": "240",
            "x-ratelimit-remaining": "237",
            "x-ratelimit-reset": "60",
        },
    )


def _routed(routes: dict, seen: list | None = None, default=None):
    """Serve ``{path: response-or-callable}``; anything else is the SPA."""

    def handler(request):
        if seen is not None:
            seen.append(request)
        entry = routes.get(request.url.path)
        if entry is None:
            return default or httpx.Response(
                200, text="<!doctype html><html><head><title>Tracearr</title>",
                headers={"content-type": "text/html"},
            )
        if callable(entry):
            return entry(request)
        return _matched(entry)

    return httpx.MockTransport(handler)


def _client(http, **kwargs):
    return TracearrClient(http, BASE_URL, API_KEY, **kwargs)


def _history(records, cursor=None):
    return {"data": records, "meta": {"nextCursor": cursor, "pageSize": 100}}


# --- the request itself -------------------------------------------------------


async def test_the_key_travels_as_a_bearer_token_on_the_v2_public_prefix():
    seen: list = []
    routes = {f"{API_PREFIX}/history": _history([])}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert seen[0].url.path == "/api/v2/public/history"
    assert seen[0].url.host == "tracearr.test.invalid"
    assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert seen[0].url.params["since"] == "2026-07-26T00:00:00Z"
    assert seen[0].url.params["media_type"] == "movie"
    assert seen[0].url.params["pageSize"] == "100"


async def test_a_trailing_slash_on_the_base_url_does_not_double_up():
    seen: list = []
    routes = {f"{API_PREFIX}/history": _history([])}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        client = TracearrClient(http, BASE_URL + "/", API_KEY)
        await client.history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert seen[0].url.path == "/api/v2/public/history"


# --- paging -------------------------------------------------------------------


async def test_history_follows_the_cursor_until_it_is_null():
    """The cursor is opaque and embeds a session identifier, so it is handed
    straight back and never inspected.

    Walked over the banked payloads rather than two invented records: page one
    is the 50-record window with Tracearr's own cursor on it, page two is the
    page the harvest got by handing that cursor back, and the empty terminal
    page ends the walk. The two banked pages were captured at different page
    sizes, so they overlap by id -- and nothing here de-duplicates. The client
    returns what it was handed, in order; what a record *means* is the ranking
    module's business one layer up.
    """
    seen: list = []
    page1 = load("tracearr_history_window.json")
    page2 = load("tracearr_history_page2.json") | {
        "meta": {"nextCursor": PAGE2_CURSOR, "pageSize": 10}
    }
    pages = iter([page1, page2, load("tracearr_history_end.json")])
    routes = {f"{API_PREFIX}/history": lambda request: _matched(next(pages))}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        records = await _client(http).history(
            since="2026-07-26T00:00:00Z", media_type="episode"
        )

    assert [record["id"] for record in records] == [
        record["id"] for record in page1["data"] + page2["data"]
    ]
    assert len(seen) == 3
    assert "cursor" not in seen[0].url.params
    assert seen[1].url.params["cursor"] == page1["meta"]["nextCursor"]
    assert seen[2].url.params["cursor"] == PAGE2_CURSOR


async def test_history_stops_at_the_page_cap_and_says_so(caplog):
    """A truncated answer that says nothing looks exactly like a complete one
    -- the ``tmdb_lists``/``imdb_lists`` judgement, one API over."""
    routes = {f"{API_PREFIX}/history": lambda request: _matched(
        _history([{"id": "x"}], cursor="ALWAYS-MORE")
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with caplog.at_level(logging.WARNING):
            records = await _client(http, max_pages=3).history(
                since="2026-07-26T00:00:00Z", media_type="episode"
            )

    assert len(records) == 3
    assert "3-page cap" in caplog.text
    assert BASE_URL not in caplog.text
    assert API_KEY not in caplog.text


async def test_the_default_page_cap_is_ten():
    assert MAX_PAGES == 10


# --- the SPA trap and the two error envelopes ---------------------------------


async def test_a_200_with_no_rate_limit_header_is_the_spa_and_not_data():
    """The trap this client exists to survive: a mistyped base URL answers 200
    with the app's own index.html, and a client that decoded it as an empty
    collection would remove every member on the next pass."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert "x-ratelimit-limit" in str(error.value)
    assert BASE_URL not in str(error.value)


async def test_the_bare_envelope_404_is_a_misrouted_request_not_a_missing_item():
    """``{"error": "Not Found"}`` comes from the not-found fallback, which sits
    outside the rate-limit plugin -- so it is caught by the same header check
    rather than being mistaken for "this media does not exist"."""
    routes = {f"{API_PREFIX}/history": lambda request: httpx.Response(
        404, json={"error": "Not Found"}
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert "x-ratelimit-limit" in str(error.value)


async def test_a_matched_404_raises_its_own_class_rather_than_answering_nothing():
    """``fetch_json`` turns a 404 into ``None``. For a collection that would
    mean "remove every member", so every method here raises instead.

    A *subclass*, because a matched 404 is the one refusal a caller can
    sometimes recover from -- one title of twenty having been deleted. It is
    still a ``TracearrRefused``, so a caller that does not care about the
    distinction is unaffected.
    """
    routes = {f"{API_PREFIX}/media/{SHOW_UUID}": lambda request: _matched(
        {"statusCode": 404, "error": "NotFoundError", "message": "Not Found"}, status=404
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrNotFound) as error:
            await _client(http).media(SHOW_UUID)

    assert isinstance(error.value, TracearrRefused)
    assert "404" in str(error.value)
    assert BASE_URL not in str(error.value)


async def test_the_spa_and_the_transport_failures_are_not_the_not_found_class():
    """The distinction is only worth having if it is narrow: everything a
    caller cannot recover from must stay the base class, or a per-item
    ``except TracearrNotFound`` would start swallowing dead deployments."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TracearrRefused) as spa:
            await _client(http).media(SHOW_UUID)

    assert not isinstance(spa.value, TracearrNotFound)

    def boom(request):
        raise httpx.ConnectError("connection refused")

    routes = {f"{API_PREFIX}/media/{SHOW_UUID}": boom}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as dead:
            await _client(http).media(SHOW_UUID)

    assert not isinstance(dead.value, TracearrNotFound)
    assert "ConnectError" in str(dead.value)
    assert BASE_URL not in str(dead.value)


# --- error hygiene ------------------------------------------------------------


async def test_a_server_error_names_the_class_and_never_the_base_url():
    """``httpx.HTTPStatusError`` puts the whole URL in its own message and the
    engine logs a failed build with its traceback, so the httpx error is
    re-raised ``from None`` with the class name and the path only."""
    routes = {f"{API_PREFIX}/history": lambda request: _matched(
        {"statusCode": 500, "error": "InternalServerError", "message": "boom"}, status=500
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert "HTTPStatusError" in str(error.value)
    assert "/history" in str(error.value)
    assert BASE_URL not in str(error.value)
    assert API_KEY not in str(error.value)
    # ``from None``: the chained httpx message, which carries the URL, must not
    # be reachable through the exception the engine logs. ``__cause__`` is
    # None and ``__suppress_context__`` is True are exactly what ``raise ...
    # from None`` sets, and together they are what keeps ``logger.exception``
    # from printing "During handling of the above exception" followed by the
    # full URL.
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "base_url, leak",
    [
        ("http://tracearr.internal:notaport", "notaport"),
        ("http://tracearr❤secret.internal", "tracearr❤secret.internal"),
    ],
    ids=["garbage-port", "bad-codepoint-host"],
)
async def test_a_malformed_base_url_is_refused_without_quoting_itself(base_url, leak):
    """The input this module's URL hygiene exists for, and the one it used to
    miss: ``httpx.InvalidURL`` is a *sibling* of ``httpx.HTTPError``, not a
    descendant, so an ``except httpx.HTTPError`` never saw it.

    It is raised while building the request -- before any transport runs -- and
    its own message quotes the offending component: ``Invalid port: 'notaport'``
    for the first case and ``Invalid IDNA hostname: '<host>'`` for the second,
    which is the whole cluster-internal hostname. Both would reach the log
    through the engine's ``logger.exception``, and both would escape as a
    non-``TracearrRefused`` class, breaking the "one dead source" contract the
    builder's per-bucket handling is built on.
    """
    async with httpx.AsyncClient(transport=_routed({})) as http:
        client = TracearrClient(http, base_url, API_KEY)
        with pytest.raises(TracearrRefused) as error:
            await client.history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    assert "InvalidURL" in message
    assert "/history" in message
    assert leak not in message
    assert API_KEY not in message
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "base_url",
    [
        "http://xn--tracearr.internal",
        "http://xn--tracearr-secret-cluster.internal",
    ],
    ids=["punycode-codepoint", "punycode-bidi"],
)
async def test_a_punycode_base_url_is_refused_by_the_total_guard(base_url):
    """The *second* idna call site, and the one an enumerated catch tuple missed.

    ``httpx``'s ``URL.host`` property (``_urls.py``) runs ``idna.decode(host)``
    on any host starting ``xn--``, unguarded -- a separate path from the
    ``idna.encode`` in ``_urlparse.py``, which httpx does wrap in
    ``InvalidURL``. It is reached on the request-build path, so it lands inside
    this client's ``try``, and it raises ``idna``'s own errors: they subclass
    ``UnicodeError``, so they are neither ``httpx.HTTPError`` nor
    ``httpx.InvalidURL`` nor ``json.JSONDecodeError``.

    Their messages carry a *transform* of the operator's host rather than the
    host verbatim -- ``Codepoint U+02E9 at position 1 of '<decoded label>'`` --
    which is still derived from the base URL and still must not reach a log.
    The point of the test is less these two classes than that the guard no
    longer depends on having named them.
    """
    async with httpx.AsyncClient(transport=_routed({})) as http:
        client = TracearrClient(http, base_url, API_KEY)
        with pytest.raises(TracearrRefused) as error:
            await client.history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    # The whole message is client-constructed: a fixed sentence around one bare
    # class name. Asserting the *entire* shape rather than a list of forbidden
    # substrings is what makes this a test of the invariant rather than a test
    # of the two idna spellings that happen to be raised today -- no quoted
    # host, no decoded label, no codepoint listing can satisfy it.
    assert re.fullmatch(
        r"the Tracearr watch history: Tracearr refused /history \(\w+\)", message
    ), message
    assert "xn--" not in message
    assert "secret" not in message
    assert API_KEY not in message
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


async def test_no_repr_of_the_client_carries_the_key():
    async with httpx.AsyncClient(transport=_routed({})) as http:
        client = _client(http)
        assert API_KEY not in repr(client)


async def test_a_media_id_that_is_not_a_uuid_is_refused_before_any_request():
    """``media_id`` is interpolated into a path. It always comes from Tracearr
    itself, and it is checked anyway: a value with a slash in it would address
    a different endpoint entirely."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(TracearrRefused):
            await _client(http).media("../../v1/public/users")

    assert seen == []


async def test_a_non_string_media_id_is_refused_rather_than_raising_a_typeerror():
    """A documented key can go missing from one record (the harvest's finding),
    and the ranking's own ``media_id: str | None`` is not runtime-enforced --
    so ``None`` (or any other non-string) reaching here must stay inside the
    ``TracearrRefused`` contract rather than escaping as a raw ``TypeError``
    from the regex match, which every caller upstream is built to not expect.
    """
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(TracearrRefused):
            await _client(http).media(None)

    assert seen == []


# --- the media document -------------------------------------------------------


async def test_media_returns_the_show_document_verbatim():
    document = load("tracearr_media_show.json")
    routes = {f"{API_PREFIX}/media/{SHOW_UUID}": document}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        answer = await _client(http).media(SHOW_UUID)

    assert answer == document
    assert answer["tvdb_id"] == 403245
    assert answer["availability"][0]["rating_key"] == "63856"


async def test_records_are_handed_back_unparsed():
    """The API carries undocumented keys and omits documented ones (harvest
    finding 7), so a model here would reject live payloads. Records cross this
    boundary as the dicts Tracearr sent."""
    record = {"id": "a", "media_type": "movie", "an_undocumented_key": 1}
    routes = {f"{API_PREFIX}/history": _history([record])}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        records = await _client(http).history(
            since="2026-07-26T00:00:00Z", media_type="movie"
        )

    assert records == [record]


async def test_a_response_with_no_data_array_is_refused():
    routes = {f"{API_PREFIX}/history": {"meta": {"nextCursor": None}}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused, match="'data'"):
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")


async def test_a_matched_route_answering_html_is_refused_not_decoded():
    """Past the SPA guard, so the header check cannot help: a route that *did*
    match but whose body is HTML -- an upstream proxy's error page in front of a
    live Tracearr, which is why it still carries the rate-limit headers.

    Without the decode inside the wrapped block this escapes as a raw
    ``json.JSONDecodeError``, which is not a ``TracearrRefused`` and so is
    invisible to the builder's per-bucket handling. The page is also attacker-
    or infrastructure-controlled text, so nothing from it may be quoted.
    """
    body = "<!doctype html><html><body>gateway PROXYSECRET failed</body></html>"
    routes = {f"{API_PREFIX}/history": lambda request: httpx.Response(
        200, text=body,
        headers={"content-type": "text/html", "x-ratelimit-limit": "240"},
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    assert "JSONDecodeError" in message
    assert "/history" in message
    assert "PROXYSECRET" not in message
    assert BASE_URL not in message
    assert API_KEY not in message


async def test_a_body_that_is_not_decodable_text_is_refused():
    """``response.json()`` decodes before it parses, so a body that is not text
    at all raises ``UnicodeDecodeError`` -- a ``ValueError``, and *not* a
    ``json.JSONDecodeError``, which is why an enumerated catch tuple listing the
    latter still let this through.

    Its message quotes the offending bytes' position and the codec's guess at
    the encoding, so a response body an upstream can control was reaching the
    log. Under the total guard the class name is all that survives.
    """
    routes = {f"{API_PREFIX}/history": lambda request: httpx.Response(
        200, content=b"\xff\xfe\x00secret",
        headers={"x-ratelimit-limit": "240"},
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    assert "UnicodeDecodeError" in message
    assert "/history" in message
    assert "secret" not in message
    assert BASE_URL not in message
    assert API_KEY not in message


async def test_a_top_level_json_array_is_refused_rather_than_returned():
    """``_get`` annotates ``-> dict`` and both callers ``.get`` what it returns,
    so a top-level array used to escape as an ``AttributeError`` from one frame
    down -- and ``media()`` is worse, since it would *return* the list to the
    ranking code, where the failure is much harder to attribute.

    The type's name is a stable label; the payload's contents are not quoted.
    """
    routes = {f"{API_PREFIX}/history": lambda request: _matched(
        [{"id": "a", "title": "A Private Title"}]
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    assert "list" in message
    assert "/history" in message
    assert "A Private Title" not in message
    assert BASE_URL not in message
    assert API_KEY not in message
    assert not isinstance(error.value, TracearrNotFound)


# --- config, secret and bundle wiring -----------------------------------------


def _secrets(tracearr_apikey=API_KEY):
    return Secrets(
        database_url="postgresql+asyncpg://unused", plex_token="x",
        tmdb_token="x", tvdb_apikey="x", fanart_apikey="x", webhook_secret="x",
        tracearr_apikey=tracearr_apikey,
    )


def _bundle_config(enabled=True, base_url=BASE_URL):
    """Only the sections ``build_source_clients`` reads."""
    return SimpleNamespace(
        providers=SimpleNamespace(cache_ttl_seconds=3600),
        radarr=SimpleNamespace(enabled=False, base_url=""),
        sonarr=SimpleNamespace(enabled=False, base_url=""),
        tracearr=SimpleNamespace(enabled=enabled, base_url=base_url),
        manual_assets_root="/manual",
    )


def test_the_config_block_defaults_off_and_carries_no_api_key_field():
    """The credential is ``AUTOPOSTER_TRACEARR_APIKEY``, never a config field --
    the RadarrConfig/SonarrConfig rule, and the reason a config document can be
    read, written and audited without ever holding a secret."""
    block = TracearrConfig()

    assert block.enabled is False
    assert block.base_url == ""
    assert "api_key" not in TracearrConfig.model_fields
    assert "apikey" not in TracearrConfig.model_fields
    assert "tracearr" in Config.model_fields


def test_the_secret_is_soft_so_a_deployment_without_one_still_boots(monkeypatch):
    """The ``mdblist_apikey`` tier: absent means the Tracearr definitions
    report themselves failed, not that the process refuses to start."""
    for name in (
        "AUTOPOSTER_DATABASE_URL", "AUTOPOSTER_PLEX_TOKEN", "AUTOPOSTER_TMDB_TOKEN",
        "AUTOPOSTER_TVDB_APIKEY", "AUTOPOSTER_FANART_APIKEY", "AUTOPOSTER_WEBHOOK_SECRET",
    ):
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("AUTOPOSTER_TRACEARR_APIKEY", raising=False)

    assert Secrets.from_env().tracearr_apikey == ""

    monkeypatch.setenv("AUTOPOSTER_TRACEARR_APIKEY", API_KEY)
    assert Secrets.from_env().tracearr_apikey == API_KEY


async def test_the_pass_bundle_carries_a_client_when_all_three_are_present():
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(_bundle_config(), _secrets(), http)

    assert isinstance(sources.tracearr, TracearrClient)
    assert API_KEY not in repr(sources)


@pytest.mark.parametrize(
    "config_kwargs, secret",
    [
        ({"enabled": False}, API_KEY),
        ({"base_url": ""}, API_KEY),
        ({}, ""),
    ],
    ids=["disabled", "no-base-url", "no-key"],
)
async def test_a_half_configured_tracearr_is_no_client_at_all(config_kwargs, secret):
    """The ``_arr_client`` triple: ``enabled`` is the operator's switch, and a
    blank base URL or key is a half-configured service whose every request
    would fail confusingly rather than saying "not configured"."""
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(
            _bundle_config(**config_kwargs), _secrets(secret), http
        )

    assert sources.tracearr is None


def test_tracearr_is_not_a_frozen_section():
    """Clients are rebuilt per pass (``build_source_clients``), so an operator
    who switches Tracearr on does not have to restart to use it -- the
    radarr/sonarr posture, deliberately."""
    from autoposter.config.live import FROZEN_SECTIONS

    assert "tracearr" not in FROZEN_SECTIONS
