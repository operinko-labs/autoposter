import asyncio
import json
from pathlib import Path

import httpx
import pytest

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, TITLE_CARD, ArtRequest,
)
from autoposter.providers.fanart import parse_fanart
from autoposter.providers.tmdb import TMDBClient, parse_tmdb_images
from autoposter.providers.tvdb import TVDBClient, TVDBListRefused, parse_tvdb_artworks

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_tmdb_posters_are_parsed():
    candidates = parse_tmdb_images(load("tmdb_movie_images.json"), POSTER)
    assert len(candidates) == 4
    assert all(c.provider == "TMDB" for c in candidates)
    assert candidates[0].url == "https://image.tmdb.org/t/p/original/textless.jpg"


def test_tmdb_null_language_is_textless():
    candidates = parse_tmdb_images(load("tmdb_movie_images.json"), POSTER)
    textless = [c for c in candidates if c.is_textless]
    assert len(textless) == 1
    assert textless[0].language is None


def test_tmdb_backdrops_and_logos_use_their_own_arrays():
    payload = load("tmdb_movie_images.json")
    assert len(parse_tmdb_images(payload, BACKGROUND)) == 1
    assert len(parse_tmdb_images(payload, LOGO)) == 1


def test_tmdb_title_cards_come_from_stills():
    candidates = parse_tmdb_images(load("tmdb_episode_images.json"), TITLE_CARD)
    assert len(candidates) == 1
    assert candidates[0].width == 3840


def test_tvdb_filters_by_artwork_type():
    payload = load("tvdb_series_artworks.json")
    posters = parse_tvdb_artworks(payload, POSTER, is_movie=False)
    backgrounds = parse_tvdb_artworks(payload, BACKGROUND, is_movie=False)
    assert len(posters) == 3
    assert len(backgrounds) == 1


def test_tvdb_uses_the_includes_text_flag_not_the_language():
    posters = parse_tvdb_artworks(load("tvdb_series_artworks.json"), POSTER, is_movie=False)
    # The English artwork with includesText false is genuinely textless.
    english_textless = [c for c in posters if c.language == "eng" and c.is_textless]
    assert len(english_textless) == 1
    assert english_textless[0].includes_text is False


def test_tvdb_urls_are_already_absolute():
    posters = parse_tvdb_artworks(load("tvdb_series_artworks.json"), POSTER, is_movie=False)
    assert all(c.url.startswith("https://artworks.thetvdb.com/") for c in posters)


def test_fanart_zero_zero_language_is_textless():
    candidates = parse_fanart(load("fanart_tv.json"), POSTER, is_movie=False, season_number=None)
    textless = [c for c in candidates if c.is_textless]
    assert len(textless) == 1
    assert textless[0].language == "00"


def test_fanart_empty_language_is_also_textless():
    candidates = parse_fanart(
        load("fanart_tv.json"), BACKGROUND, is_movie=False, season_number=None
    )
    assert candidates[0].is_textless is True


def test_fanart_season_posters_are_filtered_by_season():
    candidates = parse_fanart(
        load("fanart_tv.json"), SEASON_POSTER, is_movie=False, season_number=1
    )
    assert len(candidates) == 1
    assert candidates[0].url.endswith("s1.jpg")


def test_fanart_likes_become_the_score():
    candidates = parse_fanart(load("fanart_tv.json"), POSTER, is_movie=False, season_number=None)
    assert {c.score for c in candidates} == {3.0, 7.0}


def test_fanart_http_urls_are_upgraded_to_https():
    candidates = parse_fanart(load("fanart_tv.json"), LOGO, is_movie=False, season_number=None)
    assert candidates[0].url.startswith("https://")


def test_fanart_returns_empty_for_missing_art_type():
    assert parse_fanart({}, POSTER, is_movie=True, season_number=None) == []


