"""Rows 41, 44, 45, 46 -- the text/logo render rules.

Row 41 is the fail-open one: a provider that says NOTHING about text must be
treated as unknown and styled normally. That negative case is the first test.
"""
from pathlib import Path

from autoposter.config.loader import load_config
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import LOGO, ArtCandidate, ArtRequest
from autoposter.providers.fanart import parse_fanart
from autoposter.render import compositor, pipeline

EXAMPLE = Path("config/autoposter.example.yaml")


def movie(title="Heat", original_title=None):
    return ResolvedItem(
        rating_key="m1", library="Movies", kind="movie", title=title,
        year=1995, season_number=None, episode_number=None, root_folder="Heat",
        file_path=None, art_url=None, tmdb_id=1, tvdb_id=None, imdb_id="tt1",
        parent_rating_key=None, original_title=original_title,
    )


def candidate(includes_text=None, language="en"):
    return ArtCandidate(
        provider="TVDB", url="https://example.invalid/a.jpg", language=language,
        width=1000, height=1500, score=1.0, includes_text=includes_text,
    )


# --- row 41: SkipAddText -----------------------------------------------------

def test_a_provider_that_says_nothing_is_not_known_with_text():
    """Fail-open (facts adjudication 2). An English-tagged image with no
    includesText field is INFERRED to have text by is_textless, and that
    inference is deliberately not enough to strip an operator's styling."""
    assert candidate(includes_text=None).is_textless is False
    assert pipeline.known_with_text(candidate(includes_text=None)) is False


def test_an_explicit_includes_text_true_is_known_with_text():
    assert pipeline.known_with_text(candidate(includes_text=True)) is True


def test_an_explicit_includes_text_false_is_not_known_with_text():
    assert pipeline.known_with_text(candidate(includes_text=False)) is False


def test_a_manual_override_carries_no_candidate_and_is_not_known_with_text():
    assert pipeline.known_with_text(None) is False


# --- row 45: UseClearart -----------------------------------------------------

_FANART_TV = {
    "hdtvlogo": [{"url": "http://x/logo.png", "lang": "en", "likes": "3"}],
    "hdclearart": [{"url": "http://x/art.png", "lang": "en", "likes": "9"}],
}


def test_clearart_is_invisible_by_default():
    urls = [c.url for c in parse_fanart(_FANART_TV, LOGO, False, None)]
    assert urls == ["https://x/logo.png"]


def test_clearart_leads_when_prefer_clearart_is_set():
    urls = [
        c.url for c in
        parse_fanart(_FANART_TV, LOGO, False, None, prefer_clearart=True)
    ]
    assert urls == ["https://x/art.png", "https://x/logo.png"]


def test_prefer_clearart_defaults_to_false_on_the_request():
    assert ArtRequest(art_kind=LOGO, is_movie=True).prefer_clearart is False


def test_movie_clearart_uses_the_movie_key_names():
    payload = {
        "hdmovielogo": [{"url": "http://x/l.png", "lang": "en", "likes": "1"}],
        "hdmovieclearart": [{"url": "http://x/a.png", "lang": "en", "likes": "1"}],
    }
    urls = [
        c.url for c in
        parse_fanart(payload, LOGO, True, None, prefer_clearart=True)
    ]
    assert urls == ["https://x/a.png", "https://x/l.png"]


# --- row 46: logo recolour ---------------------------------------------------

def _style():
    return load_config(EXAMPLE).artwork.poster.text


def test_no_recolour_is_applied_by_default():
    argv = compositor.build_logo_argv("magick", "i.jpg", "l.png", _style(), "92%")
    assert "-colorize" not in argv


def test_a_flat_colour_is_applied_before_the_resize():
    argv = compositor.build_logo_argv(
        "magick", "i.jpg", "l.png", _style(), "92%", flat_color="white"
    )
    assert argv[argv.index("-fill") + 1] == "white"
    assert argv[argv.index("-colorize") + 1] == "100"
    assert argv.index("-colorize") < argv.index("-resize")


