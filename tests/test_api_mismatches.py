"""GET /api/id-mismatches -- where Plex and Radarr/Sonarr disagree.

The comparison under test is deliberately *path*-based, mirroring the
misassignment catcher in ``arr/sync.py``: matching a pair by the very id the
view exists to compare would make every matched pair agree by construction,
and the disagreement -- Radarr holding the right folder under the wrong TMDB
id -- would be invisible.
"""
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.plex.client import SectionItem

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

RADARR_KEY = "radarr-api-key-2f0c"
SONARR_KEY = "sonarr-api-key-9b41"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        radarr_apikey=RADARR_KEY, sonarr_apikey=SONARR_KEY,
        admin_password_hash=hash_password(PASSWORD),
    )


class FakePlexClient:
    """Stands in for ``PlexClient.list_items`` -- the one call the endpoint
    makes into Plex. Returns the real ``SectionItem`` the production method
    returns, so a change to that shape breaks these tests too."""

    def __init__(self, by_type: dict[str, list[SectionItem]]):
        self._by_type = by_type
        self.asked = []

    async def list_items(self, wanted_type: str) -> list[SectionItem]:
        self.asked.append(wanted_type)
        return list(self._by_type.get(wanted_type, []))


def movie(
    rating_key="1", title="Dune", year=2021,
    location="/mnt/Media/Movies/Dune (2021)/dune.mkv", **guids,
) -> SectionItem:
    return SectionItem(
        rating_key=rating_key, library="Movies", title=title, year=year,
        locations=[location], guids=guids,
    )


def show(
    rating_key="9", title="Severance", year=2022,
    location="/mnt/Media/TV/Severance", **guids,
) -> SectionItem:
    return SectionItem(
        rating_key=rating_key, library="TV", title=title, year=year,
        locations=[location], guids=guids,
    )


@pytest_asyncio.fixture
async def app(session_factory):
    config = load_config(EXAMPLE)
    config.radarr.enabled = True
    config.radarr.base_url = "https://radarr.example"
    config.radarr.plex_path = "/mnt/Media/Movies"
    config.radarr.arr_path = "/mnt/media/Movies"
    config.sonarr.enabled = True
    config.sonarr.base_url = "https://sonarr.example"
    config.sonarr.plex_path = "/mnt/Media/TV"
    config.sonarr.arr_path = "/mnt/media/TV"
    return create_app(config, session_factory, _secrets())


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest_asyncio.fixture
async def wire(app):
    """Wire ``app.state.plex``/``app.state.http`` the way the lifespan would.

    ``radarr``/``sonarr`` are the listings each service answers with, empty by
    default. Anything else -- another endpoint, another host -- fails the test
    rather than being answered: this handler must not quietly stand in for a
    request the endpoint had no business making.
    """
    clients = []

    def install(*, movies=(), shows=(), radarr=(), sonarr=()):
        seen = []

        def handler(request):
            seen.append(request)
            host = request.url.host
            if request.url.path == "/api/v3/movie" and host == "radarr.example":
                return httpx.Response(200, json=list(radarr))
            if request.url.path == "/api/v3/series" and host == "sonarr.example":
                return httpx.Response(200, json=list(sonarr))
            raise AssertionError(f"unexpected request {request.method} {request.url}")

        http = AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(http)
        app.state.http = http
        app.state.plex = FakePlexClient({"movie": list(movies), "show": list(shows)})
        return seen

    yield install
    for http in clients:
        await http.aclose()