def test_fanart_non_numeric_likes_default_to_zero_score():
    payload = {
        "tvposter": [
            {"id": "1", "url": "https://assets.fanart.tv/bad.jpg", "lang": "en", "likes": "N/A"},
            {"id": "2", "url": "https://assets.fanart.tv/good.jpg", "lang": "en", "likes": "5"},
        ]
    }
    candidates = parse_fanart(payload, POSTER, is_movie=False, season_number=None)
    scores = {c.url: c.score for c in candidates}
    assert scores == {
        "https://assets.fanart.tv/bad.jpg": 0.0,
        "https://assets.fanart.tv/good.jpg": 5.0,
    }


class _ExplodingClient:
    """Fails any test that lets TMDBClient reach the network."""

    async def get(self, *args, **kwargs):
        raise AssertionError("TMDBClient should not make an HTTP request")


async def test_tmdb_client_skips_season_poster_without_season_number():
    client = TMDBClient(token="t", language_order=["en"], client=_ExplodingClient())
    request = ArtRequest(art_kind=SEASON_POSTER, is_movie=False, tmdb_id=123)
    assert await client.fetch(request) == []


async def test_tmdb_client_skips_title_card_without_episode_number():
    client = TMDBClient(token="t", language_order=["en"], client=_ExplodingClient())
    request = ArtRequest(
        art_kind=TITLE_CARD, is_movie=False, tmdb_id=123, season_number=1
    )
    assert await client.fetch(request) == []


class _RecordingCache:
    """A ProviderCache stand-in that records the keys asked for.

    Always a miss, so every fetch reaches the transport and then writes back --
    which is what makes the recorded key the real one the cache would use.
    """

    def __init__(self):
        self.keys: list[str] = []

    async def get(self, key: str):
        self.keys.append(key)
        return None

    async def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        pass


def _recording_tmdb(language_order: list[str], cache=None):
    """A TMDB client whose transport records the URL it was asked for."""
    seen: list[httpx.URL] = []

    async def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={"posters": []})

    client = TMDBClient(
        token="t",
        language_order=language_order,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=cache,
    )
    return client, seen


# The cache key the render path's TMDB poster request has always produced, for
# ["xx", "en"] against /movie/550/images. Hard-coded rather than recomputed from
# the client's own params: recomputing would follow any change to them and pin
# nothing. A changed digest here means every cached provider response in every
# deployment was just orphaned -- which is fine for the deliberately-wider
# browse variant and not fine for the default.
RENDER_PATH_POSTER_CACHE_KEY = (
    "904c42080c0e7467156877871638ea5a4a51088929a34d0cd0f925036d5f3e64"
)


async def test_tmdb_narrows_by_language_by_default():
    client, seen = _recording_tmdb(["xx", "en"])

    await client.fetch(ArtRequest(art_kind=POSTER, is_movie=True, tmdb_id=550))

    assert seen[0].params["include_image_language"] == "null,en"


async def test_tmdb_all_languages_drops_the_language_filter_entirely():
    """TMDB has no "every language" token -- omitting include_image_language is
    how the endpoint returns the whole set, which is what a browse wants."""
    client, seen = _recording_tmdb(["xx", "en"])

    await client.fetch(
        ArtRequest(art_kind=POSTER, is_movie=True, tmdb_id=550), all_languages=True
    )

    assert "include_image_language" not in seen[0].params
    assert str(seen[0]) == "https://api.themoviedb.org/3/movie/550/images"


async def test_the_default_tmdb_cache_key_is_unchanged_by_the_browse_variant():
    """The render path's cache entries must survive this feature. A different
    key for the default call would silently re-fetch every image list in every
    deployment on the next pass."""
    cache = _RecordingCache()
    client, _seen = _recording_tmdb(["xx", "en"], cache=cache)

    await client.fetch(ArtRequest(art_kind=POSTER, is_movie=True, tmdb_id=550))

    assert cache.keys == [RENDER_PATH_POSTER_CACHE_KEY]


async def test_the_browse_variant_gets_its_own_cache_key():
    """A wider response cached under the narrow key would poison the render
    path with images in languages the config excluded."""
    cache = _RecordingCache()
    client, _seen = _recording_tmdb(["xx", "en"], cache=cache)

    await client.fetch(
        ArtRequest(art_kind=POSTER, is_movie=True, tmdb_id=550), all_languages=True
    )

    assert cache.keys != [RENDER_PATH_POSTER_CACHE_KEY]