# --- row 44: UseOriginalTitle ------------------------------------------------

def test_the_localized_title_is_drawn_by_default():
    config = load_config(EXAMPLE)
    primary, _ = pipeline.title_text_for("poster", movie("Heat", "兵天使"), config)
    assert primary == "Heat"


def test_the_original_title_is_drawn_when_the_key_is_on():
    config = load_config(EXAMPLE)
    config = config.model_copy(update={
        "artwork": config.artwork.model_copy(update={"use_original_title": True}),
    })
    primary, _ = pipeline.title_text_for("poster", movie("Heat", "兵天使"), config)
    assert primary == "兵天使"


def test_an_item_with_no_original_title_falls_back_to_the_localized_one():
    """Fail-open: Plex carries originalTitle for movies and not for shows or
    episodes, so the absent case is the common case, not the corner one."""
    config = load_config(EXAMPLE)
    config = config.model_copy(update={
        "artwork": config.artwork.model_copy(update={"use_original_title": True}),
    })
    primary, _ = pipeline.title_text_for("poster", movie("Heat", None), config)
    assert primary == "Heat"


# --- row 41: render-decision -------------------------------------------------

async def test_a_with_text_candidate_suppresses_styling(session, tmp_path, monkeypatch):
    """The render decision, not just the predicate."""
    import httpx

    from autoposter.providers.ladder import Selection

    recorded = {}

    async def fake_compose(config, art_kind, working, **kwargs):
        recorded.update(kwargs)
        return pipeline.ComposeResult(output=working, truncated=False)

    async def fake_select(providers, order, request):
        return Selection(candidate=candidate(includes_text=True), is_fallback=False)

    async def fake_download(http, url, destination):
        destination.write_bytes(b"x")
        return "sha"

    monkeypatch.setattr(pipeline, "compose_styled", fake_compose)
    monkeypatch.setattr(pipeline, "select_artwork", fake_select)
    monkeypatch.setattr(pipeline, "_download", fake_download)

    config = load_config(EXAMPLE).model_copy(update={
        "assets_root": tmp_path / "out", "manual_assets_root": tmp_path / "man",
    })
    config = config.model_copy(update={
        "artwork": config.artwork.model_copy(update={
            "background": config.artwork.background.model_copy(
                update={"skip_add_text_when_with_text": True}
            ),
        }),
    })
    async with httpx.AsyncClient() as http:
        await pipeline.render_artifact(
            session, config, http, movie(), "background", []
        )
    assert recorded["suppress_styling"] is True
    assert recorded["draw_text"] is False


async def test_a_silent_candidate_leaves_styling_alone(session, tmp_path, monkeypatch):
    """The negative case: includes_text is None, so nothing is suppressed even
    with the key on."""
    import httpx

    from autoposter.providers.ladder import Selection

    recorded = {}

    async def fake_compose(config, art_kind, working, **kwargs):
        recorded.update(kwargs)
        return pipeline.ComposeResult(output=working, truncated=False)

    async def fake_select(providers, order, request):
        return Selection(candidate=candidate(includes_text=None), is_fallback=False)

    async def fake_download(http, url, destination):
        destination.write_bytes(b"x")
        return "sha"

    monkeypatch.setattr(pipeline, "compose_styled", fake_compose)
    monkeypatch.setattr(pipeline, "select_artwork", fake_select)
    monkeypatch.setattr(pipeline, "_download", fake_download)

    config = load_config(EXAMPLE).model_copy(update={
        "assets_root": tmp_path / "out", "manual_assets_root": tmp_path / "man",
    })
    config = config.model_copy(update={
        "artwork": config.artwork.model_copy(update={
            "background": config.artwork.background.model_copy(
                update={"skip_add_text_when_with_text": True}
            ),
        }),
    })
    async with httpx.AsyncClient() as http:
        await pipeline.render_artifact(
            session, config, http, movie(), "background", []
        )
    assert recorded["suppress_styling"] is False