async def get(client, headers):
    response = await client.get("/api/id-mismatches", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- auth and wiring ---------------------------------------------------------


async def test_requires_a_session(client):
    assert (await client.get("/api/id-mismatches")).status_code == 401


async def test_503_before_plex_is_wired(client, auth_headers):
    # create_app leaves app.state.plex/http None; the lifespan sets them.
    response = await client.get("/api/id-mismatches", headers=auth_headers)
    assert response.status_code == 503


# --- the disagreement itself -------------------------------------------------


async def test_a_matched_pair_whose_ids_disagree_is_reported_with_both_sides(
    client, auth_headers, wire
):
    """The whole point: Radarr holds the right folder under the wrong TMDB id.
    Nothing else notices -- the ids never collide, so the sync sees no gap --
    and the item surfaces later as a no-art or no-Plex-item failure."""
    wire(
        movies=[movie(tmdb="438631", imdb="tt1160419")],
        radarr=[{
            "title": "Dune (1984)", "year": 1984,
            "path": "/mnt/media/Movies/Dune (2021)",
            "tmdbId": 841, "imdbId": "tt1160419",
        }],
    )

    body = await get(client, auth_headers)

    assert body["counts"]["mismatched"] == 1
    row = body["mismatched"][0]
    assert row["differing"] == ["tmdb"]
    assert row["arr_ids"]["tmdb"] == "841"
    assert row["plex_ids"]["tmdb"] == "438631"
    # The agreeing id is carried too -- an operator verifying by hand needs
    # both sides in full, not only the field that differs.
    assert row["arr_ids"]["imdb"] == "tt1160419"
    assert row["plex_ids"]["imdb"] == "tt1160419"
    assert row["plex_title"] == "Dune"
    assert row["arr_title"] == "Dune (1984)"
    assert row["rating_key"] == "1"
    assert row["library"] == "Movies"
    assert row["path"] == "/mnt/media/Movies/Dune (2021)"
    assert row["service"] == "radarr"
    assert body["counts"]["arr_only"] == 0
    assert body["counts"]["plex_only"] == 0


async def test_a_pair_whose_ids_agree_is_not_reported(client, auth_headers, wire):
    wire(
        movies=[movie(tmdb="438631", imdb="tt1160419")],
        radarr=[{
            "title": "Dune", "path": "/mnt/media/Movies/Dune (2021)",
            "tmdbId": 438631, "imdbId": "tt1160419",
        }],
    )

    body = await get(client, auth_headers)

    assert body["mismatched"] == []
    assert body["total"] == 0


async def test_an_id_only_one_side_holds_is_not_a_disagreement(
    client, auth_headers, wire
):
    """Plex's agent routinely has no IMDb guid, and a freshly added Radarr
    entry has no IMDb id yet. Absence is not disagreement, and reporting it
    would bury the real mismatches under the whole library."""
    wire(
        movies=[movie(tmdb="438631")],
        radarr=[{
            "title": "Dune", "path": "/mnt/media/Movies/Dune (2021)",
            "tmdbId": 438631, "imdbId": "tt1160419",
        }],
    )

    body = await get(client, auth_headers)

    assert body["mismatched"] == []


async def test_a_series_pair_compares_tvdb_tmdb_and_imdb(client, auth_headers, wire):
    wire(
        shows=[show(tvdb="371980", tmdb="95396", imdb="tt11280740")],
        sonarr=[{
            "title": "Severance", "path": "/mnt/media/TV/Severance",
            "tvdbId": 371980, "tmdbId": 12345, "imdbId": "tt99999",
        }],
    )

    body = await get(client, auth_headers)

    row = body["mismatched"][0]
    assert row["service"] == "sonarr"
    assert row["kind"] == "series"
    # Declared order, so the list does not depend on dict iteration order.
    assert row["differing"] == ["tmdb", "imdb"]


# --- the two unmatched groups ------------------------------------------------


async def test_an_arr_entry_with_no_plex_item_is_arr_only(client, auth_headers, wire):
    """The unmatched-episode-family catcher at series level: Sonarr manages a
    folder Plex has nothing for, so every episode under it fails to resolve."""
    wire(
        shows=[show(location="/mnt/Media/TV/Severance", tvdb="371980")],
        sonarr=[
            {"title": "Severance", "path": "/mnt/media/TV/Severance", "tvdbId": 371980},
            {"title": "Andor", "path": "/mnt/media/TV/Andor", "tvdbId": 393199},
        ],
    )

    body = await get(client, auth_headers)

    assert body["counts"]["arr_only"] == 1
    row = body["arr_only"][0]
    assert row["arr_title"] == "Andor"
    assert row["arr_ids"]["tvdb"] == "393199"
    assert row["path"] == "/mnt/media/TV/Andor"
    assert row["rating_key"] is None
    assert row["plex_ids"] == {}


async def test_a_plex_item_with_no_arr_entry_is_plex_only(client, auth_headers, wire):
    wire(
        movies=[
            movie(rating_key="1", title="Dune"),
            movie(
                rating_key="2", title="Sinners",
                location="/mnt/Media/Movies/Sinners (2025)/s.mkv", tmdb="1233413",
            ),
        ],
        radarr=[{"title": "Dune", "path": "/mnt/media/Movies/Dune (2021)", "tmdbId": 438631}],
    )

    body = await get(client, auth_headers)

    assert body["counts"]["plex_only"] == 1
    row = body["plex_only"][0]
    assert row["plex_title"] == "Sinners"
    assert row["rating_key"] == "2"
    assert row["path"] == "/mnt/media/Movies/Sinners (2025)"
    assert row["plex_ids"]["tmdb"] == "1233413"
    assert row["arr_ids"] == {}


async def test_a_plex_item_outside_the_configured_root_is_not_reported(
    client, auth_headers, wire
):
    """A second movie library on another mount is not something Radarr manages,
    so its absence there is expected rather than a finding. It is counted, not
    listed -- silently dropping items would make the totals a lie."""
    wire(
        movies=[movie(location="/elsewhere/Films/Dune (2021)/dune.mkv", tmdb="438631")],
        radarr=[],
    )

    body = await get(client, auth_headers)

    assert body["plex_only"] == []
    assert body["unmapped"] == 1


# --- services that are not there ---------------------------------------------


async def test_a_disabled_service_is_skipped_and_named(client, auth_headers, wire, app):
    app.state.config_holder.current.sonarr.enabled = False
    wire(
        movies=[movie(tmdb="438631")],
        shows=[show(tvdb="371980")],
        radarr=[{"title": "Dune", "path": "/mnt/media/Movies/Dune (2021)", "tmdbId": 841}],
    )

    body = await get(client, auth_headers)

    assert body["skipped"] == ["sonarr"]
    # The other service is still compared.
    assert body["counts"]["mismatched"] == 1


async def test_a_service_without_a_base_url_is_skipped(client, auth_headers, wire, app):
    """Enabled but unconfigured is the same answer as disabled: there is no
    instance to ask. Requesting it would be a request to the empty string."""
    app.state.config_holder.current.sonarr.base_url = ""
    wire(movies=[], shows=[show(tvdb="371980")], radarr=[])

    body = await get(client, auth_headers)

    assert body["skipped"] == ["sonarr"]


async def test_a_service_without_an_api_key_is_skipped(client, auth_headers, wire, app):
    app.state.secrets = app.state.secrets.model_copy(update={"sonarr_apikey": ""})
    wire(movies=[], shows=[show(tvdb="371980")], radarr=[])

    body = await get(client, auth_headers)

    assert body["skipped"] == ["sonarr"]


async def test_a_service_that_does_not_answer_is_a_502_naming_it(
    client, auth_headers, app
):
    """Contained rather than left to become a bare 500: the operator has to be
    able to tell "Radarr is unreachable" from "the endpoint is broken"."""
    def handler(request):
        return httpx.Response(500, text="boom")

    async with AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app.state.http = http
        app.state.plex = FakePlexClient({})
        response = await client.get("/api/id-mismatches", headers=auth_headers)

    assert response.status_code == 502
    assert "radarr" in response.json()["detail"]


# --- what must never be in the response --------------------------------------


async def test_the_response_never_carries_an_api_key(client, auth_headers, wire):
    """The keys are sent to the services as ``X-Api-Key`` headers, server-side.
    Nothing here echoes an arr entry wholesale, which is how one would leak."""
    seen = wire(
        movies=[movie(tmdb="438631")],
        shows=[show(tvdb="371980")],
        radarr=[{
            "title": "Dune", "path": "/mnt/media/Movies/Dune (2021)", "tmdbId": 841,
            # Radarr's own listing does not carry this; a hostile or unusual
            # instance answering with one must not have it reflected back.
            "apiKey": RADARR_KEY,
        }],
        sonarr=[{"title": "Severance", "path": "/mnt/media/TV/Severance", "tvdbId": 1}],
    )

    body = await get(client, auth_headers)

    assert RADARR_KEY not in json.dumps(body)
    assert SONARR_KEY not in json.dumps(body)
    # And the keys really were the ones sent, or the assertion above passes
    # against a request that never authenticated.
    assert {request.headers["X-Api-Key"] for request in seen} == {RADARR_KEY, SONARR_KEY}


@pytest.mark.parametrize("group", ["mismatched", "arr_only", "plex_only"])
async def test_rows_are_capped_while_the_counts_report_everything(
    client, auth_headers, wire, app, group
):
    """A library-sized disagreement (a remount that moved every path) must not
    serialise fifteen thousand rows into one response."""
    from autoposter.api.mismatches import ROW_LIMIT

    size = ROW_LIMIT + 5
    movies, entries = [], []
    for index in range(size):
        path = f"/mnt/Media/Movies/Film {index}/f.mkv"
        arr_path = f"/mnt/media/Movies/Film {index}"
        if group != "arr_only":
            movies.append(movie(rating_key=str(index), location=path, tmdb=str(index)))
        if group != "plex_only":
            entries.append({
                "title": f"Film {index}", "path": arr_path, "tmdbId": 900000 + index,
            })
    wire(movies=movies, radarr=entries)

    body = await get(client, auth_headers)

    assert body["counts"][group] == size
    assert body["total"] == size
    assert len(body[group]) == ROW_LIMIT
