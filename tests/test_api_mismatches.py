"""GET /api/id-mismatches -- where Plex and Radarr/Sonarr disagree.

The comparison under test is deliberately *path*-based, mirroring the
misassignment catcher in ``arr/sync.py``: matching a pair by the very id the
view exists to compare would make every matched pair agree by construction,
and the disagreement -- Radarr holding the right folder under the wrong TMDB
id -- would be invisible.
"""
import json
import logging
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
    location="/mnt/Media/Movies/Dune (2021)/dune.mkv", library="Movies", **guids,
) -> SectionItem:
    return SectionItem(
        server="plex", native_id=rating_key, library=library, title=title, year=year,
        locations=[location], guids=guids,
    )


def show(
    rating_key="9", title="Severance", year=2022,
    location="/mnt/Media/TV/Severance", library="TV", **guids,
) -> SectionItem:
    return SectionItem(
        server="plex", native_id=rating_key, library=library, title=title, year=year,
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
    default. ``radarr_roots``/``sonarr_roots`` are what ``/api/v3/rootfolder``
    answers with -- the root-folder sanity guard calls it before anything else,
    so it defaults to overlapping the fixture's configured ``arr_path`` for
    each service, and only tests of that guard itself override it. Anything
    else -- another endpoint, another host -- fails the test rather than being
    answered: this handler must not quietly stand in for a request the
    endpoint had no business making.
    """
    clients = []

    def install(
        *, movies=(), shows=(), radarr=(), sonarr=(),
        radarr_roots=("/mnt/media/Movies",), sonarr_roots=("/mnt/media/TV",),
    ):
        seen = []

        def handler(request):
            seen.append(request)
            host = request.url.host
            if request.url.path == "/api/v3/movie" and host == "radarr.example":
                return httpx.Response(200, json=list(radarr))
            if request.url.path == "/api/v3/series" and host == "sonarr.example":
                return httpx.Response(200, json=list(sonarr))
            if request.url.path == "/api/v3/rootfolder" and host == "radarr.example":
                return httpx.Response(200, json=[{"path": p} for p in radarr_roots])
            if request.url.path == "/api/v3/rootfolder" and host == "sonarr.example":
                return httpx.Response(200, json=[{"path": p} for p in sonarr_roots])
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


async def test_a_matched_pair_where_plex_has_no_ids_is_reported_as_mismatched(
    client, auth_headers, wire
):
    """The whole point, restated: a path-matched pair where Plex's agent found
    *nothing* -- no tmdb, no imdb -- disagrees about every id there is to
    disagree about, but `_differing` only compares ids both sides hold, so an
    empty side never disagrees. Without this, the pair lands in `matched` and
    is never reported anywhere -- yet "Plex has no ids for this item" is
    exactly the mismatch symptom this view exists to surface."""
    wire(
        movies=[movie()],  # no guid kwargs at all -- an agent that matched nothing
        radarr=[{
            "title": "Dune", "path": "/mnt/media/Movies/Dune (2021)",
            "tmdbId": 438631, "imdbId": "tt1160419",
        }],
    )

    body = await get(client, auth_headers)

    assert body["counts"]["mismatched"] == 1
    assert body["counts"]["arr_only"] == 0
    assert body["counts"]["plex_only"] == 0
    row = body["mismatched"][0]
    assert row["plex_ids"] == {}
    assert row["arr_ids"] == {"tmdb": "438631", "imdb": "tt1160419"}
    assert row["differing"] == ["no_ids_on_plex"]


async def test_a_matched_pair_where_the_arr_entry_has_no_ids_is_reported_as_mismatched(
    client, auth_headers, wire
):
    """The mirror case: a freshly-added Radarr entry with no external id yet,
    matched by path to a Plex item that has one. Same invisibility risk, same
    fix, the other direction."""
    wire(
        movies=[movie(tmdb="438631")],
        radarr=[{"title": "Dune", "path": "/mnt/media/Movies/Dune (2021)"}],
    )

    body = await get(client, auth_headers)

    assert body["counts"]["mismatched"] == 1
    row = body["mismatched"][0]
    assert row["arr_ids"] == {}
    assert row["plex_ids"] == {"tmdb": "438631"}
    assert row["differing"] == ["no_ids_on_arr"]


# --- the two unmatched groups ------------------------------------------------


async def test_an_arr_entry_with_no_plex_item_is_arr_only(client, auth_headers, wire):
    """The unmatched-episode-family catcher at series level: Sonarr manages a
    folder Plex has nothing for, so every episode under it fails to resolve."""
    wire(
        shows=[show(location="/mnt/Media/TV/Severance", tvdb="371980")],
        sonarr=[
            {"title": "Severance", "path": "/mnt/media/TV/Severance", "tvdbId": 371980},
            {
                "title": "Andor", "path": "/mnt/media/TV/Andor", "tvdbId": 393199,
                "statistics": {"episodeFileCount": 12},
            },
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


async def test_a_released_radarr_entry_with_no_plex_item_is_arr_only(
    client, auth_headers, wire
):
    """The ordinary case, pinning ``hasFile`` as the field that gates it:
    Radarr already has the file on disk, so its absence from Plex is a real
    finding."""
    wire(
        radarr=[{
            "title": "Dune", "path": "/mnt/media/Movies/Dune (2021)",
            "tmdbId": 438631, "hasFile": True,
        }],
    )

    body = await get(client, auth_headers)

    assert body["counts"]["arr_only"] == 1
    assert body["arr_unreleased"] == 0
    row = body["arr_only"][0]
    assert row["arr_title"] == "Dune"


async def test_an_unreleased_radarr_entry_with_no_plex_item_is_counted_not_listed(
    client, auth_headers, wire
):
    """An operator adds upcoming movies to Radarr routinely -- Plex cannot
    possibly have matched something that has not been downloaded yet, so this
    must not surface as an ``arr_only`` finding. It is still counted, the
    same "counted, not dropped" principle as ``arr_unmapped``."""
    wire(
        radarr=[{
            "title": "Dune: Part Three", "path": "/mnt/media/Movies/Dune Part Three",
            "tmdbId": 1234567, "hasFile": False,
        }],
    )

    body = await get(client, auth_headers)

    assert body["counts"]["arr_only"] == 0
    assert body["arr_only"] == []
    assert body["arr_unreleased"] == 1


async def test_a_sonarr_entry_missing_statistics_entirely_is_treated_as_unreleased(
    client, auth_headers, wire
):
    """Defensive default: an entry with no ``statistics`` key at all (an
    unusual instance, or a field renamed upstream) must not be assumed
    available -- the safe default is "not on disk", not "arr_only"."""
    wire(
        sonarr=[{"title": "Andor", "path": "/mnt/media/TV/Andor", "tvdbId": 393199}],
    )

    body = await get(client, auth_headers)

    assert body["counts"]["arr_only"] == 0
    assert body["arr_unreleased"] == 1


async def test_an_unreleased_arr_entry_that_matches_a_plex_item_is_unaffected(
    client, auth_headers, wire
):
    """Path-matched pairs are unaffected by the availability check either
    way: an unreleased movie that somehow matched a Plex item is still
    exactly the disagreement (or agreement) this view exists to surface, not
    something the availability gate should hide or double-count."""
    wire(
        movies=[movie(tmdb="438631")],
        radarr=[{
            "title": "Dune", "path": "/mnt/media/Movies/Dune (2021)",
            "tmdbId": 438631, "hasFile": False,
        }],
    )

    body = await get(client, auth_headers)

    assert body["mismatched"] == []
    assert body["counts"]["arr_only"] == 0
    assert body["arr_unreleased"] == 0


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


async def test_an_arr_entry_with_no_path_is_counted_not_dropped(client, auth_headers, wire):
    """A Radarr/Sonarr entry with a falsy ``path`` cannot be paired with
    anything on disk, so it cannot be reported in any of the three groups --
    but it must not simply vanish from the totals either, the same principle
    that keeps a Plex item outside the configured root out of ``plex_only``
    while still landing in ``unmapped`` rather than nowhere at all."""
    wire(
        movies=[movie(tmdb="438631")],
        radarr=[
            {"title": "Dune", "path": "/mnt/media/Movies/Dune (2021)", "tmdbId": 438631},
            {"title": "No Folder Yet", "path": "", "tmdbId": 999999},
        ],
    )

    body = await get(client, auth_headers)

    assert body["arr_unmapped"] == 1
    # The pathed entry is unaffected -- it still matches normally.
    assert body["counts"]["mismatched"] == 0
    assert body["counts"]["arr_only"] == 0


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


async def test_the_502_detail_never_carries_the_arr_base_url(
    client, auth_headers, app, caplog
):
    """Roadmap row 209 site (3): an httpx error's str() embeds the request
    URL, so the 502's detail carried the operator's Arr base URL to the
    browser. The class name replaces the exception text -- the api/pick 502
    precedent (test_the_502_names_the_provider_and_never_the_url) -- and the
    which-URL question is answered by the warning line's traceback in the
    pod log, pinned below."""
    def handler(request):
        return httpx.Response(500, text="boom")

    async with AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app.state.http = http
        app.state.plex = FakePlexClient({})
        with caplog.at_level(logging.WARNING, logger="autoposter.api.mismatches"):
            response = await client.get("/api/id-mismatches", headers=auth_headers)

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail == "radarr did not answer (HTTPStatusError)"
    assert "radarr.example" not in detail
    assert "://" not in detail
    # The compensating control, pinned: the exc_info warning's traceback is
    # where the URL still lives, host-only.
    pinned = [
        record for record in caplog.records
        if record.levelno == logging.WARNING and record.exc_info is not None
    ]
    assert [r.getMessage() for r in pinned] == ["id-mismatches: radarr did not answer"]
    assert "radarr.example" in caplog.text


# --- an arr instance that is not this library's ------------------------------


async def test_an_arr_instance_managing_a_different_tree_is_refused(
    client, auth_headers, wire
):
    """Root folders that share no tree with the configured arr path mean this
    instance is not the one the mapping was configured for -- the same guard
    ``sync_section`` runs before ever comparing anything. Without it, every
    item in the library would be reported twice over: once as arr_only,
    because this (wrong) instance has never heard of any of it, and once as
    plex_only for the same reason, drowning any real mismatch in noise that
    is actually just a bad base_url or the wrong instance."""
    wire(
        movies=[movie(tmdb="438631")],
        radarr=[{"title": "Dune", "path": "/mnt/media/Movies/Dune (2021)", "tmdbId": 438631}],
        # Miscased -- case-sensitive matching means this shares no tree with
        # the configured /mnt/media/Movies.
        radarr_roots=["/mnt/media/MOVIES"],
    )

    body = await get(client, auth_headers)

    assert "radarr" in body["refused"]
    # Row 213's rule on an HTTP body: the served refusal names the service
    # and a count, never the configured path or the instance's root folders
    # (the next test pins the sentence and the log line that keeps them).
    assert "/mnt/media/Movies" not in body["refused"]["radarr"]
    assert body["counts"]["mismatched"] == 0
    assert body["counts"]["arr_only"] == 0
    assert body["counts"]["plex_only"] == 0
    assert body["total"] == 0


async def test_a_refusal_serves_no_path_and_logs_both(client, auth_headers, wire, caplog):
    """Through the app: GET /api/id-mismatches' `refused` entry is rendered
    verbatim by the Mismatches page, and it carried the operator's arr_path
    and every root folder Radarr reports. The shared sentence from
    arr/sync.py now; both paths on this module's WARNING, the pod log."""
    wire(
        movies=[movie(tmdb="438631")],
        radarr=[{"title": "Dune", "path": "/mnt/media/Movies/Dune (2021)", "tmdbId": 438631}],
        radarr_roots=["/data/films"],
    )

    with caplog.at_level(logging.WARNING, logger="autoposter.api.mismatches"):
        body = await get(client, auth_headers)

    assert body["refused"]["radarr"] == (
        "the configured arr path shares no tree with any root folder radarr manages "
        "(1 root folder(s) reported) -- probably the wrong instance or a bad base_url; "
        "nothing was compared"
    )
    assert "/data/films" not in body["refused"]["radarr"]
    assert "/mnt/media/Movies" not in body["refused"]["radarr"]
    assert "/data/films" in caplog.text
    assert "/mnt/media/Movies" in caplog.text


async def test_an_arr_instance_that_overlaps_is_not_refused(client, auth_headers, wire):
    """A root folder that is a parent or child of the configured arr path --
    not only an exact match -- still counts as the same tree."""
    wire(
        movies=[movie(tmdb="438631")],
        radarr=[{"title": "Dune", "path": "/mnt/media/Movies/Dune (2021)", "tmdbId": 841}],
        radarr_roots=["/mnt/media"],
    )

    body = await get(client, auth_headers)

    assert body["refused"] == {}
    assert body["counts"]["mismatched"] == 1


# --- excluded Plex libraries --------------------------------------------------


async def test_an_excluded_plex_library_is_not_reported_as_plex_only(
    client, auth_headers, wire, app
):
    """Mirrors ``make_arr_sync_job``'s own exclusion check: a library an
    operator has deliberately excluded (a DVR library, say) must not surface
    its unregistered items as ``plex_only`` noise."""
    app.state.config_holder.current.plex.excluded_libraries = ["DVR"]
    wire(
        movies=[
            movie(rating_key="1", title="Dune"),
            movie(
                rating_key="2", title="Recording", library="DVR",
                location="/mnt/Media/Movies/Recording (2025)/r.mkv", tmdb="999999",
            ),
        ],
        radarr=[{"title": "Dune", "path": "/mnt/media/Movies/Dune (2021)", "tmdbId": 438631}],
    )

    body = await get(client, auth_headers)

    assert body["counts"]["plex_only"] == 0
    assert body["plex_only"] == []
    assert body["excluded_libraries"] == ["DVR"]


async def test_no_excluded_libraries_echoes_an_empty_list(client, auth_headers, wire, app):
    app.state.config_holder.current.plex.excluded_libraries = []
    wire(
        movies=[movie(tmdb="438631")],
        radarr=[{"title": "Dune", "path": "/mnt/media/Movies/Dune (2021)", "tmdbId": 438631}],
    )

    body = await get(client, auth_headers)

    assert body["excluded_libraries"] == []


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
                "hasFile": True,
            })
    wire(movies=movies, radarr=entries)

    body = await get(client, auth_headers)

    assert body["counts"][group] == size
    assert body["total"] == size
    assert len(body[group]) == ROW_LIMIT