def _tvdb_client(handler) -> TVDBClient:
    return TVDBClient("key", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_tvdb_a_401_triggers_one_relogin_and_the_retry_succeeds():
    calls = {"login": 0, "artworks": 0}

    async def handler(request):
        if request.url.path == "/v4/login":
            calls["login"] += 1
            return httpx.Response(200, json={"data": {"token": f"tok-{calls['login']}"}})
        calls["artworks"] += 1
        if request.headers.get("Authorization") == "Bearer tok-1":
            return httpx.Response(401)
        return httpx.Response(200, json={"data": {"artworks": []}})

    client = _tvdb_client(handler)
    request = ArtRequest(art_kind=POSTER, is_movie=False, tvdb_id=999)
    result = await client.fetch(request)

    assert result == []
    assert calls["login"] == 2  # initial cold login, then exactly one re-login after the 401
    assert calls["artworks"] == 2  # the original request, then one retry


async def test_tvdb_concurrent_fetches_on_a_cold_client_produce_one_login_call():
    calls = {"login": 0}

    async def handler(request):
        if request.url.path == "/v4/login":
            calls["login"] += 1
            return httpx.Response(200, json={"data": {"token": "tok"}})
        return httpx.Response(200, json={"data": {"artworks": []}})

    client = _tvdb_client(handler)
    request = ArtRequest(art_kind=POSTER, is_movie=False, tvdb_id=999)
    await asyncio.gather(*(client.fetch(request) for _ in range(5)))

    assert calls["login"] == 1


async def test_tvdb_season_poster_resolves_the_season_id_then_fetches_its_artwork():
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "tok"}})
        if request.url.path == "/v4/series/999/extended":
            return httpx.Response(200, json={"data": {"seasons": [
                {"id": 42, "number": 1, "type": {"type": "official"}},
                {"id": 43, "number": 2, "type": {"type": "official"}},
            ]}})
        if request.url.path == "/v4/seasons/42/extended":
            return httpx.Response(200, json={"data": {"artworks": [
                {
                    "type": 7, "image": "https://artworks.thetvdb.com/s1.jpg",
                    "language": None, "width": 680, "height": 1000, "score": 5,
                },
            ]}})
        return httpx.Response(404)

    client = _tvdb_client(handler)
    request = ArtRequest(
        art_kind=SEASON_POSTER, is_movie=False, tvdb_id=999, season_number=1
    )
    result = await client.fetch(request)

    assert len(result) == 1
    assert result[0].url == "https://artworks.thetvdb.com/s1.jpg"
    assert "/v4/series/999/extended" in calls
    assert "/v4/seasons/42/extended" in calls


async def test_tvdb_season_poster_returns_empty_when_the_series_has_no_matching_season():
    async def handler(request):
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "tok"}})
        if request.url.path == "/v4/series/999/extended":
            return httpx.Response(200, json={"data": {"seasons": [
                {"id": 42, "number": 1, "type": {"type": "official"}},
            ]}})
        return httpx.Response(404)

    client = _tvdb_client(handler)
    request = ArtRequest(
        art_kind=SEASON_POSTER, is_movie=False, tvdb_id=999, season_number=5
    )
    result = await client.fetch(request)

    assert result == []


async def test_list_entities_with_neither_id_nor_slug_raises_a_clear_error():
    """Fix round, finding 4: unreachable via the builder (``TvdbListParams``
    already enforces exactly one of them), but ``list_entities`` is a public
    client method and calling it with both None must not silently build
    ``/lists/slug/None`` and read out a confusing 404 -- it should say plainly
    that it got neither."""

    async def handler(request):
        raise AssertionError("no request should have been made")

    client = _tvdb_client(handler)
    with pytest.raises(TVDBListRefused, match="id or slug"):
        await client.list_entities()
