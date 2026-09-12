"""GET /api/items/{item_id}/candidates/{art_kind}.

The browse endpoint is the only place in this project that shows a provider's
*whole* offering rather than the ladder's single pick, so what is pinned here
is mostly the difference between the two: candidates the ladder would discard
outright (no language the config asked for) still have to appear, sorted last,
or the picker hides exactly the images it exists to let an operator choose;
and one provider being down has to cost that provider's rows and nothing else,
because a 500 here takes the whole picker away over one flaky upstream.

``app.state.providers`` is replaced with fakes throughout -- create_app leaves
it ``[]`` without the lifespan (app.py), so nothing here can reach a network.
"""
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Render
from autoposter.providers.base import ArtCandidate

from conftest import seed_media_item

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
TMDB_ORIGINAL = "https://image.tmdb.org/t/p/original"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


class _FakeProvider:
    """One provider client's whole surface as this endpoint uses it.

    ``fetch`` deliberately takes no ``all_languages`` keyword: only TMDB has
    one, and the endpoint has to keep working for the clients that do not.
    """

    def __init__(self, name: str, candidates=None, error: Exception | None = None):
        self.name = name
        self._candidates = candidates or []
        self._error = error
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return list(self._candidates)


class _WideFakeProvider(_FakeProvider):
    """A client that *does* take ``all_languages`` -- TMDB's shape.

    TMDB is the only client that narrows its own response to the configured
    languages, and the only one with the keyword to stop. The endpoint detects
    it from the signature, so this fake exists to exercise the branch the plain
    ``_FakeProvider`` above deliberately does not.
    """

    def __init__(self, name: str, candidates=None):
        super().__init__(name, candidates)
        self.all_languages: list[bool] = []

    async def fetch(self, request, *, all_languages: bool = False):
        self.all_languages.append(all_languages)
        return await super().fetch(request)


@pytest_asyncio.fixture
async def app(session_factory):
    return create_app(load_config(EXAMPLE), session_factory, _secrets())


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _item(session, kind: str = "movie", **extra) -> int:
    defaults = dict(
        rating_key="rk1", library="Movies", kind=kind, title="A Movie",
        year=1999, tmdb_id=550, tvdb_id=660, imdb_id="tt0137523",
        root_folder="A Movie (1999)",
    )
    defaults.update(extra)
    rating_key = defaults.pop("rating_key")
    item = await seed_media_item(session, rating_key, **defaults)
    return item.id


# --- refusals ---


async def test_requires_a_session(client, session):
    item_id = await _item(session)
    response = await client.get(f"/api/items/{item_id}/candidates/poster")
    assert response.status_code == 401


