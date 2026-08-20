import json
from pathlib import Path

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, TITLE_CARD, ArtRequest,
)
from autoposter.providers.fanart import parse_fanart
from autoposter.providers.tmdb import TMDBClient, parse_tmdb_images
from autoposter.providers.tvdb import parse_tvdb_artworks

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