async def test_an_unknown_item_is_404(client, auth_headers):
    response = await client.get("/api/items/999999/candidates/poster", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.parametrize("art_kind", ["season_poster", "title_card", "bogus"])
async def test_an_art_kind_the_item_cannot_have_is_404(
    client, auth_headers, session, app, art_kind
):
    """Same validation idiom as clear-override: checked against the item's own
    kind, before any provider is asked. The probe is the provider itself -- an
    unvalidated handler would fan out and answer 200 with an empty list, which
    is indistinguishable from "no art" unless the fake records the call."""
    item_id = await _item(session)
    probe = _FakeProvider("Probe")
    app.state.providers = [probe]

    response = await client.get(
        f"/api/items/{item_id}/candidates/{art_kind}", headers=auth_headers
    )

    assert response.status_code == 404
    assert probe.requests == []


async def test_logo_is_browsable_for_a_movie(client, auth_headers, session, app):
    """Logos have no art_kind downstream -- no ART_KINDS_FOR entry, no render
    row -- but all three provider clients serve them, so the browser offers
    them for the kinds that composite one."""
    item_id = await _item(session)
    app.state.providers = [_FakeProvider("TMDB")]

    response = await client.get(f"/api/items/{item_id}/candidates/logo", headers=auth_headers)

    assert response.status_code == 200


async def test_logo_is_browsable_for_a_show(client, auth_headers, session, app):
    item_id = await _item(session, kind="show", rating_key="rk2")
    app.state.providers = [_FakeProvider("TMDB")]

    response = await client.get(f"/api/items/{item_id}/candidates/logo", headers=auth_headers)

    assert response.status_code == 200


@pytest.mark.parametrize("kind", ["season", "episode"])
async def test_logo_is_not_browsable_for_a_season_or_episode(
    client, auth_headers, session, app, kind
):
    """Only the poster render composites a logo, and only movies and shows have
    one -- a season/episode logo browser would offer a pick nothing consumes."""
    item_id = await _item(session, kind=kind, rating_key="rk3", season_number=1, episode_number=1)
    probe = _FakeProvider("Probe")
    app.state.providers = [probe]

    response = await client.get(f"/api/items/{item_id}/candidates/logo", headers=auth_headers)

    assert response.status_code == 404
    assert probe.requests == []


# --- the fan-out ---


async def test_three_providers_merge_in_ladder_order_with_unranked_last(
    client, auth_headers, session, app
):
    """The example config's poster language_order is [xx, en, fi], so textless
    outranks English outranks Finnish, and German is UNRANKED.

    The ladder DISCARDS unranked candidates (providers/ladder.py) -- a browse
    that reused ``best_candidate``'s filter would drop the German poster
    entirely, hiding from the operator precisely the image the automatic pick
    refused. It has to be shown, and shown last.
    """
    app.state.providers = [
        _FakeProvider("Alpha", [
            ArtCandidate("Alpha", f"{TMDB_ORIGINAL}/de.jpg", "de", 1000, 1500, 9.0),
            ArtCandidate("Alpha", "https://a.example/en-weak.jpg", "en", 1000, 1500, 4.0),
        ]),
        _FakeProvider("Beta", [
            ArtCandidate("Beta", "https://b.example/en-strong.jpg", "en", 1000, 1500, 8.0),
        ]),
        _FakeProvider("Gamma", [
            ArtCandidate("Gamma", "https://g.example/textless.jpg", None, 2000, 3000, 1.0),
            ArtCandidate("Gamma", "https://g.example/fi.jpg", "fi", 1000, 1500, 9.9),
        ]),
    ]
    item_id = await _item(session)

    response = await client.get(f"/api/items/{item_id}/candidates/poster", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert [c["url"] for c in body["candidates"]] == [
        "https://g.example/textless.jpg",   # xx, first in language_order
        "https://b.example/en-strong.jpg",  # en, higher score
        "https://a.example/en-weak.jpg",    # en, lower score
        "https://g.example/fi.jpg",         # fi
        f"{TMDB_ORIGINAL}/de.jpg",          # UNRANKED, shown but last
    ]
    assert body["errors"] == {}
    first = body["candidates"][0]
    assert first["provider"] == "Gamma"
    assert first["language"] is None
    assert first["width"] == 2000
    assert first["height"] == 3000
    assert first["score"] == 1.0
    assert first["includes_text"] is None


async def test_one_provider_raising_lands_in_errors_and_the_others_still_answer(
    client, auth_headers, session, app
):
    """A single flaky upstream must not take the whole picker away, which a
    bare ``asyncio.gather`` would do -- the first exception propagates and the
    endpoint 500s with none of the other two providers' rows."""
    app.state.providers = [
        _FakeProvider("Alpha", error=RuntimeError("upstream said no")),
        _FakeProvider("Beta", [
            ArtCandidate("Beta", "https://b.example/en.jpg", "en", 1000, 1500, 8.0),
        ]),
        _FakeProvider("Gamma", [
            ArtCandidate("Gamma", "https://g.example/xx.jpg", None, 1000, 1500, 8.0),
        ]),
    ]
    item_id = await _item(session)

    response = await client.get(f"/api/items/{item_id}/candidates/poster", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert list(body["errors"]) == ["Alpha"]
    assert [c["url"] for c in body["candidates"]] == [
        "https://g.example/xx.jpg",
        "https://b.example/en.jpg",
    ]


async def test_a_provider_error_string_never_carries_the_failing_url(
    client, auth_headers, session, app
):
    """Fanart passes its API key as a query parameter, and httpx puts the whole
    URL in an HTTPStatusError's message -- echoing ``str(exc)`` would hand the
    browser (and the log) that key. The type name is what the UI gets."""
    app.state.providers = [
        _FakeProvider(
            "Fanart",
            error=RuntimeError("404 for url 'https://webservice.fanart.tv/x?api_key=SEKRIT'"),
        ),
    ]
    item_id = await _item(session)

    response = await client.get(f"/api/items/{item_id}/candidates/poster", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["errors"] == {"Fanart": "RuntimeError"}


async def test_the_art_request_mirrors_what_the_render_path_builds(
    client, auth_headers, session, app
):
    """Same ids and numbers ``render_artifact`` puts in its ArtRequest, so the
    browse list is the list the ladder chose from. ``season_id`` is left unset
    exactly as the render path leaves it -- TVDBClient resolves that itself."""
    item_id = await _item(
        session, kind="episode", rating_key="rk9", season_number=2, episode_number=5,
    )
    probe = _FakeProvider("Probe")
    app.state.providers = [probe]

    response = await client.get(
        f"/api/items/{item_id}/candidates/title_card", headers=auth_headers
    )

    assert response.status_code == 200
    art_request = probe.requests[0]
    assert art_request.art_kind == "title_card"
    assert art_request.is_movie is False
    assert art_request.tmdb_id == 550
    assert art_request.tvdb_id == 660
    assert art_request.imdb_id == "tt0137523"
    assert art_request.season_number == 2
    assert art_request.episode_number == 5
    assert art_request.season_id is None


async def test_a_client_that_takes_all_languages_is_asked_for_all_of_them(
    client, auth_headers, session, app
):
    """The whole point of the browse: TMDB's ``fetch`` narrows to
    ``include_image_language`` by default, so asking it the render path's
    question returns the render path's shortlist and the picker shows a grid
    with the images an operator went looking for already filtered out.

    The keyword goes only to the clients that have it -- the narrow fake beside
    it would ``TypeError`` -- and both still answer.
    """
    wide = _WideFakeProvider("TMDB", [
        ArtCandidate("TMDB", f"{TMDB_ORIGINAL}/en.jpg", "en", 1000, 1500, 9.0),
    ])
    narrow = _FakeProvider("Fanart", [
        ArtCandidate("Fanart", "https://assets.fanart.tv/x.jpg", "en", 1000, 1500, 8.0),
    ])
    app.state.providers = [wide, narrow]
    item_id = await _item(session)

    response = await client.get(f"/api/items/{item_id}/candidates/poster", headers=auth_headers)

    assert response.status_code == 200
    assert wide.all_languages == [True]
    assert len(narrow.requests) == 1
    assert [c["provider"] for c in response.json()["candidates"]] == ["TMDB", "Fanart"]


async def test_a_movie_request_is_flagged_is_movie(client, auth_headers, session, app):
    probe = _FakeProvider("Probe")
    app.state.providers = [probe]
    item_id = await _item(session)

    await client.get(f"/api/items/{item_id}/candidates/poster", headers=auth_headers)

    assert probe.requests[0].is_movie is True


async def test_logo_browsing_uses_the_logo_language_order(client, auth_headers, session, app):
    """``logo_language_order`` is [en, fi] in the example config -- no ``xx`` --
    so a textless logo is UNRANKED there while it would rank first for a
    poster. Reusing the poster order would silently reorder the logo grid."""
    app.state.providers = [
        _FakeProvider("Alpha", [
            ArtCandidate("Alpha", "https://a.example/xx.png", None, 800, 300, 9.0),
            ArtCandidate("Alpha", "https://a.example/en.png", "en", 800, 300, 1.0),
        ]),
    ]
    item_id = await _item(session)

    response = await client.get(f"/api/items/{item_id}/candidates/logo", headers=auth_headers)

    assert [c["url"] for c in response.json()["candidates"]] == [
        "https://a.example/en.png",
        "https://a.example/xx.png",
    ]


# --- thumbnails ---


async def test_thumb_url_is_the_w342_variant_for_tmdb_urls_only(
    client, auth_headers, session, app
):
    """TMDB serves size variants by URL prefix; TVDB and Fanart serve one size
    each, so their thumb is the image itself. Keyed on the URL rather than on
    the provider label: the label is a client's own ``name`` attribute and a
    mislabelled row must not rewrite a URL that has no such variant."""
    app.state.providers = [
        _FakeProvider("TMDB", [
            ArtCandidate("TMDB", f"{TMDB_ORIGINAL}/abc.jpg", None, 1000, 1500, 9.0),
        ]),
        _FakeProvider("Fanart", [
            ArtCandidate("Fanart", "https://assets.fanart.tv/fanart/x.png", None, 1000, 1500, 8.0),
        ]),
    ]
    item_id = await _item(session)

    response = await client.get(f"/api/items/{item_id}/candidates/poster", headers=auth_headers)

    thumbs = {c["url"]: c["thumb_url"] for c in response.json()["candidates"]}
    assert thumbs == {
        f"{TMDB_ORIGINAL}/abc.jpg": "https://image.tmdb.org/t/p/w342/abc.jpg",
        "https://assets.fanart.tv/fanart/x.png": "https://assets.fanart.tv/fanart/x.png",
    }


# --- the current base ---


async def test_current_comes_from_the_render_row(client, auth_headers, session, app):
    item_id = await _item(session)
    session.add(
        Render(
            item_id=item_id, art_kind="poster", status="rendered",
            asset_path="/assets/a.jpg", provider="TMDB",
            source_url=f"{TMDB_ORIGINAL}/in-use.jpg",
        )
    )
    await session.commit()
    app.state.providers = [_FakeProvider("TMDB")]

    response = await client.get(f"/api/items/{item_id}/candidates/poster", headers=auth_headers)

    assert response.json()["current"] == {
        "source_url": f"{TMDB_ORIGINAL}/in-use.jpg",
        "provider": "TMDB",
    }


async def test_current_is_null_without_a_render_row(client, auth_headers, session, app):
    item_id = await _item(session)
    app.state.providers = [_FakeProvider("TMDB")]

    response = await client.get(f"/api/items/{item_id}/candidates/poster", headers=auth_headers)

    assert response.json()["current"] is None


async def test_current_is_null_for_logos_which_have_no_render_row(
    client, auth_headers, session, app
):
    """There is no ``logo`` render row and there never will be -- the logo is
    fetched per poster render and rides into the poster's fingerprint. Reading
    the poster row here would mark a logo the operator never picked."""
    item_id = await _item(session)
    session.add(
        Render(
            item_id=item_id, art_kind="poster", status="rendered",
            asset_path="/assets/a.jpg", provider="TMDB",
            source_url=f"{TMDB_ORIGINAL}/poster.jpg",
        )
    )
    await session.commit()
    app.state.providers = [_FakeProvider("TMDB")]

    response = await client.get(f"/api/items/{item_id}/candidates/logo", headers=auth_headers)

    assert response.json()["current"] is None


async def test_no_providers_configured_is_an_empty_list_not_an_error(
    client, auth_headers, session, app
):
    """``app.state.providers`` is [] on any app without the background lifespan."""
    item_id = await _item(session)

    response = await client.get(f"/api/items/{item_id}/candidates/poster", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"candidates": [], "errors": {}, "current": None}
